"""Multi-reviewer policy: quorum, independence, reassignment, withdrawal, tamper detection."""
import sqlite3
import threading

import pytest

from automata.states import WorkflowEvent as E
from services.automata_engine import AutomataEngine, TransitionBlocked
from tests.helpers import (
    add_evidence_item, advance_to_evidence_required, advance_to_validating, assign_many, execute,
    fire, make_task, make_user, query, vote,
)


@pytest.fixture
def panel(app):
    alice = make_user(app, "alice")
    revs = [make_user(app, f"rev{i}", role="reviewer") for i in (1, 2, 3)]
    task, wf = make_task(app, alice)
    return {"app": app, "alice": alice, "revs": revs, "wf": wf, "task": task}


def state_of(app, wf):
    return query(app, "SELECT current_state FROM workflow_instances WHERE id = ?", (wf,))[0][0]


def chain_ok(app, wf):
    with app.app_context():
        return AutomataEngine().verify_history(wf)


# ---------------------------------------------------------------- policies
@pytest.mark.parametrize("k,n", [(1, 1), (2, 2), (2, 3)])
def test_verified_exactly_when_k_of_n_approve(panel, k, n):
    app, wf = panel["app"], panel["wf"]
    revs = panel["revs"][:n]
    advance_to_validating(app, wf, panel["alice"], revs, required=k)
    for i, rev in enumerate(revs[:k]):
        assert state_of(app, wf) == "VALIDATING"      # not verified before the k-th approval
        vote(app, wf, rev)
    assert state_of(app, wf) == "VERIFIED"
    assert chain_ok(app, wf)["ok"]
    assert query(app, "SELECT COUNT(*) FROM review_decisions")[0][0] == k


def test_default_policy_value_is_two(app):
    assert app.config["DEFAULT_REQUIRED_APPROVALS"] == 2


def test_two_of_three_tolerates_one_rejection(panel):
    app, wf, revs = panel["app"], panel["wf"], panel["revs"]
    advance_to_validating(app, wf, panel["alice"], revs, required=2)
    vote(app, wf, revs[0], approve=False)
    assert state_of(app, wf) == "VALIDATING"           # approval still reachable
    vote(app, wf, revs[1])
    vote(app, wf, revs[2])
    assert state_of(app, wf) == "VERIFIED"


def test_rejected_once_approval_is_impossible(panel):
    app, wf, revs = panel["app"], panel["wf"], panel["revs"]
    advance_to_validating(app, wf, panel["alice"], revs, required=2)
    vote(app, wf, revs[0], approve=False)
    vote(app, wf, revs[1], approve=False)
    assert state_of(app, wf) == "REJECTED"
    assert chain_ok(app, wf)["ok"]


def test_two_of_two_single_rejection_rejects(panel):
    app, wf, revs = panel["app"], panel["wf"], panel["revs"][:2]
    advance_to_validating(app, wf, panel["alice"], revs, required=2)
    vote(app, wf, revs[0], approve=False)
    assert state_of(app, wf) == "REJECTED"


# ------------------------------------------------------------- guard rails
def test_one_decision_per_reviewer(panel):
    app, wf, revs = panel["app"], panel["wf"], panel["revs"]
    advance_to_validating(app, wf, panel["alice"], revs, required=2)
    vote(app, wf, revs[0])
    with pytest.raises(TransitionBlocked) as err:
        vote(app, wf, revs[0])
    assert err.value.code == "GUARD_FAILED"
    assert query(app, "SELECT COUNT(*) FROM review_decisions")[0][0] == 1


def test_approve_and_reject_cannot_be_fired_directly(panel):
    app, wf, revs = panel["app"], panel["wf"], panel["revs"]
    advance_to_validating(app, wf, panel["alice"], revs, required=2)
    for event in (E.APPROVE, E.REJECT):
        for actor in (panel["alice"], revs[0]):
            with pytest.raises(TransitionBlocked) as err:
                fire(app, wf, event, actor, "forcing the outcome")
            assert err.value.code == "UNAUTHORIZED_ACTOR"
    assert state_of(app, wf) == "VALIDATING"


def test_unassigned_reviewer_cannot_vote(panel):
    app, wf, revs = panel["app"], panel["wf"], panel["revs"]
    advance_to_validating(app, wf, panel["alice"], revs[:2], required=2)
    with pytest.raises(TransitionBlocked) as err:
        vote(app, wf, revs[2])
    assert err.value.code == "UNAUTHORIZED_ACTOR"


