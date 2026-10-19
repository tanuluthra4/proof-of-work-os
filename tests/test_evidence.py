import pytest

from automata.states import WorkflowEvent as E
from services.evidence_service import (
    EvidenceError, EvidenceNotAllowed, delete_draft_evidence, score_evidence, validate_evidence,
)
from tests.helpers import (
    add_evidence_item, advance_to_evidence_required, fire, make_task, make_user, query,
)
from database.db import get_db_connection

GOOD = [
    ("github_repo", "https://github.com/octocat/Hello-World", "", ""),
    ("commit", "https://github.com/octocat/Hello-World/commit/7fd1a60b01f91b314f59955a4e4d4e80d8edf11d", "", ""),
    ("pull_request", "https://github.com/octocat/Hello-World/pull/1347", "", ""),
    ("deployed_url", "https://my-app.onrender.com", "", ""),
    ("demo_url", "https://youtu.be/abc123", "Demo", "walkthrough"),
    ("writeup", "", "", "I designed the schema, wrote the REST endpoints and the tests myself, and deployed it."),
    ("other_link", "https://example.com/certificate", "", "Course certificate showing the exam I passed."),
]


@pytest.mark.parametrize("kind,url,title,description", GOOD)
def test_valid_evidence_is_accepted(kind, url, title, description):
    cleaned = validate_evidence(kind, url, title, description)
    assert cleaned["evidence_type"] == kind and cleaned["title"]


BAD = [
    ("nonsense", "https://github.com/o/r", "unknown type"),
    ("github_repo", "", "missing url"),
    ("github_repo", "javascript:alert(1)", "javascript scheme"),
    ("github_repo", "ftp://github.com/o/r", "ftp scheme"),
    ("github_repo", "https://gitlab.com/o/r", "wrong host for github type"),
    ("github_repo", "https://github.com/o/r/commit/abcdef1", "commit url given as repo"),
    ("commit", "https://github.com/o/r", "repo given as commit"),
    ("commit", "https://github.com/o/r/commit/zzzzzzz", "non-hex sha"),
    ("pull_request", "https://github.com/o/r/pull/abc", "non numeric PR"),
    ("deployed_url", "https://user:pw@example.com", "credentials in url"),
    ("deployed_url", "https://localhost", "no dotted host"),
    ("deployed_url", "https://exa mple.com", "whitespace"),
    ("writeup", "", "too short writeup"),
    ("other_link", "https://example.com", "no explanation"),
]


@pytest.mark.parametrize("kind,url,label", BAD)
def test_malformed_evidence_is_rejected(kind, url, label):
    with pytest.raises(EvidenceError):
        validate_evidence(kind, url, "", "short")


def test_overlong_fields_rejected():
    with pytest.raises(EvidenceError):
        validate_evidence("github_repo", "https://github.com/o/r", "t" * 121, "")
    with pytest.raises(EvidenceError):
        validate_evidence("deployed_url", "https://example.com/" + "a" * 500, "", "")


@pytest.fixture
def world(app):
    alice = make_user(app, "alice")
    task, wf = make_task(app, alice)
    return app, alice, wf


def test_evidence_only_accepted_in_evidence_required(world):
    app, alice, wf = world
    with pytest.raises(EvidenceNotAllowed):
        add_evidence_item(app, wf, alice)               # CREATED
    advance_to_evidence_required(app, wf, alice)
    add_evidence_item(app, wf, alice)                   # EVIDENCE_REQUIRED -> fine
    fire(app, wf, E.SUBMIT_EVIDENCE, alice)
    with pytest.raises(EvidenceNotAllowed):
        add_evidence_item(app, wf, alice, "commit", "https://github.com/octocat/hello/commit/abcdef1")  # locked


def test_other_users_cannot_add_evidence(world):
    app, alice, wf = world
    bob = make_user(app, "bob")
    advance_to_evidence_required(app, wf, alice)
    with pytest.raises(EvidenceNotAllowed):
        add_evidence_item(app, wf, bob)


def test_duplicate_url_in_same_submission_rejected(world):
    app, alice, wf = world
    advance_to_evidence_required(app, wf, alice)
    add_evidence_item(app, wf, alice)
    with pytest.raises(EvidenceError):
        add_evidence_item(app, wf, alice)


def test_submitted_evidence_is_not_verified_evidence(world):
    app, alice, wf = world
    advance_to_evidence_required(app, wf, alice)
    add_evidence_item(app, wf, alice)
    assert query(app, "SELECT verification_status FROM evidence")[0][0] == "draft"
    fire(app, wf, E.SUBMIT_EVIDENCE, alice)
    assert query(app, "SELECT verification_status FROM evidence")[0][0] == "submitted"
    assert query(app, "SELECT current_state FROM workflow_instances WHERE id = ?", (wf,))[0][0] != "VERIFIED"
    assert query(app, "SELECT COUNT(*) FROM verifications")[0][0] == 0


def test_draft_can_be_removed_but_submitted_cannot(world):
    app, alice, wf = world
    advance_to_evidence_required(app, wf, alice)
    evidence_id = add_evidence_item(app, wf, alice)
    with app.app_context():
        conn = get_db_connection()
        delete_draft_evidence(conn, wf, alice, evidence_id)
        assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
        conn.close()
    evidence_id = add_evidence_item(app, wf, alice)
    fire(app, wf, E.SUBMIT_EVIDENCE, alice)
    with app.app_context():
        conn = get_db_connection()
        with pytest.raises(EvidenceNotAllowed):
            delete_draft_evidence(conn, wf, alice, evidence_id)
        conn.close()


def test_completeness_checklist_is_transparent():
    def row(kind, description=""):
        return {"evidence_type": kind, "description": description}
    assert score_evidence([])["score"] == 0
    full = score_evidence([row("github_repo"), row("commit"), row("deployed_url"), row("writeup", "x" * 100)])
    assert full["score"] == 100 and all(item["met"] for item in full["items"])
    partial = score_evidence([row("github_repo"), row("writeup", "too short")])
    assert partial["score"] == 25
    assert [i["label"] for i in partial["items"] if i["met"]] == ["Source repository linked"]
