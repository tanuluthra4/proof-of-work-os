"""Tamper-evidence: edits to history are detected by replaying it through the automaton."""
import sqlite3

import pytest

from automata.states import WorkflowEvent as E
from services.automata_engine import AutomataEngine
from tests.helpers import advance_to_validating, execute, fire, make_task, make_user, query


@pytest.fixture
def verified(app):
    alice, rev = make_user(app, "alice"), make_user(app, "rev", role="reviewer")
    task, wf = make_task(app, alice)
    advance_to_validating(app, wf, alice, rev)
    fire(app, wf, E.APPROVE, rev, "Evidence checks out.")
    return app, wf


def check(app, wf):
    with app.app_context():
        return AutomataEngine().verify_history(wf)


def test_untouched_history_verifies(verified):
    app, wf = verified
    result = check(app, wf)
    assert result["ok"] and result["length"] == 5 and result["replayed_state"] == "VERIFIED"


def test_database_blocks_updates_and_direct_deletes(verified):
    app, wf = verified
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        execute(app, "UPDATE state_transitions SET reason = 'edited' WHERE workflow_id = ?", (wf,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        execute(app, "DELETE FROM state_transitions WHERE workflow_id = ?", (wf,))
    assert check(app, wf)["ok"]


def test_edited_reason_is_detected(verified):
    app, wf = verified
    execute(app, "UPDATE state_transitions SET reason = 'Totally different' WHERE workflow_id = ? AND seq = 5",
            (wf,), drop_triggers=True)
    result = check(app, wf)
    assert not result["ok"]
    assert result["error"]["code"] == "CONTENT_ALTERED" and result["error"]["seq"] == 5


def test_rewritten_outcome_is_detected(verified):
    """Flipping APPROVE to REJECT without recomputing hashes breaks the chain."""
    app, wf = verified
    execute(app, "UPDATE state_transitions SET event = 'REJECT', to_state = 'REJECTED' "
                 "WHERE workflow_id = ? AND seq = 5", (wf,), drop_triggers=True)
    assert check(app, wf)["error"]["code"] == "CONTENT_ALTERED"


def test_deleted_middle_record_is_detected(verified):
    app, wf = verified
    execute(app, "DELETE FROM state_transitions WHERE workflow_id = ? AND seq = 3", (wf,), drop_triggers=True)
    assert check(app, wf)["error"]["code"] == "SEQUENCE_GAP"


def test_truncated_tail_is_detected(verified):
    app, wf = verified
    execute(app, "DELETE FROM state_transitions WHERE workflow_id = ? AND seq = 5", (wf,), drop_triggers=True)
    assert check(app, wf)["error"]["code"] == "COUNT_MISMATCH"


def test_stored_state_that_disagrees_with_history_is_detected(verified):
    app, wf = verified
    execute(app, "UPDATE workflow_instances SET current_state = 'REJECTED' WHERE id = ?", (wf,))
    assert check(app, wf)["error"]["code"] == "STATE_MISMATCH"


def test_evidence_edited_after_submission_is_detected(verified):
    app, wf = verified
    execute(app, "UPDATE evidence SET evidence_url = 'https://github.com/evil/repo' WHERE workflow_id = ?", (wf,))
    result = check(app, wf)
    assert result["error"]["code"] == "EVIDENCE_ALTERED" and result["error"]["seq"] == 3


def test_a_forged_but_correctly_hashed_illegal_edge_is_rejected_by_the_automaton(app):
    """Even a perfectly re-hashed chain fails if it contains a move delta forbids."""
    from services.integrity import GENESIS_HASH, compute_hash
    alice = make_user(app, "alice")
    task, wf = make_task(app, alice)
    record = {"workflow_id": wf, "seq": 1, "from_state": "CREATED", "event": "APPROVE",
              "to_state": "VERIFIED", "condition_result": "PASS", "reason": "forged",
              "actor_id": alice, "actor_role": "owner", "evidence_digest": None,
              "created_at": "2026-01-01 00:00:00", "prev_hash": GENESIS_HASH}
    record["hash"] = compute_hash(record)
    execute(app, """INSERT INTO state_transitions
        (workflow_id, seq, from_state, event, to_state, condition_result, reason, actor_id,
         actor_role, evidence_digest, created_at, prev_hash, hash)
        VALUES (:workflow_id, :seq, :from_state, :event, :to_state, :condition_result, :reason,
                :actor_id, :actor_role, :evidence_digest, :created_at, :prev_hash, :hash)""", record)
    execute(app, "UPDATE workflow_instances SET current_state='VERIFIED', transition_count=1, chain_head=? WHERE id=?",
            (record["hash"], wf))
    result = check(app, wf)
    assert not result["ok"] and result["error"]["code"] == "NOT_ACCEPTED_BY_AUTOMATON"


def test_blocked_attempts_are_not_part_of_the_chain(verified):
    app, wf = verified
    with pytest.raises(Exception):
        fire(app, wf, E.START, 1)
    assert check(app, wf)["length"] == 5
