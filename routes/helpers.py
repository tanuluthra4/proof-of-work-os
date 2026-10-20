import hmac
import re
import secrets
from functools import wraps
from urllib.parse import urlparse

from flask import abort, flash, g, redirect, request, session, url_for

from database.db import get_db_connection


# --------------------------------------------------------------------- CSRF
def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def check_csrf():
    """Reject any state-changing request that lacks the session's CSRF token."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    expected = session.get("_csrf", "")
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not expected or not hmac.compare_digest(expected, supplied):
        abort(400, description="Your session expired or the form was invalid. Reload the page and try again.")


# --------------------------------------------------------------------- auth
def load_current_user():
    """Attach the logged-in user (with a fresh role from the DB) to `g.user`."""
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, username, email, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        session.clear()
        return
    g.user = row


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def reviewer_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "reviewer":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def developer_required(view):
    """Developer-only pages. Reviewers are sent to their own queue."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] == "reviewer":
            return redirect(url_for("tasks.review_queue"))
        return view(*args, **kwargs)

    return wrapped


def start_session(user_row):
    session.clear()
    session.permanent = True
    session["user_id"] = user_row["id"]
    session["user"] = user_row["username"]


# --------------------------------------------------------------- validation
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")


def valid_email(value):
    return bool(value) and len(value) <= 254 and bool(_EMAIL.match(value))


def valid_username(value):
    return bool(_USERNAME.match(value or ""))


def clean_http_url(value):
    """Return a safe http(s) URL or None. Blocks javascript:/data: links."""
    value = (value or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a valid http(s) URL.")
    return value[:500]


def flash_errors(problems):
    for problem in problems:
        flash(problem, "error")
