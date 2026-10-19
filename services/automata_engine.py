"""Database-backed workflow engine.

Responsibilities, in the order a request is checked:

  1. the event exists                      -> UNKNOWN_EVENT
  2. delta(state, event) is defined        -> INVALID_TRANSITION   (automata/)
  3. the actor is allowed to fire it       -> UNAUTHORIZED_ACTOR
  4. business guards pass                  -> GUARD_FAILED         (evidence, reasons)
  5. state + hash-chained history + side effects are written in ONE
     transaction (BEGIN IMMEDIATE, compare-and-swap on the old state).

Anything that fails in 1-4 is recorded in `blocked_actions` (outside the
rolled-back transaction) so rejected attempts are visible in the UI.
"""
import sqlite3
from datetime import datetime, timezone

from automata.states import Actor, WorkflowEvent, WorkflowState
from automata.transitions import REQUIRED_ACTOR, get_allowed_events
from automata.verification_machine import VerificationMachine
from database.db import get_db_connection, transaction
from services.integrity import GENESIS_HASH, compute_hash, evidence_digest, verify_chain

MIN_REASON_LENGTH = 5


class TransitionBlocked(ValueError):
    """A requested transition was refused. `code` says why."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class WorkflowNotFound(LookupError):
    pass


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class AutomataEngine:

    # ------------------------------------------------------------------ create
    def create_workflow(self, user_id, task_id=None, conn=None):
        """Create the workflow for a task. Idempotent: a task has at most one.

        Pass `conn` to join the caller's transaction (e.g. task creation).
        """
        if conn is None:
            with transaction() as own:
                return self.create_workflow(user_id, task_id, conn=own)

        if task_id is not None:
            existing = conn.execute(
                "SELECT id FROM workflow_instances WHERE task_id = ?", (task_id,)
            ).fetchone()
            if existing:
                return existing["id"]

        cursor = conn.execute(
            "INSERT INTO workflow_instances (user_id, task_id, current_state) VALUES (?, ?, ?)",
            (user_id, task_id, WorkflowState.CREATED.value),
        )
        return cursor.lastrowid

    # -------------------------------------------------------------------- read
    def get_workflow(self, workflow_id):
        conn = get_db_connection()
        try:
            return conn.execute(
                "SELECT * FROM workflow_instances WHERE id = ?", (workflow_id,)
            ).fetchone()
        finally:
            conn.close()

    def get_workflow_for_task(self, task_id):
        conn = get_db_connection()
        try:
            return conn.execute(
                "SELECT * FROM workflow_instances WHERE task_id = ?", (task_id,)
            ).fetchone()
        finally:
            conn.close()

    def get_history(self, workflow_id):
        conn = get_db_connection()
        try:
            return conn.execute(
                """
                SELECT t.seq, t.from_state, t.event, t.to_state, t.condition_result,
                       t.reason, t.actor_id, t.actor_role, t.evidence_digest,
                       t.prev_hash, t.hash, t.created_at, u.username AS actor_name
                FROM state_transitions t
                LEFT JOIN users u ON u.id = t.actor_id
                WHERE t.workflow_id = ?
                ORDER BY t.seq ASC, t.id ASC
                """,
                (workflow_id,),
            ).fetchall()
        finally:
            conn.close()

    def get_blocked_actions(self, workflow_id, limit=10):
        conn = get_db_connection()
        try:
            return conn.execute(
                """
                SELECT b.event, b.from_state, b.code, b.message, b.created_at,
                       u.username AS actor_name
                FROM blocked_actions b
                LEFT JOIN users u ON u.id = b.actor_id
                WHERE b.workflow_id = ?
                ORDER BY b.id DESC LIMIT ?
                """,
                (workflow_id, limit),
            ).fetchall()
        finally:
            conn.close()

    def verify_history(self, workflow_id):
        conn = get_db_connection()
        try:
            return verify_chain(conn, workflow_id)
        finally:
            conn.close()

    # -------------------------------------------------------------- transition
    def transition(self, workflow_id, event, actor_id, reason=None):
        """Fire `event` on a workflow as `actor_id`.

        Returns {"from_state", "event", "to_state", "seq", "hash"}.
        Raises TransitionBlocked (a ValueError) when refused, WorkflowNotFound
        when the workflow does not exist. On any failure nothing is written.
        """
        reason = (reason or "").strip() or None
        conn = get_db_connection()
        event_name = event.value if isinstance(event, WorkflowEvent) else str(event)
        from_state = None
        try:
            try:
                with transaction(conn):
                    return self._apply(conn, workflow_id, event, actor_id, reason)
            except TransitionBlocked as blocked:
                from_state = self._peek_state(conn, workflow_id)
                self._log_blocked(conn, workflow_id, actor_id, event_name, from_state, blocked)
                raise
        finally:
            conn.close()

    @staticmethod
    def _peek_state(conn, workflow_id):
        row = conn.execute(
            "SELECT current_state FROM workflow_instances WHERE id = ?", (workflow_id,)
        ).fetchone()
        return row["current_state"] if row else None

    @staticmethod
    def _log_blocked(conn, workflow_id, actor_id, event_name, from_state, blocked):
        try:
            with transaction(conn):
                conn.execute(
                    """
                    INSERT INTO blocked_actions
                        (workflow_id, actor_id, event, from_state, code, message)
                    SELECT ?, ?, ?, ?, ?, ?
                    WHERE EXISTS (SELECT 1 FROM workflow_instances WHERE id = ?)
                    """,
                    (workflow_id, actor_id, event_name, from_state, blocked.code,
                     str(blocked), workflow_id),
                )
        except sqlite3.Error:
            # Logging a refusal must never mask the refusal itself.
            pass

    def _apply(self, conn, workflow_id, event, actor_id, reason):
        workflow = conn.execute(
            "SELECT * FROM workflow_instances WHERE id = ?", (workflow_id,)
        ).fetchone()
        if workflow is None:
            raise WorkflowNotFound("Workflow not found.")

        # 1. known event
        if not isinstance(event, WorkflowEvent):
            try:
                event = WorkflowEvent(str(event))
            except ValueError:
                raise TransitionBlocked(
                    "UNKNOWN_EVENT", f"'{event}' is not an event of this automaton."
                ) from None

        try:
            state = WorkflowState(workflow["current_state"])
        except ValueError:
            raise TransitionBlocked(
                "CORRUPT_STATE",
                f"Workflow has an unknown stored state '{workflow['current_state']}'.",
            ) from None

        # 2. is delta(state, event) defined?
        machine = VerificationMachine(state)
        if not machine.can_transition(event):
            allowed = ", ".join(e.value for e in get_allowed_events(state)) or "none (terminal state)"
            raise TransitionBlocked(
                "INVALID_TRANSITION",
                f"{event.value} is not allowed in state {state.value}. "
                f"Events accepted here: {allowed}.",
            )

        # 3. is this actor allowed to fire it? (role is read from the DB, never trusted from the client)
        actor = conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (actor_id,)
        ).fetchone()
        if actor is None:
            raise TransitionBlocked("UNAUTHORIZED_ACTOR", "Unknown user.")
        self._check_actor(event, workflow, actor)

        # 4. business guards
        evidence_rows = []
        sealed_digest = None
        if event == WorkflowEvent.SUBMIT_EVIDENCE:
            evidence_rows = self._evidence_for_round(conn, workflow)
            if not evidence_rows:
                raise TransitionBlocked(
                    "GUARD_FAILED",
                    "Add at least one piece of evidence before submitting for review.",
                )
            sealed_digest = evidence_digest(evidence_rows)
        if event in (WorkflowEvent.APPROVE, WorkflowEvent.REJECT):
            if not reason or len(reason) < MIN_REASON_LENGTH:
                raise TransitionBlocked(
                    "GUARD_FAILED",
                    f"A decision reason of at least {MIN_REASON_LENGTH} characters is required.",
                )

        # 5. commit: pure transition, then state + chained history + side effects atomically
        step = machine.transition(event)
        seq = workflow["transition_count"] + 1
        record = {
            "workflow_id": workflow_id,
            "seq": seq,
            "from_state": step["from_state"],
            "event": step["event"],
            "to_state": step["to_state"],
            "condition_result": "PASS",
            "reason": reason,
            "actor_id": actor["id"],
            "actor_role": actor["role"] if REQUIRED_ACTOR[event] is Actor.REVIEWER else "owner",
            "evidence_digest": sealed_digest,
            "created_at": _now(),
            "prev_hash": workflow["chain_head"] or GENESIS_HASH,
        }
        record["hash"] = compute_hash(record)

        conn.execute(
            """
            INSERT INTO state_transitions
                (workflow_id, seq, from_state, event, to_state, condition_result, reason,
                 actor_id, actor_role, evidence_digest, created_at, prev_hash, hash)
            VALUES (:workflow_id, :seq, :from_state, :event, :to_state, :condition_result,
                    :reason, :actor_id, :actor_role, :evidence_digest, :created_at,
                    :prev_hash, :hash)
            """,
            record,
        )

        new_state = step["to_state"]
        is_decision = event in (WorkflowEvent.APPROVE, WorkflowEvent.REJECT)
        is_restart = event == WorkflowEvent.RESTART
        cursor = conn.execute(
            """
            UPDATE workflow_instances
            SET current_state = ?,
                result = ?,
                completed_at = ?,
                round = round + ?,
                chain_head = ?,
                transition_count = ?
            WHERE id = ? AND current_state = ? AND transition_count = ?
            """,
            (
                new_state,
                new_state if is_decision else (None if is_restart else workflow["result"]),
                _now() if is_decision else (None if is_restart else workflow["completed_at"]),
                1 if is_restart else 0,
                record["hash"],
                seq,
                workflow_id,
                state.value,
                workflow["transition_count"],
            ),
        )
        if cursor.rowcount != 1:
            raise TransitionBlocked(
                "CONFLICT", "The workflow changed while this action was being processed. Try again."
            )

        self._side_effects(conn, workflow, event, actor, reason, evidence_rows)

        return {
            "from_state": step["from_state"],
            "event": step["event"],
            "to_state": new_state,
            "seq": seq,
            "hash": record["hash"],
        }

    @staticmethod
    def _check_actor(event, workflow, actor):
        required = REQUIRED_ACTOR[event]
        is_owner = actor["id"] == workflow["user_id"]
        if required is Actor.OWNER and not is_owner:
            raise TransitionBlocked(
                "UNAUTHORIZED_ACTOR", f"Only the task owner can fire {event.value}."
            )
        if required is Actor.REVIEWER:
            if is_owner:
                raise TransitionBlocked(
                    "UNAUTHORIZED_ACTOR",
                    "You cannot review your own work: a different reviewer must decide.",
                )
            if actor["role"] != "reviewer":
                raise TransitionBlocked(
                    "UNAUTHORIZED_ACTOR", f"Only a reviewer can fire {event.value}."
                )

    @staticmethod
    def _evidence_for_round(conn, workflow):
        return conn.execute(
            """
            SELECT id, evidence_type, evidence_url, title, description, created_at
            FROM evidence
            WHERE workflow_id = ? AND round = ?
            """,
            (workflow["id"], workflow["round"]),
        ).fetchall()

    @staticmethod
    def _side_effects(conn, workflow, event, actor, reason, evidence_rows):
        workflow_id, round_no = workflow["id"], workflow["round"]

        if event == WorkflowEvent.SUBMIT_EVIDENCE:
            conn.execute(
                "UPDATE evidence SET verification_status = 'submitted' "
                "WHERE workflow_id = ? AND round = ?",
                (workflow_id, round_no),
            )

        elif event in (WorkflowEvent.APPROVE, WorkflowEvent.REJECT):
            approved = event == WorkflowEvent.APPROVE
            status = "approved" if approved else "rejected"
            conn.execute(
                "UPDATE evidence SET verification_status = ? WHERE workflow_id = ? AND round = ?",
                (status, workflow_id, round_no),
            )
            conn.execute(
                """
                INSERT INTO verifications
                    (evidence_id, status, verified_by, verification_method, verified_at, comments)
                SELECT id, ?, ?, 'manual_review', CURRENT_TIMESTAMP, ?
                FROM evidence WHERE workflow_id = ? AND round = ?
                """,
                (status, actor["id"], reason, workflow_id, round_no),
            )
            if approved and workflow["task_id"] is not None:
                conn.execute(
                    "UPDATE tasks SET status = 'Completed', "
                    "completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP) WHERE id = ?",
                    (workflow["task_id"],),
                )
