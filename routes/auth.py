import sqlite3

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database.db import get_db_connection, transaction
from routes.helpers import start_session, valid_email, valid_username

auth_bp = Blueprint("auth", __name__)


def _maybe_promote(conn, user_id, email):
    """Grant the reviewer role to e-mails listed in REVIEWER_EMAILS."""
    if email.lower() in current_app.config["REVIEWER_EMAILS"]:
        conn.execute("UPDATE users SET role = 'reviewer' WHERE id = ?", (user_id,))


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not valid_username(username):
            error = "Username must be 3-30 characters: letters, numbers, '.', '_' or '-'."
        elif not valid_email(email):
            error = "Enter a valid email address."
        elif not (8 <= len(password) <= 128):
            error = "Password must be between 8 and 128 characters."
        else:
            try:
                with transaction() as conn:
                    taken = conn.execute(
                        "SELECT username, email FROM users WHERE lower(email) = ? OR username = ?",
                        (email, username),
                    ).fetchone()
                    if taken:
                        error = ("Email already exists" if taken["email"].lower() == email
                                 else "Username already taken")
                    else:
                        cursor = conn.execute(
                            "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                            (username, email, generate_password_hash(password)),
                        )
                        user_id = cursor.lastrowid
                        conn.execute(
                            "INSERT INTO profiles (user_id, full_name) VALUES (?, ?)",
                            (user_id, username),
                        )
                        _maybe_promote(conn, user_id, email)
            except sqlite3.IntegrityError:
                error = "That username or email is already registered."
            else:
                if error is None:
                    start_session({"id": user_id, "username": username})
                    return redirect(url_for("main.dashboard"))

    return render_template("signup.html", error=error)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db_connection()
        try:
            user = conn.execute(
                "SELECT id, username, email, password FROM users WHERE lower(email) = ?",
                (email,),
            ).fetchone()
            if user and check_password_hash(user["password"], password):
                with transaction(conn):
                    _maybe_promote(conn, user["id"], user["email"])
                start_session(user)
                return redirect(url_for("main.dashboard"))
        finally:
            conn.close()

        error = "Invalid email or password"

    return render_template("login.html", error=error)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
