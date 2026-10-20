"""Database-backed engine: persistence, permissions, guards, atomicity, concurrency."""
import threading

import pytest

from automata.states import WorkflowEvent as E
from database.db import get_db_connection
from services.automata_engine import AutomataEngine, TransitionBlocked, WorkflowNotFound
from tests.helpers import (
    add_evidence_item, advance_to_evidence_required, advance_to_validating, engine, execute,
    fire, make_task, make_user, query,
)


@pytest.fixture
def world(app):
    alice = make_user(app, "alice")
    bob = make_user(app, "bob")
    rev = make_user(app, "rev", role="reviewer")
    task_id, wf = make_task(app, alice)
    return {"app": app, "alice": alice, "bob": bob, "rev": rev, "task": task_id, "wf": wf}


def state_of(app, wf):
    return query(app, "SELECT current_state FROM workflow_instances WHERE id = ?", (wf,))[0][0]


def test_workflow_initialisation_is_idempotent(world):
    app = world["app"]
    with app.app_context():
        assert engine.create_workflow(world["alice"], world["task"]) == world["wf"]
    assert state_of(app, world["wf"]) == "CREATED"
    assert query(app, "SELECT COUNT(*) FROM workflow_instances WHERE task_id = ?", (world["task"],))[0][0] == 1


def test_full_happy_path_records_chained_history(world):
    app, alice, rev, wf = world["app"], world["alice"], world["rev"], world["wf"]
    advance_to_validating(app, wf, alice, rev)
    result = fire(app, wf, E.APPROVE, rev, "Repo and commit match the described work.")

    assert result["to_state"] == "VERIFIED"
    rows = query(app, "SELECT seq, event, from_state, to_state, actor_id, prev_hash, hash "
                      "FROM state_transitions WHERE workflow_id = ? ORDER BY seq", (wf,))
    assert [r["event"] for r in rows] == [
        "START", "REQUEST_EVIDENCE", "SUBMIT_EVIDENCE", "START_VALIDATION", "APPROVE"]
    assert [r["seq"] for r in rows] == [1, 2, 3, 4, 5]
    assert rows[0]["prev_hash"] == "0" * 64
    for earlier, later in zip(rows, rows[1:]):
        assert later["prev_hash"] == earlier["hash"]
        assert later["from_state"] == earlier["to_state"]

    workflow = query(app, "SELECT * FROM workflow_instances WHERE id = ?", (wf,))[0]
    assert workflow["result"] == "VERIFIED" and workflow["completed_at"]
    assert workflow["transition_count"] == 5 and workflow["chain_head"] == rows[-1]["hash"]
    # the decision is recorded, the evidence is marked approved, the self-reported status follows
    assert query(app, "SELECT status, verified_by, comments FROM verifications")[0]["status"] == "approved"
    assert query(app, "SELECT verification_status FROM evidence")[0][0] == "approved"
    assert query(app, "SELECT status FROM tasks WHERE id = ?", (world["task"],))[0][0] == "Completed"
    with app.app_context():
        assert AutomataEngine().verify_history(wf)["ok"]


def test_invalid_transition_is_blocked_logged_and_changes_nothing(world):
    app, wf = world["app"], world["wf"]
    with pytest.raises(TransitionBlocked) as error:
        fire(app, wf, E.APPROVE, world["rev"], "trying to skip straight to verified")
    assert error.value.code == "INVALID_TRANSITION"
    assert "CREATED" in str(error.value)
    assert state_of(app, wf) == "CREATED"
    assert query(app, "SELECT COUNT(*) FROM state_transitions")[0][0] == 0
    blocked = query(app, "SELECT code, event, from_state FROM blocked_actions")
    assert [tuple(b) for b in blocked] == [("INVALID_TRANSITION", "APPROVE", "CREATED")]


def test_cannot_reach_verified_without_evidence(world):
    app, alice, rev, wf = world["app"], world["alice"], world["rev"], world["wf"]
    advance_to_evidence_required(app, wf, alice)
    with pytest.raises(TransitionBlocked) as error:
        fire(app, wf, E.SUBMIT_EVIDENCE, alice)
    assert error.value.code == "GUARD_FAILED"
    # and no shortcut around the review states exists either
    for event in (E.START_VALIDATION, E.APPROVE):
        with pytest.raises(TransitionBlocked):
            fire(app, wf, event, rev, "shortcut attempt")
    assert state_of(app, wf) == "EVIDENCE_REQUIRED"


def test_unknown_event_is_blocked_not_crashing(world):
    with pytest.raises(TransitionBlocked) as error:
        fire(world["app"], world["wf"], "TELEPORT", world["alice"])
    assert error.value.code == "UNKNOWN_EVENT"