def test_vote_needs_a_real_note(panel):
    app, wf, revs = panel["app"], panel["wf"], panel["revs"]
    advance_to_validating(app, wf, panel["alice"], revs, required=2)
    with pytest.raises(TransitionBlocked):
        vote(app, wf, revs[0], notes="ok")


def test_decisions_table_is_append_only(panel):
    app, wf, revs = panel["app"], panel["wf"], panel["revs"]
    advance_to_validating(app, wf, panel["alice"], revs, required=2)
    vote(app, wf, revs[0])
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        execute(app, "UPDATE review_decisions SET decision = 'reject'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        execute(app, "DELETE FROM review_decisions")


# ----------------------------------------------------------- reassignment
def test_reassign_unavailable_reviewer(panel):
    app, wf, revs = panel["app"], panel["wf"], panel["revs"]
    advance_to_validating(app, wf, panel["alice"], revs[:2], required=2)
    fire(app, wf, E.REASSIGN_REVIEWER, panel["alice"], "rev2 is on leave",
         {"remove": "rev2", "add": "rev3"})
    with pytest.raises(TransitionBlocked):
        vote(app, wf, revs[1])                        # replaced reviewer can no longer act
    vote(app, wf, revs[0])
    vote(app, wf, revs[2])
    assert state_of(app, wf) == "VERIFIED"
    assert chain_ok(app, wf)["ok"]
    statuses = {r["reviewer_id"]: r["status"] for r in query(app, "SELECT * FROM review_assignments")}
    assert statuses[revs[1]] == "cancelled"           # history kept, not deleted


def test_cannot_replace_reviewer_who_already_decided_or_duplicate(panel):
    app, wf, revs = panel["app"], panel["wf"], panel["revs"]
    advance_to_validating(app, wf, panel["alice"], revs[:2], required=2)
    vote(app, wf, revs[0])
    with pytest.raises(TransitionBlocked):
        fire(app, wf, E.REASSIGN_REVIEWER, panel["alice"], "x", {"remove": "rev1", "add": "rev3"})
    with pytest.raises(TransitionBlocked):
        fire(app, wf, E.REASSIGN_REVIEWER, panel["alice"], "x", {"remove": "rev2", "add": "rev1"})
    with pytest.raises(TransitionBlocked):
        fire(app, wf, E.REASSIGN_REVIEWER, revs[0], "x", {"remove": "rev2", "add": "rev3"})


# --------------------------------------------------------------- withdraw
def test_withdraw_invalidates_round_and_keeps_history(panel):
    app, wf, revs, alice = panel["app"], panel["wf"], panel["revs"], panel["alice"]
    advance_to_validating(app, wf, alice, revs, required=2)
    vote(app, wf, revs[0])
    fire(app, wf, E.WITHDRAW, alice, "adding a missing link")
    row = query(app, "SELECT current_state, round FROM workflow_instances WHERE id = ?", (wf,))[0]
    assert (row[0], row[1]) == ("IN_PROGRESS", 2)
    # old decision survives under round 1 and does not count in round 2
    assert query(app, "SELECT round, decision FROM review_decisions")[0][:] == (1, "approve")
    fire(app, wf, E.REQUEST_EVIDENCE, alice)
    add_evidence_item(app, wf, alice, "commit", "https://github.com/octocat/hello/commit/abcdef1")
    fire(app, wf, E.SUBMIT_EVIDENCE, alice)
    fire(app, wf, E.START_VALIDATION, revs[0])
    vote(app, wf, revs[0])                            # allowed again: new round
    assert state_of(app, wf) == "VALIDATING"          # one approval of two is not enough
    assert chain_ok(app, wf)["ok"]


def test_only_owner_can_withdraw_and_not_after_verification(panel):
    app, wf, revs, alice = panel["app"], panel["wf"], panel["revs"], panel["alice"]
    advance_to_validating(app, wf, alice, revs[:1], required=1)
    with pytest.raises(TransitionBlocked):
        fire(app, wf, E.WITHDRAW, revs[0], "nope")
    vote(app, wf, revs[0])
    with pytest.raises(TransitionBlocked):
        fire(app, wf, E.WITHDRAW, alice, "too late")


def test_evidence_change_after_submission_blocks_votes(panel):
    app, wf, revs, alice = panel["app"], panel["wf"], panel["revs"], panel["alice"]
    advance_to_validating(app, wf, alice, revs, required=2)
    execute(app, "UPDATE evidence SET description = 'swapped after submission'")
    with pytest.raises(TransitionBlocked) as err:
        vote(app, wf, revs[0])
    assert err.value.code == "EVIDENCE_CHANGED"
    assert state_of(app, wf) == "VALIDATING"


# ------------------------------------------------------------- concurrency
def test_concurrent_votes_cannot_double_count_or_over_verify(panel):
    app, wf, revs, alice = panel["app"], panel["wf"], panel["revs"], panel["alice"]
    advance_to_validating(app, wf, alice, revs, required=2)
    outcomes = []

    def worker(rev):
        try:
            vote(app, wf, rev)
            outcomes.append("ok")
        except TransitionBlocked as blocked:
            outcomes.append(blocked.code)

    threads = [threading.Thread(target=worker, args=(r,)) for r in revs + [revs[0]] * 3]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state_of(app, wf) == "VERIFIED"
    assert query(app, "SELECT COUNT(DISTINCT reviewer_id) FROM review_decisions")[0][0] == \
        query(app, "SELECT COUNT(*) FROM review_decisions")[0][0]
    assert query(app, "SELECT COUNT(*) FROM state_transitions WHERE event = 'APPROVE'")[0][0] == 1
    assert chain_ok(app, wf)["ok"]


# ------------------------------------------------------- tamper detection
@pytest.fixture
def verified_2of3(panel):
    app, wf, revs = panel["app"], panel["wf"], panel["revs"]
    advance_to_validating(app, wf, panel["alice"], revs, required=2)
    vote(app, wf, revs[0])
    vote(app, wf, revs[1])
    return app, wf


def test_decision_row_edited_outside_chain_is_detected(verified_2of3):
    app, wf = verified_2of3
    execute(app, "DROP TRIGGER review_decisions_no_update")
    execute(app, "UPDATE review_decisions SET notes = 'rewritten later' WHERE rowid = 1")
    assert chain_ok(app, wf)["error"]["code"] == "DECISION_MISMATCH"


def test_fabricated_decision_row_is_detected(verified_2of3):
    app, wf = verified_2of3
    third = query(app, "SELECT id FROM users WHERE username = 'rev3'")[0][0]
    execute(app, "INSERT INTO review_decisions (workflow_id, round, reviewer_id, decision, notes, evidence_digest) "
                 "VALUES (?, 1, ?, 'approve', 'fabricated', 'x')", (wf, third))
    assert chain_ok(app, wf)["error"]["code"] == "DECISION_MISMATCH"


def test_removed_decision_row_is_detected(verified_2of3):
    app, wf = verified_2of3
    execute(app, "DROP TRIGGER review_decisions_no_delete")
    execute(app, "DELETE FROM review_decisions WHERE rowid = 1")
    assert chain_ok(app, wf)["error"]["code"] == "DECISION_MISMATCH"


def test_forged_quorum_is_detected(panel):
    """An APPROVE whose chain does not contain enough approvals fails replay."""
    app, wf, revs = panel["app"], panel["wf"], panel["revs"]
    advance_to_validating(app, wf, panel["alice"], revs, required=2)
    vote(app, wf, revs[0])
    from services.integrity import compute_hash
    # forge: append APPROVE after only one approval, recomputing the hash correctly
    row = query(app, "SELECT * FROM state_transitions WHERE workflow_id = ? ORDER BY seq DESC LIMIT 1", (wf,))[0]
    forged = dict(row)
    forged.update(seq=row["seq"] + 1, from_state="VALIDATING", event="APPROVE", to_state="VERIFIED",
                  actor_role="system", prev_hash=row["hash"], meta=None, reason="forced", evidence_digest=None)
    forged["hash"] = compute_hash(forged)
    cols = [c for c in forged if c != "id"]
    execute(app, f"INSERT INTO state_transitions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            tuple(forged[c] for c in cols))
    execute(app, "UPDATE workflow_instances SET current_state = 'VERIFIED', transition_count = ?, chain_head = ? WHERE id = ?",
            (forged["seq"], forged["hash"], wf))
    assert chain_ok(app, wf)["error"]["code"] == "QUORUM_NOT_MET"
