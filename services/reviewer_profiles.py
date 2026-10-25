"""Reviewer profiles and the reviewer directory.

Trust model (read this before changing anything):

* Anyone may register as a reviewer. An account proves nothing about identity,
  expertise or affiliation.
* Everything a reviewer writes in a profile is SELF-REPORTED and is labelled so.
  The platform currently verifies NONE of it (no employer, certificate, skill
  or identity verification exists).
* The only platform-derived facts shown are computed from real rows: the date
  the account was created and how many review decisions the account has
  recorded. No ratings, endorsements or reputation scores are computed.
* Visibility: 'private' (owner only, default), 'unlisted' (public page by
  link, not in the directory), 'listed' (directory + public page). Optional
  detail blocks can be hidden individually with the pub_* flags.
"""
import re

from services.evidence_service import _valid_http_url

LEVELS = {
    "student": "Student / learning",
    "junior": "Junior",
    "mid": "Mid-level",
    "senior": "Senior",
    "lead": "Lead / principal",
}
VISIBILITIES = {
    "private": "Private - only me",
    "unlisted": "Unlisted - public page by link, not in the directory",
    "listed": "Listed - appear in the reviewer directory",
}
PER_PAGE = 10
MAX_TAGS = 15
MAX_TAG_LEN = 30

_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+#/&_-]*$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ProfileError(ValueError):
    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def _clean_text(value, limit, label, problems, minimum=0):
    text = _CONTROL.sub("", (value or "")).strip()
    if len(text) > limit:
        problems.append(f"{label} must be at most {limit} characters.")
    elif len(text) < minimum:
        problems.append(f"{label} must be at least {minimum} characters.")
    return text


def parse_tags(raw, label, problems):
    """'Flask, SQL\nPython' -> [(normalised, label), ...], distinct, validated."""
    tags, seen = [], set()
    for part in re.split(r"[,\n;]+", raw or ""):
        part = _CONTROL.sub("", part).strip()
        if not part:
            continue
        if len(part) > MAX_TAG_LEN or not _TAG_RE.match(part):
            problems.append(f"{label}: '{part[:MAX_TAG_LEN]}' is not valid (letters, numbers, space and . + # / & _ -; "
                            f"up to {MAX_TAG_LEN} characters).")
            continue
        norm = part.lower()
        if norm not in seen:
            seen.add(norm)
            tags.append((norm, part))
    if len(tags) > MAX_TAGS:
        problems.append(f"{label}: at most {MAX_TAGS} entries.")
    return tags[:MAX_TAGS]


def _clean_url(value, label, problems, host_suffix=None):
    url = (value or "").strip()
    if not url:
        return None
    if len(url) > 300 or not _valid_http_url(url):
        problems.append(f"{label} must be a valid http(s) URL (up to 300 characters).")
        return None
    if host_suffix:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        if host != host_suffix and not host.endswith("." + host_suffix):
            problems.append(f"{label} must be on {host_suffix}.")
            return None
    return url


def validate_form(form):
    """Validate the profile form. Returns (clean, skills, expertise). Raises ProfileError."""
    problems = []
    clean = {
        "full_name": _clean_text(form.get("display_name"), 60, "Display name", problems, minimum=2),
        "headline": _clean_text(form.get("headline"), 100, "Headline", problems) or None,
        "bio": _clean_text(form.get("bio"), 600, "Bio", problems) or None,
        "work_history": _clean_text(form.get("work_history"), 2000, "Projects and work history", problems) or None,
        "education": _clean_text(form.get("education"), 600, "Education or certifications", problems) or None,
        "github_url": _clean_url(form.get("github_url"), "GitHub link", problems, "github.com"),
        "portfolio_url": _clean_url(form.get("portfolio_url"), "Portfolio link", problems),
        "linkedin_url": _clean_url(form.get("linkedin_url"), "Professional profile link", problems),
    }
    level = (form.get("experience_level") or "").strip()
    if level and level not in LEVELS:
        problems.append("Choose a valid experience level.")
        level = ""
    clean["experience_level"] = level or None

    visibility = (form.get("profile_visibility") or "private").strip()
    if visibility not in VISIBILITIES:
        problems.append("Choose a valid visibility.")
        visibility = "private"
    clean["profile_visibility"] = visibility

    for flag in ("pub_bio", "pub_experience", "pub_work", "pub_education", "pub_links"):
        clean[flag] = 1 if form.get(flag) == "1" else 0

    skills = parse_tags(form.get("skills"), "Skills", problems)
    expertise = parse_tags(form.get("expertise"), "Areas of expertise", problems)
    if visibility == "listed" and not (skills or expertise):
        problems.append("Add at least one skill or area of expertise to appear in the directory.")
    if problems:
        raise ProfileError(problems)
    return clean, skills, expertise


