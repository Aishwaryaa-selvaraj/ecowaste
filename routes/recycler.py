from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from db import execute, query, update
from utils import attach_items, notify, role_guard, to_float

bp = Blueprint("recycler", __name__, url_prefix="/recycler")
role_guard(bp, "recycler")


@bp.route("/")
def dashboard():
    uid = session["uid"]
    prof = query("SELECT u.name,u.email,u.phone,r.company,r.address FROM users u JOIN recyclers r ON r.user_id=u.id "
                 "WHERE u.id=%s", (uid,), one=True)
    stock = query("SELECT c.name, SUM(w.available_kg) kg FROM waste_records w JOIN waste_categories c ON c.id=w.category_id "
                  "WHERE w.listed=1 AND w.available_kg>0 GROUP BY c.name ORDER BY kg DESC")
    o = query("SELECT COALESCE(SUM(status='requested'),0) requested, COALESCE(SUM(status='accepted'),0) accepted, "
              "COALESCE(SUM(status='completed'),0) completed, COALESCE(SUM(CASE WHEN status='completed' THEN total END),0) spent "
              "FROM marketplace_orders WHERE recycler_id=%s", (uid,), one=True)
    txns = query("SELECT * FROM transactions WHERE user_id=%s ORDER BY created_at DESC, id DESC LIMIT 5", (uid,))
    d = query("SELECT COALESCE(SUM(status='delivered' AND recycling_stage IS NULL),0) awaiting, "
              "COALESCE(SUM(status='delivered' AND recycling_stage IS NOT NULL),0) active, "
              "COALESCE(SUM(status='completed'),0) completed, COALESCE(SUM(final_weight),0) kg "
              "FROM collection_requests WHERE recycler_id=%s AND status IN ('delivered','completed')", (uid,), one=True)
    return render_template("recycler/dashboard.html", prof=prof, stock=stock, o=o, d=d, txns=txns,
                           total_kg=sum(s["kg"] for s in stock))


@bp.route("/deliveries")
def deliveries():
    """Waste delivered to this recycler by collectors, with every material and its weight."""
    rows = attach_items(query(
        "SELECT r.id, r.status, r.recycling_stage, r.delivered_at, r.est_weight, r.est_value, r.final_weight, r.final_value, "
        "cu.name collector, cu.phone cphone, col.vehicle FROM collection_requests r "
        "JOIN users cu ON cu.id=r.collector_id JOIN collectors col ON col.user_id=r.collector_id "
        "WHERE r.recycler_id=%s AND r.status IN ('delivered','completed') "
        "ORDER BY r.delivered_at DESC, r.id DESC", (session["uid"],)))
    return render_template("recycler/deliveries.html", active=[r for r in rows if r["status"] == "delivered"],
                           done=[r for r in rows if r["status"] == "completed"])


@bp.route("/deliveries/<int:rid>/progress", methods=["POST"])
def progress(rid):
    """Move a delivery one step: received -> recycling in progress -> process completed."""
    uid = session["uid"]
    r = query("SELECT household_id, collector_id, status, recycling_stage FROM collection_requests "
              "WHERE id=%s AND recycler_id=%s", (rid, uid), one=True)
    if not r or r["status"] != "delivered":
        flash("This delivery can no longer be updated.", "warning")
        return redirect(url_for("recycler.deliveries"))
    base = "UPDATE collection_requests SET {} WHERE id=%s AND recycler_id=%s AND status='delivered' AND recycling_stage {}"
    if r["recycling_stage"] is None:
        n, msg = update(base.format("recycling_stage='received'", "IS NULL"), (rid, uid)), "received by the recycler"
    elif r["recycling_stage"] == "received":
        n, msg = update(base.format("recycling_stage='processing'", "='received'"), (rid, uid)), "being recycled"
    else:
        n, msg = update(base.format("status='completed'", "='processing'"), (rid, uid)), "recycled - process completed"
    if n:
        notify(r["household_id"], f"Your waste from pickup #{rid} is {msg}.")
        if msg.endswith("completed"):
            notify(r["collector_id"], f"Delivery #{rid} was recycled - process completed.")
        flash(f"Request #{rid}: {msg}.", "success")
    else:
        flash("This delivery was already updated.", "warning")
    return redirect(url_for("recycler.deliveries"))


