from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from db import execute, query, transaction, update
from utils import MAX_COLLECT_KG, attach_items, load_items, notify, pay, role_guard, to_float

bp = Blueprint("collector", __name__, url_prefix="/collector")
role_guard(bp, "collector")
OPEN_SQL = ("SELECT r.id, r.est_weight, r.est_value, r.final_weight, r.final_value, r.pickup_date, r.pickup_time, "
            "r.address, r.lat, r.lng, u.name household "
            "FROM collection_requests r JOIN users u ON u.id=r.household_id "
            "WHERE r.status='pending' AND r.id NOT IN (SELECT request_id FROM pickup_assignments WHERE collector_id=%s "
            "AND status='rejected') ORDER BY r.pickup_date, r.pickup_time")


@bp.route("/")
def dashboard():
    uid = session["uid"]
    prof = query("SELECT u.name,u.email,u.phone,c.vehicle,c.area,c.available FROM users u JOIN collectors c "
                 "ON c.user_id=u.id WHERE u.id=%s", (uid,), one=True)
    s = query("SELECT COALESCE(SUM(status IN ('assigned','in_progress','collected')),0) active, "
              "COALESCE(SUM(status='delivered'),0) delivered, "
              "COALESCE(SUM(status='completed'),0) completed FROM collection_requests WHERE collector_id=%s", (uid,), one=True)
    s["open"] = len(query(OPEN_SQL, (uid,)))
    rating = query("SELECT ROUND(AVG(rating),1) avg, COUNT(*) n FROM feedback WHERE collector_id=%s", (uid,), one=True)
    t = query("SELECT COALESCE(SUM(CASE WHEN direction='credit' THEN amount END),0) cr, "
              "COALESCE(SUM(CASE WHEN direction='debit' THEN amount END),0) db FROM transactions WHERE user_id=%s", (uid,), one=True)
    recent = attach_items(query("SELECT r.id, r.status, r.pickup_date, r.est_weight, r.est_value, r.final_weight, r.final_value, "
                                "u.name household FROM collection_requests r JOIN users u ON u.id=r.household_id "
                                "WHERE r.collector_id=%s ORDER BY r.created_at DESC, r.id DESC LIMIT 6", (uid,)))
    return render_template("collector/dashboard.html", prof=prof, s=s, rating=rating, t=t, recent=recent)


@bp.route("/availability", methods=["POST"])
def availability():
    update("UPDATE collectors SET available = 1 - available WHERE user_id=%s", (session["uid"],))
    flash("Availability updated.", "success")
    return redirect(request.referrer or url_for("collector.dashboard"))


@bp.route("/requests")
def requests():
    return render_template("collector/requests.html", rows=attach_items(query(OPEN_SQL, (session["uid"],))))


@bp.route("/requests/<int:rid>/<action>", methods=["POST"])
def respond(rid, action):
    uid = session["uid"]
    if action not in ("accept", "reject"):
        flash("Unknown action.", "danger")
        return redirect(url_for("collector.requests"))
    if action == "reject":
        execute("INSERT INTO pickup_assignments (request_id,collector_id,status) SELECT id,%s,'rejected' FROM "
                "collection_requests WHERE id=%s AND status='pending' ON DUPLICATE KEY UPDATE status='rejected'", (uid, rid))
        flash("Request hidden from your list.", "info")
        return redirect(url_for("collector.requests"))
    if not query("SELECT available FROM collectors WHERE user_id=%s", (uid,), one=True)["available"]:
        flash("You are marked unavailable. Turn availability on to accept pickups.", "warning")
        return redirect(url_for("collector.requests"))
    r = query("SELECT household_id FROM collection_requests WHERE id=%s", (rid,), one=True)
    with transaction():
        n = update("UPDATE collection_requests SET status='assigned', collector_id=%s WHERE id=%s AND status='pending'", (uid, rid))
        if n:
            execute("INSERT INTO pickup_assignments (request_id,collector_id,status) VALUES (%s,%s,'accepted') "
                    "ON DUPLICATE KEY UPDATE status='accepted'", (rid, uid))
    if n:
        notify(r["household_id"], f"Collector {session['name']} accepted your pickup #{rid}.")
        flash(f"Pickup #{rid} accepted. Find it under My Pickups.", "success")
        return redirect(url_for("collector.pickups"))
    flash("This request is no longer available.", "warning")
    return redirect(url_for("collector.requests"))


@bp.route("/pickups")
def pickups():
    rows = attach_items(query(
        "SELECT r.id, r.status, r.est_weight, r.est_value, r.final_weight, r.final_value, r.pickup_date, r.pickup_time, "
        "r.address, r.lat, r.lng, u.name household, u.phone FROM collection_requests r JOIN users u ON u.id=r.household_id "
        "WHERE r.collector_id=%s AND r.status IN ('assigned','in_progress','collected') "
        "ORDER BY r.pickup_date, r.pickup_time", (session["uid"],)))
    recyclers = query("SELECT y.user_id id, y.company, y.address FROM recyclers y ORDER BY y.company")
    return render_template("collector/pickups.html", rows=rows, recyclers=recyclers)


