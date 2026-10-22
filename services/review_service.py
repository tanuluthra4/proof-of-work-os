"""Read-side helpers for the multi-reviewer panel and the public proof page.

What a viewer may see is decided HERE, on the server:

  * the developer sees every reviewer's decision and shared note, never the
    reviewers' private notes;
  * a reviewer sees their own private note only;
  * until a reviewer has decided (and while the review is open) they see that
    other reviewers have decided but not what they decided, so decisions stay
    independent;
  * the public page shows only reviewers' usernames, decisions, timestamps and
    notes the reviewer explicitly marked public. Never e-mails or private notes.
"""


def _decision_rows(conn, workflow_id, round_no):
    return {
        r["reviewer_id"]: r
        for r in conn.execute(
            "SELECT reviewer_id, decision, notes, private_notes, note_public, created_at "
            "FROM review_decisions WHERE workflow_id = ? AND round = ?",
            (workflow_id, round_no),
        )
    }


def review_panel(conn, workflow_id, round_no, viewer_id, viewer_is_owner, state):
    """Every reviewer for this round with what `viewer` is allowed to see of their decision."""
    assignments = conn.execute(
        """
        SELECT a.reviewer_id, a.status, a.assigned_at, a.cancelled_at, u.username
        FROM review_assignments a JOIN users u ON u.id = a.reviewer_id
        WHERE a.workflow_id = ? AND a.round = ?
        ORDER BY u.username
        """,
        (workflow_id, round_no),
    ).fetchall()
    decisions = _decision_rows(conn, workflow_id, round_no)
    closed = state in ("VERIFIED", "REJECTED")
    viewer_decided = viewer_id in decisions

    panel = []
    for a in assignments:
        d = decisions.get(a["reviewer_id"])
        is_me = a["reviewer_id"] == viewer_id
        reveal = d is not None and (viewer_is_owner or is_me or viewer_decided or closed)
        panel.append({
            "id": a["reviewer_id"],
            "username": a["username"],
            "status": a["status"],
            "is_me": is_me,
            "decided": d is not None,
            "decision": d["decision"] if reveal else None,
            "notes": d["notes"] if reveal else None,
            "private_notes": d["private_notes"] if (d is not None and is_me) else None,
            "note_public": bool(d["note_public"]) if d is not None else False,
            "at": d["created_at"] if d is not None else None,
        })
    return panel


def tally(panel):
    """(approvals, rejections, total active reviewers) from a panel built for an owner or a closed review."""
    active = [r for r in panel if r["status"] == "assigned"]
    return (sum(1 for r in active if r["decision"] == "approve"),
            sum(1 for r in active if r["decision"] == "reject"),
            len(active))


def public_summary(conn, workflow_id, round_no):
    """What the PUBLIC may see of a verified round: usernames, decisions, public notes only."""
    rows = conn.execute(
        """
        SELECT u.username, d.decision, d.created_at,
               CASE WHEN d.note_public = 1 THEN d.notes END AS public_note
        FROM review_assignments a
        JOIN users u ON u.id = a.reviewer_id
        LEFT JOIN review_decisions d ON d.workflow_id = a.workflow_id AND d.round = a.round
                                    AND d.reviewer_id = a.reviewer_id
        WHERE a.workflow_id = ? AND a.round = ? AND a.status = 'assigned'
        ORDER BY u.username
        """,
        (workflow_id, round_no),
    ).fetchall()
    reviewers = [dict(r) for r in rows]
    if not any(r["decision"] for r in reviewers):
        # Proofs verified before multi-reviewer support: derive from the chain, never invent.
        legacy = conn.execute(
            """
            SELECT u.username, t.event, t.created_at
            FROM state_transitions t JOIN users u ON u.id = t.actor_id
            WHERE t.workflow_id = ? AND t.event IN ('APPROVE', 'REJECT') AND t.actor_role = 'reviewer'
            ORDER BY t.seq DESC LIMIT 1
            """,
            (workflow_id,),
        ).fetchone()
        if legacy:
            reviewers = [{"username": legacy["username"],
                          "decision": "approve" if legacy["event"] == "APPROVE" else "reject",
                          "created_at": legacy["created_at"], "public_note": None, "legacy": True}]
    return reviewers
