"""HTTP-level tests: auth, CSRF, isolation, the end-to-end workflow, error paths."""
import re

import pytest

from tests.helpers import execute, query


class Browser:
    """A test client that automatically attaches the session's CSRF token."""

    def __init__(self, app):
        self.client = app.test_client()

    def token(self):
        with self.client.session_transaction() as session:
            token = session.get("_csrf")
        if not token:
            self.client.get("/login")
            with self.client.session_transaction() as session:
                token = session["_csrf"]
        return token

    def get(self, url, **kwargs):
        return self.client.get(url, **kwargs)

    def post(self, url, data=None, csrf=True, **kwargs):
        data = dict(data or {})
        if csrf:
            data["csrf_token"] = self.token()
        return self.client.post(url, data=data, **kwargs)

    def signup(self, username, email=None, password="correct-horse", role="developer"):
        return self.post("/signup", {"username": username, "email": email or f"{username}@example.com",
                                     "password": password, "role": role})

    def login(self, email, password="correct-horse"):
        return self.post("/login", {"email": email, "password": password})


@pytest.fixture
def alice(app):
    b = Browser(app)
    assert b.signup("alice").status_code == 302
    return b


@pytest.fixture
def bob(app):
    b = Browser(app)
    assert b.signup("bob").status_code == 302
    return b


@pytest.fixture
def reviewer(app):
    b = Browser(app)
    assert b.signup("rev", "rev@example.com", role="reviewer").status_code == 302
    return b


def create_task(browser, title="Ship a REST API", **extra):
    response = browser.post("/add-task", {"title": title, **extra})
    assert response.status_code == 302, response.get_data(as_text=True)
    return int(re.search(r"/tasks/(\d+)", response.headers["Location"]).group(1))


def fire(browser, task_id, event, reason=None):
    data = {"reason": reason} if reason else {}
    return browser.post(f"/tasks/{task_id}/events/{event}", data, follow_redirects=True)


def state(app, task_id):
    return query(app, "SELECT current_state FROM workflow_instances WHERE task_id = ?", (task_id,))[0][0]


# ------------------------------------------------------------------- auth
def test_protected_pages_redirect_when_logged_out(app):
    b = Browser(app)
    for url in ("/dashboard", "/add-task", "/projects", "/projects/new", "/tasks/1"):
        response = b.get(url)
        assert response.status_code == 302 and "/login" in response.headers["Location"], url
    assert b.get("/review").status_code == 302


def test_signup_validation(app):
    b = Browser(app)
    cases = [
        ({"username": "ab", "email": "a@b.co", "password": "longenough"}, "Username"),
        ({"username": "valid_name", "email": "not-an-email", "password": "longenough"}, "valid email"),
        ({"username": "valid_name", "email": "a@b.co", "password": "short"}, "Password"),
    ]
    for data, fragment in cases:
        response = b.post("/signup", data)
        assert response.status_code == 200 and fragment in response.get_data(as_text=True)
    assert query(app, "SELECT COUNT(*) FROM users")[0][0] == 0


def test_signup_duplicates_and_password_hashing(app, alice):
    other = Browser(app)
    response = other.post("/signup", {"username": "alice2", "email": "ALICE@example.com", "password": "correct-horse"})
    assert "Email already exists" in response.get_data(as_text=True)
    response = other.post("/signup", {"username": "alice", "email": "new@example.com", "password": "correct-horse"})
    assert "Username already taken" in response.get_data(as_text=True)
    stored = query(app, "SELECT password FROM users WHERE username = 'alice'")[0][0]
    assert stored != "correct-horse" and stored.startswith(("scrypt:", "pbkdf2:"))


def test_home_page_offers_separate_role_portals(app):
    b = Browser(app)
    response = b.get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "I'm a Developer" in html
    assert "I'm a Reviewer" in html
    assert "/login?role=developer" in html
    assert "/login?role=reviewer" in html


def test_login_portal_checks_actual_account_role(app, alice, reviewer):
    # A developer cannot gain reviewer permissions by choosing the reviewer portal.
    developer_attempt = alice.post("/login?role=reviewer", {
        "email": "alice@example.com", "password": "correct-horse", "role": "reviewer"
    }, follow_redirects=True)
    assert "developer account" in developer_attempt.get_data(as_text=True)
    assert reviewer.get("/review").status_code == 200


