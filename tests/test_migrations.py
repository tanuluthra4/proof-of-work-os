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