@bp.route("/market")
def market():
    cid, q = request.args.get("cat", type=int), request.args.get("q", "").strip()
    sql = ("SELECT w.id, w.available_kg, w.ask_price_per_kg, w.recorded_at, c.name category, c.type, u.name collector, "
           "u.phone, u.email, col.area FROM waste_records w JOIN waste_categories c ON c.id=w.category_id "
           "JOIN users u ON u.id=w.collector_id JOIN collectors col ON col.user_id=u.id "
           "WHERE w.listed=1 AND w.available_kg>0")
    args = []
    if cid:
        sql += " AND w.category_id=%s"; args.append(cid)
    if q:
        sql += " AND (c.name LIKE %s OR u.name LIKE %s OR col.area LIKE %s)"; args += [f"%{q}%"] * 3
    rows = query(sql + " ORDER BY w.recorded_at DESC", tuple(args))
    cats = query("SELECT id, name FROM waste_categories WHERE type<>'non-recyclable' ORDER BY name")
    return render_template("recycler/market.html", rows=rows, cats=cats, cid=cid, q=q)


@bp.route("/market/<int:wid>/request", methods=["POST"])
def request_purchase(wid):
    uid = session["uid"]
    w = query("SELECT collector_id, available_kg, ask_price_per_kg FROM waste_records WHERE id=%s AND listed=1 "
              "AND available_kg>0", (wid,), one=True)
    qty, price = to_float(request.form.get("qty")), to_float(request.form.get("price"))
    if not w:
        flash("This material is no longer available.", "warning")
    elif qty is None or not 0 < qty <= w["available_kg"]:
        flash(f"Quantity must be between 0.1 and {w['available_kg']:g} kg.", "danger")
    elif price is None or not 0 < price <= 100000:
        flash("Enter a valid offer price per kg.", "danger")
    else:
        total = round(qty * price, 2)
        oid = execute("INSERT INTO marketplace_orders (record_id,recycler_id,collector_id,quantity_kg,offer_price_per_kg,total) "
                      "VALUES (%s,%s,%s,%s,%s,%s)", (wid, uid, w["collector_id"], qty, price, total))
        notify(w["collector_id"], f"New purchase request #{oid}: {qty:g} kg at ₹{price:g}/kg from {session['name']}.")
        flash(f"Purchase request #{oid} sent. Total offer ₹{total:,.2f}.", "success")
        return redirect(url_for("recycler.orders"))
    return redirect(url_for("recycler.market"))


@bp.route("/orders")
def orders():
    rows = query("SELECT o.*, c.name category, u.name collector, u.phone, u.email FROM marketplace_orders o "
                 "JOIN waste_records w ON w.id=o.record_id JOIN waste_categories c ON c.id=w.category_id "
                 "JOIN users u ON u.id=o.collector_id WHERE o.recycler_id=%s ORDER BY o.created_at DESC, o.id DESC",
                 (session["uid"],))
    return render_template("recycler/orders.html", rows=rows)


@bp.route("/orders/<int:oid>/cancel", methods=["POST"])
def cancel(oid):
    if update("UPDATE marketplace_orders SET status='cancelled' WHERE id=%s AND recycler_id=%s AND status='requested'",
              (oid, session["uid"])):
        flash("Order cancelled.", "info")
    else:
        flash("Only requested orders can be cancelled.", "warning")
    return redirect(url_for("recycler.orders"))


@bp.route("/history")
def history():
    txns = query("SELECT * FROM transactions WHERE user_id=%s ORDER BY created_at DESC, id DESC", (session["uid"],))
    return render_template("recycler/history.html", txns=txns)
