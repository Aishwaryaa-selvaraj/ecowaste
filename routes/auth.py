import hashlib
import re
import secrets

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import execute, query, transaction, update
from utils import EMAIL_RE, PHONE_RE, valid_password

bp = Blueprint("auth", __name__)
ROLES = ("household", "collector", "recycler")


def _login_required():
    return "uid" not in session


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        name, email = f.get("name", "").strip(), f.get("email", "").strip().lower()
        phone, pw, role = re.sub(r"[\s-]", "", f.get("phone", "")), f.get("password", ""), f.get("role", "")
        address, vehicle = f.get("address", "").strip(), f.get("vehicle", "").strip()
        area, company = f.get("area", "").strip(), f.get("company", "").strip()
        errs = []
        if not 2 <= len(name) <= 100: errs.append("Enter your full name.")
        if not EMAIL_RE.match(email) or len(email) > 150: errs.append("Enter a valid email address.")
        if not PHONE_RE.match(phone): errs.append("Enter a valid phone number (10-13 digits).")
        if not valid_password(pw): errs.append("Password needs 8+ characters with letters and numbers.")
        if pw != f.get("confirm", ""): errs.append("Passwords do not match.")
        if role not in ROLES: errs.append("Choose a role: Household, Collector or Recycler.")
        if role == "household" and len(address) < 8: errs.append("Enter your full address.")
        if role == "collector" and (not vehicle or not area): errs.append("Enter your vehicle and service area.")
        if role == "recycler" and (not company or len(address) < 4): errs.append("Enter company name and address.")
        if not errs and query("SELECT id FROM users WHERE email=%s", (email,), one=True):
            errs.append("This email is already registered.")
        if errs:
            for e in errs: flash(e, "danger")
            return render_template("register.html", form=f)
        with transaction():
            uid = execute("INSERT INTO users (name,email,phone,password_hash,role) VALUES (%s,%s,%s,%s,%s)",
                          (name, email, phone, generate_password_hash(pw), role))
            if role == "household":
                execute("INSERT INTO households (user_id,address) VALUES (%s,%s)", (uid, address[:255]))
            elif role == "collector":
                execute("INSERT INTO collectors (user_id,vehicle,area) VALUES (%s,%s,%s)", (uid, vehicle[:60], area[:100]))
            else:
                execute("INSERT INTO recyclers (user_id,company,address) VALUES (%s,%s,%s)", (uid, company[:120], address[:255]))
        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("register.html", form={"role": request.args.get("role", "household")})


@bp.route("/login", methods=["GET", "POST"])
def login():
    if "uid" in session:
        return redirect(url_for(f"{session['role']}.dashboard"))
    if request.method == "POST":
        email, pw = request.form.get("email", "").strip().lower(), request.form.get("password", "")
        u = query("SELECT id,name,role,password_hash FROM users WHERE email=%s", (email,), one=True)
        if u and check_password_hash(u["password_hash"], pw):
            session.clear()
            session.update(uid=u["id"], role=u["role"], name=u["name"])
            return redirect(url_for(f"{u['role']}.dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    link = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        u = query("SELECT id FROM users WHERE email=%s", (email,), one=True)
        if u:
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode()).hexdigest()
            update("UPDATE users SET reset_token=%s, reset_expires=DATE_ADD(NOW(), INTERVAL 30 MINUTE) WHERE id=%s",
                   (digest, u["id"]))
            if current_app.config["DEMO_SHOW_RESET_LINK"]:
                link = url_for("auth.reset", token=token, _external=True)
        flash("If that email is registered, a reset link has been generated.", "info")
    return render_template("forgot.html", link=link)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    u = query("SELECT id FROM users WHERE reset_token=%s AND reset_expires > NOW()", (digest,), one=True)
    if not u:
        flash("This reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.forgot"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        if not valid_password(pw) or pw != request.form.get("confirm", ""):
            flash("Passwords must match and have 8+ characters with letters and numbers.", "danger")
        else:
            update("UPDATE users SET password_hash=%s, reset_token=NULL, reset_expires=NULL WHERE id=%s",
                   (generate_password_hash(pw), u["id"]))
            flash("Password updated. Please log in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("reset.html")


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    if _login_required():
        return redirect(url_for("auth.login"))
    uid, role = session["uid"], session["role"]
    if request.method == "POST":
        f = request.form
        name, phone = f.get("name", "").strip(), re.sub(r"[\s-]", "", f.get("phone", ""))
        errs = []
        if not 2 <= len(name) <= 100: errs.append("Enter a valid name.")
        if not PHONE_RE.match(phone): errs.append("Enter a valid phone number.")
        new_pw = f.get("new_password", "")
        if new_pw:
            u = query("SELECT password_hash FROM users WHERE id=%s", (uid,), one=True)
            if not check_password_hash(u["password_hash"], f.get("current_password", "")):
                errs.append("Current password is incorrect.")
            elif not valid_password(new_pw):
                errs.append("New password needs 8+ characters with letters and numbers.")
        a, b = f.get("field1", "").strip(), f.get("field2", "").strip()
        if role == "household" and len(a) < 8: errs.append("Enter your full address.")
        if role == "collector" and (not a or not b): errs.append("Vehicle and area are required.")
        if role == "recycler" and (not a or not b): errs.append("Company and address are required.")
        if errs:
            for e in errs: flash(e, "danger")
        else:
            with transaction():
                update("UPDATE users SET name=%s, phone=%s WHERE id=%s", (name, phone, uid))
                if new_pw:
                    update("UPDATE users SET password_hash=%s WHERE id=%s", (generate_password_hash(new_pw), uid))
                if role == "household":
                    update("UPDATE households SET address=%s WHERE user_id=%s", (a[:255], uid))
                elif role == "collector":
                    update("UPDATE collectors SET vehicle=%s, area=%s WHERE user_id=%s", (a[:60], b[:100], uid))
                else:
                    update("UPDATE recyclers SET company=%s, address=%s WHERE user_id=%s", (a[:120], b[:255], uid))
            session["name"] = name
            flash("Profile updated.", "success")
            return redirect(url_for("auth.profile"))
    user = query("SELECT name,email,phone FROM users WHERE id=%s", (uid,), one=True)
    if role == "household":
        extra = query("SELECT address AS f1 FROM households WHERE user_id=%s", (uid,), one=True)
        labels = ("Address", None)
    elif role == "collector":
        extra = query("SELECT vehicle AS f1, area AS f2 FROM collectors WHERE user_id=%s", (uid,), one=True)
        labels = ("Vehicle", "Service area")
    else:
        extra = query("SELECT company AS f1, address AS f2 FROM recyclers WHERE user_id=%s", (uid,), one=True)
        labels = ("Company", "Address")
    return render_template("profile.html", user=user, extra=extra, labels=labels)


@bp.route("/notifications")
def notifications():
    if _login_required():
        return redirect(url_for("auth.login"))
    rows = query("SELECT message,is_read,created_at FROM notifications WHERE user_id=%s "
                 "ORDER BY created_at DESC, id DESC LIMIT 50", (session["uid"],))
    update("UPDATE notifications SET is_read=1 WHERE user_id=%s AND is_read=0", (session["uid"],))
    return render_template("notifications.html", rows=rows)
