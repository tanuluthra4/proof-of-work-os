"""Small helpers shared by the test modules."""
from automata.states import WorkflowEvent as E
from database.db import get_db_connection, transaction
from services.automata_engine import AutomataEngine
from services.evidence_service import add_evidence, assign_reviewer

engine = AutomataEngine()


def make_user(app, username, role="developer"):
    with app.app_context(), transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO users (username, email, password, role) VALUES (?, ?, 'x', ?)",
            (username, f"{username}@example.com", role),
        )
        return cursor.lastrowid


def make_task(app, user_id, title="Build an API"):
    """Create a task + its workflow the same way the route does."""
    with app.app_context(), transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO tasks (user_id, title, status) VALUES (?, ?, 'Pending')", (user_id, title)
        )
        task_id = cursor.lastrowid
        workflow_id = engine.create_workflow(user_id, task_id, conn=conn)
    return task_id, workflow_id


def fire(app, workflow_id, event, actor_id, reason=None):
    with app.app_context():
        return engine.transition(workflow_id, event, actor_id, reason)


def add_evidence_item(app, workflow_id, user_id, kind="github_repo",
                      url="https://github.com/octocat/hello", title="", description=""):
    with app.app_context():
        conn = get_db_connection()
        try:
            return add_evidence(conn, workflow_id, user_id, kind, url, title, description)
        finally:
            conn.close()


def assign(app, workflow_id, owner_id, reviewer_id):
    """Send the workflow's evidence to a reviewer (by username), as the owner."""
    name = query(app, "SELECT username FROM users WHERE id = ?", (reviewer_id,))[0][0]
    with app.app_context():
        conn = get_db_connection()
        try:
            return assign_reviewer(conn, workflow_id, owner_id, name)
        finally:
            conn.close()


def advance_to_evidence_required(app, workflow_id, owner_id):
    fire(app, workflow_id, E.START, owner_id)
    fire(app, workflow_id, E.REQUEST_EVIDENCE, owner_id)


def advance_to_validating(app, workflow_id, owner_id, reviewer_id):
    advance_to_evidence_required(app, workflow_id, owner_id)
    add_evidence_item(app, workflow_id, owner_id)
    assign(app, workflow_id, owner_id, reviewer_id)
    fire(app, workflow_id, E.SUBMIT_EVIDENCE, owner_id)
    fire(app, workflow_id, E.START_VALIDATION, reviewer_id)


def query(app, sql, params=()):
    with app.app_context():
        conn = get_db_connection()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


def execute(app, sql, params=(), drop_triggers=False):
    """Run raw SQL, as an attacker with database access would."""
    with app.app_context():
        conn = get_db_connection()
        try:
            if drop_triggers:
                conn.execute("DROP TRIGGER IF EXISTS state_transitions_no_update")
                conn.execute("DROP TRIGGER IF EXISTS state_transitions_no_delete")
            conn.execute(sql, params)
        finally:
            conn.close()