def test_reviewer_login_redirects_to_review_queue(app, reviewer):
    # The fixture creates a configured reviewer and leaves that account logged in;
    # use a fresh browser to exercise the actual reviewer login form.
    b = Browser(app)
    response = b.post("/login?role=reviewer", {
        "email": "rev@example.com", "password": "correct-horse", "role": "reviewer"
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/review")


def test_login_logout_flow(app, alice):
    b = Browser(app)
    assert b.login("alice@example.com", "wrong-password").status_code == 200
    assert b.get("/dashboard").status_code == 302
    assert b.login("Alice@Example.com").status_code == 302          # e-mail is case-insensitive
    assert b.get("/dashboard").status_code == 200
    assert b.get("/logout").status_code == 405                      # logout is POST-only
    assert b.post("/logout").status_code == 302
    assert b.get("/dashboard").status_code == 302


def test_csrf_token_is_required_for_every_post(app, alice):
    task_id = create_task(alice)
    for url in ("/login", "/signup", "/logout", "/add-task", f"/tasks/{task_id}/delete",
                f"/tasks/{task_id}/events/START", f"/tasks/{task_id}/evidence", "/projects/new",
                f"/tasks/{task_id}/complete"):
        response = alice.post(url, {"title": "x"}, csrf=False)
        assert response.status_code == 400, url
    assert state(app, task_id) == "CREATED"
    assert alice.post(f"/tasks/{task_id}/delete", {"csrf_token": "forged"}, csrf=False).status_code == 400


def test_session_cookie_flags(app, alice):
    cookie = alice.client.get_cookie("session")
    assert cookie.http_only is True and cookie.same_site == "Lax"


def test_secret_key_is_required_in_production(monkeypatch):
    from app import create_app
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"ENV": "production", "SECRET_KEY": None, "DATABASE": ":memory:"})


# --------------------------------------------------------------- tasks CRUD
def test_task_crud_and_workflow_initialisation(app, alice):
    task_id = create_task(alice, "Learn SQL", description="window functions", priority="high",
                          due_date="2027-01-31")
    assert state(app, task_id) == "CREATED"
    assert query(app, "SELECT COUNT(*) FROM workflow_instances")[0][0] == 1

    page = alice.get(f"/tasks/{task_id}")
    assert page.status_code == 200 and "Learn SQL" in page.get_data(as_text=True)

    assert alice.post(f"/tasks/{task_id}/edit", {"title": "Learn SQL deeply", "priority": "low"}).status_code == 302
    assert query(app, "SELECT title, priority FROM tasks")[0][:] == ("Learn SQL deeply", "low")

    response = alice.post(f"/tasks/{task_id}/delete")
    assert response.status_code == 302
    assert query(app, "SELECT COUNT(*) FROM tasks")[0][0] == 0
    assert query(app, "SELECT COUNT(*) FROM workflow_instances")[0][0] == 0


def test_task_form_validation(app, alice):
    for data in ({"title": ""}, {"title": "x" * 201}, {"title": "ok", "priority": "urgent"},
                 {"title": "ok", "due_date": "tomorrow"}, {"title": "ok", "project_id": "9999"},
                 {"title": "ok", "project_id": "abc"}):
        response = alice.post("/add-task", data)
        assert response.status_code == 200, data
    assert query(app, "SELECT COUNT(*) FROM tasks")[0][0] == 0


def test_cannot_attach_task_to_someone_elses_project(app, alice, bob):
    bob.post("/projects/new", {"title": "Bob's secret", "status": "Active"})
    bobs_project = query(app, "SELECT id FROM projects")[0][0]
    response = alice.post("/add-task", {"title": "sneaky", "project_id": str(bobs_project)})
    assert response.status_code == 200 and "your own projects" in response.get_data(as_text=True)
    assert query(app, "SELECT COUNT(*) FROM tasks")[0][0] == 0