def _record_weighing(rid, r, uid):
    """Validate the weight of every material, then store weights, values and totals. Returns an error text or None."""
    items = load_items([rid]).get(rid, [])
    if not items:
        return "This request has no materials."
    rows, errs = [], []
    for i in items:
        wt = to_float(request.form.get(f"w_{i['category_id']}"))
        if wt is None or not 0 <= wt <= MAX_COLLECT_KG:
            errs.append(f"Enter the actual weight of {i['name']} (0 - {MAX_COLLECT_KG} kg).")
        else:
            price = i["cur_price"] or 0          # price per kg is always taken from the price table on the server
            rows.append((i["category_id"], round(wt, 2), price, round(wt * price, 2)))
    if errs:
        return " ".join(errs)
    if not any(w > 0 for _, w, _, _ in rows):
        return "At least one material must have a weight above 0."
    total_wt, total_val = round(sum(x[1] for x in rows), 2), round(sum(x[3] for x in rows), 2)
    with transaction():
        if not update("UPDATE collection_requests SET status='collected', final_weight=%s, final_value=%s "
                      "WHERE id=%s AND status='in_progress'", (total_wt, total_val, rid)):
            return "This pickup was already updated."
        for cid, wt, price, val in rows:
            execute("INSERT INTO waste_records (request_id,collector_id,category_id,actual_weight,price_per_kg,final_value,"
                    "available_kg) VALUES (%s,%s,%s,%s,%s,%s,%s)", (rid, uid, cid, wt, price, val, wt))
        if total_val > 0:
            ref = pay(uid, "debit", total_val, f"Payout to household for pickup #{rid}", request_id=rid)
            pay(r["household_id"], "credit", total_val, f"Payment for pickup #{rid}", request_id=rid, ref=ref)
    notify(r["household_id"], f"Pickup #{rid} collected: {total_wt:g} kg in total, final value ₹{total_val:,.2f}. "
                              "See the full breakdown under Request Status.")
    flash(f"Waste collected: {total_wt:g} kg, final value ₹{total_val:,.2f} (payment recorded, simulated).", "success")
    return None


@bp.route("/pickups/<int:rid>/advance", methods=["POST"])
def advance(rid):
    uid = session["uid"]
    r = query("SELECT * FROM collection_requests WHERE id=%s AND collector_id=%s", (rid, uid), one=True)
    if not r:
        flash("Pickup not found.", "danger")
        return redirect(url_for("collector.pickups"))
    st = r["status"]
    if st == "assigned":
        if update("UPDATE collection_requests SET status='in_progress' WHERE id=%s AND status='assigned'", (rid,)):
            notify(r["household_id"], f"Collection in progress for pickup #{rid}.")
            flash("Status: Collection in Progress. Weigh each material and enter the actual weights.", "success")
    elif st == "in_progress":
        err = _record_weighing(rid, r, uid)
        if err:
            flash(err, "danger")
    elif st == "collected":
        rec_id = request.form.get("recycler_id", type=int)
        rec = query("SELECT y.user_id, y.company FROM recyclers y WHERE y.user_id=%s", (rec_id,), one=True) if rec_id else None
        if not rec:
            flash("Choose the recycler you are delivering to.", "danger")
            return redirect(url_for("collector.pickups"))
        with transaction():
            n = update("UPDATE collection_requests SET status='delivered', recycler_id=%s, recycling_stage=NULL, "
                       "delivered_at=NOW() WHERE id=%s AND status='collected'", (rec["user_id"], rid))
            if n:   # the waste has left the collector's stock, so it can no longer be listed or sold
                update("UPDATE waste_records SET available_kg=0, listed=0 WHERE request_id=%s", (rid,))
        if n:
            notify(r["household_id"], f"Pickup #{rid}: your waste was delivered to {rec['company']}.")
            notify(rec["user_id"], f"New delivery: request #{rid} from {session['name']} ({(r['final_weight'] or 0):g} kg).")
            flash(f"Delivered to {rec['company']}. The recycler will update the recycling progress.", "success")
    return redirect(url_for("collector.pickups"))


@bp.route("/history")
def history():
    rows = attach_items(query(
        "SELECT r.id, r.status, r.recycling_stage, r.pickup_date, r.address, r.est_weight, r.est_value, r.final_weight, "
        "r.final_value, r.delivered_at, u.name household, y.company recycler FROM collection_requests r "
        "JOIN users u ON u.id=r.household_id LEFT JOIN recyclers y ON y.user_id=r.recycler_id "
        "WHERE r.collector_id=%s AND r.status IN ('delivered','completed') "
        "ORDER BY r.created_at DESC, r.id DESC", (session["uid"],)))
    return render_template("collector/history.html", rows=rows)


