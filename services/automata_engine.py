from automata.states import WorkflowEvent, WorkflowState
from automata.verification_machine import VerificationMachine
from database.db import get_db_connection


class AutomataEngine:

    def create_workflow(self, user_id, task_id=None):
        conn = get_db_connection()

        cursor = conn.execute(
            """
            INSERT INTO workflow_instances
            (user_id, task_id, current_state)
            VALUES (?, ?, ?)
            """,
            (
                user_id,
                task_id,
                WorkflowState.CREATED.value,
            ),
        )

        workflow_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return workflow_id

    def get_workflow(self, workflow_id):
        conn = get_db_connection()

        workflow = conn.execute(
            """
            SELECT *
            FROM workflow_instances
            WHERE id = ?
            """,
            (workflow_id,),
        ).fetchone()

        conn.close()

        return workflow

    def transition(self, workflow_id, event, reason=None):
        workflow = self.get_workflow(workflow_id)

        if workflow is None:
            raise ValueError("Workflow not found.")

        current_state = WorkflowState(
            workflow["current_state"]
        )

        machine = VerificationMachine(current_state)

        try:
            transition = machine.transition(event)
        except ValueError:
            raise

        new_state = transition["to_state"]

        conn = get_db_connection()

        conn.execute(
            """
            UPDATE workflow_instances
            SET current_state = ?,
                result = ?,
                completed_at = CASE
                    WHEN ? IN ('VERIFIED', 'REJECTED')
                    THEN CURRENT_TIMESTAMP
                    ELSE completed_at
                END
            WHERE id = ?
            """,
            (
                new_state,
                (
                    "VERIFIED"
                    if new_state == WorkflowState.VERIFIED.value
                    else "REJECTED"
                    if new_state == WorkflowState.REJECTED.value
                    else None
                ),
                new_state,
                workflow_id,
            ),
        )

        conn.execute(
            """
            INSERT INTO state_transitions
            (
                workflow_id,
                from_state,
                event,
                to_state,
                condition_result,
                reason
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                transition["from_state"],
                transition["event"],
                transition["to_state"],
                "PASS",
                reason,
            ),
        )

        conn.commit()
        conn.close()

        return transition

    def get_history(self, workflow_id):
        conn = get_db_connection()

        history = conn.execute(
            """
            SELECT
                from_state,
                event,
                to_state,
                condition_result,
                reason,
                created_at
            FROM state_transitions
            WHERE workflow_id = ?
            ORDER BY id ASC
            """,
            (workflow_id,),
        ).fetchall()

        conn.close()

        return history