def test_project_urls_must_be_http(app, alice):
    response = alice.post("/projects/new", {"title": "P", "status": "Active", "github_url": "javascript:alert(1)"})
    assert response.status_code == 200
    assert query(app, "SELECT COUNT(*) FROM projects")[0][0] == 0
    ok = alice.post("/projects/new", {"title": "P", "status": "Active", "github_url": "https://github.com/o/r"})
    assert ok.status_code == 302


def test_self_reported_completion_does_not_verify(app, alice):
    task_id = create_task(alice)
    alice.post(f"/tasks/{task_id}/complete")
    assert query(app, "SELECT status FROM tasks")[0][0] == "Completed"
    assert state(app, task_id) == "CREATED"
    assert query(app, "SELECT COUNT(*) FROM verifications")[0][0] == 0


# ---------------------------------------------------------- user isolation
def test_users_cannot_touch_each_others_tasks(app, alice, bob):
    task_id = create_task(alice, "Alice only")
    alice.post(f"/tasks/{task_id}/events/START")

    assert bob.get(f"/tasks/{task_id}").status_code == 404
    assert bob.get(f"/tasks/{task_id}/edit").status_code == 404
    assert bob.post(f"/tasks/{task_id}/edit", {"title": "pwned"}).status_code == 404
    assert bob.post(f"/tasks/{task_id}/delete").status_code == 404
    assert bob.post(f"/tasks/{task_id}/complete").status_code == 404
    assert bob.post(f"/tasks/{task_id}/events/REQUEST_EVIDENCE").status_code == 404
    assert bob.post(f"/tasks/{task_id}/evidence", {"evidence_type": "writeup", "description": "x" * 60}).status_code == 404
    assert bob.post(f"/tasks/{task_id}/evidence/1/delete").status_code == 404

    assert query(app, "SELECT title FROM tasks")[0][0] == "Alice only"
    assert state(app, task_id) == "IN_PROGRESS"
    assert "Alice only" not in bob.get("/dashboard").get_data(as_text=True)


def test_reviewer_cannot_see_drafts_or_open_the_queue_as_developer(app, alice, reviewer):
    task_id = create_task(alice)
    assert reviewer.get(f"/tasks/{task_id}").status_code == 404      # not submitted yet
    assert alice.get("/review").status_code == 403                    # developers have no queue


