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

    # Accounts whose e-mail is listed here are granted the `reviewer` role
    # when they sign up or log in. Reviewers can approve/reject evidence for
    # tasks that belong to *other* users (never their own).
    REVIEWER_EMAILS = _csv_env("REVIEWER_EMAILS")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = ENV == "production"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    MAX_CONTENT_LENGTH = 1024 * 1024  # 1 MB; there are no file uploads
    JSON_SORT_KEYS = True

    TESTING = False
