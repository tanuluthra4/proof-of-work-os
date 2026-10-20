# Proof-of-Work OS

**An automata-based evidence verification system for developer skills.**

Live deployment (previous version): https://proof-of-work-os.onrender.com — redeploy to get this version.

## Problem

Skill lists and task trackers are self-reported: anyone can tick "Completed" or write "I know Flask". Recruiters cannot tell claim from proof.
Proof-of-Work OS makes a claim count only after it passes a **controlled verification lifecycle**: the developer attaches evidence, a *different* person reviews it, and every step is recorded in a tamper-evident log. The lifecycle is enforced by a finite-state machine, not by convention.

## Features

- Accounts with hashed passwords, CSRF protection, per-user data isolation
- Projects and tasks (create / view / edit / delete); every task gets one workflow
- **Finite-state machine** governing `CREATED → IN_PROGRESS → EVIDENCE_REQUIRED → EVIDENCE_SUBMITTED → VALIDATING → VERIFIED / REJECTED` (+ `RESTART` after rejection)
- Evidence submission (repo, commit, PR, deployment, demo, write-up, other link) with strict validation; evidence is locked once submitted
- **Two separate account types, chosen at sign-up and sign-in** (landing page: *I'm a Developer* / *I'm a Reviewer*). Developers manage tasks and evidence; reviewers have their own page. Neither can use the other's area.
- **Send evidence to a named reviewer** (username or e-mail). Only that reviewer sees it and only they can start a review, approve or reject (with a written reason). Nobody can review their own work.
- **Shareable verified proof**: after approval, the developer can create a public read-only link (and `/u/<username>` profile) to put on a resume. It shows the evidence, the named reviewer, the reviewer's note and a live integrity check. Private until the developer creates the link; can be disabled.
- **Standout feature — tamper-evident audit trail + live automaton view** (below)
- Reviewer aid: transparent evidence *completeness* checklist (not automated verification)
- Safe, repeatable schema migrations that preserve existing data

## Standout feature: hash-chained audit trail replayed through the automaton

Every successful transition is appended to `state_transitions` with `hash = SHA-256(fields + prev_hash)`, so each entry commits to all earlier ones. On every task page the app **verifies the chain and replays the recorded events through the automaton**. History is accepted only if:

1. every hash and link matches (no edited, removed or reordered records),
2. every step is an edge of the transition table (the event word is accepted by the DFA),
3. the replayed end state equals the state stored on the workflow,
4. the evidence that was submitted still matches the digest sealed at submission time.

The page also draws the automaton as SVG from the real transition table: current state, transitions already taken, transitions you can fire now, and transitions waiting on another role. Refused actions (invalid transition, wrong role, failed guard) are logged and shown under *Blocked attempts*; a "Demo" panel lets you try a forbidden event on purpose.

**Limits (say this in interviews):** it is tamper-*evident*, not tamper-proof. Someone with full write access to the SQLite file can recompute the whole chain. Anchoring `chain_head` externally would close that gap. Updates are also blocked at DB level by triggers, but a DB admin can drop triggers.

## The automaton

Formally a DFA `M = (Q, Σ, δ, q0, F)`:

- **Q** = 7 states, **Σ** = 7 events (`automata/states.py`)
- **δ** = a *partial* transition function (`automata/transitions.py`); undefined (state, event) pairs are rejected
- **q0** = `CREATED`, **F** = `{VERIFIED}`; `VERIFIED` is terminal

```mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> IN_PROGRESS: START (owner)
    IN_PROGRESS --> EVIDENCE_REQUIRED: REQUEST_EVIDENCE (owner)
    EVIDENCE_REQUIRED --> EVIDENCE_SUBMITTED: SUBMIT_EVIDENCE (owner, needs evidence)
    EVIDENCE_SUBMITTED --> VALIDATING: START_VALIDATION (reviewer)
    VALIDATING --> VERIFIED: APPROVE (reviewer, reason)
    VALIDATING --> REJECTED: REJECT (reviewer, reason)
    REJECTED --> IN_PROGRESS: RESTART (owner)
    VERIFIED --> [*]
```

Beyond δ, the engine checks, in order: event exists → δ defined → actor allowed (role read from DB; owner can never review own work) → guards (evidence present; decision reason ≥ 5 chars) → atomic write.

**Change from the earlier version:** `VERIFIED → RESTART` was removed so a verified proof cannot be silently re-opened.

## Architecture

| Path | Responsibility |
|---|---|
| `automata/` | Pure FSM: states, events, δ, role requirements. No DB/Flask |
| `services/automata_engine.py` | Transactional engine: checks, atomic state + history + side effects |
| `services/integrity.py` | Hash chain, evidence digest, chain verification/replay |
| `services/evidence_service.py` | Evidence validation, storage, completeness checklist |
| `services/fsm_view.py` | View-model for the SVG diagram (from real data) |
| `routes/` | Flask blueprints: auth, main/dashboard, tasks, projects |
| `database/` | `schema.sql` (original baseline), `migrations.py`, connection + `transaction()` |
| `templates/`, `static/` | Jinja UI |
| `tests/` | pytest suite |

