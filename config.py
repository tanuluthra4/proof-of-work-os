"""Application configuration, read from environment variables.

Nothing secret is hardcoded. See README.md for the full variable list.
"""
import os
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "database")

# Kept for backwards compatibility with code that imports DB_PATH directly.
DB_PATH = os.getenv("DATABASE_PATH", os.path.join(DATABASE_DIR, "pow_os.db"))


def _csv_env(name):
    raw = os.getenv(name, "")
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


class Config:
    # "production" turns on Secure cookies and refuses to start without SECRET_KEY.
    ENV = os.getenv("POW_ENV", "development").lower()

    DATABASE = DB_PATH
    SECRET_KEY = os.getenv("SECRET_KEY") or None

    # Roles are exclusive account types chosen at sign-up: "developer" or "reviewer".
    # A reviewer only ever sees evidence that a developer explicitly sent to them.
    # If REVIEWER_OPEN_SIGNUP is false, only e-mails listed in REVIEWER_EMAILS may
    # register as reviewers (use this on a public deployment).
    REVIEWER_EMAILS = _csv_env("REVIEWER_EMAILS")
    REVIEWER_OPEN_SIGNUP = os.getenv("REVIEWER_OPEN_SIGNUP", "1").lower() not in ("0", "false", "no")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = ENV == "production"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    MAX_CONTENT_LENGTH = 1024 * 1024  # 1 MB; there are no file uploads
    JSON_SORT_KEYS = True

    TESTING = False
