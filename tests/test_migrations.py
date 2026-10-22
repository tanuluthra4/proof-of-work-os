"""Upgrade safety: an old database keeps its data and gains the new structures."""
import os
import sqlite3

from app import init_db
from database.db import get_db_connection
from database.migrations import LATEST_VERSION
from services.integrity import verify_chain

SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "schema.sql")


def build_legacy_database(path):
    """The database as it looked before this upgrade (baseline schema + old-style rows)."""
    conn = sqlite3.connect(path)
    conn.executescript(open(SCHEMA, encoding="utf-8").read())
    conn.execute("INSERT INTO users (username, email, password) VALUES ('old', 'old@example.com', 'hash')")
    conn.execute("INSERT INTO projects (user_id, title) VALUES (1, 'Legacy project')")
    conn.execute("INSERT INTO tasks (user_id, project_id, title, status) VALUES (1, 1, 'Old task A', 'Completed')")
    conn.execute("INSERT INTO tasks (user_id, title, status) VALUES (1, 'Old task B', 'Pending')")
    conn.execute("INSERT INTO tasks (user_id, title, status) VALUES (1, 'Task without workflow', 'Pending')")
    conn.execute("INSERT INTO workflow_instances (user_id, task_id, current_state) VALUES (1, 1, 'EVIDENCE_REQUIRED')")
    conn.execute("INSERT INTO workflow_instances (user_id, task_id, current_state) VALUES (1, 2, 'CREATED')")
    for frm, event, to in (("CREATED", "START", "IN_PROGRESS"), ("IN_PROGRESS", "REQUEST_EVIDENCE", "EVIDENCE_REQUIRED")):
        conn.execute("INSERT INTO state_transitions (workflow_id, from_state, event, to_state, reason) "
                     "VALUES (1, ?, ?, ?, 'legacy reason')", (frm, event, to))
    conn.commit()
    conn.close()


def test_legacy_database_is_upgraded_in_place(tmp_path):
    path = str(tmp_path / "legacy.db")
    build_legacy_database(path)

    assert init_db(path) == LATEST_VERSION

    conn = get_db_connection(path)
    # nothing was lost
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 3
    assert conn.execute("SELECT username, role FROM users").fetchone()["role"] == "developer"
    legacy = conn.execute("SELECT event, reason FROM state_transitions ORDER BY id").fetchall()
    assert [(r["event"], r["reason"]) for r in legacy] == [("START", "legacy reason"), ("REQUEST_EVIDENCE", "legacy reason")]

    # the task that had no workflow got exactly one, once
    assert conn.execute("SELECT COUNT(*) FROM workflow_instances WHERE task_id = 3").fetchone()[0] == 1
    # existing history was sealed into a verifiable chain and keeps its state
    assert verify_chain(conn, 1)["ok"] and verify_chain(conn, 2)["ok"]
    row = conn.execute("SELECT current_state, transition_count FROM workflow_instances WHERE id = 1").fetchone()
    assert (row["current_state"], row["transition_count"]) == ("EVIDENCE_REQUIRED", 2)
    assert conn.execute("SELECT DISTINCT actor_role FROM state_transitions").fetchall()[0][0] == "legacy"
    conn.close()


def test_migration_is_repeatable(tmp_path):
    path = str(tmp_path / "legacy.db")
    build_legacy_database(path)
    init_db(path)
    conn = get_db_connection(path)
    before = [tuple(r) for r in conn.execute("SELECT id, seq, hash FROM state_transitions ORDER BY id")]
    workflows = conn.execute("SELECT COUNT(*) FROM workflow_instances").fetchone()[0]
    conn.close()

    assert init_db(path) == LATEST_VERSION
    assert init_db(path) == LATEST_VERSION

    conn = get_db_connection(path)
    assert [tuple(r) for r in conn.execute("SELECT id, seq, hash FROM state_transitions ORDER BY id")] == before
    assert conn.execute("SELECT COUNT(*) FROM workflow_instances").fetchone()[0] == workflows
    conn.close()