Atomicity: each transition runs in `BEGIN IMMEDIATE`; state update is a compare-and-swap on the old state and count; the history insert, state update, verification records and task update commit together or roll back together.

## Database

Existing tables are kept. Migration 1 (tracked via `PRAGMA user_version`) adds: `users.role`; `workflow_instances.round/chain_head/transition_count`; chain columns on `state_transitions`; `evidence.workflow_id/round`; table `blocked_actions`; indexes (one workflow per task, unique `(workflow_id, seq)`); append-only triggers. Tasks lacking a workflow get one (replacing the old create-on-dashboard-load behaviour). Pre-existing transitions are sealed into the chain and marked `actor_role='legacy'`. Nothing is dropped or deleted.

Migration 2 adds `workflow_instances.reviewer_id` (who the evidence was sent to) and `share_token` (public link; unique, null = private).

Upgrade: just start the app or run `flask --app app init-db`. Safe to repeat. Back up your `.db` first as good practice.

`tasks.status` (Pending/Completed) is the owner's **self-reported** progress. The **workflow state** is the verified truth. Approval also sets status to Completed.

## Run locally

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
export REVIEWER_EMAILS=reviewer@example.com        # optional; see below
python app.py                                       # http://127.0.0.1:5000
```

Open `http://127.0.0.1:5000/` and choose **Developer** or **Reviewer** to reach the matching sign-in portal. The selected portal does not grant permissions: login checks the role stored for the account. Open `http://127.0.0.1:5000/`, choose **Developer** or **Reviewer**, and create an account for each (use two browsers or a private window). The role is fixed at sign-up and checked at every sign-in. On a public deployment set `REVIEWER_OPEN_SIGNUP=0` so only e-mails in `REVIEWER_EMAILS` can register as reviewers. `flask --app app set-role someone@example.com reviewer` changes an existing account.

### Demo script (3 minutes)
1. Developer: create task → *Start work* → *Ready to provide proof* → add repo + write-up → choose reviewer (their username/e-mail) → *Submit*.
2. Open "Demo: try an action the automaton should refuse", attempt `APPROVE` → blocked and logged.
3. Reviewer (other browser): Review requests → open → *Begin review* → approve/reject with a reason. A different reviewer sees nothing.
4. Developer: *Create public link* → open it logged-out: evidence, reviewer, integrity check.
5. Tamper (below) and reload: "TAMPERING DETECTED" on both the task page and the public page.

Tamper demo (sqlite3 shell on your local DB):
```sql
DROP TRIGGER state_transitions_no_update;
UPDATE state_transitions SET reason = 'edited' WHERE seq = 1;
```

## Configuration

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Session signing. **Required** when `POW_ENV=production` |
| `POW_ENV` | `production` enables Secure cookies and the SECRET_KEY check |
| `DATABASE_PATH` | SQLite file (default `database/pow_os.db`) |
| `REVIEWER_OPEN_SIGNUP` | `1` (default) anyone may register as a reviewer; `0` restricts reviewer sign-up to `REVIEWER_EMAILS` |
| `REVIEWER_EMAILS` | Comma-separated e-mails allowed to register as reviewers when open sign-up is off |
| `PORT` | Used by `python app.py` |

## Tests

```bash
python -m pytest -q
```
Tests use temporary databases and never touch `database/pow_os.db`. They cover transitions, invalid moves, role/guard enforcement, rollback, concurrency, tamper detection, evidence validation, auth/CSRF, user isolation, migrations, and an HTTP end-to-end flow.

## Deployment (Render)

`render.yaml` runs gunicorn with a generated `SECRET_KEY`. Set `REVIEWER_EMAILS` in the dashboard. **SQLite on Render's free plan is ephemeral** — data resets on deploy/restart. For persistence attach a paid disk and set `DATABASE_PATH` (see comments in `render.yaml`) or move to PostgreSQL (not implemented).

## Known limitations / future work

- **Trust in reviewers is the weak point.** Reviewer accounts are self-registered, so a developer could create a second account and approve themselves. The public page therefore shows the reviewer's name and says what "verified" does and does not mean; a recruiter decides how much to trust that reviewer. Stronger options (invitation-only reviewers, e-mail verification, domain/organisation checks) are future work
- The reviewer for a submission cannot be changed after submitting; there is no reviewer reassignment, reminder or expiry
- No automated checks of GitHub (repo exists, commit authorship); the completeness checklist is not verification
- Hash chain is not externally anchored; no login rate-limiting or password reset; no file uploads
- A public recruiter-facing profile of verified skills is not built; skills/`evidence_skills` tables exist but are unused
- No screenshots are bundled: run the app and capture your own

## Author
Tanu Luthra
