import logging
import os
import secrets

import click
from flask import Flask, g, render_template, request, session

from config import Config
from database.db import get_db_connection, transaction
from database.migrations import run_migrations
from routes.auth import auth_bp
from routes.helpers import check_csrf, csrf_token, load_current_user
from routes.main import main_bp
from routes.projects import projects_bp
from routes.tasks import tasks_bp

log = logging.getLogger(__name__)


def init_db(database_path=None):
    """Create/upgrade the database. Safe to run any number of times."""
    conn = get_db_connection(database_path)
    try:
        schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "database", "schema.sql")
        with open(schema_path, "r", encoding="utf-8") as schema_file:
            conn.executescript(schema_file.read())
        conn.execute("PRAGMA journal_mode = WAL")
        return run_migrations(conn)
    finally:
        conn.close()


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    if not app.config["SECRET_KEY"]:
        if app.config["ENV"] == "production":
            raise RuntimeError("SECRET_KEY must be set when POW_ENV=production.")
        # Development/test only: sessions reset on every restart.
        app.config["SECRET_KEY"] = secrets.token_hex(32)
        log.warning("SECRET_KEY is not set; using a temporary random key.")

    for blueprint in (main_bp, auth_bp, projects_bp, tasks_bp):
        app.register_blueprint(blueprint)

    app.before_request(load_current_user)
    app.before_request(check_csrf)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.context_processor
    def inject_user():
        return {"current_user": g.get("user")}

    def _error_page(code, title, message):
        return render_template("error.html", code=code, title=title, message=message), code

    @app.errorhandler(400)
    def bad_request(error):
        return _error_page(400, "Request rejected", getattr(error, "description", None)
                           or "The request could not be processed.")

    @app.errorhandler(403)
    def forbidden(error):
        return _error_page(403, "Not allowed", "You do not have permission to view this page.")

    @app.errorhandler(404)
    def not_found(error):
        return _error_page(404, "Not found", "That page does not exist, or it is not yours to see.")

    @app.errorhandler(413)
    def too_large(error):
        return _error_page(413, "Request too large", "That request was too large.")

    @app.errorhandler(500)
    def server_error(error):
        # Details go to the log, never to the user.
        return _error_page(500, "Something went wrong", "An unexpected error occurred.")

    @app.cli.command("init-db")
    def init_db_command():
        """Create or upgrade the database."""
        version = init_db(app.config["DATABASE"])
        click.echo(f"Database ready (schema version {version}).")

    @app.cli.command("set-role")
    @click.argument("email")
    @click.argument("role", type=click.Choice(["developer", "reviewer"]))
    def set_role_command(email, role):
        """Grant or revoke the reviewer role for an account."""
        with transaction() as conn:
            cursor = conn.execute(
                "UPDATE users SET role = ? WHERE lower(email) = ?", (role, email.strip().lower())
            )
        click.echo(f"{cursor.rowcount} account updated.")

    init_db(app.config["DATABASE"])
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)
