from flask import Blueprint, render_template, request, redirect, session
from database.db import get_db_connection

projects_bp = Blueprint('projects', __name__)

@projects_bp.route('/projects')
def projects():
    if 'user_id' not in session:
        return redirect('/login')

    conn = get_db_connection()

    projects = conn.execute(
        """
        SELECT
            p.id,
            p.title,
            p.description,
            p.status,
            p.start_date,
            p.end_date,
            p.github_url,
            p.live_url,
            COUNT(t.id) AS task_count,
            SUM(
                CASE
                    WHEN t.status = 'Completed' THEN 1
                    ELSE 0
                END
            ) AS completed_tasks
        FROM projects p
        LEFT JOIN tasks t ON p.id = t.project_id
        WHERE p.user_id = ?
        GROUP BY p.id
        ORDER BY p.created_at DESC 
        """,
        (session['user_id'],)
    ).fetchall()

    conn.close()

    return render_template('projects/projects.html', projects=projects)

@projects_bp.route('/projects/new', methods=['GET', 'POST'])
def create_project():
    if 'user_id' not in session:
        return redirect('/login')

    if request.method == 'POST':
        title = request.form['title'].strip()
        description = request.form.get('description', '').strip()
        status = request.form.get('status', 'active').strip()
        start_date = request.form.get('start_date') or None
        end_date = request.form.get('end_date') or None
        github_url = request.form.get('github_url', '').strip() or None
        live_url = request.form.get('live_url', '').strip() or None
        user_id = session['user_id']

        if not title:
            error = "Title is required."
            return render_template('projects/new_project.html', error=error)

        conn = get_db_connection()

        conn.execute(
            """
            INSERT INTO projects (
                title, 
                description, 
                status, 
                start_date, 
                end_date, 
                github_url, 
                live_url, 
                user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (title, description, status, start_date, end_date, github_url, live_url, user_id)
        )

        conn.commit()
        conn.close()

        return redirect('/projects')

    return render_template('projects/new_project.html')