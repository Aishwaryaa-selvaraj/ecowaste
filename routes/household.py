from datetime import date

from flask import Blueprint, abort, flash, jsonify, make_response, redirect, render_template, request, session, url_for

from db import execute, query, transaction, update
from utils import (ACTIVE_SQL, MAX_REQ_KG, PAID, PIN_RE, attach_items, build_address, clean_coords, item_totals,
                   load_items, notify, parse_date, parse_items, parse_time, prefill_rows, price_overview,
                   role_guard, split_address)

bp = Blueprint("household", __name__, url_prefix="/household")
role_guard(bp, "household")


def own_request(rid):
    """One of this household's requests, with its materials, collector and recycler details."""
    r = query("SELECT * FROM collection_requests WHERE id=%s AND household_id=%s", (rid, session["uid"]), one=True)
    if not r:
        abort(404)
    attach_items([r])
    r["collector"] = r["recycler"] = None
    if r["collector_id"]:
        r["collector"] = query("SELECT u.name, u.phone, c.vehicle FROM collectors c JOIN users u ON u.id=c.user_id "
                               "WHERE c.user_id=%s", (r["collector_id"],), one=True)
    if r["recycler_id"]:
        r["recycler"] = query("SELECT u.name, u.phone, y.company FROM recyclers y JOIN users u ON u.id=y.user_id "
                              "WHERE y.user_id=%s", (r["recycler_id"],), one=True)
    return r


@bp.route("/")
def dashboard():
    uid = session["uid"]
    s = query(f"SELECT COALESCE(SUM(status='completed'),0) completed, "
              f"COALESCE(SUM(status IN {ACTIVE_SQL}),0) pending FROM collection_requests WHERE household_id=%s",
              (uid,), one=True)
    kg = query("SELECT COALESCE(SUM(w.actual_weight),0) kg FROM waste_records w JOIN collection_requests r "
               "ON r.id=w.request_id WHERE r.household_id=%s", (uid,), one=True)["kg"]
    earn = query("SELECT COALESCE(SUM(amount),0) e FROM transactions WHERE user_id=%s AND direction='credit' "
                 "AND request_id IS NOT NULL", (uid,), one=True)["e"]
    bycat = query("SELECT c.name, SUM(w.actual_weight) kg FROM waste_records w JOIN collection_requests r ON r.id=w.request_id "
                  "JOIN waste_categories c ON c.id=w.category_id WHERE r.household_id=%s GROUP BY c.name ORDER BY kg DESC", (uid,))
    recent = attach_items(query("SELECT id, status, pickup_date, est_weight, est_value, final_weight, final_value "
                                "FROM collection_requests WHERE household_id=%s ORDER BY created_at DESC, id DESC LIMIT 6", (uid,)))
    notes = query("SELECT message, created_at FROM notifications WHERE user_id=%s ORDER BY created_at DESC, id DESC LIMIT 5", (uid,))
    return render_template("household/dashboard.html", s=s, kg=kg, earn=earn, bycat=bycat, recent=recent, notes=notes)


@bp.route("/scanner")
def scanner():
    cats = query("SELECT id, name, type FROM waste_categories ORDER BY name")
    return render_template("household/scanner.html", cats=cats)


@bp.route("/estimator")
def estimator():
    return render_template("household/estimator.html", cats=price_overview())


@bp.route("/prices")
def prices():
    return render_template("household/prices.html", cats=price_overview())


@bp.route("/api/price-history")
def price_history():
    cid = request.args.get("cat", type=int)
    rows = query("SELECT effective_date d, price_per_kg p FROM waste_prices WHERE category_id=%s ORDER BY effective_date, id", (cid,))
    return jsonify(labels=[r["d"].strftime("%b %Y") for r in rows], values=[r["p"] for r in rows])