def test_missing_workflow_raises_not_found(app):
    with pytest.raises(WorkflowNotFound):
        fire(app, 9999, E.START, 1)


def test_owner_cannot_approve_own_work_and_non_reviewer_cannot_either(world):
    app, alice, bob, rev, wf = world["app"], world["alice"], world["bob"], world["rev"], world["wf"]
    advance_to_validating(app, wf, alice, rev)

    with pytest.raises(TransitionBlocked) as own:
        fire(app, wf, E.APPROVE, alice, "I approve myself")
    assert own.value.code == "UNAUTHORIZED_ACTOR"
    with pytest.raises(TransitionBlocked) as other:
        fire(app, wf, E.APPROVE, bob, "I am just another developer")
    assert other.value.code == "UNAUTHORIZED_ACTOR"
    assert state_of(app, wf) == "VALIDATING"

    # a reviewer who owns the task still cannot review it
    execute(app, "UPDATE users SET role = 'reviewer' WHERE id = ?", (alice,))
    with pytest.raises(TransitionBlocked):
        fire(app, wf, E.APPROVE, alice, "reviewer reviewing herself")


def test_only_owner_can_drive_owner_events(world):
    with pytest.raises(TransitionBlocked) as error:
        fire(world["app"], world["wf"], E.START, world["bob"])
    assert error.value.code == "UNAUTHORIZED_ACTOR"


def test_decision_requires_a_reason(world):
    app, alice, rev, wf = world["app"], world["alice"], world["rev"], world["wf"]
    advance_to_validating(app, wf, alice, rev)
    for reason in (None, "", "ok"):
        with pytest.raises(TransitionBlocked) as error:
            fire(app, wf, E.REJECT, rev, reason)
        assert error.value.code == "GUARD_FAILED"
    assert state_of(app, wf) == "VALIDATING"


def test_rejection_then_restart_starts_a_new_evidence_round(world):
    app, alice, rev, wf = world["app"], world["alice"], world["rev"], world["wf"]
    advance_to_validating(app, wf, alice, rev)
    fire(app, wf, E.REJECT, rev, "Repository has no commits by you.")
    assert state_of(app, wf) == "REJECTED"
    assert query(app, "SELECT verification_status FROM evidence")[0][0] == "rejected"
    assert query(app, "SELECT result FROM workflow_instances WHERE id = ?", (wf,))[0][0] == "REJECTED"

    fire(app, wf, E.RESTART, alice, "Adding my real commit")
    workflow = query(app, "SELECT current_state, round, result, completed_at FROM workflow_instances WHERE id = ?", (wf,))[0]
    assert (workflow["current_state"], workflow["round"], workflow["result"], workflow["completed_at"]) == (
        "IN_PROGRESS", 2, None, None)

    fire(app, wf, E.REQUEST_EVIDENCE, alice)
    # round 1's rejected evidence does not count for round 2
    with pytest.raises(TransitionBlocked):
        fire(app, wf, E.SUBMIT_EVIDENCE, alice)
    add_evidence_item(app, wf, alice, "commit", "https://github.com/octocat/hello/commit/abcdef1")
    fire(app, wf, E.SUBMIT_EVIDENCE, alice)
    fire(app, wf, E.START_VALIDATION, rev)
    fire(app, wf, E.APPROVE, rev, "Commit authored by the developer; matches the claim.")
    assert state_of(app, wf) == "VERIFIED"
    with app.app_context():
        assert AutomataEngine().verify_history(wf)["ok"]


def test_verified_is_terminal(world):
    app, alice, rev, wf = world["app"], world["alice"], world["rev"], world["wf"]
    advance_to_validating(app, wf, alice, rev)
    fire(app, wf, E.APPROVE, rev, "Looks genuine to me.")
    for event in E:
        with pytest.raises(TransitionBlocked):
            fire(app, wf, event, alice if event in (E.START, E.RESTART) else rev, "attempt after verification")
    assert state_of(app, wf) == "VERIFIED"


def test_failed_history_insert_rolls_back_the_state_change(world):
    """If writing the history fails, the state update must not survive."""
    app, alice, wf = world["app"], world["alice"], world["wf"]
    execute(app, """
        CREATE TRIGGER sabotage BEFORE INSERT ON state_transitions
        BEGIN SELECT RAISE(ABORT, 'disk on fire'); END""")
    with pytest.raises(Exception, match="disk on fire"):
        fire(app, wf, E.START, alice)

    workflow = query(app, "SELECT current_state, transition_count, chain_head FROM workflow_instances WHERE id = ?", (wf,))[0]
    assert tuple(workflow) == ("CREATED", 0, None)
    assert query(app, "SELECT COUNT(*) FROM state_transitions")[0][0] == 0

    execute(app, "DROP TRIGGER sabotage")
    assert fire(app, wf, E.START, alice)["to_state"] == "IN_PROGRESS"


