import re
import secrets
from datetime import date, datetime, timedelta

from flask import abort, flash, redirect, session, url_for

from db import execute, query

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?\d{10,13}$")

# Collection workflow shown to every role (stored in collection_requests.status).
STEPS = ["pending", "assigned", "in_progress", "collected", "delivered", "completed"]
STATUS_LABELS = {
    "pending": "Request Submitted", "assigned": "Request Accepted", "in_progress": "Collection in Progress",
    "collected": "Waste Collected", "delivered": "Waste Delivered to Recycler", "completed": "Process Completed",
    "cancelled": "Cancelled",
    "requested": "Requested", "accepted": "Accepted", "rejected": "Rejected",   # marketplace orders
}
# Recycler progress while a request is in the 'delivered' state (collection_requests.recycling_stage).
STAGE_LABELS = {None: "Awaiting recycler", "received": "Received by recycler", "processing": "Recycling in progress"}
ACTIVE_SQL = "('pending','assigned','in_progress','collected','delivered')"   # not yet completed or cancelled
PAID = "('collected','delivered','completed')"   # statuses where the weighing is final and the household is paid
PIN_RE = re.compile(r"^[1-9]\d{5}$")
MAX_REQ_KG, MAX_COLLECT_KG = 500, 2000


def role_guard(bp, role):
    """Every route in the blueprint requires a logged-in user with this exact role."""
    @bp.before_request
    def _check():
        if "uid" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))
        if session.get("role") != role:
            abort(403)


def to_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def valid_password(pw):
    return len(pw) >= 8 and re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)


def notify(user_id, message):
    execute("INSERT INTO notifications (user_id, message) VALUES (%s, %s)", (user_id, message[:255]))


def current_price(category_id):
    r = query("SELECT price_per_kg FROM waste_prices WHERE category_id=%s "
              "ORDER BY effective_date DESC, id DESC LIMIT 1", (category_id,), one=True)
    return r["price_per_kg"] if r else 0.0


def price_overview():
    """Every category with its current and previous price."""
    return query("""
        SELECT c.id, c.name, c.type, c.description,
          (SELECT price_per_kg FROM waste_prices p WHERE p.category_id=c.id
             ORDER BY effective_date DESC, id DESC LIMIT 1) AS cur,
          (SELECT price_per_kg FROM waste_prices p WHERE p.category_id=c.id
             ORDER BY effective_date DESC, id DESC LIMIT 1 OFFSET 1) AS prev
        FROM waste_categories c ORDER BY c.name""")


def pay(user_id, direction, amount, description, request_id=None, order_id=None, ref=None):
    """Sandbox payment: records a successful transaction (no real gateway is called)."""
    ref = ref or "SIM-" + secrets.token_hex(4).upper()
    execute("INSERT INTO transactions (user_id, request_id, order_id, direction, amount, description, reference) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)", (user_id, request_id, order_id, direction, round(amount, 2), description, ref))
    return ref


def parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_time(s):
    try:
        return datetime.strptime(s, "%H:%M").time()
    except (TypeError, ValueError):
        return None


def clean_coords(lat, lng):
    lat, lng = to_float(lat), to_float(lng)
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, None
    return lat, lng


def fmt_date(v):
    if isinstance(v, (date, datetime)):
        return v.strftime("%d %b %Y")
    return v or ""


def fmt_datetime(v):
    return v.strftime("%d %b %Y, %H:%M") if isinstance(v, datetime) else (v or "")


def fmt_time(v):
    if isinstance(v, timedelta):
        m = int(v.total_seconds() // 60)
        return f"{m // 60:02d}:{m % 60:02d}"
    return str(v or "")[:5]


# ---------- multiple materials per request ----------

def parse_items(form, cats):
    """Read the parallel category_id[] / weight[] lists of a request form.
    Returns (items, errors). Prices always come from the database, never from the browser."""
    ids, wts = form.getlist("category_id"), form.getlist("weight")
    if not ids:
        return [], ["Select at least one waste material."]
    if len(ids) != len(wts):
        return [], ["Enter a weight for every selected material."]
    by_id = {c["id"]: c for c in cats}
    items, seen, errs = [], set(), []
    for raw, w in zip(ids, wts):
        cid = int(raw) if str(raw).isdigit() else None
        c = by_id.get(cid)
        if not c:
            errs.append("An unknown waste material was selected.")
        elif cid in seen:
            errs.append(f"{c['name']} was added more than once - each material can be added only once.")
        else:
            seen.add(cid)
            wt = to_float(w)
            if wt is None or not 0 < wt <= MAX_REQ_KG:
                errs.append(f"Enter a valid weight for {c['name']} (more than 0 and up to {MAX_REQ_KG} kg).")
            else:
                price = c["cur"] or 0
                items.append(dict(category_id=cid, name=c["name"], weight=round(wt, 2), price=price,
                                  value=round(wt * price, 2)))
    return items, errs


def item_totals(items):
    """Total weight and total value = sum of the per-material figures (each already rounded to paise)."""
    return round(sum(i["weight"] for i in items), 2), round(sum(i["value"] for i in items), 2)


def prefill_rows(spec, cats):
    """'3:5,4:2.5' -> [{'id': '3', 'weight': '5'}, ...] (used by the estimator -> booking link)."""
    ok, rows = {str(c["id"]) for c in cats}, []
    for part in (spec or "").split(",")[:20]:
        cid, _, w = part.partition(":")
        if cid in ok and all(r["id"] != cid for r in rows):
            rows.append({"id": cid, "weight": w if to_float(w) is not None else ""})
    return rows


def load_items(request_ids):
    """{request_id: [item, ...]} with requested figures and, once weighed, the collector's actual figures."""
    ids = list(request_ids)
    if not ids:
        return {}
    rows = query(
        "SELECT i.request_id, i.category_id, c.name, i.est_weight, i.price_per_kg est_price, i.est_value, "
        "w.actual_weight, w.price_per_kg act_price, w.final_value, "
        "(SELECT price_per_kg FROM waste_prices p WHERE p.category_id=i.category_id "
        " ORDER BY effective_date DESC, id DESC LIMIT 1) cur_price "
        "FROM request_items i JOIN waste_categories c ON c.id=i.category_id "
        "LEFT JOIN waste_records w ON w.request_id=i.request_id AND w.category_id=i.category_id "
        f"WHERE i.request_id IN ({','.join(['%s'] * len(ids))}) ORDER BY c.name", tuple(ids))
    out = {}
    for r in rows:
        out.setdefault(r["request_id"], []).append(r)
    return out


def attach_items(rows):
    """Add row['items'] and a short row['materials'] label to every request row."""
    by_req = load_items([r["id"] for r in rows])
    for r in rows:
        r["items"] = by_req.get(r["id"], [])
        r["materials"] = ", ".join(i["name"] for i in r["items"]) or "-"
    return rows


# ---------- pickup address ----------

def split_address(text):
    """Separate a trailing 6-digit PIN code from a saved address so the form can show them in two fields."""
    text = (text or "").strip()
    m = re.search(r"[\s,-]*\b([1-9]\d{5})\s*$", text)
    return (text[:m.start()].strip(" ,-"), m.group(1)) if m else (text, "")


def build_address(addr, pin, landmark):
    """One complete address string, e.g. '12 Green St, RS Puram, Coimbatore, near Park - 641002'."""
    out = addr.strip().rstrip(",")
    if landmark:
        out += f", near {landmark}"
    return f"{out} - {pin}"