# -------------------------------------------------- end-to-end, via HTTP
def test_full_workflow_end_to_end(app, alice, reviewer):
    task_id = create_task(alice, "Prove my Flask skills")
    fire(alice, task_id, "START")
    fire(alice, task_id, "REQUEST_EVIDENCE")
    assert state(app, task_id) == "EVIDENCE_REQUIRED"

    # submitting with nothing attached is refused
    page = fire(alice, task_id, "SUBMIT_EVIDENCE")
    assert "GUARD_FAILED" in page.get_data(as_text=True)
    assert state(app, task_id) == "EVIDENCE_REQUIRED"

    bad = alice.post(f"/tasks/{task_id}/evidence", {"evidence_type": "github_repo", "evidence_url": "not a url"},
                     follow_redirects=True)
    assert "valid http(s) URL" in bad.get_data(as_text=True)
    alice.post(f"/tasks/{task_id}/evidence", {"evidence_type": "github_repo",
                                              "evidence_url": "https://github.com/octocat/hello"})
    alice.post(f"/tasks/{task_id}/evidence", {"evidence_type": "writeup",
                                              "description": "I built the models, routes and tests, and deployed it on Render."})
    # evidence is complete but no reviewer was chosen yet -> refused
    assert "Choose the reviewer" in fire(alice, task_id, "SUBMIT_EVIDENCE").get_data(as_text=True)
    assert state(app, task_id) == "EVIDENCE_REQUIRED"
    unknown = alice.post(f"/tasks/{task_id}/reviewer", {"reviewer": "nobody"}, follow_redirects=True)
    assert "No reviewer account matches" in unknown.get_data(as_text=True)
    alice.post(f"/tasks/{task_id}/reviewer", {"reviewer": "REV@example.com"})
    fire(alice, task_id, "SUBMIT_EVIDENCE")
    assert state(app, task_id) == "EVIDENCE_SUBMITTED"

    # owner cannot review; evidence is locked
    assert "UNAUTHORIZED_ACTOR" in fire(alice, task_id, "START_VALIDATION").get_data(as_text=True)
    assert state(app, task_id) == "EVIDENCE_SUBMITTED"
    locked = alice.post(f"/tasks/{task_id}/evidence", {"evidence_type": "writeup", "description": "y" * 60},
                        follow_redirects=True)
    assert "EVIDENCE_REQUIRED" in locked.get_data(as_text=True)

    # the reviewer sees it in the queue and reviews it
    assert "Prove my Flask skills" in reviewer.get("/review").get_data(as_text=True)
    fire(reviewer, task_id, "START_VALIDATION")
    assert state(app, task_id) == "VALIDATING"

    # self-approval through the UI is refused
    own = fire(alice, task_id, "APPROVE", "approving myself")
    assert "UNAUTHORIZED_ACTOR" in own.get_data(as_text=True) and state(app, task_id) == "VALIDATING"

    # a decision needs a reason
    assert "GUARD_FAILED" in fire(reviewer, task_id, "REJECT").get_data(as_text=True)

    fire(reviewer, task_id, "REJECT", "The repository does not contain the code described.")
    assert state(app, task_id) == "REJECTED"
    detail = alice.get(f"/tasks/{task_id}").get_data(as_text=True)
    assert "The repository does not contain the code described." in detail

    # corrected resubmission
    fire(alice, task_id, "RESTART")
    fire(alice, task_id, "REQUEST_EVIDENCE")
    alice.post(f"/tasks/{task_id}/evidence", {"evidence_type": "commit",
                                              "evidence_url": "https://github.com/octocat/hello/commit/abcdef1"})
    fire(alice, task_id, "SUBMIT_EVIDENCE")
    fire(reviewer, task_id, "START_VALIDATION")
    fire(reviewer, task_id, "APPROVE", "Commit is authored by the developer and implements the claim.")
    assert state(app, task_id) == "VERIFIED"

    page = alice.get(f"/tasks/{task_id}").get_data(as_text=True)
    assert "Integrity verified" in page and "Verified" in page
    # verified proofs are permanent
    assert alice.post(f"/tasks/{task_id}/delete", follow_redirects=True).status_code == 200
    assert query(app, "SELECT COUNT(*) FROM tasks")[0][0] == 1
    assert alice.post(f"/tasks/{task_id}/edit", {"title": "renamed"}, follow_redirects=True).status_code == 200
    assert query(app, "SELECT title FROM tasks")[0][0] == "Prove my Flask skills"


def test_blocked_attempt_shows_on_the_task_page(app, alice):
    task_id = create_task(alice)
    page = fire(alice, task_id, "APPROVE", "skip everything please").get_data(as_text=True)
    assert "INVALID_TRANSITION" in page
    detail = alice.get(f"/tasks/{task_id}").get_data(as_text=True)
    assert "Blocked attempts" in detail and "INVALID_TRANSITION" in detail
    assert state(app, task_id) == "CREATED"


def test_unknown_event_url_is_handled(app, alice):
    task_id = create_task(alice)
    page = fire(alice, task_id, "TELEPORT")
    assert page.status_code == 200 and "UNKNOWN_EVENT" in page.get_data(as_text=True)


def test_tampering_is_visible_on_the_page(app, alice):
    task_id = create_task(alice)
    fire(alice, task_id, "START", "kick-off")
    execute(app, "UPDATE state_transitions SET reason = 'rewritten' WHERE seq = 1", drop_triggers=True)
    page = alice.get(f"/tasks/{task_id}").get_data(as_text=True)
    assert "TAMPERING DETECTED" in page and "CONTENT_ALTERED" in page