def test_failed_side_effect_rolls_back_everything(world):
    """A failure *after* the history insert (in the side effects) also rolls back."""
    app, alice, rev, wf = world["app"], world["alice"], world["rev"], world["wf"]
    advance_to_validating(app, wf, alice, rev)
    execute(app, """
        CREATE TRIGGER sabotage BEFORE INSERT ON verifications
        BEGIN SELECT RAISE(ABORT, 'verification store down'); END""")
    with pytest.raises(Exception, match="verification store down"):
        fire(app, wf, E.APPROVE, rev, "approving while the store is broken")
    assert state_of(app, wf) == "VALIDATING"
    assert query(app, "SELECT COUNT(*) FROM state_transitions WHERE event = 'APPROVE'")[0][0] == 0
    assert query(app, "SELECT status FROM tasks WHERE id = ?", (world["task"],))[0][0] == "Pending"
    with app.app_context():
        assert AutomataEngine().verify_history(wf)["ok"]


def test_concurrent_identical_events_advance_exactly_once(world):
    app, alice, wf = world["app"], world["alice"], world["wf"]
    outcomes = []

    def worker():
        try:
            fire(app, wf, E.START, alice)
            outcomes.append("ok")
        except TransitionBlocked as blocked:
            outcomes.append(blocked.code)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("ok") == 1
    assert outcomes.count("INVALID_TRANSITION") == 5
    assert query(app, "SELECT COUNT(*) FROM state_transitions WHERE workflow_id = ?", (wf,))[0][0] == 1
    with app.app_context():
        assert AutomataEngine().verify_history(wf)["ok"]


def test_history_api_returns_actor_names(world):
    app, alice, wf = world["app"], world["alice"], world["wf"]
    fire(app, wf, E.START, alice, "kick-off")
    with app.app_context():
        history = AutomataEngine().get_history(wf)
    assert history[0]["actor_name"] == "alice" and history[0]["reason"] == "kick-off"


def test_only_the_chosen_reviewer_can_act(world):
    app, alice, rev, wf = world["app"], world["alice"], world["rev"], world["wf"]
    other = make_user(app, "otto", role="reviewer")
    advance_to_validating(app, wf, alice, rev)
    with pytest.raises(TransitionBlocked) as error:
        fire(app, wf, E.APPROVE, other, "I was not asked to review this")
    assert error.value.code == "UNAUTHORIZED_ACTOR" and "not sent to you" in str(error.value)
    assert state_of(app, wf) == "VALIDATING"
    assert fire(app, wf, E.APPROVE, rev, "I was asked.")["to_state"] == "VERIFIED"


def test_cannot_submit_without_choosing_a_reviewer(world):
    from tests.helpers import add_evidence_item
    app, alice, wf = world["app"], world["alice"], world["wf"]
    advance_to_evidence_required(app, wf, alice)
    add_evidence_item(app, wf, alice)
    with pytest.raises(TransitionBlocked) as error:
        fire(app, wf, E.SUBMIT_EVIDENCE, alice)
    assert error.value.code == "GUARD_FAILED" and "reviewer" in str(error.value)


def test_assignment_rules(world):
    from services.evidence_service import EvidenceError, EvidenceNotAllowed, assign_reviewer
    from tests.helpers import add_evidence_item, assign
    app, alice, bob, rev, wf = world["app"], world["alice"], world["bob"], world["rev"], world["wf"]

    def try_assign(owner, name):
        with app.app_context():
            conn = get_db_connection()
            try:
                return assign_reviewer(conn, wf, owner, name)
            finally:
                conn.close()

    with pytest.raises(EvidenceNotAllowed):
        try_assign(alice, "rev")                       # still CREATED
    advance_to_evidence_required(app, wf, alice)
    with pytest.raises(EvidenceError):
        try_assign(alice, "bob")                       # a developer account
    with pytest.raises(EvidenceError):
        try_assign(alice, "")
    with pytest.raises(EvidenceNotAllowed):
        try_assign(bob, "rev")                         # not the owner
    execute(app, "UPDATE users SET role = 'reviewer' WHERE id = ?", (alice,))
    with pytest.raises(EvidenceError):
        try_assign(alice, "alice")                     # cannot pick yourself
    execute(app, "UPDATE users SET role = 'developer' WHERE id = ?", (alice,))
    assert try_assign(alice, "REV@example.com") == "rev"
    add_evidence_item(app, wf, alice)
    fire(app, wf, E.SUBMIT_EVIDENCE, alice)
    with pytest.raises(EvidenceNotAllowed):
        try_assign(alice, "rev")                       # final after submission
