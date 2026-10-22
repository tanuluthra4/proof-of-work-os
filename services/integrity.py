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
    # `meta` (reviewer policy, reassignment details) joins the hash only when
    # present, so chains written before multi-reviewer support still verify.
    try:
        meta = record["meta"]
    except (KeyError, IndexError):
        meta = None
    if meta is not None:
        payload["meta"] = meta
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


def _load_meta(row):
    try:
        return json.loads(row["meta"]) if row["meta"] else None
    except ValueError:
        return None


def _check_review_rules(conn, row, event, workflow_id, evidence_round, policy,
                        approvals, rejections, submitted_digest):
    """Replay the multi-reviewer rules for one link. Returns (code, message) or None."""
    seq = row["seq"]
    if event == WorkflowEvent.SUBMIT_EVIDENCE and row["actor_role"] != "legacy":
        meta = _load_meta(row)
        if meta is not None and not (
                isinstance(meta.get("reviewers"), list) and meta["reviewers"]
                and isinstance(meta.get("required"), int)
                and 1 <= meta["required"] <= len(meta["reviewers"])
                and len(set(meta["reviewers"])) == len(meta["reviewers"])):
            return ("BAD_POLICY", f"Link #{seq} seals an impossible approval policy.")

    if policy is None:
        return None          # legacy submission: no sealed policy to enforce

    if event == WorkflowEvent.REASSIGN_REVIEWER:
        meta = _load_meta(row) or {}
        remove, add = meta.get("remove"), meta.get("add")
        if (remove not in policy["reviewers"] or add in policy["reviewers"]
                or remove in approvals or remove in rejections or add == row["actor_id"]):
            return ("ILLEGAL_REASSIGNMENT", f"Link #{seq} is not a legal reviewer reassignment.")

    if event in (WorkflowEvent.VOTE_APPROVE, WorkflowEvent.VOTE_REJECT):
        voter = row["actor_id"]
        if voter not in policy["reviewers"]:
            return ("ILLEGAL_VOTE", f"Link #{seq} is a vote by someone who was not an assigned reviewer.")
        if voter in approvals or voter in rejections:
            return ("ILLEGAL_VOTE", f"Link #{seq} is a second vote by the same reviewer.")
        decision = conn.execute(
            "SELECT decision, notes, evidence_digest FROM review_decisions "
            "WHERE workflow_id = ? AND round = ? AND reviewer_id = ?",
            (workflow_id, evidence_round, voter),
        ).fetchone()
        wanted = "approve" if event == WorkflowEvent.VOTE_APPROVE else "reject"
        if (decision is None or decision["decision"] != wanted
                or decision["notes"] != row["reason"]
                or decision["evidence_digest"] != submitted_digest):
            return ("DECISION_MISMATCH",
                    f"The stored decision for link #{seq} does not match what the chain recorded "
                    "(decision, note or evidence version differs).")

    if event == WorkflowEvent.APPROVE and len(approvals) < policy["required"]:
        return ("QUORUM_NOT_MET",
                f"Verified with {len(approvals)} approvals but {policy['required']} were required.")
    if event == WorkflowEvent.REJECT and len(rejections) <= len(policy["reviewers"]) - policy["required"]:
        return ("QUORUM_NOT_MET", "Rejected although enough approvals were still possible.")
    return None


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
    policy = None            # {"reviewers": [ids], "required": k}, sealed by SUBMIT_EVIDENCE
    submitted_digest = None
    approvals, rejections = set(), set()
    vote_events = 0

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

        problem = _check_review_rules(conn, row, event, workflow_id, evidence_round,
                                      policy, approvals, rejections, submitted_digest)
        if problem:
            return fail(seq, problem[0], problem[1], state.value)

        if event == WorkflowEvent.SUBMIT_EVIDENCE:
            policy = _load_meta(row)
            submitted_digest = row["evidence_digest"]
            approvals, rejections = set(), set()
        elif event == WorkflowEvent.REASSIGN_REVIEWER and policy is not None:
            meta = _load_meta(row)
            policy["reviewers"] = [meta["add"] if r == meta["remove"] else r
                                   for r in policy["reviewers"]]
        elif event in (WorkflowEvent.VOTE_APPROVE, WorkflowEvent.VOTE_REJECT):
            vote_events += 1
            if policy is not None:
                (approvals if event == WorkflowEvent.VOTE_APPROVE else rejections).add(row["actor_id"])

        if event in (WorkflowEvent.RESTART, WorkflowEvent.WITHDRAW):
            evidence_round += 1
            policy, submitted_digest = None, None
            approvals, rejections = set(), set()

        previous_hash = row["hash"]
        state = to_state

    stored_decisions = conn.execute(
        "SELECT COUNT(*) FROM review_decisions WHERE workflow_id = ?", (workflow_id,)
    ).fetchone()[0]
    if stored_decisions != vote_events:
        return fail(None, "DECISION_MISMATCH",
                    f"{stored_decisions} reviewer decisions are stored but the chain records "
                    f"{vote_events} votes: a decision was added or removed outside the chain.",
                    state.value)

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