def save_profile(conn, user_id, clean, skills, expertise):
    """Create or update the caller's own profile. Never touches users.role."""
    row = conn.execute("SELECT id FROM profiles WHERE user_id = ? ORDER BY id LIMIT 1", (user_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO profiles (user_id, full_name) VALUES (?, ?)", (user_id, clean["full_name"]))
    columns = list(clean)
    conn.execute(
        "UPDATE profiles SET " + ", ".join(f"{c} = ?" for c in columns) + ", updated_at = CURRENT_TIMESTAMP "
        "WHERE id = (SELECT id FROM profiles WHERE user_id = ? ORDER BY id LIMIT 1)",
        tuple(clean[c] for c in columns) + (user_id,),
    )
    conn.execute("DELETE FROM reviewer_tags WHERE user_id = ?", (user_id,))
    for kind, tags in (("skill", skills), ("expertise", expertise)):
        for norm, label in tags:
            conn.execute("INSERT OR IGNORE INTO reviewer_tags (user_id, kind, tag, label) VALUES (?, ?, ?, ?)",
                         (user_id, kind, norm, label))


def _tags_for(conn, user_ids):
    out = {uid: {"skill": [], "expertise": []} for uid in user_ids}
    if not user_ids:
        return out
    marks = ",".join("?" * len(user_ids))
    for r in conn.execute(f"SELECT user_id, kind, label FROM reviewer_tags WHERE user_id IN ({marks}) "
                          "ORDER BY label COLLATE NOCASE", tuple(user_ids)):
        out[r["user_id"]][r["kind"]].append(r["label"])
    return out


def review_stats(conn, user_id):
    """Facts derived from real rows only. Counts decisions; never exposes what was reviewed."""
    row = conn.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT workflow_id) AS w FROM review_decisions "
                       "WHERE reviewer_id = ?", (user_id,)).fetchone()
    return {"decisions": row["n"], "submissions": row["w"]}


def completeness(profile, skills, expertise):
    """(percent, missing labels). Counts which fields are filled, not their quality."""
    checks = [
        ("display name", bool(profile.get("full_name") and profile["full_name"].strip())),
        ("headline", bool(profile.get("headline"))),
        ("bio", bool(profile.get("bio"))),
        ("skills", bool(skills)),
        ("areas of expertise", bool(expertise)),
        ("experience level", bool(profile.get("experience_level"))),
        ("projects and work history", bool(profile.get("work_history"))),
        ("links", bool(profile.get("github_url") or profile.get("portfolio_url") or profile.get("linkedin_url"))),
        ("education or certifications", bool(profile.get("education"))),
    ]
    filled = sum(1 for _, ok in checks if ok)
    return round(100 * filled / len(checks)), [name for name, ok in checks if not ok]


def _shape(row, tags, owner_view=False):
    """Turn a joined users/profiles row into the dict templates use, applying the pub_* flags."""
    show = lambda flag: owner_view or bool(row[flag])  # noqa: E731
    skills, expertise = tags["skill"], tags["expertise"]
    percent, missing = completeness(dict(row), skills, expertise)
    links = {}
    if show("pub_links"):
        for key in ("github_url", "portfolio_url", "linkedin_url"):
            if row[key]:
                links[key] = row[key]
    level = row["experience_level"] if show("pub_experience") and row["experience_level"] in LEVELS else None
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "display_name": row["full_name"] or row["username"],
        "headline": row["headline"],
        "bio": row["bio"] if show("pub_bio") else None,
        "level": level,
        "level_label": LEVELS.get(level),
        "work_history": row["work_history"] if show("pub_work") else None,
        "education": row["education"] if show("pub_education") else None,
        "skills": skills,
        "expertise": expertise,
        "links": links,
        "visibility": row["profile_visibility"],
        "completeness": percent,
        "missing": missing,
        "member_since": (row["created_at"] or "")[:10],
    }


_BASE = """
    SELECT u.id AS user_id, u.username, u.created_at, p.full_name, p.headline, p.bio, p.experience_level,
           p.work_history, p.education, p.github_url, p.portfolio_url, p.linkedin_url, p.profile_visibility,
           p.pub_bio, p.pub_experience, p.pub_work, p.pub_education, p.pub_links
    FROM users u JOIN profiles p ON p.user_id = u.id
"""


