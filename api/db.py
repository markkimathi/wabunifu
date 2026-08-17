"""
SQLite storage for employer-submitted job postings (the "post a job" journey),
designer accounts/profiles, and site analytics (admin dashboard). Raw sqlite3,
no ORM, matching scraper/store.py's style.

Every submission starts as 'pending' and is invisible to the public feed until
an admin approves it via /api/admin/submissions/{id}/approve. This is the only
gate against spam/fake listings, since submission itself is unauthenticated.

Designer profiles follow the same review-queue model (designers.status:
pending/approved/rejected), gated on top of real per-account auth: a
designer's email/password/session data is private, everything else on an
approved profile (name, bio, discipline, location, photo, links) is public.
Editing an approved profile drops it back to 'pending' for re-review, same as
job listings. Session tokens and one-time email tokens (verify/reset) expire
and are swept on startup, same "good enough given how often this app
redeploys" approach used for analytics retention — see cleanup_stale_analytics
and cleanup_stale_designer_tokens.

Analytics tables (pageviews, search_events, apply_clicks) store no personal
data — no IP addresses, no cookies, no identifiers that could tie two visits
to the same person. Just: what page/search/apply, roughly when, device class
(mobile/tablet/desktop) and country (resolved from the request IP via a local
GeoIP lookup, see geoip.py, then the IP itself is discarded). Rows older than
ANALYTICS_RETENTION_DAYS are deleted on startup; see cleanup_stale_analytics.
"""
from __future__ import annotations
import json
import os
import re
import secrets
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone

ANALYTICS_RETENTION_DAYS = 90
SESSION_DAYS = 30
VERIFY_CODE_MINUTES = 15
RESET_TOKEN_HOURS = 1
INVITE_EXPIRY_DAYS = 5

