"""Evidence validation, storage and the completeness checklist.

Important distinction used everywhere in the app:

  draft      - added by the owner, not yet submitted
  submitted  - locked and sent for review
  approved   - a reviewer accepted it (only set by the engine on APPROVE)
  rejected   - a reviewer rejected it (only set by the engine on REJECT)

Adding evidence never changes a task's verified status; only the automaton
reaching VERIFIED does.
"""
import re
from urllib.parse import urlparse

from automata.states import WorkflowState
from database.db import transaction

EVIDENCE_TYPES = {
    "github_repo": "GitHub repository",
    "commit": "Commit",
    "pull_request": "Pull request",
    "deployed_url": "Deployed project",
    "demo_url": "Demo / video",
    "writeup": "Written explanation",
    "other_link": "Other link",
}
URL_TYPES = set(EVIDENCE_TYPES) - {"writeup"}

_GITHUB = r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
_PATTERNS = {
    "github_repo": (re.compile(rf"^{_GITHUB}/?$"),
                    "Use the repository root, e.g. https://github.com/owner/repo"),
    "commit": (re.compile(rf"^{_GITHUB}/commit/[0-9a-fA-F]{{7,40}}/?$"),
               "Use a commit URL, e.g. https://github.com/owner/repo/commit/<sha>"),
    "pull_request": (re.compile(rf"^{_GITHUB}/pull/\d+/?$"),
                     "Use a pull-request URL, e.g. https://github.com/owner/repo/pull/12"),
}

MAX_URL = 500
MAX_TITLE = 120
MAX_DESCRIPTION = 5000
MIN_WRITEUP = 50


