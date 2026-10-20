"""Public, read-only pages. Nothing here is visible unless the developer
explicitly created a share link for a VERIFIED proof."""
from flask import Blueprint, abort, render_template

from database.db import get_db_connection
from services.evidence_service import EVIDENCE_TYPES
from services.integrity import verify_chain

public_bp = Blueprint("public", __name__)


@public_bp.route("/proof/<token>")
def proof(token):
    conn = get_db_connection()
    try:
        proof_row = conn.execute(
            """
            SELECT w.id AS workflow_id, w.round, w.completed_at, w.chain_head,
                   t.title, t.description, p.title AS project_title,
                   u.username AS owner_name, rv.username AS reviewer_name
            FROM workflow_instances w
            JOIN tasks t ON t.id = w.task_id
            JOIN users u ON u.id = w.user_id
            LEFT JOIN users rv ON rv.id = w.reviewer_id
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE w.share_token = ? AND w.current_state = 'VERIFIED'
            """,
            (token,),
        ).fetchone()
        if proof_row is None:
            abort(404)

        evidence = conn.execute(
            """
            SELECT evidence_type, evidence_url, title, description
            FROM evidence
            WHERE workflow_id = ? AND round = ? AND verification_status = 'approved'
            ORDER BY id
            """,
            (proof_row["workflow_id"], proof_row["round"]),
        ).fetchall()
        decision = conn.execute(
            """
            SELECT reason, created_at FROM state_transitions
            WHERE workflow_id = ? AND event = 'APPROVE' ORDER BY seq DESC LIMIT 1
            """,
            (proof_row["workflow_id"],),
        ).fetchone()
        chain = verify_chain(conn, proof_row["workflow_id"])
    finally:
        conn.close()

    return render_template("proof.html", proof=proof_row, evidence=evidence, decision=decision,
                           chain=chain, evidence_types=EVIDENCE_TYPES)


@public_bp.route("/u/<username>")
def profile(username):
    conn = get_db_connection()
    try:
        owner = conn.execute("SELECT id, username FROM users WHERE username = ? AND role = 'developer'",
                             (username,)).fetchone()
        if owner is None:
            abort(404)
        proofs = conn.execute(
            """
            SELECT t.title, w.share_token, w.completed_at, rv.username AS reviewer_name
            FROM workflow_instances w
            JOIN tasks t ON t.id = w.task_id
            LEFT JOIN users rv ON rv.id = w.reviewer_id
            WHERE w.user_id = ? AND w.current_state = 'VERIFIED' AND w.share_token IS NOT NULL
            ORDER BY w.completed_at DESC
            """,
            (owner["id"],),
        ).fetchall()
    finally:
        conn.close()
    return render_template("profile.html", owner=owner, proofs=proofs)