# ----------------------------------------------------------------- dashboard
def test_dashboard_shows_each_tasks_own_state_without_creating_workflows(app, alice, bob):
    t1, t2, t3 = create_task(alice, "Task one"), create_task(alice, "Task two"), create_task(alice, "Task three")
    create_task(bob, "Bob task")
    fire(alice, t2, "START")
    fire(alice, t3, "START")
    fire(alice, t3, "REQUEST_EVIDENCE")

    before = query(app, "SELECT COUNT(*) FROM workflow_instances")[0][0]
    html = alice.get("/dashboard").get_data(as_text=True)
    alice.get("/dashboard")
    assert query(app, "SELECT COUNT(*) FROM workflow_instances")[0][0] == before == 4   # GET creates nothing

    cards = {}
    for match in re.finditer(r'<article class="task-card">.*?</article>', html, re.S):
        title = re.search(r"<h3><a[^>]*>(.*?)</a>", match.group(0)).group(1)
        cards[title] = match.group(0)
    assert {"Task one", "Task two", "Task three"} <= set(cards) and "Bob task" not in html
    assert "Created" in cards["Task one"] and "In progress" in cards["Task two"] and "Evidence required" in cards["Task three"]
    assert "Bob task" not in html


def test_dashboard_with_no_tasks_has_an_empty_state(app, alice):
    assert "No tasks yet" in alice.get("/dashboard").get_data(as_text=True)


# ------------------------------------------------------------- misc / errors
def test_error_pages_do_not_leak_internals(app, alice):
    page = alice.get("/tasks/999999")
    text = page.get_data(as_text=True)
    assert page.status_code == 404 and "Traceback" not in text
    assert alice.get("/nope").status_code == 404


def test_server_errors_are_generic(app, alice, monkeypatch):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    import routes.main as main

    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(main, "events_available_to", boom)
    create_task(alice)
    response = alice.get("/dashboard")
    text = response.get_data(as_text=True)
    assert response.status_code == 500 and "secret internal detail" not in text and "Traceback" not in text


def test_healthz_and_security_headers(app):
    response = Browser(app).get("/healthz")
    assert response.get_json() == {"status": "ok"}
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_signup_creates_the_chosen_role_and_lands_on_the_right_home(app):
    dev, rev = Browser(app), Browser(app)
    assert dev.signup("devon", role="developer").headers["Location"].endswith("/dashboard")
    assert rev.signup("riley", role="reviewer").headers["Location"].endswith("/review")
    assert query(app, "SELECT role FROM users WHERE username = 'devon'")[0][0] == "developer"
    assert query(app, "SELECT role FROM users WHERE username = 'riley'")[0][0] == "reviewer"
    assert b"reviewer" in rev.get("/signup?role=reviewer").data
    assert Browser(app).get("/signup?role=admin").status_code == 400


def test_reviewer_signup_can_be_restricted_to_an_allowlist(app):
    app.config["REVIEWER_OPEN_SIGNUP"] = False
    stranger = Browser(app)
    response = stranger.signup("stranger", role="reviewer")
    assert response.status_code == 200 and "invitation-only" in response.get_data(as_text=True)
    assert query(app, "SELECT COUNT(*) FROM users")[0][0] == 0
    assert Browser(app).signup("rev", "rev@example.com", role="reviewer").status_code == 302


def test_roles_are_separate_areas(app, alice, reviewer):
    assert reviewer.get("/dashboard").headers["Location"].endswith("/review")
    assert reviewer.get("/add-task").headers["Location"].endswith("/review")
    assert reviewer.get("/projects").headers["Location"].endswith("/review")
    assert reviewer.post("/add-task", {"title": "x"}).status_code == 302
    assert query(app, "SELECT COUNT(*) FROM tasks")[0][0] == 0
    assert alice.get("/review").status_code == 403
    assert alice.get("/").headers["Location"].endswith("/dashboard")
    assert reviewer.get("/").headers["Location"].endswith("/review")


def submit_for(browser, task_id, reviewer_name):
    fire(browser, task_id, "START")
    fire(browser, task_id, "REQUEST_EVIDENCE")
    browser.post(f"/tasks/{task_id}/evidence", {"evidence_type": "github_repo",
                                                "evidence_url": "https://github.com/octocat/hello"})
    browser.post(f"/tasks/{task_id}/reviewer", {"reviewer": reviewer_name})
    fire(browser, task_id, "SUBMIT_EVIDENCE")