class EvidenceError(ValueError):
    """Raised with a list of human-readable problems."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def _valid_http_url(value):
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.hostname)
        and "." in parsed.hostname
        and not parsed.username
        and not parsed.password
        and not any(ch.isspace() for ch in value)
    )


def validate_evidence(evidence_type, url, title, description):
    """Return a cleaned dict or raise EvidenceError listing every problem."""
    evidence_type = (evidence_type or "").strip()
    url = (url or "").strip()
    title = (title or "").strip()
    description = (description or "").strip()
    problems = []

    if evidence_type not in EVIDENCE_TYPES:
        raise EvidenceError(["Choose a valid evidence type."])

    if len(title) > MAX_TITLE:
        problems.append(f"Title must be at most {MAX_TITLE} characters.")
    if len(description) > MAX_DESCRIPTION:
        problems.append(f"Description must be at most {MAX_DESCRIPTION} characters.")

    if evidence_type == "writeup":
        url = ""
        if len(description) < MIN_WRITEUP:
            problems.append(
                f"A written explanation needs at least {MIN_WRITEUP} characters "
                "describing what you built and what you did."
            )
    else:
        if not url:
            problems.append("A URL is required for this evidence type.")
        elif len(url) > MAX_URL:
            problems.append(f"URL must be at most {MAX_URL} characters.")
        elif not _valid_http_url(url):
            problems.append("Enter a valid http(s) URL.")
        elif evidence_type in _PATTERNS:
            pattern, hint = _PATTERNS[evidence_type]
            if not pattern.match(url):
                problems.append(hint)
        if evidence_type == "other_link" and len(description) < 20:
            problems.append("Explain in at least 20 characters what this link proves.")

    if problems:
        raise EvidenceError(problems)

    return {
        "evidence_type": evidence_type,
        "evidence_url": url or None,
        "title": title or EVIDENCE_TYPES[evidence_type],
        "description": description or None,
    }


def score_evidence(rows):
    """Transparent completeness checklist for reviewers (0-100).

    This measures how *complete* the submitted package is. It is not
    automated verification and says nothing about the quality of the work.
    """
    types = {row["evidence_type"] for row in rows}
    writeup_len = max(
        (len(row["description"] or "") for row in rows if row["evidence_type"] == "writeup"),
        default=0,
    )
    criteria = [
        ("Source repository linked", 25, "github_repo" in types),
        ("Specific commit or pull request cited", 25, bool(types & {"commit", "pull_request"})),
        ("Working deployment or demo provided", 20, bool(types & {"deployed_url", "demo_url"})),
        ("Written explanation of at least 100 characters", 20, writeup_len >= 100),
        ("Three or more different evidence types", 10, len(types) >= 3),
    ]
    items = [{"label": label, "points": points, "met": met} for label, points, met in criteria]
    return {
        "score": sum(item["points"] for item in items if item["met"]),
        "max": sum(item["points"] for item in items),
        "items": items,
    }


def list_evidence(conn, workflow_id):
    return conn.execute(
        """
        SELECT id, round, evidence_type, evidence_url, title, description,
               verification_status, created_at
        FROM evidence
        WHERE workflow_id = ?
        ORDER BY round DESC, id ASC
        """,
        (workflow_id,),
    ).fetchall()


class EvidenceNotAllowed(ValueError):
    """The workflow is not in a state that accepts evidence changes."""


def add_evidence(conn, workflow_id, user_id, evidence_type, url, title, description):
    """Store a draft evidence item.

    Allowed only for the owner while the workflow is EVIDENCE_REQUIRED - the
    automaton, not the form, decides when evidence can be added.
    """
    cleaned = validate_evidence(evidence_type, url, title, description)

    with transaction(conn):
        workflow = conn.execute(
            "SELECT id, user_id, task_id, current_state, round FROM workflow_instances WHERE id = ?",
            (workflow_id,),
        ).fetchone()
        if workflow is None or workflow["user_id"] != user_id:
            raise EvidenceNotAllowed("Task not found.")
        if workflow["current_state"] != WorkflowState.EVIDENCE_REQUIRED.value:
            raise EvidenceNotAllowed(
                "Evidence can only be added while the task is in EVIDENCE_REQUIRED "
                f"(currently {workflow['current_state']})."
            )

        if cleaned["evidence_url"]:
            duplicate = conn.execute(
                "SELECT 1 FROM evidence WHERE workflow_id = ? AND round = ? AND evidence_url = ?",
                (workflow_id, workflow["round"], cleaned["evidence_url"]),
            ).fetchone()
            if duplicate:
                raise EvidenceError(["That URL is already attached to this submission."])

        project = conn.execute(
            "SELECT project_id FROM tasks WHERE id = ?", (workflow["task_id"],)
        ).fetchone()

        cursor = conn.execute(
            """
            INSERT INTO evidence
                (user_id, project_id, task_id, workflow_id, round, title, description,
                 evidence_type, evidence_url, verification_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
            """,
            (
                user_id,
                project["project_id"] if project else None,
                workflow["task_id"],
                workflow_id,
                workflow["round"],
                cleaned["title"],
                cleaned["description"],
                cleaned["evidence_type"],
                cleaned["evidence_url"],
            ),
        )
        return cursor.lastrowid


def delete_draft_evidence(conn, workflow_id, user_id, evidence_id):
    """Remove a draft item. Submitted evidence is locked and cannot be removed."""
    with transaction(conn):
        workflow = conn.execute(
            "SELECT user_id, current_state, round FROM workflow_instances WHERE id = ?",
            (workflow_id,),
        ).fetchone()
        if workflow is None or workflow["user_id"] != user_id:
            raise EvidenceNotAllowed("Task not found.")
        if workflow["current_state"] != WorkflowState.EVIDENCE_REQUIRED.value:
            raise EvidenceNotAllowed("Submitted evidence is locked and cannot be removed.")

        cursor = conn.execute(
            """
            DELETE FROM evidence
            WHERE id = ? AND workflow_id = ? AND round = ? AND verification_status = 'draft'
            """,
            (evidence_id, workflow_id, workflow["round"]),
        )
        if cursor.rowcount != 1:
            raise EvidenceNotAllowed("Evidence item not found.")


def assign_reviewer(conn, workflow_id, owner_id, identifier):
    """Choose which reviewer evidence is sent to (by username or e-mail).

    Allowed while the owner is still collecting evidence, or - for workflows
    that were submitted before assignment existed - while still unassigned.
    Returns the reviewer's username.
    """
    identifier = (identifier or "").strip().lower()
    if not identifier:
        raise EvidenceError(["Enter the username or e-mail of the reviewer."])

    with transaction(conn):
        workflow = conn.execute(
            "SELECT user_id, current_state, reviewer_id FROM workflow_instances WHERE id = ?",
            (workflow_id,),
        ).fetchone()
        if workflow is None or workflow["user_id"] != owner_id:
            raise EvidenceNotAllowed("Task not found.")
        state = workflow["current_state"]
        if not (state == WorkflowState.EVIDENCE_REQUIRED.value
                or (state == WorkflowState.EVIDENCE_SUBMITTED.value and workflow["reviewer_id"] is None)):
            raise EvidenceNotAllowed(
                "The reviewer can only be chosen while you are preparing evidence; "
                "after submission the request is final."
            )

        reviewer = conn.execute(
            """
            SELECT id, username FROM users
            WHERE role = 'reviewer' AND (lower(username) = ? OR lower(email) = ?)
            """,
            (identifier, identifier),
        ).fetchone()
        if reviewer is None:
            raise EvidenceError(["No reviewer account matches that username or e-mail."])
        if reviewer["id"] == owner_id:
            raise EvidenceError(["You cannot review your own work."])

        conn.execute(
            "UPDATE workflow_instances SET reviewer_id = ? WHERE id = ?",
            (reviewer["id"], workflow_id),
        )
        return reviewer["username"]
