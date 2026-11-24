import secrets

import pymysql
from flask import Flask, abort, render_template, request, session
from werkzeug.security import generate_password_hash

from config import Config
from db import close_db, execute, query
from routes import auth, collector, household, recycler
from utils import STAGE_LABELS, STATUS_LABELS, STEPS, fmt_date, fmt_datetime, fmt_time

app = Flask(__name__)
app.config.from_object(Config)
app.teardown_appcontext(close_db)
for m in (auth, household, collector, recycler):
    app.register_blueprint(m.bp)

# (endpoint, icon, translation-key, label)
NAVS = {
    "household": [
        ("household.dashboard", "bi-speedometer2", "dashboard", "Dashboard"),
        ("household.scanner", "bi-camera", "scanner", "Waste Scanner"),
        ("household.estimator", "bi-calculator", "estimator", "Value Estimator"),
        ("household.prices", "bi-graph-up", "prices", "Price Trends"),
        ("household.book", "bi-calendar-plus", "book", "Book Pickup"),
        ("household.status", "bi-card-checklist", "status", "Request Status"),
        ("household.history", "bi-clock-history", "history", "Collection History"),
        ("household.analytics", "bi-bar-chart", "analytics", "Monthly Analytics"),
    ],
    "collector": [
        ("collector.dashboard", "bi-speedometer2", "dashboard", "Dashboard"),
        ("collector.requests", "bi-inbox", "requests", "Pickup Requests"),
        ("collector.pickups", "bi-truck", "pickups", "My Pickups"),
        ("collector.history", "bi-clock-history", "history", "Collection History"),
        ("collector.inventory", "bi-boxes", "inventory", "My Stock"),
        ("collector.orders", "bi-receipt", "orders", "Purchase Requests"),
        ("collector.earnings", "bi-wallet2", "earnings", "Earnings"),
    ],
    "recycler": [
        ("recycler.dashboard", "bi-speedometer2", "dashboard", "Dashboard"),
        ("recycler.deliveries", "bi-box-seam", "deliveries", "Deliveries"),
        ("recycler.market", "bi-shop", "market", "Marketplace"),
        ("recycler.orders", "bi-receipt", "orders", "My Orders"),
        ("recycler.history", "bi-clock-history", "history", "Transactions"),
    ],
}


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
        if not session.get("_csrf") or not secrets.compare_digest(session["_csrf"], sent):
            abort(400)


@app.context_processor
def inject():
    unread = 0
    if "uid" in session:
        try:
            unread = query("SELECT COUNT(*) n FROM notifications WHERE user_id=%s AND is_read=0",
                           (session["uid"],), one=True)["n"]
        except Exception:
            pass
    return dict(csrf_token=csrf_token, NAV=NAVS.get(session.get("role"), []), unread=unread,
                STATUS_LABELS=STATUS_LABELS, STAGE_LABELS=STAGE_LABELS, STEPS=STEPS)


app.jinja_env.filters["inr"] = lambda v: "₹{:,.2f}".format(v or 0)
app.jinja_env.filters["fdate"] = fmt_date
app.jinja_env.filters["fdt"] = fmt_datetime
app.jinja_env.filters["ftime"] = fmt_time


@app.route("/")
def index():
    stats = dict(kg=0, pickups=0, households=0, collectors=0, recyclers=0)
    try:
        r = query("SELECT COALESCE(SUM(actual_weight),0) kg FROM waste_records", one=True)
        stats["kg"] = r["kg"]
        stats["pickups"] = query("SELECT COUNT(*) n FROM collection_requests WHERE status='completed'", one=True)["n"]
        for role in ("household", "collector", "recycler"):
            stats[role + "s"] = query("SELECT COUNT(*) n FROM users WHERE role=%s", (role,), one=True)["n"]
    except pymysql.MySQLError:
        pass  # homepage still renders if the database is unreachable
    return render_template("index.html", stats=stats)


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def client_error(e):
    return render_template("error.html", code=e.code, msg=e.description if e.code != 404 else "Page not found."), e.code


@app.errorhandler(Exception)
def server_error(e):
    if hasattr(e, "code") and isinstance(e.code, int) and e.code < 500:
        return e
    app.logger.exception(e)
    msg = "The database is not reachable. Check config.py / environment variables." \
        if isinstance(e, pymysql.err.OperationalError) else "Something went wrong. Please try again."
    return render_template("error.html", code=500, msg=msg), 500


@app.cli.command("seed-demo")
def seed_demo():
    """Create one demo account per role (password: Demo@1234). For presentations only."""
    pw = generate_password_hash("Demo@1234")
    demo = [("Demo Household", "household@demo.com", "household"),
            ("Demo Collector", "collector@demo.com", "collector"),
            ("Demo Recycler", "recycler@demo.com", "recycler")]
    with app.app_context():
        for name, email, role in demo:
            if query("SELECT id FROM users WHERE email=%s", (email,), one=True):
                continue
            uid = execute("INSERT INTO users (name,email,phone,password_hash,role) VALUES (%s,%s,%s,%s,%s)",
                          (name, email, "9876543210", pw, role))
            if role == "household":
                execute("INSERT INTO households (user_id,address) VALUES (%s,%s)", (uid, "12, Green Street, Coimbatore"))
            elif role == "collector":
                execute("INSERT INTO collectors (user_id,vehicle,area) VALUES (%s,%s,%s)", (uid, "Mini truck", "Coimbatore"))
            else:
                execute("INSERT INTO recyclers (user_id,company,address) VALUES (%s,%s,%s)", (uid, "Demo Recyclers Pvt Ltd", "Coimbatore"))
        print("Demo users ready: household@demo.com, collector@demo.com, recycler@demo.com (Demo@1234)")


if __name__ == "__main__":
    import os
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")