def test_reviewer_only_sees_evidence_sent_to_them(app, alice, reviewer):
    other = Browser(app)
    other.signup("otto", role="reviewer")
    task_id = create_task(alice, "Sent to rev only")
    submit_for(alice, task_id, "rev")

    assert "Sent to rev only" in reviewer.get("/review").get_data(as_text=True)
    assert "Sent to rev only" not in other.get("/review").get_data(as_text=True)
    assert reviewer.get(f"/tasks/{task_id}").status_code == 200
    assert other.get(f"/tasks/{task_id}").status_code == 404
    fire(other, task_id, "START_VALIDATION")                  # 404, not even visible to them
    assert state(app, task_id) == "EVIDENCE_SUBMITTED"      # another reviewer cannot move it
    assert query(app, "SELECT reason FROM state_transitions WHERE event = 'SUBMIT_EVIDENCE'")[0][0] == "Sent to reviewer rev"


def test_reviewer_choice_rules(app, alice, bob, reviewer):
    task_id = create_task(alice)
    fire(alice, task_id, "START")
    assert "preparing evidence" in alice.post(f"/tasks/{task_id}/reviewer", {"reviewer": "rev"},
                                              follow_redirects=True).get_data(as_text=True)
    fire(alice, task_id, "REQUEST_EVIDENCE")
    # a developer account is not a valid reviewer
    assert "No reviewer account" in alice.post(f"/tasks/{task_id}/reviewer", {"reviewer": "bob"},
                                               follow_redirects=True).get_data(as_text=True)
    # nobody else can assign on alice's task
    assert bob.post(f"/tasks/{task_id}/reviewer", {"reviewer": "rev"}).status_code == 404
    alice.post(f"/tasks/{task_id}/reviewer", {"reviewer": "rev"})
    assert query(app, "SELECT reviewer_id FROM workflow_instances")[0][0] is not None


def verify_task(app, alice, reviewer, title="Shareable work"):
    task_id = create_task(alice, title)
    submit_for(alice, task_id, "rev")
    fire(reviewer, task_id, "START_VALIDATION")
    fire(reviewer, task_id, "APPROVE", "Matches the claim.")
    assert state(app, task_id) == "VERIFIED"
    return task_id


def test_public_proof_link_is_private_until_shared(app, alice, reviewer):
    task_id = verify_task(app, alice, reviewer)
    visitor = Browser(app)
    assert visitor.get("/proof/guess").status_code == 404
    assert "Shareable work" not in visitor.get("/u/alice").get_data(as_text=True)

    alice.post(f"/tasks/{task_id}/share")
    token = query(app, "SELECT share_token FROM workflow_instances")[0][0]
    assert token
    page = visitor.get(f"/proof/{token}")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Shareable work" in html and "Reviewed by" in html and "rev" in html
    assert "Matches the claim." in html and "github.com/octocat/hello" in html
    assert "every hash matched" in html
    assert "alice@example.com" not in html                     # no e-mail leakage
    assert "Shareable work" in visitor.get("/u/alice").get_data(as_text=True)

    alice.post(f"/tasks/{task_id}/unshare")
    assert visitor.get(f"/proof/{token}").status_code == 404


def test_only_verified_owned_tasks_can_be_shared(app, alice, bob, reviewer):
    pending = create_task(alice, "Not verified")
    alice.post(f"/tasks/{pending}/share")
    assert query(app, "SELECT share_token FROM workflow_instances")[0][0] is None
    done = verify_task(app, alice, reviewer)
    assert bob.post(f"/tasks/{done}/share").status_code == 404
    assert bob.post(f"/tasks/{done}/unshare").status_code == 404


def test_tampered_proof_is_flagged_publicly(app, alice, reviewer):
    task_id = verify_task(app, alice, reviewer)
    alice.post(f"/tasks/{task_id}/share")
    token = query(app, "SELECT share_token FROM workflow_instances")[0][0]
    execute(app, "UPDATE state_transitions SET reason = 'forged' WHERE event = 'APPROVE'", drop_triggers=True)
    assert "failed its integrity check" in Browser(app).get(f"/proof/{token}").get_data(as_text=True)