def get_own(conn, user_id):
    row = conn.execute(_BASE + " WHERE u.id = ? AND u.role = 'reviewer' ORDER BY p.id LIMIT 1", (user_id,)).fetchone()
    if row is None:
        return None
    return _shape(row, _tags_for(conn, [user_id])[user_id], owner_view=True) | {"row": dict(row)}


def get_public(conn, username):
    """Public view of a reviewer, or None unless the profile is listed/unlisted."""
    row = conn.execute(
        _BASE + " WHERE u.username = ? AND u.role = 'reviewer' AND p.profile_visibility IN ('listed', 'unlisted') "
                "ORDER BY p.id LIMIT 1", (username,)).fetchone()
    if row is None:
        return None
    data = _shape(row, _tags_for(conn, [row["user_id"]])[row["user_id"]])
    data["stats"] = review_stats(conn, row["user_id"])
    return data


def _like(value):
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").lower() + "%"


def directory(conn, q="", skill="", expertise="", level="", page=1, per_page=PER_PAGE):
    """Server-side search over reviewers whose profile is 'listed'. Returns (items, total, page, pages)."""
    where = ["u.role = 'reviewer'", "p.profile_visibility = 'listed'",
             "EXISTS (SELECT 1 FROM reviewer_tags t0 WHERE t0.user_id = u.id)"]
    params = []
    q, skill, expertise = (q or "").strip()[:60], (skill or "").strip()[:30], (expertise or "").strip()[:30]
    if q:
        where.append("(lower(p.full_name) LIKE ? ESCAPE '\\' OR lower(u.username) LIKE ? ESCAPE '\\')")
        params += [_like(q), _like(q)]
    for kind, value in (("skill", skill), ("expertise", expertise)):
        if value:
            where.append("EXISTS (SELECT 1 FROM reviewer_tags t WHERE t.user_id = u.id AND t.kind = '%s' "
                         "AND t.tag LIKE ? ESCAPE '\\')" % kind)
            params.append(_like(value))
    if level in LEVELS:
        where.append("p.experience_level = ? AND p.pub_experience = 1")
        params.append(level)
    clause = " WHERE " + " AND ".join(where)

    total = conn.execute("SELECT COUNT(*) FROM users u JOIN profiles p ON p.user_id = u.id" + clause,
                         params).fetchone()[0]
    pages = max(1, -(-total // per_page))
    page = min(max(1, int(page or 1)), pages)
    rows = conn.execute(_BASE + clause + " ORDER BY lower(p.full_name), u.id LIMIT ? OFFSET ?",
                        params + [per_page, (page - 1) * per_page]).fetchall()
    tags = _tags_for(conn, [r["user_id"] for r in rows])
    items = []
    for r in rows:
        item = _shape(r, tags[r["user_id"]])
        item["stats"] = review_stats(conn, r["user_id"])
        items.append(item)
    return items, total, page, pages


def selectable_reviewers(conn, ids, owner_id):
    """Validate picker ids against the database. Returns [(id, username, display_name)] or raises ProfileError.

    Authorisation lives HERE (server side): every id must be an existing
    reviewer account with a 'listed' profile, distinct, and not the owner.
    """
    problems, found, seen = [], [], set()
    for raw in ids:
        try:
            uid = int(raw)
        except (TypeError, ValueError):
            problems.append("One of the selected reviewers is not valid.")
            continue
        if uid in seen:
            problems.append("A reviewer was selected twice; reviewers must be distinct.")
            continue
        seen.add(uid)
        if uid == owner_id:
            problems.append("You cannot choose yourself as a reviewer.")
            continue
        row = conn.execute(
            "SELECT u.id, u.username, p.full_name FROM users u JOIN profiles p ON p.user_id = u.id "
            "WHERE u.id = ? AND u.role = 'reviewer' AND p.profile_visibility = 'listed' "
            "AND EXISTS (SELECT 1 FROM reviewer_tags t WHERE t.user_id = u.id) LIMIT 1", (uid,)).fetchone()
        if row is None:
            problems.append("One of the selected reviewers is not available in the directory.")
        else:
            found.append((row["id"], row["username"], row["full_name"] or row["username"]))
    if problems:
        raise ProfileError(sorted(set(problems)))
    return found
