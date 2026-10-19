from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from database.db import get_db_connection, transaction
from routes.helpers import clean_http_url, login_required

projects_bp = Blueprint("projects", __name__)

PROJECT_STATUSES = ("Active", "Pending", "Completed", "Paused")


@projects_bp.route("/projects")
@login_required
def projects():
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                p.id, p.title, p.description, p.status, p.start_date, p.end_date,
                p.github_url, p.live_url,
                COUNT(t.id) AS task_count,
                SUM(CASE WHEN w.current_state = 'VERIFIED' THEN 1 ELSE 0 END) AS verified_tasks
            FROM projects p
            LEFT JOIN tasks t ON p.id = t.project_id
            LEFT JOIN workflow_instances w ON w.task_id = t.id
            WHERE p.user_id = ?
            GROUP BY p.id
            ORDER BY p.created_at DESC
            """,
            (g.user["id"],),
        ).fetchall()
    finally:
        conn.close()

    return render_template("projects/projects.html", projects=rows)


@projects_bp.route("/projects/new", methods=["GET", "POST"])
@login_required
def create_project():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        status = request.form.get("status", "Active").strip()
        start_date = request.form.get("start_date") or None
        end_date = request.form.get("end_date") or None

        problems = []
        if not title:
            problems.append("Title is required.")
        elif len(title) > 200:
            problems.append("Title must be at most 200 characters.")
        if len(description) > 2000:
            problems.append("Description must be at most 2000 characters.")
        if status not in PROJECT_STATUSES:
            problems.append("Choose a valid status.")

        urls = {}
        for field in ("github_url", "live_url"):
            try:
                urls[field] = clean_http_url(request.form.get(field))
            except ValueError:
                problems.append(f"{field.replace('_', ' ').capitalize()}: enter a valid http(s) URL.")

        if problems:
            for problem in problems:
                flash(problem, "error")
            return render_template("projects/new_project.html", statuses=PROJECT_STATUSES,
                                   form=request.form)

        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO projects (title, description, status, start_date, end_date,
                                      github_url, live_url, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (title, description, status, start_date, end_date,
                 urls["github_url"], urls["live_url"], g.user["id"]),
            )
        flash("Project created.", "success")
        return redirect(url_for("projects.projects"))

    return render_template("projects/new_project.html", statuses=PROJECT_STATUSES, form={})