def test_fresh_database_starts_at_latest_version(tmp_path):
    path = str(tmp_path / "fresh.db")
    assert init_db(path) == LATEST_VERSION
    conn = get_db_connection(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"users", "tasks", "workflow_instances", "state_transitions", "evidence", "blocked_actions"} <= tables
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_duplicate_workflows_are_never_deleted_to_fit_a_constraint(tmp_path):
    path = str(tmp_path / "dups.db")
    build_legacy_database(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO workflow_instances (user_id, task_id, current_state) VALUES (1, 1, 'CREATED')")
    conn.commit()
    conn.close()

    init_db(path)
    conn = get_db_connection(path)
    assert conn.execute("SELECT COUNT(*) FROM workflow_instances WHERE task_id = 1").fetchone()[0] == 2
    conn.close()


def test_migration_2_adds_reviewer_and_share_columns_without_touching_data(tmp_path):
    path = str(tmp_path / "legacy.db")
    build_legacy_database(path)
    init_db(path)
    conn = get_db_connection(path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(workflow_instances)")}
    assert {"reviewer_id", "share_token"} <= columns
    assert conn.execute("SELECT COUNT(*) FROM workflow_instances WHERE reviewer_id IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION >= 2
    conn.close()


def build_v2_database(path):
    """A database as the single-reviewer release left it (schema version 2), with a
    VERIFIED proof whose chain was written before multi-reviewer support existed."""
    from database.migrations import MIGRATIONS
    from services.integrity import GENESIS_HASH, compute_hash
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(open(SCHEMA, encoding="utf-8").read())
    for target, migrate in MIGRATIONS:
        if target <= 2:
            migrate(conn)
            conn.execute(f"PRAGMA user_version = {target}")
    conn.execute("INSERT INTO users (username, email, password, role) VALUES ('dev', 'dev@example.com', 'x', 'developer')")
    conn.execute("INSERT INTO users (username, email, password, role) VALUES ('oldrev', 'oldrev@example.com', 'x', 'reviewer')")
    conn.execute("INSERT INTO tasks (user_id, title, status) VALUES (1, 'Legacy verified', 'Completed')")
    conn.execute("INSERT INTO workflow_instances (user_id, task_id, current_state, reviewer_id, result, share_token) "
                 "VALUES (1, 1, 'VERIFIED', 2, 'VERIFIED', 'legacytoken')")
    conn.execute("INSERT INTO evidence (workflow_id, user_id, round, evidence_type, evidence_url, title, description, "
                 "verification_status, created_at) VALUES (1, 1, 1, 'github_repo', 'https://github.com/octocat/hello', "
                 "'repo', 'd', 'approved', '2026-01-01 00:00:00')")
    from services.integrity import evidence_digest
    digest = evidence_digest(conn.execute("SELECT id, evidence_type, evidence_url, title, description, created_at "
                                          "FROM evidence WHERE workflow_id = 1").fetchall())
    steps = [("CREATED", "START", "IN_PROGRESS", 1, "owner"),
             ("IN_PROGRESS", "REQUEST_EVIDENCE", "EVIDENCE_REQUIRED", 1, "owner"),
             ("EVIDENCE_REQUIRED", "SUBMIT_EVIDENCE", "EVIDENCE_SUBMITTED", 1, "owner"),
             ("EVIDENCE_SUBMITTED", "START_VALIDATION", "VALIDATING", 2, "reviewer"),
             ("VALIDATING", "APPROVE", "VERIFIED", 2, "reviewer")]
    prev = GENESIS_HASH
    for seq, (frm, ev, to, actor, role) in enumerate(steps, start=1):
        rec = dict(workflow_id=1, seq=seq, from_state=frm, event=ev, to_state=to, condition_result=None,
                   reason="legacy note here", actor_id=actor, actor_role=role,
                   evidence_digest=digest if ev == "SUBMIT_EVIDENCE" else None,
                   created_at="2026-01-01 00:00:00", prev_hash=prev)
        rec["hash"] = compute_hash(rec)
        cols = list(rec)
        conn.execute(f"INSERT INTO state_transitions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     tuple(rec[c] for c in cols))
        prev = rec["hash"]
    conn.execute("UPDATE workflow_instances SET transition_count = 5, chain_head = ? WHERE id = 1", (prev,))
    conn.commit()
    conn.close()


def test_migration_3_preserves_legacy_proof_and_fabricates_no_decisions(tmp_path):
    path = str(tmp_path / "v2.db")
    build_v2_database(path)
    assert init_db(path) == LATEST_VERSION >= 3

    conn = get_db_connection(path)
    # the old single reviewer becomes an assignment; nothing is invented as a decision
    a = conn.execute("SELECT reviewer_id, status, round FROM review_assignments").fetchall()
    assert [(r["reviewer_id"], r["status"], r["round"]) for r in a] == [(2, "assigned", 1)]
    assert conn.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0] == 0
    # the old chain (no meta, APPROVE by the reviewer) still replays cleanly
    result = verify_chain(conn, 1)
    assert result["ok"], result
    assert conn.execute("SELECT required_approvals FROM workflow_instances").fetchone()[0] == 1
    # the legacy public page derives the reviewer from the chain, not from invented rows
    from services.review_service import public_summary
    summary = public_summary(conn, 1, 1)
    assert [(r["username"], r["decision"]) for r in summary] == [("oldrev", "approve")]
    conn.close()
    assert init_db(path) == LATEST_VERSION            # idempotent


def test_migration_3_adds_append_only_decision_table(tmp_path):
    path = str(tmp_path / "v2.db")
    build_v2_database(path)
    init_db(path)
    conn = get_db_connection(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(review_decisions)")}
    assert {"decision", "notes", "private_notes", "note_public", "evidence_digest", "round"} <= cols
    assert "share_revoked_at" in {r["name"] for r in conn.execute("PRAGMA table_info(workflow_instances)")}
    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"review_decisions_no_update", "review_decisions_no_delete"} <= triggers
    conn.close()