# Override with KAZI_DB_PATH in production to point at a mounted volume:
# e.g. a host that wipes the container filesystem on every deploy needs this
# database living outside the app's own source directory, or a redeploy
# silently erases every employer submission.
DB = Path(os.environ.get("KAZI_DB_PATH", str(Path(__file__).parent / "kazi_submissions.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  company TEXT NOT NULL,
  url TEXT NOT NULL,
  contact_email TEXT NOT NULL,
  location TEXT DEFAULT '',
  work_type TEXT NOT NULL DEFAULT 'On-site',
  discipline TEXT NOT NULL,
  level TEXT NOT NULL DEFAULT 'Mid',
  eligibility TEXT NOT NULL,
  salary TEXT,
  description TEXT NOT NULL DEFAULT '',
  agreed_to_terms INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pageviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL,
  device TEXT NOT NULL,
  country TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pageviews_created ON pageviews(created_at);

CREATE TABLE IF NOT EXISTS search_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query TEXT NOT NULL,
  device TEXT NOT NULL,
  country TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_created ON search_events(created_at);

CREATE TABLE IF NOT EXISTS apply_clicks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  job_title TEXT NOT NULL,
  company TEXT NOT NULL,
  device TEXT NOT NULL,
  country TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apply_created ON apply_clicks(created_at);

CREATE TABLE IF NOT EXISTS designers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  bio TEXT NOT NULL DEFAULT '',
  discipline TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  photo_path TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  contact_email TEXT NOT NULL DEFAULT '',
  handle TEXT NOT NULL DEFAULT '',
  headline TEXT NOT NULL DEFAULT '',
  years_experience TEXT NOT NULL DEFAULT '',
  availability_status TEXT NOT NULL DEFAULT '',
  skills TEXT NOT NULL DEFAULT '[]',
  resume_path TEXT NOT NULL DEFAULT '',
  resume_filename TEXT NOT NULL DEFAULT '',
  resume_uploaded_at TEXT NOT NULL DEFAULT '',
  resume_public INTEGER NOT NULL DEFAULT 0,
  onboarding_completed INTEGER NOT NULL DEFAULT 0,
  email_verified INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_designers_status ON designers(status);

CREATE TABLE IF NOT EXISTS designer_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  designer_id INTEGER NOT NULL,
  label TEXT NOT NULL,
  url TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_designer_links_designer ON designer_links(designer_id);

CREATE TABLE IF NOT EXISTS designer_projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  designer_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  image_path TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_designer_projects_designer ON designer_projects(designer_id);

CREATE TABLE IF NOT EXISTS designer_sessions (
  token TEXT PRIMARY KEY,
  designer_id INTEGER NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS designer_email_tokens (
  token TEXT PRIMARY KEY,
  designer_id INTEGER NOT NULL,
  purpose TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  website TEXT NOT NULL DEFAULT '',
  blurb TEXT NOT NULL DEFAULT '',
  logo_path TEXT NOT NULL DEFAULT '',
  eligibility TEXT NOT NULL DEFAULT '',
  eligibility_note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);

CREATE TABLE IF NOT EXISTS employers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  full_name TEXT NOT NULL,
  role_title TEXT NOT NULL DEFAULT '',
  team_role TEXT NOT NULL DEFAULT 'owner',
  is_pending_approval INTEGER NOT NULL DEFAULT 0,
  invited_by_employer_id INTEGER,
  email_verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_employers_company ON employers(company_id);

CREATE TABLE IF NOT EXISTS employer_sessions (
  token TEXT PRIMARY KEY,
  employer_id INTEGER NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employer_email_tokens (
  token TEXT PRIMARY KEY,
  employer_id INTEGER NOT NULL,
  purpose TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_invites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token TEXT NOT NULL UNIQUE,
  company_id INTEGER NOT NULL,
  invited_email TEXT NOT NULL,
  invited_by_employer_id INTEGER NOT NULL,
  proposed_team_role TEXT NOT NULL DEFAULT 'can_post',
  needs_approval INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'pending',
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  responded_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_team_invites_company ON team_invites(company_id);
"""

# Columns added after the table first shipped. A fresh database gets them
# via SCHEMA above; an existing one (e.g. the persistent volume already
# running in production) needs them backfilled here instead, or every
# insert/select against the new columns breaks.
_MIGRATIONS = [
    "ALTER TABLE submissions ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE submissions ADD COLUMN agreed_to_terms INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE designers ADD COLUMN phone TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN contact_email TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN handle TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN headline TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN years_experience TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN availability_status TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN skills TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE designers ADD COLUMN onboarding_completed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE designers ADD COLUMN resume_path TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN resume_filename TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN resume_uploaded_at TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN resume_public INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE submissions ADD COLUMN company_id INTEGER",
    "ALTER TABLE submissions ADD COLUMN employer_id INTEGER",
]


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    for stmt in _MIGRATIONS:
        try:
            c.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    c.commit()
    return c


def init_db() -> None:
    c = _conn()
    c.close()
    cleanup_stale_analytics()
    cleanup_stale_designer_tokens()
    cleanup_stale_employer_tokens()
    backfill_designer_handles()
    backfill_onboarding_completed()


def insert_submission(data: dict, company_id: int | None = None, employer_id: int | None = None) -> int:
    """company_id/employer_id are None for the anonymous /post.html flow
    (unchanged since before employer accounts existed) and set when an
    authenticated employer posts from their own dashboard — either way the
    row lands in the exact same admin review queue, no parallel path."""
    c = _conn()
    row = {**data, "company_id": company_id, "employer_id": employer_id,
           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    cur = c.execute("""
        INSERT INTO submissions
          (title, company, url, contact_email, location, work_type,
           discipline, level, eligibility, salary, description, agreed_to_terms,
           company_id, employer_id, status, created_at)
        VALUES
          (:title, :company, :url, :contact_email, :location, :work_type,
           :discipline, :level, :eligibility, :salary, :description, :agreed_to_terms,
           :company_id, :employer_id, 'pending', :created_at)
    """, row)
    c.commit()
    sub_id = cur.lastrowid
    c.close()
    return sub_id


def update_submission(sub_id: int, **fields) -> bool:
    """Editing any content field on a listing sends it back to 'pending' for
    re-review, same rule as update_designer_profile. Closing a listing is a
    pure status change with nothing to re-review, so it goes through
    set_status() directly instead of this function."""
    if not fields:
        return False
    c = _conn()
    cols = ", ".join(f"{k} = ?" for k in fields)
    cur = c.execute(
        f"UPDATE submissions SET {cols}, status = 'pending' WHERE id = ?",
        (*fields.values(), sub_id),
    )
    updated = cur.rowcount > 0
    c.commit()
    c.close()
    return updated


def list_submissions_for_company(company_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM submissions WHERE company_id = ? ORDER BY created_at DESC", (company_id,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def list_submissions(status: str | None = None) -> list[dict]:
    c = _conn()
    if status:
        rows = c.execute(
            "SELECT * FROM submissions WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM submissions ORDER BY created_at DESC").fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_submission(sub_id: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def set_status(sub_id: int, status: str) -> None:
    c = _conn()
    c.execute("UPDATE submissions SET status = ? WHERE id = ?", (status, sub_id))
    c.commit()
    c.close()


# ---------------------------------------------------------------------------
# Designer accounts and profiles. Same review-queue model as submissions
# above (pending/approved/rejected), sitting on top of real per-account auth.
# ---------------------------------------------------------------------------

def _slugify_handle(name: str) -> str:
    """Turn a display name into a valid handle base: lowercase letters/digits
    only, starting with a letter, at least 3 characters. Matches main.py's
    HANDLE_RE (^[a-z][a-z0-9_]{2,29}$) by construction — this is the only
    place that generates a handle without going through that validator."""
    base = re.sub(r"[^a-z0-9]", "", name.lower())
    if not base or not base[0].isalpha():
        base = "designer" + base
    base = base[:30]
    while len(base) < 3:
        base += "0"
    return base


def _unique_handle(c: sqlite3.Connection, display_name: str, exclude_id: int | None = None) -> str:
    base = _slugify_handle(display_name)
    candidate = base
    n = 2
    while True:
        row = c.execute(
            "SELECT id FROM designers WHERE lower(handle) = ?", (candidate,)
        ).fetchone()
        if not row or row["id"] == exclude_id:
            return candidate
        candidate = f"{base}{n}"[:30]
        n += 1


def backfill_designer_handles() -> None:
    """One-time-per-startup pass: any designer row from before handles
    existed (or created some other way with handle left blank) gets one
    auto-picked from their display name, so every profile always has a URL."""
    c = _conn()
    rows = c.execute(
        "SELECT id, display_name FROM designers WHERE handle = '' OR handle IS NULL"
    ).fetchall()
    for row in rows:
        handle = _unique_handle(c, row["display_name"], exclude_id=row["id"])
        c.execute("UPDATE designers SET handle = ? WHERE id = ?", (handle, row["id"]))
    if rows:
        c.commit()
    c.close()


def backfill_onboarding_completed() -> None:
    """One-time-per-startup pass: onboarding_completed defaults to 0 for
    every row (new column), which would wrongly force every designer who
    signed up before this flag existed back into the onboarding wizard on
    their next login. Anyone who already has real profile data clearly
    finished setting up their profile already, even though no explicit
    "completed" event was ever recorded for them — flip them to 1 using
    the same signal account.html's isProfileEmpty() uses, inverted.
    Idempotent: only ever flips 0 -> 1, never touches rows already at 1."""
    c = _conn()
    rows = c.execute("""
        SELECT id FROM designers
        WHERE onboarding_completed = 0
          AND (bio != '' OR location != '' OR photo_path != '' OR headline != ''
               OR years_experience != '' OR availability_status != ''
               OR (discipline != '' AND discipline != '[]')
               OR (skills != '' AND skills != '[]'))
    """).fetchall()
    for row in rows:
        c.execute("UPDATE designers SET onboarding_completed = 1 WHERE id = ?", (row["id"],))
    if rows:
        c.commit()
    c.close()


def create_designer(email: str, password_hash: str, display_name: str) -> int:
    c = _conn()
    handle = _unique_handle(c, display_name)
    cur = c.execute(
        "INSERT INTO designers (email, password_hash, display_name, handle, status, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?)",
        (email, password_hash, display_name, handle, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    designer_id = cur.lastrowid
    c.close()
    return designer_id


def get_designer(designer_id: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM designers WHERE id = ?", (designer_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def get_designer_by_email(email: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM designers WHERE email = ?", (email,)).fetchone()
    c.close()
    return dict(row) if row else None


def get_designer_by_handle(handle: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM designers WHERE lower(handle) = ?", (handle.lower(),)).fetchone()
    c.close()
    return dict(row) if row else None


def parse_multi_field(value: str | None) -> list[str]:
    """discipline/skills are stored as a JSON array of strings. Falls back to
    treating the raw value as a single legacy item for rows written before
    either field held more than one value (a plain discipline string)."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return [value]
    if isinstance(parsed, list):
        return [str(x) for x in parsed]
    return [value]


class HandleTaken(Exception):
    """Raised by update_designer_profile when another designer already
    holds the requested handle — kept as a distinct exception so main.py
    can turn it into a friendly 400 instead of a generic 500."""


def update_designer_profile(
    designer_id: int, *, display_name: str, bio: str, discipline: list[str], location: str,
    phone: str = "", contact_email: str = "",
    headline: str = "", years_experience: str = "", availability_status: str = "",
    skills: list[str] | None = None,
) -> None:
    c = _conn()
    # Editing an approved profile sends it back for re-review, same as a job
    # listing would if it were editable — never let a live public profile
    # change without another pass through the admin queue.
    row = c.execute("SELECT status FROM designers WHERE id = ?", (designer_id,)).fetchone()
    new_status = "pending" if row and row["status"] == "approved" else (row["status"] if row else "pending")
    c.execute(
        "UPDATE designers SET display_name = ?, bio = ?, discipline = ?, location = ?, "
        "phone = ?, contact_email = ?, headline = ?, years_experience = ?, "
        "availability_status = ?, skills = ?, status = ? WHERE id = ?",
        (display_name, bio, json.dumps(discipline), location, phone, contact_email,
         headline, years_experience, availability_status, json.dumps(skills or []),
         new_status, designer_id),
    )
    c.commit()
    c.close()


def update_designer_handle(designer_id: int, handle: str) -> None:
    """Handle edits are deliberately kept separate from update_designer_profile
    and DON'T reset an approved profile back to pending: a handle is a URL
    slug, not moderated content, so changing it shouldn't pull a live profile
    out of the directory for re-review the way editing bio/photo/etc. does."""
    c = _conn()
    clash = c.execute(
        "SELECT id FROM designers WHERE lower(handle) = ? AND id != ?",
        (handle.lower(), designer_id),
    ).fetchone()
    if clash:
        c.close()
        raise HandleTaken(handle)
    c.execute("UPDATE designers SET handle = ? WHERE id = ?", (handle, designer_id))
    c.commit()
    c.close()


def set_designer_photo(designer_id: int, photo_path: str) -> None:
    c = _conn()
    # Same re-review rule as update_designer_profile — a new photo on an
    # already-public profile shouldn't go live without another look.
    row = c.execute("SELECT status FROM designers WHERE id = ?", (designer_id,)).fetchone()
    new_status = "pending" if row and row["status"] == "approved" else (row["status"] if row else "pending")
    c.execute(
        "UPDATE designers SET photo_path = ?, status = ? WHERE id = ?",
        (photo_path, new_status, designer_id),
    )
    c.commit()
    c.close()


def set_designer_resume(designer_id: int, resume_path: str, resume_filename: str, uploaded_at: str) -> None:
    # Unlike photo/bio/discipline, a resume isn't part of what admins review
    # for profile legitimacy (and defaults to private) — uploading or
    # replacing one never resets an approved profile back to pending.
    c = _conn()
    c.execute(
        "UPDATE designers SET resume_path = ?, resume_filename = ?, resume_uploaded_at = ? WHERE id = ?",
        (resume_path, resume_filename, uploaded_at, designer_id),
    )
    c.commit()
    c.close()


def clear_designer_resume(designer_id: int) -> None:
    c = _conn()
    c.execute(
        "UPDATE designers SET resume_path = '', resume_filename = '', resume_uploaded_at = '', resume_public = 0 WHERE id = ?",
        (designer_id,),
    )
    c.commit()
    c.close()


def set_resume_visibility(designer_id: int, public: bool) -> None:
    c = _conn()
    c.execute("UPDATE designers SET resume_public = ? WHERE id = ?", (1 if public else 0, designer_id))
    c.commit()
    c.close()


def set_designer_email_verified(designer_id: int) -> None:
    c = _conn()
    c.execute("UPDATE designers SET email_verified = 1 WHERE id = ?", (designer_id,))
    c.commit()
    c.close()


def set_designer_password(designer_id: int, password_hash: str) -> None:
    c = _conn()
    c.execute("UPDATE designers SET password_hash = ? WHERE id = ?", (password_hash, designer_id))
    c.commit()
    c.close()


def mark_onboarding_completed(designer_id: int) -> None:
    """Called once, from the last step of the onboarding wizard — flips the
    routing flag that keeps a designer out of the wizard on future logins.
    Editing an existing profile afterward (Edit Profile page) never touches
    this; it's a one-way flag set exactly once per account."""
    c = _conn()
    c.execute("UPDATE designers SET onboarding_completed = 1 WHERE id = ?", (designer_id,))
    c.commit()
    c.close()


def set_designer_status(designer_id: int, status: str) -> None:
    c = _conn()
    c.execute("UPDATE designers SET status = ? WHERE id = ?", (status, designer_id))
    c.commit()
    c.close()


def list_designers(status: str | None = None) -> list[dict]:
    c = _conn()
    if status:
        rows = c.execute(
            "SELECT * FROM designers WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM designers ORDER BY created_at DESC").fetchall()
    c.close()
    return [dict(r) for r in rows]


def list_approved_designers(discipline: str | None = None) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM designers WHERE status = 'approved' ORDER BY created_at DESC"
    ).fetchall()
    c.close()
    result = [dict(r) for r in rows]
    # discipline is stored as a JSON array (a designer can have up to 5), so
    # this can no longer be a SQL equality filter — match if the requested
    # value is any one of the designer's disciplines.
    if discipline:
        result = [r for r in result if discipline in parse_multi_field(r.get("discipline"))]
    return result


def delete_designer(designer_id: int) -> None:
    c = _conn()
    c.execute("DELETE FROM designer_links WHERE designer_id = ?", (designer_id,))
    c.execute("DELETE FROM designer_projects WHERE designer_id = ?", (designer_id,))
    c.execute("DELETE FROM designer_sessions WHERE designer_id = ?", (designer_id,))
    c.execute("DELETE FROM designer_email_tokens WHERE designer_id = ?", (designer_id,))
    c.execute("DELETE FROM designers WHERE id = ?", (designer_id,))
    c.commit()
    c.close()


def replace_designer_links(designer_id: int, links: list[dict]) -> None:
    """Delete-then-insert the whole list — simplest correct way to save an
    edit form that lets someone add/remove/reorder freely."""
    c = _conn()
    c.execute("DELETE FROM designer_links WHERE designer_id = ?", (designer_id,))
    for i, link in enumerate(links):
        c.execute(
            "INSERT INTO designer_links (designer_id, label, url, sort_order) VALUES (?, ?, ?, ?)",
            (designer_id, link["label"], link["url"], i),
        )
    c.commit()
    c.close()


def list_designer_links(designer_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT label, url FROM designer_links WHERE designer_id = ? ORDER BY sort_order", (designer_id,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def list_designer_projects(designer_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT id, title, description, url, category, image_path FROM designer_projects "
        "WHERE designer_id = ? ORDER BY sort_order",
        (designer_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def count_designer_projects(designer_id: int) -> int:
    c = _conn()
    n = c.execute(
        "SELECT COUNT(*) FROM designer_projects WHERE designer_id = ?", (designer_id,)
    ).fetchone()[0]
    c.close()
    return n


def create_designer_project(designer_id: int, title: str, description: str, url: str, category: str) -> int:
    c = _conn()
    n = c.execute(
        "SELECT COUNT(*) FROM designer_projects WHERE designer_id = ?", (designer_id,)
    ).fetchone()[0]
    cur = c.execute(
        "INSERT INTO designer_projects (designer_id, title, description, url, category, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (designer_id, title, description, url, category, n),
    )
    project_id = cur.lastrowid
    c.commit()
    c.close()
    return project_id


def update_designer_project(designer_id: int, project_id: int, title: str, description: str, url: str, category: str) -> bool:
    c = _conn()
    cur = c.execute(
        "UPDATE designer_projects SET title = ?, description = ?, url = ?, category = ? "
        "WHERE id = ? AND designer_id = ?",
        (title, description, url, category, project_id, designer_id),
    )
    updated = cur.rowcount > 0
    c.commit()
    c.close()
    return updated


def set_project_image(designer_id: int, project_id: int, image_path: str) -> bool:
    c = _conn()
    cur = c.execute(
        "UPDATE designer_projects SET image_path = ? WHERE id = ? AND designer_id = ?",
        (image_path, project_id, designer_id),
    )
    updated = cur.rowcount > 0
    c.commit()
    c.close()
    return updated


def delete_designer_project(designer_id: int, project_id: int) -> bool:
    c = _conn()
    cur = c.execute(
        "DELETE FROM designer_projects WHERE id = ? AND designer_id = ?", (project_id, designer_id)
    )
    deleted = cur.rowcount > 0
    c.commit()
    c.close()
    return deleted


def reorder_designer_projects(designer_id: int, ordered_ids: list[int]) -> None:
    """ordered_ids is the caller's desired final order — sort_order is
    reassigned 0..n by position, and any row not in the list (shouldn't
    happen from a trusted caller, but ownership is still enforced per
    row) is simply left untouched rather than erroring."""
    c = _conn()
    for i, project_id in enumerate(ordered_ids):
        c.execute(
            "UPDATE designer_projects SET sort_order = ? WHERE id = ? AND designer_id = ?",
            (i, project_id, designer_id),
        )
    c.commit()
    c.close()


def create_session(designer_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    c = _conn()
    c.execute(
        "INSERT INTO designer_sessions (token, designer_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token, designer_id, (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"),
         now.isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()
    return token


def get_session(token: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM designer_sessions WHERE token = ?", (token,)).fetchone()
    c.close()
    if not row:
        return None
    if row["expires_at"] < datetime.now(timezone.utc).isoformat(timespec="seconds"):
        return None
    return dict(row)


def delete_session(token: str) -> None:
    c = _conn()
    c.execute("DELETE FROM designer_sessions WHERE token = ?", (token,))
    c.commit()
    c.close()


def delete_sessions_for_designer(designer_id: int) -> None:
    """Invalidate every active session for this designer — called on
    password reset, in case the reset was triggered by a stolen session."""
    c = _conn()
    c.execute("DELETE FROM designer_sessions WHERE designer_id = ?", (designer_id,))
    c.commit()
    c.close()


def create_email_token(designer_id: int, purpose: str) -> str:
    now = datetime.now(timezone.utc)
    if purpose == "verify":
        # A short numeric code (typed in by hand, not clicked) needs a much
        # smaller guess-space to stay valid for than a 256-bit link token —
        # 15 minutes, plus the endpoint that checks it is auth-scoped (see
        # api/main.py's designer_verify_email), not a bare anonymous lookup.
        token = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = now + timedelta(minutes=VERIFY_CODE_MINUTES)
    else:
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(hours=RESET_TOKEN_HOURS)
    c = _conn()
    c.execute(
        "INSERT INTO designer_email_tokens (token, designer_id, purpose, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (token, designer_id, purpose, expires_at.isoformat(timespec="seconds"),
         now.isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()
    return token


def consume_email_token(token: str, purpose: str) -> int | None:
    """Returns the designer_id if the token is valid, unused, unexpired, and
    matches the expected purpose — and marks it used so it can't be replayed."""
    c = _conn()
    row = c.execute(
        "SELECT * FROM designer_email_tokens WHERE token = ? AND purpose = ?", (token, purpose)
    ).fetchone()
    if not row or row["used"] or row["expires_at"] < datetime.now(timezone.utc).isoformat(timespec="seconds"):
        c.close()
        return None
    c.execute("UPDATE designer_email_tokens SET used = 1 WHERE token = ?", (token,))
    c.commit()
    c.close()
    return row["designer_id"]


def cleanup_stale_designer_tokens() -> None:
    """Sweep expired sessions and email tokens on startup — same
    good-enough-given-how-often-this-redeploys approach as analytics."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    c = _conn()
    c.execute("DELETE FROM designer_sessions WHERE expires_at < ?", (now,))
    c.execute("DELETE FROM designer_email_tokens WHERE expires_at < ?", (now,))
    c.commit()
    c.close()


# ---------------------------------------------------------------------------
# Employer accounts and companies. Same review-queue model as designers
# (companies.status: pending/approved/rejected) sitting on top of real
# per-account auth (mirrors designer_sessions/designer_email_tokens exactly).
# One company can hold several employer accounts (a "team"); the first
# employer created at signup is always team_role='owner'. Everyone else
# arrives through a team_invites row (see create_team_invite et al below).
# ---------------------------------------------------------------------------

def _slugify_company(name: str) -> str:
    """Same construction as _slugify_handle above, applied to a company name
    instead of a designer's display name."""
    base = re.sub(r"[^a-z0-9]", "", name.lower())
    if not base or not base[0].isalpha():
        base = "company" + base
    base = base[:30]
    while len(base) < 3:
        base += "0"
    return base


def _unique_company_slug(c: sqlite3.Connection, name: str, exclude_id: int | None = None) -> str:
    base = _slugify_company(name)
    candidate = base
    n = 2
    while True:
        row = c.execute(
            "SELECT id FROM companies WHERE lower(slug) = ?", (candidate,)
        ).fetchone()
        if not row or row["id"] == exclude_id:
            return candidate
        candidate = f"{base}{n}"[:30]
        n += 1


def create_company(name: str, website: str = "", blurb: str = "",
                    eligibility: str = "", eligibility_note: str = "") -> int:
    c = _conn()
    slug = _unique_company_slug(c, name)
    cur = c.execute(
        "INSERT INTO companies (name, slug, website, blurb, eligibility, eligibility_note, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (name, slug, website, blurb, eligibility, eligibility_note,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    company_id = cur.lastrowid
    c.close()
    return company_id


def get_company(company_id: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def get_company_by_slug(slug: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM companies WHERE lower(slug) = ?", (slug.lower(),)).fetchone()
    c.close()
    return dict(row) if row else None


def get_company_by_domain(domain: str) -> dict | None:
    """Matches a company whose website host equals `domain` exactly — used at
    employer signup to steer someone toward requesting a team invite instead
    of silently creating a second company for the same organisation. A bare
    substring match would false-positive (e.g. "aflutterwave.com"), so this
    strips scheme/www/path the same way api/main.py's email-domain check does
    and compares the whole host."""
    c = _conn()
    rows = c.execute("SELECT * FROM companies WHERE status != 'rejected'").fetchall()
    c.close()
    for row in rows:
        host = re.sub(r"^https?://", "", row["website"] or "", flags=re.I)
        host = re.sub(r"^www\.", "", host, flags=re.I).split("/")[0].lower()
        if host and host == domain.lower():
            return dict(row)
    return None


def update_company(company_id: int, name: str, website: str, blurb: str,
                    eligibility: str, eligibility_note: str) -> None:
    # Same re-review-on-edit rule as update_designer_profile/update_submission
    # — an approved company page doesn't change without another look.
    c = _conn()
    row = c.execute("SELECT status FROM companies WHERE id = ?", (company_id,)).fetchone()
    new_status = "pending" if row and row["status"] == "approved" else (row["status"] if row else "pending")
    c.execute(
        "UPDATE companies SET name = ?, website = ?, blurb = ?, eligibility = ?, "
        "eligibility_note = ?, status = ? WHERE id = ?",
        (name, website, blurb, eligibility, eligibility_note, new_status, company_id),
    )
    c.commit()
    c.close()


def set_company_logo(company_id: int, logo_path: str) -> None:
    c = _conn()
    c.execute("UPDATE companies SET logo_path = ? WHERE id = ?", (logo_path, company_id))
    c.commit()
    c.close()


def list_companies(status: str | None = None) -> list[dict]:
    c = _conn()
    if status:
        rows = c.execute(
            "SELECT * FROM companies WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM companies ORDER BY created_at DESC").fetchall()
    c.close()
    return [dict(r) for r in rows]


def set_company_status(company_id: int, status: str) -> None:
    c = _conn()
    c.execute("UPDATE companies SET status = ? WHERE id = ?", (status, company_id))
    c.commit()
    c.close()


def create_employer(company_id: int, email: str, password_hash: str, full_name: str,
                     role_title: str = "", team_role: str = "owner",
                     is_pending_approval: bool = False,
                     invited_by_employer_id: int | None = None) -> int:
    c = _conn()
    cur = c.execute(
        "INSERT INTO employers (company_id, email, password_hash, full_name, role_title, "
        "team_role, is_pending_approval, invited_by_employer_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, email, password_hash, full_name, role_title, team_role,
         1 if is_pending_approval else 0, invited_by_employer_id,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    employer_id = cur.lastrowid
    c.close()
    return employer_id


def get_employer(employer_id: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM employers WHERE id = ?", (employer_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def get_employer_by_email(email: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM employers WHERE email = ?", (email,)).fetchone()
    c.close()
    return dict(row) if row else None


def list_employers_for_company(company_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM employers WHERE company_id = ? ORDER BY created_at", (company_id,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def count_company_owners(company_id: int, exclude_id: int | None = None) -> int:
    """Used to block the last owner of a company from deleting their own
    account (api/main.py returns a 400 instead) — there is no ownership
    transfer endpoint in this version, so this is a deliberate wall."""
    c = _conn()
    row = c.execute(
        "SELECT COUNT(*) FROM employers WHERE company_id = ? AND team_role = 'owner' AND id != ?",
        (company_id, exclude_id or -1),
    ).fetchone()
    c.close()
    return row[0]


def set_employer_email_verified(employer_id: int) -> None:
    c = _conn()
    c.execute("UPDATE employers SET email_verified = 1 WHERE id = ?", (employer_id,))
    c.commit()
    c.close()


def set_employer_password(employer_id: int, password_hash: str) -> None:
    c = _conn()
    c.execute("UPDATE employers SET password_hash = ? WHERE id = ?", (password_hash, employer_id))
    c.commit()
    c.close()


def update_employer_profile(employer_id: int, full_name: str, role_title: str) -> None:
    c = _conn()
    c.execute(
        "UPDATE employers SET full_name = ?, role_title = ? WHERE id = ?",
        (full_name, role_title, employer_id),
    )
    c.commit()
    c.close()


def approve_pending_employer(employer_id: int) -> None:
    c = _conn()
    c.execute("UPDATE employers SET is_pending_approval = 0 WHERE id = ?", (employer_id,))
    c.commit()
    c.close()


def delete_employer(employer_id: int) -> None:
    c = _conn()
    c.execute("DELETE FROM employer_sessions WHERE employer_id = ?", (employer_id,))
    c.execute("DELETE FROM employer_email_tokens WHERE employer_id = ?", (employer_id,))
    c.execute("DELETE FROM employers WHERE id = ?", (employer_id,))
    c.commit()
    c.close()


def create_employer_session(employer_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    c = _conn()
    c.execute(
        "INSERT INTO employer_sessions (token, employer_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token, employer_id, (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"),
         now.isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()
    return token


def get_employer_session(token: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM employer_sessions WHERE token = ?", (token,)).fetchone()
    c.close()
    if not row:
        return None
    if row["expires_at"] < datetime.now(timezone.utc).isoformat(timespec="seconds"):
        return None
    return dict(row)


def delete_employer_session(token: str) -> None:
    c = _conn()
    c.execute("DELETE FROM employer_sessions WHERE token = ?", (token,))
    c.commit()
    c.close()


def delete_employer_sessions_for_employer(employer_id: int) -> None:
    c = _conn()
    c.execute("DELETE FROM employer_sessions WHERE employer_id = ?", (employer_id,))
    c.commit()
    c.close()


def create_employer_email_token(employer_id: int, purpose: str) -> str:
    now = datetime.now(timezone.utc)
    if purpose == "verify":
        token = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = now + timedelta(minutes=VERIFY_CODE_MINUTES)
    else:
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(hours=RESET_TOKEN_HOURS)
    c = _conn()
    c.execute(
        "INSERT INTO employer_email_tokens (token, employer_id, purpose, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (token, employer_id, purpose, expires_at.isoformat(timespec="seconds"),
         now.isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()
    return token


def consume_employer_email_token(token: str, purpose: str) -> int | None:
    c = _conn()
    row = c.execute(
        "SELECT * FROM employer_email_tokens WHERE token = ? AND purpose = ?", (token, purpose)
    ).fetchone()
    if not row or row["used"] or row["expires_at"] < datetime.now(timezone.utc).isoformat(timespec="seconds"):
        c.close()
        return None
    c.execute("UPDATE employer_email_tokens SET used = 1 WHERE token = ?", (token,))
    c.commit()
    c.close()
    return row["employer_id"]


def cleanup_stale_employer_tokens() -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    c = _conn()
    c.execute("DELETE FROM employer_sessions WHERE expires_at < ?", (now,))
    c.execute("DELETE FROM employer_email_tokens WHERE expires_at < ?", (now,))
    c.commit()
    c.close()


# ---------------------------------------------------------------------------
# Team invites: a colleague joins an existing company instead of setting one
# up twice. needs_approval is decided once, at send time, from the
# inviter's own team_role — an owner can add people directly, anyone else's
# invitees land pending until an owner approves them (see
# approve_pending_employer / employer_delete_me's sibling in main.py).
# ---------------------------------------------------------------------------

def create_team_invite(company_id: int, invited_email: str, invited_by_employer_id: int,
                        proposed_team_role: str, needs_approval: bool) -> str:
    token = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    c = _conn()
    c.execute(
        "INSERT INTO team_invites (token, company_id, invited_email, invited_by_employer_id, "
        "proposed_team_role, needs_approval, status, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (token, company_id, invited_email, invited_by_employer_id, proposed_team_role,
         1 if needs_approval else 0,
         (now + timedelta(days=INVITE_EXPIRY_DAYS)).isoformat(timespec="seconds"),
         now.isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()
    return token


def get_team_invite_by_token(token: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM team_invites WHERE token = ?", (token,)).fetchone()
    c.close()
    return dict(row) if row else None


def list_pending_team_invites(company_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM team_invites WHERE company_id = ? AND status = 'pending' ORDER BY created_at DESC",
        (company_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def set_invite_status(token: str, status: str) -> None:
    c = _conn()
    c.execute(
        "UPDATE team_invites SET status = ?, responded_at = ? WHERE token = ?",
        (status, datetime.now(timezone.utc).isoformat(timespec="seconds"), token),
    )
    c.commit()
    c.close()


# ---------------------------------------------------------------------------
# Analytics: pageviews, searches, apply-clicks, and the aggregate summary
# the admin dashboard reads. No personal data stored — see module docstring.
# ---------------------------------------------------------------------------

def log_pageview(path: str, device: str, country: str | None) -> None:
    c = _conn()
    c.execute(
        "INSERT INTO pageviews (path, device, country, created_at) VALUES (?, ?, ?, ?)",
        (path, device, country, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()


def log_search(query: str, device: str, country: str | None) -> None:
    c = _conn()
    c.execute(
        "INSERT INTO search_events (query, device, country, created_at) VALUES (?, ?, ?, ?)",
        (query, device, country, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()


def log_apply_click(job_id: str, job_title: str, company: str, device: str, country: str | None) -> None:
    c = _conn()
    c.execute(
        "INSERT INTO apply_clicks (job_id, job_title, company, device, country, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, job_title, company, device, country, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()


def cleanup_stale_analytics(days: int = ANALYTICS_RETENTION_DAYS) -> None:
    """Delete analytics rows older than `days`. Called once on server
    startup (see init_db) rather than per-request — good enough given how
    often this app redeploys/restarts, without adding a scheduler."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    c = _conn()
    for table in ("pageviews", "search_events", "apply_clicks"):
        c.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
    c.commit()
    c.close()


def count_profile_views(handle: str, designer_id: int) -> int:
    """Total pageviews of a designer's public profile, matched against both
    the numeric-id path and the handle path (older views may be logged under
    either, e.g. before a handle existed or via an id-based link). Rows age
    out after ANALYTICS_RETENTION_DAYS like the rest of this module's
    analytics, so this is a rolling ~90-day count, not a lifetime total —
    and a handle change silently orphans views logged under the old handle."""
    paths = [f"/designers/{designer_id}"]
    if handle:
        paths.append(f"/designers/{handle}")
    c = _conn()
    placeholders = ",".join("?" * len(paths))
    row = c.execute(f"SELECT COUNT(*) FROM pageviews WHERE path IN ({placeholders})", paths).fetchone()
    c.close()
    return row[0]


def get_analytics_summary(days: int = 30) -> dict:
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    prev_since = (now - timedelta(days=days * 2)).isoformat(timespec="seconds")
    c = _conn()

    def count(table: str) -> int:
        return c.execute(f"SELECT COUNT(*) FROM {table} WHERE created_at >= ?", (since,)).fetchone()[0]

    def count_prev(table: str) -> int:
        return c.execute(
            f"SELECT COUNT(*) FROM {table} WHERE created_at >= ? AND created_at < ?",
            (prev_since, since),
        ).fetchone()[0]

    def rows(sql: str) -> list[dict]:
        return [dict(r) for r in c.execute(sql, (since,)).fetchall()]

    summary = {
        "days": days,
        "total_views": count("pageviews"),
        "total_searches": count("search_events"),
        "total_applies": count("apply_clicks"),
        "prev_views": count_prev("pageviews"),
        "prev_searches": count_prev("search_events"),
        "prev_applies": count_prev("apply_clicks"),
        "by_day": rows("""
            SELECT substr(created_at,1,10) AS day, COUNT(*) AS n
            FROM pageviews WHERE created_at >= ?
            GROUP BY day ORDER BY day
        """),
        "by_weekday": rows("""
            SELECT CAST(strftime('%w', created_at) AS INTEGER) AS wd, COUNT(*) AS n
            FROM pageviews WHERE created_at >= ?
            GROUP BY wd
        """),
        "by_device": rows("""
            SELECT device, COUNT(*) AS n FROM pageviews WHERE created_at >= ?
            GROUP BY device ORDER BY n DESC
        """),
        "by_country": rows("""
            SELECT COALESCE(country, 'Unknown') AS country, COUNT(*) AS n
            FROM pageviews WHERE created_at >= ?
            GROUP BY country ORDER BY n DESC LIMIT 15
        """),
        "top_pages": rows("""
            SELECT path, COUNT(*) AS n FROM pageviews WHERE created_at >= ?
            GROUP BY path ORDER BY n DESC LIMIT 15
        """),
        "top_searches": rows("""
            SELECT query, COUNT(*) AS n FROM search_events WHERE created_at >= ?
            GROUP BY query ORDER BY n DESC LIMIT 15
        """),
        "top_applied": rows("""
            SELECT job_title, company, COUNT(*) AS n FROM apply_clicks WHERE created_at >= ?
            GROUP BY job_id, job_title, company ORDER BY n DESC LIMIT 15
        """),
    }
    c.close()
    return summary