@bp.route("/book", methods=["GET", "POST"])
def book():
    uid = session["uid"]
    cats = price_overview()
    if request.method == "POST":
        f = request.form
        items, errs = parse_items(f, cats)
        addr, pin, mark = f.get("address", "").strip(), f.get("pincode", "").strip(), f.get("landmark", "").strip()
        pd, pt = parse_date(f.get("date")), parse_time(f.get("time"))
        raw_lat, raw_lng = f.get("lat", "").strip(), f.get("lng", "").strip()
        lat, lng = clean_coords(raw_lat, raw_lng)
        if not 8 <= len(addr) <= 150:
            errs.append("Enter your pickup address: house/flat number, street, area and city (8-150 characters).")
        if not PIN_RE.match(pin):
            errs.append("Enter a valid 6-digit PIN code.")
        if len(mark) > 60:
            errs.append("Landmark can be at most 60 characters.")
        if not pd or pd < date.today():
            errs.append("Choose a pickup date today or later.")
        if not pt:
            errs.append("Choose a pickup time.")
        if (raw_lat or raw_lng) and lat is None:
            errs.append("The pin placed on the map is not valid. Pick the spot again or clear the pin.")
        if errs:
            for e in errs:
                flash(e, "danger")
            rows = [{"id": i, "weight": w} for i, w in zip(f.getlist("category_id"), f.getlist("weight"))]
            return render_template("household/book.html", cats=cats, form=f, rows=rows)
        full = build_address(addr, pin, mark)
        total_wt, total_val = item_totals(items)
        with transaction():
            rid = execute("INSERT INTO collection_requests (household_id,est_weight,est_value,pickup_date,pickup_time,"
                          "address,lat,lng) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                          (uid, total_wt, total_val, pd, pt.strftime("%H:%M"), full[:255], lat, lng))
            for i in items:
                execute("INSERT INTO request_items (request_id,category_id,est_weight,price_per_kg,est_value) "
                        "VALUES (%s,%s,%s,%s,%s)", (rid, i["category_id"], i["weight"], i["price"], i["value"]))
            notify(uid, f"Pickup #{rid} submitted for {pd:%d %b %Y}. We are finding a collector.")
            execute("INSERT INTO notifications (user_id, message) SELECT user_id, %s FROM collectors WHERE available=1",
                    (f"New pickup request #{rid}: {len(items)} material(s), ~{total_wt:g} kg.",))
        flash(f"Request submitted! Pickup #{rid} - {len(items)} material(s), {total_wt:g} kg, "
              f"estimated value ₹{total_val:,.2f}.", "success")
        return redirect(url_for("household.status", id=rid))
    h = query("SELECT address FROM households WHERE user_id=%s", (uid,), one=True) or {}
    text, pin = split_address(h.get("address"))
    form = {"address": text, "pincode": pin}
    return render_template("household/book.html", cats=cats, form=form,
                           rows=prefill_rows(request.args.get("items"), cats))


@bp.route("/status")
def status():
    """Latest status of every request (one page, no map or GPS)."""
    uid = session["uid"]
    reqs = query("SELECT id, status, pickup_date, est_weight FROM collection_requests WHERE household_id=%s "
                 "ORDER BY created_at DESC, id DESC LIMIT 30", (uid,))
    attach_items(reqs)
    sel = request.args.get("id", type=int) or (reqs[0]["id"] if reqs else None)
    return render_template("household/status.html", reqs=reqs, req=own_request(sel) if sel else None)


@bp.route("/cancel/<int:rid>", methods=["POST"])
def cancel(rid):
    own_request(rid)
    if update("UPDATE collection_requests SET status='cancelled' WHERE id=%s AND status='pending'", (rid,)):
        flash(f"Pickup #{rid} cancelled.", "info")
    else:
        flash("Only pending pickups can be cancelled.", "warning")
    return redirect(url_for("household.status", id=rid))


@bp.route("/history")
def history():
    uid = session["uid"]
    rows = attach_items(query(
        "SELECT r.id, r.status, r.recycling_stage, r.pickup_date, r.est_weight, r.est_value, r.final_weight, r.final_value, "
        "u.name collector, f.rating FROM collection_requests r LEFT JOIN users u ON u.id=r.collector_id "
        "LEFT JOIN feedback f ON f.request_id=r.id WHERE r.household_id=%s ORDER BY r.created_at DESC, r.id DESC", (uid,)))
    txns = query("SELECT * FROM transactions WHERE user_id=%s ORDER BY created_at DESC, id DESC", (uid,))
    return render_template("household/history.html", rows=rows, txns=txns, PAID=PAID)


@bp.route("/receipt/<int:rid>")
def receipt(rid):
    r = query("SELECT r.id, r.address, r.pickup_date, r.est_weight, r.est_value, r.final_weight, r.final_value, "
              "hu.name household, hu.phone hphone, cu.name collector, cu.phone cphone, "
              "(SELECT reference FROM transactions t WHERE t.request_id=r.id AND t.user_id=r.household_id LIMIT 1) ref, "
              "(SELECT MAX(recorded_at) FROM waste_records w WHERE w.request_id=r.id) recorded_at "
              f"FROM collection_requests r JOIN users hu ON hu.id=r.household_id JOIN users cu ON cu.id=r.collector_id "
              f"WHERE r.id=%s AND r.household_id=%s AND r.status IN {PAID}", (rid, session["uid"]), one=True)
    if not r:
        abort(404)
    r["items"] = load_items([rid]).get(rid, [])
    resp = make_response(render_template("household/receipt.html", r=r, dl=bool(request.args.get("dl"))))
    if request.args.get("dl"):
        resp.headers["Content-Disposition"] = f"attachment; filename=ecowaste-receipt-{rid}.html"
    return resp


@bp.route("/feedback/<int:rid>", methods=["POST"])
def feedback(rid):
    r = own_request(rid)
    rating, comment = request.form.get("rating", type=int), request.form.get("comment", "").strip()[:500]
    if r["status"] not in ("collected", "delivered", "completed") or not r["collector_id"] or rating not in range(1, 6):
        flash("Feedback needs a collected pickup and a rating of 1-5.", "danger")
    elif query("SELECT id FROM feedback WHERE request_id=%s", (rid,), one=True):
        flash("You already rated this pickup.", "warning")
    else:
        execute("INSERT INTO feedback (request_id,household_id,collector_id,rating,comment) VALUES (%s,%s,%s,%s,%s)",
                (rid, session["uid"], r["collector_id"], rating, comment))
        notify(r["collector_id"], f"You received a {rating}-star rating for pickup #{rid}.")
        flash("Thank you for your feedback!", "success")
    return redirect(url_for("household.history"))


@bp.route("/analytics")
def analytics():
    rows = query("SELECT DATE_FORMAT(w.recorded_at,'%%Y-%%m') m, SUM(w.actual_weight) total_kg, "
                 "SUM(CASE WHEN c.type='recyclable' THEN w.actual_weight ELSE 0 END) recyclable_kg, "
                 f"SUM(CASE WHEN r.status IN {PAID} THEN w.final_value ELSE 0 END) value, COUNT(DISTINCT r.id) pickups "
                 "FROM waste_records w JOIN collection_requests r ON r.id=w.request_id "
                 "JOIN waste_categories c ON c.id=w.category_id WHERE r.household_id=%s GROUP BY m ORDER BY m DESC LIMIT 12",
                 (session["uid"],))
    return render_template("household/analytics.html", rows=rows)