@bp.route("/inventory")
def inventory():
    rows = query("SELECT w.id, w.request_id, w.actual_weight, w.available_kg, w.listed, w.ask_price_per_kg, w.price_per_kg, "
                 "c.name category, c.type FROM waste_records w JOIN waste_categories c ON c.id=w.category_id "
                 "WHERE w.collector_id=%s AND w.available_kg>0 ORDER BY w.recorded_at DESC", (session["uid"],))
    return render_template("collector/inventory.html", rows=rows)


@bp.route("/inventory/<int:wid>/<action>", methods=["POST"])
def stock(wid, action):
    uid = session["uid"]
    if action == "list":
        ask = to_float(request.form.get("ask"))
        if ask is None or not 0 <= ask <= 100000:
            flash("Enter a valid asking price per kg.", "danger")
        elif update("UPDATE waste_records w JOIN waste_categories c ON c.id=w.category_id SET w.listed=1, w.ask_price_per_kg=%s "
                    "WHERE w.id=%s AND w.collector_id=%s AND c.type<>'non-recyclable'", (ask, wid, uid)):
            flash("Listed on the Recycler Marketplace.", "success")
        else:
            flash("Non-recyclable waste cannot be listed.", "warning")
    elif action == "unlist":
        update("UPDATE waste_records SET listed=0 WHERE id=%s AND collector_id=%s", (wid, uid))
        flash("Removed from the marketplace.", "info")
    return redirect(url_for("collector.inventory"))


@bp.route("/orders")
def orders():
    rows = query("SELECT o.*, r.company, u.phone, u.email, c.name category FROM marketplace_orders o "
                 "JOIN recyclers r ON r.user_id=o.recycler_id JOIN users u ON u.id=o.recycler_id "
                 "JOIN waste_records w ON w.id=o.record_id JOIN waste_categories c ON c.id=w.category_id "
                 "WHERE o.collector_id=%s ORDER BY o.created_at DESC, o.id DESC", (session["uid"],))
    return render_template("collector/orders.html", rows=rows)


@bp.route("/orders/<int:oid>/<action>", methods=["POST"])
def order_action(oid, action):
    uid = session["uid"]
    o = query("SELECT * FROM marketplace_orders WHERE id=%s AND collector_id=%s", (oid, uid), one=True)
    if not o or action not in ("accept", "reject", "complete"):
        flash("Order not found.", "danger")
        return redirect(url_for("collector.orders"))
    if action == "accept":
        n = 0
        try:
            with transaction():
                n = update("UPDATE marketplace_orders SET status='accepted' WHERE id=%s AND status='requested'", (oid,))
                if n and not update("UPDATE waste_records SET available_kg=available_kg-%s WHERE id=%s AND available_kg>=%s",
                                    (o["quantity_kg"], o["record_id"], o["quantity_kg"])):
                    raise ValueError("insufficient stock")  # rolls back the status change
        except ValueError:
            n = 0
            flash("Not enough stock left for this order.", "danger")
            return redirect(url_for("collector.orders"))
        msg = ("accepted", "Order accepted. Stock reserved.") if n else (None, None)
    elif action == "reject":
        n = update("UPDATE marketplace_orders SET status='rejected' WHERE id=%s AND status='requested'", (oid,))
        msg = ("rejected", "Order rejected.") if n else (None, None)
    else:
        with transaction():
            n = update("UPDATE marketplace_orders SET status='completed' WHERE id=%s AND status='accepted'", (oid,))
            if n:
                ref = pay(o["recycler_id"], "debit", o["total"], f"Purchase order #{oid}", order_id=oid)
                pay(uid, "credit", o["total"], f"Sale for order #{oid}", order_id=oid, ref=ref)
        msg = ("completed", "Order completed and payment recorded (simulated).") if n else (None, None)
    if msg[0]:
        notify(o["recycler_id"], f"Your purchase order #{oid} was {msg[0]} by {session['name']}.")
        flash(msg[1], "success")
    else:
        flash("This order can no longer be changed.", "warning")
    return redirect(url_for("collector.orders"))


@bp.route("/earnings")
def earnings():
    uid = session["uid"]
    t = query("SELECT COALESCE(SUM(CASE WHEN direction='credit' THEN amount END),0) cr, "
              "COALESCE(SUM(CASE WHEN direction='debit' THEN amount END),0) db FROM transactions WHERE user_id=%s", (uid,), one=True)
    monthly = query("SELECT DATE_FORMAT(created_at,'%%Y-%%m') m, SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END) cr, "
                    "SUM(CASE WHEN direction='debit' THEN amount ELSE 0 END) db FROM transactions WHERE user_id=%s "
                    "GROUP BY m ORDER BY m DESC LIMIT 12", (uid,))
    txns = query("SELECT * FROM transactions WHERE user_id=%s ORDER BY created_at DESC, id DESC", (uid,))
    return render_template("collector/earnings.html", t=t, monthly=monthly[::-1], txns=txns)
