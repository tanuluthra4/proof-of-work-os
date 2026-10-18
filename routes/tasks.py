from flask import Blueprint, render_template, request, redirect, session, flash

from database.db import get_db_connection
from services.automata_engine import AutomataEngine
from automata.states import WorkflowEvent

tasks_bp = Blueprint("tasks", __name__)

automata_engine = AutomataEngine()


@tasks_bp.route("/add-task", methods=["GET", "POST"])
def add_task():
    if "user_id" not in session:
        return redirect("/login")

    conn = get_db_connection()

    projects = conn.execute(
        """
        SELECT id, title
        FROM projects
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (session["user_id"],),
    ).fetchall()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        project_id = request.form.get("project_id") or None
        user_id = session["user_id"]

        if not title:
            conn.close()
            return render_template(
                "add_task.html",
                projects=projects,
                error="Task title is required.",
            )

        cursor = conn.execute(
            """
            INSERT INTO tasks (
                user_id,
                project_id,
                title,
                status
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                user_id,
                project_id,
                title,
                "Pending",
            ),
        )

        task_id = cursor.lastrowid

        conn.commit()
        conn.close()

        # Every task starts its proof workflow in CREATED state.
        automata_engine.create_workflow(
            user_id=user_id,
            task_id=task_id,
        )

        return redirect("/dashboard")

    conn.close()

    return render_template(
        "add_task.html",
        projects=projects,
    )


@tasks_bp.route("/delete-task/<int:task_id>")
def delete_task(task_id):
    if "user_id" not in session:
        return redirect("/login")

    conn = get_db_connection()

    conn.execute(
        """
        DELETE FROM tasks
        WHERE id = ? AND user_id = ?
        """,
        (task_id, session["user_id"]),
    )

    conn.commit()
    conn.close()

    return redirect("/dashboard")


@tasks_bp.route("/complete-task/<int:task_id>")
def complete_task(task_id):
    if "user_id" not in session:
        return redirect("/login")

    conn = get_db_connection()

    conn.execute(
        """
        UPDATE tasks
        SET status = 'Completed',
            completed_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
        """,
        (task_id, session["user_id"]),
    )

    conn.commit()
    conn.close()

    return redirect("/dashboard")


@tasks_bp.route("/tasks/<int:task_id>/start", methods=["POST"])
def start_task_workflow(task_id):
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    conn = get_db_connection()

    try:
        # Confirm that this task belongs to the logged-in user.
        task = conn.execute(
            """
            SELECT id
            FROM tasks
            WHERE id = ? AND user_id = ?
            """,
            (task_id, user_id),
        ).fetchone()

        if task is None:
            return "Task not found.", 404

        # Find this task's workflow, scoped to the current user.
        workflow = conn.execute(
            """
            SELECT id, current_state
            FROM workflow_instances
            WHERE task_id = ? AND user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id, user_id),
        ).fetchone()

        if workflow is None:
            return "Workflow not found for this task.", 404

        workflow_id = workflow["id"]

    finally:
        conn.close()

    try:
        automata_engine.transition(
            workflow_id,
            WorkflowEvent.START,
            reason="Task workflow started by its owner.",
        )
    except ValueError as error:
        flash(str(error), "error")

    return redirect("/dashboard")

@tasks_bp.route(
    "/tasks/<int:task_id>/request-evidence",
    methods=["POST"]
)
def request_evidence(task_id):
    if "user_id" not in session:
        return redirect("/login")

    conn = get_db_connection()

    workflow = conn.execute(
        """
        SELECT w.id
        FROM workflow_instances w
        JOIN tasks t ON t.id = w.task_id
        WHERE t.id = ?
          AND t.user_id = ?
          AND w.user_id = ?
        ORDER BY w.id DESC
        LIMIT 1
        """,
        (task_id, session["user_id"], session["user_id"]),
    ).fetchone()

    conn.close()

    if workflow is None:
        flash("No workflow exists for this task.", "error")
        return redirect("/dashboard")

    try:
        automata_engine.transition(
            workflow["id"],
            WorkflowEvent.REQUEST_EVIDENCE,
            reason="Evidence requested by task owner.",
        )
        flash("Evidence requested successfully.", "success")
    except ValueError as error:
        flash(str(error), "error")

    return redirect("/dashboard")