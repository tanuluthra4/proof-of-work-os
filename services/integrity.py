"""Tamper-evident audit trail for workflow transitions.

Every row in `state_transitions` carries `prev_hash` and `hash`, where

    hash = SHA-256( canonical JSON of the row's fields + prev_hash )

so each entry commits to the whole history before it (a hash chain). Editing
any past row changes its hash and breaks the link to the next row.

`verify_chain` goes further than checking hashes: it *replays the recorded
events through the automaton*. A history is only accepted if

  1. the hash chain is intact,
  2. every step is an edge of the transition table (the word is accepted by
     the DFA from CREATED),
  3. the replayed final state equals the state stored on the workflow,
  4. the evidence that was submitted still matches the digest sealed at the
     moment of submission.

Limits (be honest in interviews): this is tamper-*evident*, not tamper-proof.
Someone with full write access to the SQLite file could recompute the whole
chain. Anchoring `chain_head` somewhere external (e.g. a public log) would
close that gap and is listed as future work.
"""
import hashlib
import json

from automata.states import WorkflowEvent, WorkflowState
from automata.transitions import INITIAL_STATE, TRANSITIONS

GENESIS_HASH = "0" * 64

HASH_FIELDS = (
    "workflow_id",
    "seq",
    "from_state",
    "event",
    "to_state",
    "condition_result",
    "reason",
    "actor_id",
    "actor_role",
    "evidence_digest",
    "created_at",
    "prev_hash",
)


def compute_hash(record) -> str:
    """SHA-256 over a canonical encoding of the transition's fields."""
    payload = {field: record[field] for field in HASH_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def evidence_digest(evidence_rows) -> str:
    """Digest of the immutable fields of a set of evidence rows.

    Mutable columns (verification_status) are deliberately excluded so the
    digest sealed at submission time stays valid after a review decision.
    """
    items = [
        [
            row["id"],
            row["evidence_type"],
            row["evidence_url"],
            row["title"],
            row["description"],
            row["created_at"],
        ]
        for row in sorted(evidence_rows, key=lambda r: r["id"])
    ]
    encoded = json.dumps(items, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _result(ok, length, replayed_state=None, error=None, head=None):
    return {
        "ok": ok,
        "length": length,
        "replayed_state": replayed_state,
        "head": head,
        "error": error,
    }


def verify_chain(conn, workflow_id) -> dict:
    """Verify the audit trail of one workflow. Never raises on tampered data."""
    workflow = conn.execute(
        "SELECT * FROM workflow_instances WHERE id = ?", (workflow_id,)
    ).fetchone()
    if workflow is None:
        return _result(False, 0, error={"seq": None, "code": "NO_WORKFLOW",
                                        "message": "Workflow does not exist."})

    rows = conn.execute(
        "SELECT * FROM state_transitions WHERE workflow_id = ? ORDER BY seq, id",
        (workflow_id,),
    ).fetchall()

    def fail(seq, code, message, state=None):
        return _result(False, len(rows), state,
                       {"seq": seq, "code": code, "message": message})

    previous_hash = GENESIS_HASH
    state = INITIAL_STATE
    evidence_round = 1

    for expected_seq, row in enumerate(rows, start=1):
        seq = row["seq"]
        if seq != expected_seq:
            return fail(seq, "SEQUENCE_GAP",
                        f"Expected link #{expected_seq} but found #{seq}: "
                        "a record was removed, reordered or inserted.", state.value)

        if row["prev_hash"] != previous_hash:
            return fail(seq, "BROKEN_LINK",
                        f"Link #{seq} does not point at the hash of link #{seq - 1}.",
                        state.value)

        if compute_hash(row) != row["hash"]:
            return fail(seq, "CONTENT_ALTERED",
                        f"Link #{seq} was modified after it was written "
                        "(its stored hash no longer matches its content).", state.value)

        try:
            from_state = WorkflowState(row["from_state"])
            event = WorkflowEvent(row["event"])
            to_state = WorkflowState(row["to_state"])
        except ValueError:
            return fail(seq, "UNKNOWN_SYMBOL",
                        f"Link #{seq} references a state or event the automaton does not have.",
                        state.value)

        if from_state != state:
            return fail(seq, "STATE_DISCONTINUITY",
                        f"Link #{seq} starts in {from_state.value} but the replayed "
                        f"state was {state.value}.", state.value)

        if TRANSITIONS.get(from_state, {}).get(event) != to_state:
            return fail(seq, "NOT_ACCEPTED_BY_AUTOMATON",
                        f"{from_state.value} --{event.value}--> {to_state.value} "
                        "is not an edge of the transition table.", state.value)

        if event == WorkflowEvent.SUBMIT_EVIDENCE and row["actor_role"] != "legacy":
            current = conn.execute(
                """
                SELECT id, evidence_type, evidence_url, title, description, created_at
                FROM evidence
                WHERE workflow_id = ? AND round = ?
                """,
                (workflow_id, evidence_round),
            ).fetchall()
            if evidence_digest(current) != row["evidence_digest"]:
                return fail(seq, "EVIDENCE_ALTERED",
                            "The evidence stored for this submission no longer matches "
                            "the digest that was sealed when it was submitted.",
                            state.value)

        if event == WorkflowEvent.RESTART:
            evidence_round += 1

        previous_hash = row["hash"]
        state = to_state

    if len(rows) != workflow["transition_count"]:
        return fail(None, "COUNT_MISMATCH",
                    f"The workflow records {workflow['transition_count']} transitions "
                    f"but {len(rows)} exist: history was truncated or extended.", state.value)

    expected_head = previous_hash if rows else None
    if workflow["chain_head"] != expected_head:
        return fail(None, "HEAD_MISMATCH",
                    "The stored chain head does not match the last link.", state.value)

    if state.value != workflow["current_state"]:
        return fail(None, "STATE_MISMATCH",
                    f"Replaying the history ends in {state.value}, but the workflow is "
                    f"stored as {workflow['current_state']}.", state.value)

    return _result(True, len(rows), state.value, None, expected_head)
