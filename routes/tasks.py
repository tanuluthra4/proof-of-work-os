import re
import secrets
from collections import OrderedDict

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from automata.states import WorkflowEvent, WorkflowState
from automata.transitions import events_available_to, get_allowed_events
from database.db import get_db_connection, transaction
from routes.helpers import developer_required, flash_errors, login_required, reviewer_required
from services.automata_engine import AutomataEngine, TransitionBlocked, WorkflowNotFound
from services.evidence_service import (
    EVIDENCE_TYPES,
    EvidenceError,
    EvidenceNotAllowed,
    add_evidence,
    assign_reviewer,
    delete_draft_evidence,
    list_evidence,
    score_evidence,
)
from services.fsm_view import EVENT_LABELS, STATE_LABELS, build_fsm_view

tasks_bp = Blueprint("tasks", __name__)
engine = AutomataEngine()

PRIORITIES = ("low", "medium", "high")
LOCKED_FOR_EDIT = {"EVIDENCE_SUBMITTED", "VALIDATING", "VERIFIED"}
# Reviewers may open a task only once its evidence has been submitted.
REVIEWER_VISIBLE = {"EVIDENCE_SUBMITTED", "VALIDATING", "VERIFIED", "REJECTED"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------------ helpers
def _load_task(conn, task_id):
    return conn.execute(
        """
        SELECT t.id, t.user_id, t.project_id, t.title, t.description, t.status, t.priority,
               t.due_date, t.created_at, p.title AS project_title,
               w.id AS workflow_id, w.current_state, w.round, w.result,
               w.completed_at AS decided_at, w.reviewer_id, w.share_token,
               u.username AS owner_name, rv.username AS reviewer_name
        FROM tasks t
        JOIN workflow_instances w ON w.task_id = t.id
        JOIN users u ON u.id = t.user_id
        LEFT JOIN users rv ON rv.id = w.reviewer_id
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE t.id = ?
        """,
        (task_id,),
    ).fetchone()


def _access_for(task):
    """'owner', 'reviewer' or None. None must look like a 404 to the caller."""
    if task is None:
        return None
    if task["user_id"] == g.user["id"]:
        return "owner"
    # A reviewer only ever sees evidence that was sent to *them*.
    if (g.user["role"] == "reviewer" and task["reviewer_id"] == g.user["id"]
            and task["current_state"] in REVIEWER_VISIBLE):
        return "reviewer"
    return None


def _owned_task_or_404(conn, task_id):
    task = _load_task(conn, task_id)
    if task is None or task["user_id"] != g.user["id"]:
        abort(404)
    return task


def _user_projects(conn):
    return conn.execute(
        "SELECT id, title FROM projects WHERE user_id = ? ORDER BY created_at DESC",
        (g.user["id"],),
    ).fetchall()


def _read_task_form(conn):
    """Validate the task form. Returns (values, problems)."""
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip() or None
    priority = request.form.get("priority", "medium")
    due_date = request.form.get("due_date", "").strip() or None
    project_id = request.form.get("project_id", "").strip() or None
    problems = []

    if not title:
        problems.append("Task title is required.")
    elif len(title) > 200:
        problems.append("Task title must be at most 200 characters.")
    if description and len(description) > 2000:
        problems.append("Description must be at most 2000 characters.")
    if priority not in PRIORITIES:
        problems.append("Choose a valid priority.")
    if due_date and not _DATE.match(due_date):
        problems.append("Due date must look like YYYY-MM-DD.")

    if project_id is not None:
        owned = (
            conn.execute(
                "SELECT id FROM projects WHERE id = ? AND user_id = ?",
                (project_id, g.user["id"]),
            ).fetchone()
            if project_id.isdigit() else None
        )
        if owned is None:
            problems.append("Choose one of your own projects.")
        else:
            project_id = owned["id"]

    values = {
        "title": title, "description": description, "priority": priority,
        "due_date": due_date, "project_id": project_id,
    }
    return values, problems


def _redirect_after_event():
    if request.form.get("next") == "dashboard":
        return url_for("main.dashboard")
    return None


# -------------------------------------------------------------- create/edit
@tasks_bp.route("/add-task", methods=["GET", "POST"])
@developer_required
def add_task():
    conn = get_db_connection()
    try:
        projects = _user_projects(conn)
        if request.method == "POST":
            values, problems = _read_task_form(conn)
            if problems:
                flash_errors(problems)
                return render_template("task_form.html", projects=projects, task=values,
                                       mode="create", priorities=PRIORITIES)

            # The task and its workflow are created together or not at all.
            with transaction(conn):
                cursor = conn.execute(
                    """
                    INSERT INTO tasks (user_id, project_id, title, description, status,
                                       priority, due_date)
                    VALUES (?, ?, ?, ?, 'Pending', ?, ?)
                    """,
                    (g.user["id"], values["project_id"], values["title"],
                     values["description"], values["priority"], values["due_date"]),
                )
                task_id = cursor.lastrowid
                engine.create_workflow(g.user["id"], task_id, conn=conn)

            flash("Task created. Its proof workflow starts in CREATED.", "success")
            return redirect(url_for("tasks.task_detail", task_id=task_id))

        return render_template("task_form.html", projects=projects, task={}, mode="create",
                               priorities=PRIORITIES)
    finally:
        conn.close()


@tasks_bp.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@developer_required
def edit_task(task_id):
    conn = get_db_connection()
    try:
        task = _owned_task_or_404(conn, task_id)
        if task["current_state"] in LOCKED_FOR_EDIT:
            flash("This task is locked while its evidence is under review or after verification.",
                  "error")
            return redirect(url_for("tasks.task_detail", task_id=task_id))

        projects = _user_projects(conn)
        if request.method == "POST":
            values, problems = _read_task_form(conn)
            if problems:
                flash_errors(problems)
                values["id"] = task_id
                return render_template("task_form.html", projects=projects, task=values,
                                       mode="edit", priorities=PRIORITIES)
            with transaction(conn):
                conn.execute(
                    """
                    UPDATE tasks SET title = ?, description = ?, project_id = ?,
                                     priority = ?, due_date = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (values["title"], values["description"], values["project_id"],
                     values["priority"], values["due_date"], task_id, g.user["id"]),
                )
            flash("Task updated.", "success")
            return redirect(url_for("tasks.task_detail", task_id=task_id))

        return render_template("task_form.html", projects=projects, task=task, mode="edit",
                               priorities=PRIORITIES)
    finally:
        conn.close()


@tasks_bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
@developer_required
def delete_task(task_id):
    conn = get_db_connection()
    try:
        task = _owned_task_or_404(conn, task_id)
        if task["current_state"] == "VERIFIED":
            flash("Verified tasks are permanent proof records and cannot be deleted.", "error")
            return redirect(url_for("tasks.task_detail", task_id=task_id))
        with transaction(conn):
            conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, g.user["id"]))
        flash("Task deleted.", "success")
    finally:
        conn.close()
    return redirect(url_for("main.dashboard"))


@tasks_bp.route("/tasks/<int:task_id>/complete", methods=["POST"])
@developer_required
def complete_task(task_id):
    """Self-reported completion. It does NOT verify anything."""
    conn = get_db_connection()
    try:
        _owned_task_or_404(conn, task_id)
        with transaction(conn):
            conn.execute(
                "UPDATE tasks SET status = 'Completed', "
                "completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP) "
                "WHERE id = ? AND user_id = ?",
                (task_id, g.user["id"]),
            )
        flash("Marked done (self-reported). Submit evidence to get it verified.", "success")
    finally:
        conn.close()
    return redirect(_redirect_after_event() or url_for("tasks.task_detail", task_id=task_id))


# ------------------------------------------------------------------- detail
@tasks_bp.route("/tasks/<int:task_id>")
@login_required
def task_detail(task_id):
    conn = get_db_connection()
    try:
        task = _load_task(conn, task_id)
        access = _access_for(task)
        if access is None:
            abort(404)

        is_owner = access == "owner"
        is_reviewer = access == "reviewer"
        state = WorkflowState(task["current_state"])
        workflow_id = task["workflow_id"]

        history = engine.get_history(workflow_id)
        actions = events_available_to(state, is_owner, is_reviewer)
        fsm = build_fsm_view(task["current_state"], history, actions)

        evidence_by_round = OrderedDict()
        for item in list_evidence(conn, workflow_id):
            evidence_by_round.setdefault(item["round"], []).append(item)
        current_round_items = evidence_by_round.get(task["round"], [])
        # While REJECTED the round counter has not advanced yet, so the rejected
        # submission is still "current"; after RESTART a new empty round starts.
        scored_items = current_round_items or (
            next(iter(evidence_by_round.values()), []) if evidence_by_round else []
        )

        probe_events = [e for e in WorkflowEvent if e not in get_allowed_events(state)]

        return render_template(
            "task_detail.html",
            task=task,
            is_owner=is_owner,
            is_reviewer=is_reviewer,
            state=state,
            state_label=STATE_LABELS[state],
            actions=actions,
            event_labels=EVENT_LABELS,
            probe_events=probe_events,
            fsm=fsm,
            history=history,
            blocked=engine.get_blocked_actions(workflow_id),
            chain=engine.verify_history(workflow_id),
            evidence_by_round=evidence_by_round,
            current_round=task["round"],
            evidence_score=score_evidence(scored_items),
            evidence_types=EVIDENCE_TYPES,
            can_add_evidence=is_owner and state == WorkflowState.EVIDENCE_REQUIRED,
            can_edit=is_owner and task["current_state"] not in LOCKED_FOR_EDIT,
            can_assign=is_owner and (state == WorkflowState.EVIDENCE_REQUIRED or (
                state == WorkflowState.EVIDENCE_SUBMITTED and task["reviewer_id"] is None)),
            share_url=(url_for("public.proof", token=task["share_token"], _external=True)
                       if task["share_token"] else None),
        )
    finally:
        conn.close()


# ------------------------------------------------------- workflow events
@tasks_bp.route("/tasks/<int:task_id>/events/<event_name>", methods=["POST"])
@login_required
def fire_event(task_id, event_name):
    conn = get_db_connection()
    try:
        task = _load_task(conn, task_id)
        if _access_for(task) is None:
            abort(404)
    finally:
        conn.close()

    try:
        result = engine.transition(
            task["workflow_id"], event_name, g.user["id"], request.form.get("reason")
        )
        label = EVENT_LABELS.get(WorkflowEvent(result["event"]), result["event"])
        flash(f"{label}: {result['from_state']} → {result['to_state']}.", "success")
    except TransitionBlocked as blocked:
        flash(f"Blocked ({blocked.code}): {blocked}", "error")
    except WorkflowNotFound:
        abort(404)

    return redirect(_redirect_after_event() or url_for("tasks.task_detail", task_id=task_id))


# ---------------------------------------------------------------- evidence
@tasks_bp.route("/tasks/<int:task_id>/evidence", methods=["POST"])
@developer_required
def submit_evidence_item(task_id):
    conn = get_db_connection()
    try:
        task = _owned_task_or_404(conn, task_id)
        try:
            add_evidence(
                conn,
                task["workflow_id"],
                g.user["id"],
                request.form.get("evidence_type"),
                request.form.get("evidence_url"),
                request.form.get("title"),
                request.form.get("description"),
            )
            flash("Evidence added as a draft. Submit it for review when you are ready.", "success")
        except EvidenceError as error:
            flash_errors(error.problems)
        except EvidenceNotAllowed as error:
            flash(str(error), "error")
    finally:
        conn.close()
    return redirect(url_for("tasks.task_detail", task_id=task_id))


@tasks_bp.route("/tasks/<int:task_id>/evidence/<int:evidence_id>/delete", methods=["POST"])
@developer_required
def remove_evidence_item(task_id, evidence_id):
    conn = get_db_connection()
    try:
        task = _owned_task_or_404(conn, task_id)
        try:
            delete_draft_evidence(conn, task["workflow_id"], g.user["id"], evidence_id)
            flash("Draft evidence removed.", "success")
        except EvidenceNotAllowed as error:
            flash(str(error), "error")
    finally:
        conn.close()
    return redirect(url_for("tasks.task_detail", task_id=task_id))


# ------------------------------------------------- reviewer + sharing
@tasks_bp.route("/tasks/<int:task_id>/reviewer", methods=["POST"])
@developer_required
def choose_reviewer(task_id):
    conn = get_db_connection()
    try:
        task = _owned_task_or_404(conn, task_id)
        try:
            name = assign_reviewer(conn, task["workflow_id"], g.user["id"],
                                   request.form.get("reviewer"))
            flash(f"Evidence will be sent to reviewer {name}.", "success")
        except EvidenceError as error:
            flash_errors(error.problems)
        except EvidenceNotAllowed as error:
            flash(str(error), "error")
    finally:
        conn.close()
    return redirect(url_for("tasks.task_detail", task_id=task_id))


@tasks_bp.route("/tasks/<int:task_id>/share", methods=["POST"])
@developer_required
def share_task(task_id):
    """Create a public, read-only link to a VERIFIED proof (private until you do this)."""
    conn = get_db_connection()
    try:
        task = _owned_task_or_404(conn, task_id)
        if task["current_state"] != "VERIFIED":
            flash("Only verified proofs can be shared.", "error")
        else:
            with transaction(conn):
                conn.execute(
                    "UPDATE workflow_instances SET share_token = COALESCE(share_token, ?) WHERE id = ?",
                    (secrets.token_urlsafe(18), task["workflow_id"]),
                )
            flash("Public link created. Anyone with the link can view this verified proof.", "success")
    finally:
        conn.close()
    return redirect(url_for("tasks.task_detail", task_id=task_id))


@tasks_bp.route("/tasks/<int:task_id>/unshare", methods=["POST"])
@developer_required
def unshare_task(task_id):
    conn = get_db_connection()
    try:
        task = _owned_task_or_404(conn, task_id)
        with transaction(conn):
            conn.execute("UPDATE workflow_instances SET share_token = NULL WHERE id = ?",
                         (task["workflow_id"],))
        flash("Public link disabled.", "success")
    finally:
        conn.close()
    return redirect(url_for("tasks.task_detail", task_id=task_id))


# ------------------------------------------------------------ review queue
@tasks_bp.route("/review")
@reviewer_required
def review_queue():
    conn = get_db_connection()
    try:
        base = """
            SELECT t.id AS task_id, t.title, u.username AS owner_name, w.current_state,
                   w.round, w.result, w.completed_at AS decided_at,
                   (SELECT COUNT(*) FROM evidence e
                     WHERE e.workflow_id = w.id AND e.round = w.round) AS evidence_count,
                   (SELECT MAX(created_at) FROM state_transitions s
                     WHERE s.workflow_id = w.id) AS last_change
            FROM workflow_instances w
            JOIN tasks t ON t.id = w.task_id
            JOIN users u ON u.id = w.user_id
            WHERE w.reviewer_id = ? AND w.user_id != ? AND w.current_state IN (%s)
            ORDER BY last_change %s
        """
        me = g.user["id"]
        queue = conn.execute(base % ("'EVIDENCE_SUBMITTED', 'VALIDATING'", "ASC"), (me, me)).fetchall()
        decided = conn.execute(base % ("'VERIFIED', 'REJECTED'", "DESC"), (me, me)).fetchall()
    finally:
        conn.close()
    return render_template("review_queue.html", queue=queue, decided=decided)
