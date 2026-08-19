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
import statistics
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
  cross_border_note TEXT NOT NULL DEFAULT '',
  eligibility_source TEXT NOT NULL DEFAULT 'employer-claimed',
  eligibility_override_reason TEXT NOT NULL DEFAULT '',
  eligibility_overridden_at TEXT NOT NULL DEFAULT '',
  -- ISO date the employer stops accepting applications. Employer-posted roles
  -- only: no ATS we scrape exposes one, and inventing a date for a listing
  -- nobody set is exactly the kind of guess this product doesn't make.
  closes_at TEXT NOT NULL DEFAULT '',
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
  -- Free-text `location` ("Nairobi, Kenya") is what a designer types and what
  -- we display. `country` is the structured half the job board matches against
  -- an eligibility scope, so it must stay a bare country name.
  country TEXT NOT NULL DEFAULT '',
  photo_path TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  contact_email TEXT NOT NULL DEFAULT '',
  handle TEXT NOT NULL DEFAULT '',
  headline TEXT NOT NULL DEFAULT '',
  years_experience TEXT NOT NULL DEFAULT '',
  availability_status TEXT NOT NULL DEFAULT '',
  -- What kind of work, as opposed to whether they're looking at all. An
  -- employer scanning the directory needs to tell a staff hire from a
  -- freelancer, and availability_status alone never said.
  open_to TEXT NOT NULL DEFAULT '[]',
  skills TEXT NOT NULL DEFAULT '[]',
  resume_path TEXT NOT NULL DEFAULT '',
  resume_filename TEXT NOT NULL DEFAULT '',
  resume_uploaded_at TEXT NOT NULL DEFAULT '',
  resume_public INTEGER NOT NULL DEFAULT 0,
  onboarding_completed INTEGER NOT NULL DEFAULT 0,
  email_verified INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  company_id INTEGER,
  failed_login_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT NOT NULL DEFAULT '',
  suspend_reason TEXT NOT NULL DEFAULT '',
  suspend_rule TEXT NOT NULL DEFAULT ''
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
  sort_order INTEGER NOT NULL DEFAULT 0,
  problem TEXT NOT NULL DEFAULT '',
  results TEXT NOT NULL DEFAULT '[]',
  credits TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_designer_projects_designer ON designer_projects(designer_id);

-- Multiple gallery images per project (image_path above is just the cover).
CREATE TABLE IF NOT EXISTS project_images (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  image_path TEXT NOT NULL,
  caption TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_project_images_project ON project_images(project_id);

-- A designer's past roles, for the Profile "Experience" tab.
CREATE TABLE IF NOT EXISTS role_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  designer_id INTEGER NOT NULL,
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  start_date TEXT NOT NULL DEFAULT '',
  end_date TEXT NOT NULL DEFAULT '',
  is_current INTEGER NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_role_history_designer ON role_history(designer_id);

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
  suspended INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  failed_login_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT NOT NULL DEFAULT ''
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

CREATE TABLE IF NOT EXISTS job_applicants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL,
  company_id INTEGER NOT NULL,
  full_name TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  portfolio_url TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  stage INTEGER NOT NULL DEFAULT 0,
  created_by_employer_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_applicants_submission ON job_applicants(submission_id);
CREATE INDEX IF NOT EXISTS idx_job_applicants_company ON job_applicants(company_id);

CREATE TABLE IF NOT EXISTS designer_saved_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  designer_id INTEGER NOT NULL,
  job_id TEXT NOT NULL,
  title TEXT NOT NULL,
  company TEXT NOT NULL,
  location TEXT NOT NULL DEFAULT '',
  eligibility TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  saved_at TEXT NOT NULL,
  UNIQUE(designer_id, job_id)
);
CREATE INDEX IF NOT EXISTS idx_saved_jobs_designer ON designer_saved_jobs(designer_id);

-- A named filter set, so a designer hunting one specific kind of role doesn't
-- rebuild the same query on every visit. `filters` is the board's own filter
-- state as JSON rather than a column per filter: the board owns what a filter
-- means, and adding one there shouldn't need a migration here.
-- last_notified_at is the digest's high-water mark — roles posted after it are
-- what the next email has to report.
CREATE TABLE IF NOT EXISTS designer_saved_searches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  designer_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  filters TEXT NOT NULL DEFAULT '{}',
  alerts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_notified_at TEXT NOT NULL DEFAULT '',
  UNIQUE(designer_id, name)
);
CREATE INDEX IF NOT EXISTS idx_saved_searches_designer ON designer_saved_searches(designer_id);

-- A company's shortlist of designers. Scoped to the company rather than the
-- individual recruiter: hiring is a team activity here (see team_invites), and
-- a shortlist that disappeared when one person left would be worse than none.
-- `note` is why they were saved — the thing everyone otherwise keeps in a
-- separate document.
CREATE TABLE IF NOT EXISTS company_shortlist (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER NOT NULL,
  designer_id INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  saved_by_employer_id INTEGER NOT NULL,
  saved_at TEXT NOT NULL,
  UNIQUE(company_id, designer_id)
);
CREATE INDEX IF NOT EXISTS idx_shortlist_company ON company_shortlist(company_id);

-- One table for both sides: (recipient_type, recipient_id) rather than two
-- near-identical tables, because every notification is the same shape and the
-- only thing that differs is who reads it.
--
-- Deliberately narrow. A notification is created only for something a person
-- would want to interrupt their day for — a message, a company saving their
-- profile. Anything that is merely activity belongs on a page someone chooses
-- to visit, not in a bell that trains them to ignore it.
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recipient_type TEXT NOT NULL,          -- 'designer' | 'employer'
  recipient_id INTEGER NOT NULL,
  kind TEXT NOT NULL,                    -- 'message' | 'shortlisted' | 'reply'
  title TEXT NOT NULL,                   -- already rendered: the sender is the point
  body TEXT NOT NULL DEFAULT '',
  href TEXT NOT NULL DEFAULT '',         -- where acting on it goes
  read_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_recipient
  ON notifications(recipient_type, recipient_id, read_at);

-- Self-reported: we hand applications to the company's own page and never see
-- what happens next, so this records only that the designer says they applied.
-- The snapshot columns exist because a listing ages out of the feed long before
-- it stops mattering to the person who applied to it.
CREATE TABLE IF NOT EXISTS designer_applied_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  designer_id INTEGER NOT NULL,
  job_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  company TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  applied_at TEXT NOT NULL,
  UNIQUE(designer_id, job_id)
);
CREATE INDEX IF NOT EXISTS idx_applied_designer ON designer_applied_jobs(designer_id);

-- Original shape (company_id NOT NULL, no peer_designer_id). Every run —
-- fresh database or not — goes through _migrate_conversations_peer_support()
-- right after this script, which is the single source of truth for the
-- final (nullable company_id + peer_designer_id) shape; CREATE TABLE IF NOT
-- EXISTS can't safely declare that shape here because it's a no-op against
-- an already-existing table, so a plain SCHEMA edit alone can't upgrade one.
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  designer_id INTEGER NOT NULL,
  company_id INTEGER NOT NULL,
  started_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_message_at TEXT NOT NULL,
  UNIQUE(designer_id, company_id)
);
CREATE INDEX IF NOT EXISTS idx_conversations_designer ON conversations(designer_id);
CREATE INDEX IF NOT EXISTS idx_conversations_company ON conversations(company_id);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL,
  sender_type TEXT NOT NULL,
  sender_designer_id INTEGER,
  sender_employer_id INTEGER,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  read_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS community_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT '',
  session_date TEXT NOT NULL,
  time TEXT NOT NULL DEFAULT '',
  length TEXT NOT NULL DEFAULT '',
  blurb TEXT NOT NULL DEFAULT '',
  host TEXT NOT NULL DEFAULT '',
  host_initials TEXT NOT NULL DEFAULT '',
  host_bg TEXT NOT NULL DEFAULT '',
  host_fg TEXT NOT NULL DEFAULT '',
  reviewer_bio TEXT NOT NULL DEFAULT '',
  seats INTEGER NOT NULL DEFAULT 6,
  joining_link TEXT NOT NULL DEFAULT '',
  bring_list TEXT NOT NULL DEFAULT '[]',
  agenda TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'scheduled',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_sessions_date ON community_sessions(session_date);

CREATE TABLE IF NOT EXISTS session_bookings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL,
  designer_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'booked',
  created_at TEXT NOT NULL,
  UNIQUE(session_id, designer_id)
);
CREATE INDEX IF NOT EXISTS idx_session_bookings_session ON session_bookings(session_id);
CREATE INDEX IF NOT EXISTS idx_session_bookings_designer ON session_bookings(designer_id);

CREATE TABLE IF NOT EXISTS community_questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  designer_id INTEGER NOT NULL,
  topic TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  accepted_reply_id INTEGER,
  status TEXT NOT NULL DEFAULT 'visible',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_questions_designer ON community_questions(designer_id);
CREATE INDEX IF NOT EXISTS idx_community_questions_status ON community_questions(status);

CREATE TABLE IF NOT EXISTS community_replies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id INTEGER NOT NULL,
  designer_id INTEGER NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'visible',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_community_replies_question ON community_replies(question_id);

CREATE TABLE IF NOT EXISTS reply_votes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reply_id INTEGER NOT NULL,
  designer_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(reply_id, designer_id)
);
CREATE INDEX IF NOT EXISTS idx_reply_votes_reply ON reply_votes(reply_id);

CREATE TABLE IF NOT EXISTS question_follows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id INTEGER NOT NULL,
  designer_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(question_id, designer_id)
);
CREATE INDEX IF NOT EXISTS idx_question_follows_question ON question_follows(question_id);

-- Generic follow: a designer following another designer's profile or a
-- company's page (distinct from question_follows above, which is
-- "notify me about this thread", not a profile relationship).
CREATE TABLE IF NOT EXISTS follows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  follower_designer_id INTEGER NOT NULL,
  target_type TEXT NOT NULL,
  target_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(follower_designer_id, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS idx_follows_follower ON follows(follower_designer_id);
CREATE INDEX IF NOT EXISTS idx_follows_target ON follows(target_type, target_id);

CREATE TABLE IF NOT EXISTS pay_submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  designer_id INTEGER NOT NULL,
  discipline TEXT NOT NULL,
  level TEXT NOT NULL,
  market TEXT NOT NULL,
  raw_currency TEXT NOT NULL DEFAULT '',
  raw_amount REAL NOT NULL DEFAULT 0,
  amount_monthly_usd REAL NOT NULL,
  outlier_check TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pay_submissions_status ON pay_submissions(status);
CREATE INDEX IF NOT EXISTS idx_pay_submissions_group ON pay_submissions(discipline, level, market);

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  target_id INTEGER NOT NULL,
  reporter_designer_id INTEGER,
  reporter_employer_id INTEGER,
  summary TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  resolution TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  resolved_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
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
    "ALTER TABLE employers ADD COLUMN suspended INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE designers ADD COLUMN company_id INTEGER",
    "ALTER TABLE designer_projects ADD COLUMN problem TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designer_projects ADD COLUMN results TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE designer_projects ADD COLUMN credits TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE designers ADD COLUMN locked_until TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE employers ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE employers ADD COLUMN locked_until TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE submissions ADD COLUMN cross_border_note TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN suspend_reason TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN suspend_rule TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE submissions ADD COLUMN eligibility_source TEXT NOT NULL DEFAULT 'employer-claimed'",
    "ALTER TABLE submissions ADD COLUMN eligibility_override_reason TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE submissions ADD COLUMN eligibility_overridden_at TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN country TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE designers ADD COLUMN open_to TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE submissions ADD COLUMN closes_at TEXT NOT NULL DEFAULT ''",
]


def _migrate_conversations_peer_support(c: sqlite3.Connection) -> None:
    """conversations.company_id was NOT NULL from launch (every thread was
    designer<->company). Peer (designer<->designer) messaging needs
    company_id nullable plus a new peer_designer_id column — SQLite can't
    relax a NOT NULL via ALTER, so this rebuilds the table once. Guarded by
    checking for the new column first, since this runs on every _conn()."""
    cols = [r["name"] for r in c.execute("PRAGMA table_info(conversations)").fetchall()]
    if "peer_designer_id" in cols:
        return
    c.executescript("""
        CREATE TABLE conversations_new (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          designer_id INTEGER NOT NULL,
          company_id INTEGER,
          peer_designer_id INTEGER,
          started_by TEXT NOT NULL,
          created_at TEXT NOT NULL,
          last_message_at TEXT NOT NULL
        );
        INSERT INTO conversations_new (id, designer_id, company_id, peer_designer_id, started_by, created_at, last_message_at)
          SELECT id, designer_id, company_id, NULL, started_by, created_at, last_message_at FROM conversations;
        DROP TABLE conversations;
        ALTER TABLE conversations_new RENAME TO conversations;
        CREATE UNIQUE INDEX idx_conversations_designer_company ON conversations(designer_id, company_id) WHERE company_id IS NOT NULL;
        CREATE UNIQUE INDEX idx_conversations_peer_pair ON conversations(designer_id, peer_designer_id) WHERE peer_designer_id IS NOT NULL;
        CREATE INDEX idx_conversations_designer ON conversations(designer_id);
        CREATE INDEX idx_conversations_company ON conversations(company_id);
        CREATE INDEX idx_conversations_peer ON conversations(peer_designer_id);
    """)
    c.commit()


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
    _migrate_conversations_peer_support(c)
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
           cross_border_note, closes_at, company_id, employer_id, status, created_at)
        VALUES
          (:title, :company, :url, :contact_email, :location, :work_type,
           :discipline, :level, :eligibility, :salary, :description, :agreed_to_terms,
           :cross_border_note, :closes_at, :company_id, :employer_id, 'pending', :created_at)
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


def override_submission_eligibility(sub_id: int, eligibility: str, reason: str) -> None:
    """Admin correcting the employer's own eligibility claim during review —
    recorded with a source flag and reason so every listing surface can show
    it was admin-set, and the employer can be told why (see review modal)."""
    c = _conn()
    c.execute(
        "UPDATE submissions SET eligibility = ?, eligibility_source = 'admin-set', "
        "eligibility_override_reason = ?, eligibility_overridden_at = ? WHERE id = ?",
        (eligibility, reason, datetime.now(timezone.utc).isoformat(timespec="seconds"), sub_id),
    )
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


LOGIN_LOCKOUT_THRESHOLD = 5
LOGIN_LOCKOUT_MINUTES = 15


def record_designer_login_failure(designer_id: int) -> dict:
    """Bumps the failed-attempt counter and, once it reaches the threshold,
    locks the account for LOGIN_LOCKOUT_MINUTES and resets the counter so a
    fresh run starts counting from zero after the lock clears. Returns the
    post-update {attempts, locked_until} so the caller can build the
    "N attempts left" / lockout message without a second query."""
    c = _conn()
    row = c.execute("SELECT failed_login_attempts FROM designers WHERE id = ?", (designer_id,)).fetchone()
    attempts = (row["failed_login_attempts"] if row else 0) + 1
    locked_until = ""
    if attempts >= LOGIN_LOCKOUT_THRESHOLD:
        locked_until = (datetime.now(timezone.utc) + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat(timespec="seconds")
        attempts = 0
    c.execute(
        "UPDATE designers SET failed_login_attempts = ?, locked_until = ? WHERE id = ?",
        (attempts, locked_until, designer_id),
    )
    c.commit()
    c.close()
    return {"attempts": attempts, "locked_until": locked_until}


def reset_designer_login_failures(designer_id: int) -> None:
    c = _conn()
    c.execute("UPDATE designers SET failed_login_attempts = 0, locked_until = '' WHERE id = ?", (designer_id,))
    c.commit()
    c.close()


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
    country: str = "",
    phone: str = "", contact_email: str = "",
    headline: str = "", years_experience: str = "", availability_status: str = "",
    skills: list[str] | None = None, open_to: list[str] | None = None,
) -> None:
    c = _conn()
    # Editing an approved profile sends it back for re-review, same as a job
    # listing would if it were editable — never let a live public profile
    # change without another pass through the admin queue.
    row = c.execute("SELECT status FROM designers WHERE id = ?", (designer_id,)).fetchone()
    new_status = "pending" if row and row["status"] == "approved" else (row["status"] if row else "pending")
    c.execute(
        "UPDATE designers SET display_name = ?, bio = ?, discipline = ?, location = ?, "
        "country = ?, phone = ?, contact_email = ?, headline = ?, years_experience = ?, "
        "availability_status = ?, skills = ?, open_to = ?, status = ? WHERE id = ?",
        (display_name, bio, json.dumps(discipline), location, country, phone, contact_email,
         headline, years_experience, availability_status, json.dumps(skills or []),
         json.dumps(open_to or []), new_status, designer_id),
    )
    c.commit()
    c.close()


def designers_missing_country() -> list[dict]:
    """Everyone who has typed a location but has no structured country yet."""
    c = _conn()
    rows = c.execute(
        "SELECT id, location FROM designers WHERE country = '' AND location != ''"
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def set_designer_country(designer_id: int, country: str) -> None:
    """Country only — deliberately not update_designer_profile(), which sends an
    approved profile back for re-review. Deriving a country from what someone
    already typed is not an edit they made, so it must not cost them their
    listing."""
    c = _conn()
    c.execute("UPDATE designers SET country = ? WHERE id = ?", (country, designer_id))
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


def set_designer_company(designer_id: int, company_id: int | None) -> None:
    """Self-reported "I work here", for the Company page's design-team
    grid — separate from update_designer_profile, same non-re-review
    reasoning as update_designer_handle (not moderated content)."""
    c = _conn()
    c.execute("UPDATE designers SET company_id = ? WHERE id = ?", (company_id, designer_id))
    c.commit()
    c.close()


def list_designers_by_company(company_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM designers WHERE company_id = ? AND status = 'approved' ORDER BY display_name",
        (company_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def list_role_history(designer_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT id, company, title, start_date, end_date, is_current, description "
        "FROM role_history WHERE designer_id = ? ORDER BY sort_order",
        (designer_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def create_role_history(designer_id: int, company: str, title: str, start_date: str, end_date: str,
                         is_current: bool, description: str) -> int:
    c = _conn()
    n = c.execute("SELECT COUNT(*) FROM role_history WHERE designer_id = ?", (designer_id,)).fetchone()[0]
    cur = c.execute(
        "INSERT INTO role_history (designer_id, company, title, start_date, end_date, is_current, description, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (designer_id, company, title, start_date, end_date, int(is_current), description, n),
    )
    role_id = cur.lastrowid
    c.commit()
    c.close()
    return role_id


def update_role_history(designer_id: int, role_id: int, company: str, title: str, start_date: str,
                         end_date: str, is_current: bool, description: str) -> bool:
    c = _conn()
    cur = c.execute(
        "UPDATE role_history SET company = ?, title = ?, start_date = ?, end_date = ?, "
        "is_current = ?, description = ? WHERE id = ? AND designer_id = ?",
        (company, title, start_date, end_date, int(is_current), description, role_id, designer_id),
    )
    updated = cur.rowcount > 0
    c.commit()
    c.close()
    return updated


def delete_role_history(designer_id: int, role_id: int) -> bool:
    c = _conn()
    cur = c.execute("DELETE FROM role_history WHERE id = ? AND designer_id = ?", (role_id, designer_id))
    deleted = cur.rowcount > 0
    c.commit()
    c.close()
    return deleted


def reorder_role_history(designer_id: int, ordered_ids: list[int]) -> None:
    c = _conn()
    for i, role_id in enumerate(ordered_ids):
        c.execute(
            "UPDATE role_history SET sort_order = ? WHERE id = ? AND designer_id = ?",
            (i, role_id, designer_id),
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


def suspend_designer(designer_id: int, rule: str, reason: str) -> None:
    """Suspending sets status='suspended' directly rather than a separate
    flag — require_designer() already gates on this exact value, and the
    public directory/profile queries already only ever surface
    status='approved', so a suspended profile disappears from both without
    any extra filtering."""
    c = _conn()
    c.execute(
        "UPDATE designers SET status = 'suspended', suspend_rule = ?, suspend_reason = ? WHERE id = ?",
        (rule, reason, designer_id),
    )
    c.commit()
    c.close()


def unsuspend_designer(designer_id: int) -> None:
    c = _conn()
    c.execute(
        "UPDATE designers SET status = 'approved', suspend_rule = '', suspend_reason = '' WHERE id = ?",
        (designer_id,),
    )
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
        "SELECT id, title, description, url, category, image_path, problem, results, credits "
        "FROM designer_projects WHERE designer_id = ? ORDER BY sort_order",
        (designer_id,),
    ).fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["results"] = json.loads(d["results"] or "[]")
        except (ValueError, TypeError):
            d["results"] = []
        d["gallery"] = list_project_images(d["id"])
        out.append(d)
    return out


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


def update_project_story(designer_id: int, project_id: int, problem: str, results: list, credits: str) -> bool:
    """The Case Study article fields — separate from update_designer_project
    (title/description/url/category), same ownership-checked update shape
    as set_project_image below."""
    c = _conn()
    cur = c.execute(
        "UPDATE designer_projects SET problem = ?, results = ?, credits = ? WHERE id = ? AND designer_id = ?",
        (problem, json.dumps(results or []), credits, project_id, designer_id),
    )
    updated = cur.rowcount > 0
    c.commit()
    c.close()
    return updated


def list_project_images(project_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT id, image_path, caption FROM project_images WHERE project_id = ? ORDER BY sort_order",
        (project_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def add_project_image(designer_id: int, project_id: int, image_path: str, caption: str = "") -> int | None:
    c = _conn()
    owns = c.execute(
        "SELECT id FROM designer_projects WHERE id = ? AND designer_id = ?", (project_id, designer_id)
    ).fetchone()
    if not owns:
        c.close()
        return None
    n = c.execute("SELECT COUNT(*) FROM project_images WHERE project_id = ?", (project_id,)).fetchone()[0]
    cur = c.execute(
        "INSERT INTO project_images (project_id, image_path, caption, sort_order) VALUES (?, ?, ?, ?)",
        (project_id, image_path, caption, n),
    )
    image_id = cur.lastrowid
    c.commit()
    c.close()
    return image_id


def update_project_image_caption(designer_id: int, project_id: int, image_id: int, caption: str) -> bool:
    c = _conn()
    owns = c.execute(
        "SELECT id FROM designer_projects WHERE id = ? AND designer_id = ?", (project_id, designer_id)
    ).fetchone()
    if not owns:
        c.close()
        return False
    cur = c.execute(
        "UPDATE project_images SET caption = ? WHERE id = ? AND project_id = ?",
        (caption, image_id, project_id),
    )
    updated = cur.rowcount > 0
    c.commit()
    c.close()
    return updated


def delete_project_image(designer_id: int, project_id: int, image_id: int) -> bool:
    c = _conn()
    owns = c.execute(
        "SELECT id FROM designer_projects WHERE id = ? AND designer_id = ?", (project_id, designer_id)
    ).fetchone()
    if not owns:
        c.close()
        return False
    cur = c.execute(
        "DELETE FROM project_images WHERE id = ? AND project_id = ?", (image_id, project_id)
    )
    deleted = cur.rowcount > 0
    c.commit()
    c.close()
    return deleted


def reorder_project_images(designer_id: int, project_id: int, ordered_ids: list[int]) -> bool:
    c = _conn()
    owns = c.execute(
        "SELECT id FROM designer_projects WHERE id = ? AND designer_id = ?", (project_id, designer_id)
    ).fetchone()
    if not owns:
        c.close()
        return False
    for i, image_id in enumerate(ordered_ids):
        c.execute(
            "UPDATE project_images SET sort_order = ? WHERE id = ? AND project_id = ?",
            (i, image_id, project_id),
        )
    c.commit()
    c.close()
    return True


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


def record_employer_login_failure(employer_id: int) -> dict:
    """Mirrors record_designer_login_failure() — see its docstring."""
    c = _conn()
    row = c.execute("SELECT failed_login_attempts FROM employers WHERE id = ?", (employer_id,)).fetchone()
    attempts = (row["failed_login_attempts"] if row else 0) + 1
    locked_until = ""
    if attempts >= LOGIN_LOCKOUT_THRESHOLD:
        locked_until = (datetime.now(timezone.utc) + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat(timespec="seconds")
        attempts = 0
    c.execute(
        "UPDATE employers SET failed_login_attempts = ?, locked_until = ? WHERE id = ?",
        (attempts, locked_until, employer_id),
    )
    c.commit()
    c.close()
    return {"attempts": attempts, "locked_until": locked_until}


def reset_employer_login_failures(employer_id: int) -> None:
    c = _conn()
    c.execute("UPDATE employers SET failed_login_attempts = 0, locked_until = '' WHERE id = ?", (employer_id,))
    c.commit()
    c.close()


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
# Applicants: a manual per-listing tracker an employer fills in themselves.
# Kazi doesn't collect real applications (they go to the company's own
# apply link), so every row here is entered by hand — this is a pipeline for
# candidates an employer is already talking to, not an application inbox.
# Every function takes company_id and filters on it, same ownership pattern
# as designer_projects filtering on designer_id, so one company can never
# read or touch another's rows even given a guessed id.
# ---------------------------------------------------------------------------

def create_applicant(submission_id: int, company_id: int, full_name: str, email: str,
                      location: str, portfolio_url: str, note: str, created_by_employer_id: int) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    c = _conn()
    cur = c.execute(
        "INSERT INTO job_applicants (submission_id, company_id, full_name, email, location, "
        "portfolio_url, note, stage, created_by_employer_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (submission_id, company_id, full_name, email, location, portfolio_url, note,
         created_by_employer_id, now, now),
    )
    c.commit()
    applicant_id = cur.lastrowid
    c.close()
    return applicant_id


def list_applicants_for_submission(submission_id: int, company_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM job_applicants WHERE submission_id = ? AND company_id = ? ORDER BY stage, created_at",
        (submission_id, company_id),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def count_applicants_by_submission(company_id: int) -> dict[int, int]:
    """{submission_id: count} for every listing this company has — the
    per-row number the Listings view shows without a query per row."""
    c = _conn()
    rows = c.execute(
        "SELECT submission_id, COUNT(*) AS n FROM job_applicants WHERE company_id = ? GROUP BY submission_id",
        (company_id,),
    ).fetchall()
    c.close()
    return {r["submission_id"]: r["n"] for r in rows}


def update_applicant(applicant_id: int, company_id: int, full_name: str, email: str,
                      location: str, portfolio_url: str, note: str) -> bool:
    c = _conn()
    cur = c.execute(
        "UPDATE job_applicants SET full_name = ?, email = ?, location = ?, portfolio_url = ?, "
        "note = ?, updated_at = ? WHERE id = ? AND company_id = ?",
        (full_name, email, location, portfolio_url, note,
         datetime.now(timezone.utc).isoformat(timespec="seconds"), applicant_id, company_id),
    )
    updated = cur.rowcount > 0
    c.commit()
    c.close()
    return updated


def set_applicant_stage(applicant_id: int, company_id: int, stage: int) -> bool:
    c = _conn()
    cur = c.execute(
        "UPDATE job_applicants SET stage = ?, updated_at = ? WHERE id = ? AND company_id = ?",
        (stage, datetime.now(timezone.utc).isoformat(timespec="seconds"), applicant_id, company_id),
    )
    updated = cur.rowcount > 0
    c.commit()
    c.close()
    return updated


def delete_applicant(applicant_id: int, company_id: int) -> bool:
    c = _conn()
    cur = c.execute(
        "DELETE FROM job_applicants WHERE id = ? AND company_id = ?", (applicant_id, company_id)
    )
    deleted = cur.rowcount > 0
    c.commit()
    c.close()
    return deleted


# ---------------------------------------------------------------------------
# Saved roles. title/company/location/eligibility/url are a snapshot taken
# at save time, not a live join against _combined_jobs() — a scraped listing
# ages out of web/jobs.json (MAX_AGE_DAYS) and an employer submission can
# close, and a saved role should still show something real rather than a
# broken reference once that happens.
# ---------------------------------------------------------------------------

def save_job(designer_id: int, job_id: str, title: str, company: str,
             location: str, eligibility: str, url: str) -> None:
    c = _conn()
    c.execute(
        "INSERT OR IGNORE INTO designer_saved_jobs "
        "(designer_id, job_id, title, company, location, eligibility, url, saved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (designer_id, job_id, title, company, location, eligibility, url,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()


def list_saved_jobs(designer_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM designer_saved_jobs WHERE designer_id = ? ORDER BY saved_at DESC", (designer_id,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def unsave_job(designer_id: int, job_id: str) -> bool:
    c = _conn()
    cur = c.execute(
        "DELETE FROM designer_saved_jobs WHERE designer_id = ? AND job_id = ?", (designer_id, job_id)
    )
    deleted = cur.rowcount > 0
    c.commit()
    c.close()
    return deleted


# ---------------- Notifications ----------------

def notify(recipient_type: str, recipient_id: int, kind: str,
           title: str, body: str = "", href: str = "") -> None:
    """Fire-and-forget. A notification is a nicety; failing to write one must
    never take down the action that caused it, so callers don't check."""
    try:
        c = _conn()
        c.execute(
            "INSERT INTO notifications (recipient_type, recipient_id, kind, title, body, href, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (recipient_type, recipient_id, kind, title[:200], body[:400], href[:300],
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        c.commit()
        c.close()
    except Exception:
        pass


def list_notifications(recipient_type: str, recipient_id: int, limit: int = 30) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM notifications WHERE recipient_type = ? AND recipient_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (recipient_type, recipient_id, limit),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def count_unread_notifications(recipient_type: str, recipient_id: int) -> int:
    c = _conn()
    n = c.execute(
        "SELECT COUNT(*) FROM notifications WHERE recipient_type = ? AND recipient_id = ? AND read_at = ''",
        (recipient_type, recipient_id),
    ).fetchone()[0]
    c.close()
    return n


def mark_notifications_read(recipient_type: str, recipient_id: int, notif_id: int | None = None) -> None:
    c = _conn()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if notif_id is None:
        c.execute(
            "UPDATE notifications SET read_at = ? WHERE recipient_type = ? AND recipient_id = ? AND read_at = ''",
            (now, recipient_type, recipient_id),
        )
    else:
        c.execute(
            "UPDATE notifications SET read_at = ? WHERE id = ? AND recipient_type = ? AND recipient_id = ?",
            (now, notif_id, recipient_type, recipient_id),
        )
    c.commit()
    c.close()


# ---------------- Company shortlist ----------------

def shortlist_add(company_id: int, designer_id: int, note: str, employer_id: int) -> None:
    c = _conn()
    c.execute(
        "INSERT INTO company_shortlist (company_id, designer_id, note, saved_by_employer_id, saved_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(company_id, designer_id) DO UPDATE SET note = excluded.note",
        (company_id, designer_id, note, employer_id,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()


def shortlist_remove(company_id: int, designer_id: int) -> bool:
    c = _conn()
    cur = c.execute(
        "DELETE FROM company_shortlist WHERE company_id = ? AND designer_id = ?", (company_id, designer_id)
    )
    removed = cur.rowcount > 0
    c.commit()
    c.close()
    return removed


def shortlist_ids(company_id: int) -> list[int]:
    c = _conn()
    rows = c.execute(
        "SELECT designer_id FROM company_shortlist WHERE company_id = ?", (company_id,)
    ).fetchall()
    c.close()
    return [r["designer_id"] for r in rows]


def shortlist_entries(company_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM company_shortlist WHERE company_id = ? ORDER BY saved_at DESC", (company_id,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ---------------- Applied roles (self-reported) ----------------

def mark_applied(designer_id: int, job_id: str, title: str, company: str,
                 location: str, url: str) -> None:
    c = _conn()
    c.execute(
        "INSERT OR IGNORE INTO designer_applied_jobs "
        "(designer_id, job_id, title, company, location, url, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (designer_id, job_id, title, company, location, url,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()


def list_applied_jobs(designer_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM designer_applied_jobs WHERE designer_id = ? ORDER BY applied_at DESC",
        (designer_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def unmark_applied(designer_id: int, job_id: str) -> bool:
    c = _conn()
    cur = c.execute(
        "DELETE FROM designer_applied_jobs WHERE designer_id = ? AND job_id = ?", (designer_id, job_id)
    )
    deleted = cur.rowcount > 0
    c.commit()
    c.close()
    return deleted


# ---------------- Saved searches ----------------

def save_search(designer_id: int, name: str, filters: dict, alerts: bool) -> dict:
    """Upsert by name, so re-saving under a name someone already used updates
    that search rather than silently making a near-duplicate they'd have to
    tell apart later."""
    c = _conn()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    c.execute(
        "INSERT INTO designer_saved_searches (designer_id, name, filters, alerts, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(designer_id, name) DO UPDATE SET filters = excluded.filters, alerts = excluded.alerts",
        (designer_id, name, json.dumps(filters), 1 if alerts else 0, now),
    )
    c.commit()
    row = c.execute(
        "SELECT * FROM designer_saved_searches WHERE designer_id = ? AND name = ?", (designer_id, name)
    ).fetchone()
    c.close()
    return dict(row)


def list_saved_searches(designer_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM designer_saved_searches WHERE designer_id = ? ORDER BY created_at DESC",
        (designer_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def delete_saved_search(designer_id: int, search_id: int) -> bool:
    c = _conn()
    cur = c.execute(
        "DELETE FROM designer_saved_searches WHERE designer_id = ? AND id = ?", (designer_id, search_id)
    )
    deleted = cur.rowcount > 0
    c.commit()
    c.close()
    return deleted


def set_search_alerts(designer_id: int, search_id: int, alerts: bool) -> bool:
    c = _conn()
    cur = c.execute(
        "UPDATE designer_saved_searches SET alerts = ? WHERE designer_id = ? AND id = ?",
        (1 if alerts else 0, designer_id, search_id),
    )
    changed = cur.rowcount > 0
    c.commit()
    c.close()
    return changed


def searches_with_alerts() -> list[dict]:
    """Every alert-enabled search with the owner's email and country attached —
    what the weekly digest iterates. Suspended and unverified accounts are
    excluded here rather than at send time, so there is one place that decides
    who is emailable."""
    c = _conn()
    rows = c.execute(
        "SELECT s.*, d.email, d.display_name, d.country "
        "FROM designer_saved_searches s JOIN designers d ON d.id = s.designer_id "
        "WHERE s.alerts = 1 AND d.email_verified = 1 AND d.status != 'suspended'"
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def mark_search_notified(search_id: int, when: str) -> None:
    c = _conn()
    c.execute("UPDATE designer_saved_searches SET last_notified_at = ? WHERE id = ?", (when, search_id))
    c.commit()
    c.close()


# ---------------------------------------------------------------------------
# Messages: one thread per (designer, company) pair, either side can start
# it, any teammate on the company side can read/reply. Plain polling, no
# realtime infra — matches this codebase's plain-JS-no-libraries style.
# ---------------------------------------------------------------------------

def get_or_create_conversation(designer_id: int, company_id: int, started_by: str) -> int:
    c = _conn()
    row = c.execute(
        "SELECT id FROM conversations WHERE designer_id = ? AND company_id = ?",
        (designer_id, company_id),
    ).fetchone()
    if row:
        c.close()
        return row["id"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = c.execute(
        "INSERT INTO conversations (designer_id, company_id, started_by, created_at, last_message_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (designer_id, company_id, started_by, now, now),
    )
    c.commit()
    conversation_id = cur.lastrowid
    c.close()
    return conversation_id


def get_or_create_peer_conversation(designer_a_id: int, designer_b_id: int, started_by_id: int) -> int:
    """Designer-to-designer equivalent of get_or_create_conversation().
    Storage is normalised (designer_id = the smaller id) so a conversation
    started from either side's profile lands in the same row — otherwise
    A messaging B and B messaging A would create two separate threads."""
    lo, hi = sorted((designer_a_id, designer_b_id))
    c = _conn()
    row = c.execute(
        "SELECT id FROM conversations WHERE designer_id = ? AND peer_designer_id = ?", (lo, hi)
    ).fetchone()
    if row:
        c.close()
        return row["id"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = c.execute(
        "INSERT INTO conversations (designer_id, peer_designer_id, started_by, created_at, last_message_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (lo, hi, str(started_by_id), now, now),
    )
    c.commit()
    conversation_id = cur.lastrowid
    c.close()
    return conversation_id


def get_conversation(conversation_id: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def _conversation_summaries(rows: list[sqlite3.Row], reader_type: str, reader_designer_id: int | None = None) -> list[dict]:
    """reader_designer_id disambiguates unread counts on a PEER
    (designer<->designer) conversation, where sender_type is 'designer' on
    both sides so 'sender_type != reader_type' can't tell the two apart —
    it's ignored for company conversations, where that check already works."""
    c = _conn()
    out = []
    for row in rows:
        conv = dict(row)
        # id, not created_at — two messages can land in the same second
        # (created_at only has second resolution), and id is the one value
        # that always reflects true insertion order regardless.
        last = c.execute(
            "SELECT body, sender_type, created_at FROM messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT 1", (conv["id"],)
        ).fetchone()
        if conv.get("peer_designer_id") is not None and reader_designer_id is not None:
            unread = c.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND sender_designer_id != ? AND read_at = ''",
                (conv["id"], reader_designer_id),
            ).fetchone()[0]
        else:
            unread = c.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND sender_type != ? AND read_at = ''",
                (conv["id"], reader_type),
            ).fetchone()[0]
        conv["last_message"] = dict(last) if last else None
        conv["unread_count"] = unread
        out.append(conv)
    c.close()
    return out


def list_conversations_for_designer(designer_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM conversations WHERE designer_id = ? OR peer_designer_id = ? "
        "ORDER BY last_message_at DESC, id DESC", (designer_id, designer_id)
    ).fetchall()
    c.close()
    return _conversation_summaries(rows, "designer", reader_designer_id=designer_id)


def list_conversations_for_company(company_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM conversations WHERE company_id = ? ORDER BY last_message_at DESC, id DESC", (company_id,)
    ).fetchall()
    c.close()
    return _conversation_summaries(rows, "employer")


def list_messages(conversation_id: int) -> list[dict]:
    # id, not created_at — see the note in _conversation_summaries above.
    c = _conn()
    rows = c.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def create_message(conversation_id: int, sender_type: str, sender_designer_id: int | None,
                    sender_employer_id: int | None, body: str) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    c = _conn()
    cur = c.execute(
        "INSERT INTO messages (conversation_id, sender_type, sender_designer_id, sender_employer_id, "
        "body, created_at, read_at) VALUES (?, ?, ?, ?, ?, ?, '')",
        (conversation_id, sender_type, sender_designer_id, sender_employer_id, body, now),
    )
    c.execute("UPDATE conversations SET last_message_at = ? WHERE id = ?", (now, conversation_id))
    c.commit()
    message_id = cur.lastrowid
    c.close()
    return message_id


def mark_conversation_read(conversation_id: int, reader_type: str, reader_designer_id: int | None = None) -> None:
    """Marks every message NOT sent by the reader as read — i.e. opening a
    thread clears the other side's unread count, never your own messages.
    On a peer conversation, sender_type is 'designer' on both sides, so
    reader_designer_id is what actually tells the two apart (see
    _conversation_summaries for the same distinction)."""
    c = _conn()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conv = c.execute("SELECT peer_designer_id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if conv and conv["peer_designer_id"] is not None and reader_designer_id is not None:
        c.execute(
            "UPDATE messages SET read_at = ? WHERE conversation_id = ? AND sender_designer_id != ? AND read_at = ''",
            (now, conversation_id, reader_designer_id),
        )
    else:
        c.execute(
            "UPDATE messages SET read_at = ? WHERE conversation_id = ? AND sender_type != ? AND read_at = ''",
            (now, conversation_id, reader_type),
        )
    c.commit()
    c.close()


def get_message(message_id: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    c.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Community: sessions (fixed-capacity seat reservations, not calendar
# booking — see api/main.py's community section for the full behavioral
# note), questions/replies/votes/follows. Sessions are admin-authored;
# everything else is designer-authored, same ownership-scoped-query pattern
# as the rest of this module.
# ---------------------------------------------------------------------------

def create_community_session(**fields) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cols = list(fields.keys()) + ["status", "created_at"]
    placeholders = ", ".join("?" * len(cols))
    values = list(fields.values()) + ["scheduled", now]
    c = _conn()
    cur = c.execute(
        f"INSERT INTO community_sessions ({', '.join(cols)}) VALUES ({placeholders})", values
    )
    c.commit()
    session_id = cur.lastrowid
    c.close()
    return session_id


def list_community_sessions(status: str | None = None) -> list[dict]:
    c = _conn()
    if status == "upcoming":
        now = datetime.now(timezone.utc).date().isoformat()
        rows = c.execute(
            "SELECT * FROM community_sessions WHERE status = 'scheduled' AND session_date >= ? ORDER BY session_date",
            (now,),
        ).fetchall()
    elif status == "past":
        now = datetime.now(timezone.utc).date().isoformat()
        rows = c.execute(
            "SELECT * FROM community_sessions WHERE session_date < ? OR status != 'scheduled' ORDER BY session_date DESC",
            (now,),
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM community_sessions ORDER BY session_date DESC").fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_community_session(session_id: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM community_sessions WHERE id = ?", (session_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def update_community_session(session_id: int, **fields) -> bool:
    if not fields:
        return False
    c = _conn()
    cols = ", ".join(f"{k} = ?" for k in fields)
    cur = c.execute(f"UPDATE community_sessions SET {cols} WHERE id = ?", (*fields.values(), session_id))
    updated = cur.rowcount > 0
    c.commit()
    c.close()
    return updated


def set_session_status(session_id: int, status: str) -> None:
    c = _conn()
    c.execute("UPDATE community_sessions SET status = ? WHERE id = ?", (status, session_id))
    c.commit()
    c.close()


def count_session_seats(session_id: int) -> dict:
    c = _conn()
    taken = c.execute(
        "SELECT COUNT(*) FROM session_bookings WHERE session_id = ? AND status = 'booked'", (session_id,)
    ).fetchone()[0]
    waitlisted = c.execute(
        "SELECT COUNT(*) FROM session_bookings WHERE session_id = ? AND status = 'waitlisted'", (session_id,)
    ).fetchone()[0]
    c.close()
    return {"taken": taken, "waitlisted": waitlisted}


def get_booking(session_id: int, designer_id: int) -> dict | None:
    c = _conn()
    row = c.execute(
        "SELECT * FROM session_bookings WHERE session_id = ? AND designer_id = ? AND status != 'cancelled'",
        (session_id, designer_id),
    ).fetchone()
    c.close()
    return dict(row) if row else None


def book_session(session_id: int, designer_id: int, seats: int) -> str:
    """Returns 'booked' or 'waitlisted'. A cancelled row for the same pair is
    reused (UPDATE) rather than inserted again, since (session_id,
    designer_id) is unique — someone who cancels and rejoins gets a fresh
    queue position either way, decided by count_session_seats at call time."""
    c = _conn()
    taken = c.execute(
        "SELECT COUNT(*) FROM session_bookings WHERE session_id = ? AND status = 'booked'", (session_id,)
    ).fetchone()[0]
    new_status = "booked" if taken < seats else "waitlisted"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = c.execute(
        "SELECT id FROM session_bookings WHERE session_id = ? AND designer_id = ?", (session_id, designer_id)
    ).fetchone()
    if existing:
        c.execute(
            "UPDATE session_bookings SET status = ?, created_at = ? WHERE id = ?",
            (new_status, now, existing["id"]),
        )
    else:
        c.execute(
            "INSERT INTO session_bookings (session_id, designer_id, status, created_at) VALUES (?, ?, ?, ?)",
            (session_id, designer_id, new_status, now),
        )
    c.commit()
    c.close()
    return new_status


def cancel_booking(session_id: int, designer_id: int) -> None:
    c = _conn()
    c.execute(
        "UPDATE session_bookings SET status = 'cancelled' WHERE session_id = ? AND designer_id = ?",
        (session_id, designer_id),
    )
    c.commit()
    c.close()
    # Promote the earliest waitlisted booking into the freed seat, if any.
    c = _conn()
    next_up = c.execute(
        "SELECT id FROM session_bookings WHERE session_id = ? AND status = 'waitlisted' ORDER BY id LIMIT 1",
        (session_id,),
    ).fetchone()
    if next_up:
        c.execute("UPDATE session_bookings SET status = 'booked' WHERE id = ?", (next_up["id"],))
        c.commit()
    c.close()


def list_session_bookings(session_id: int) -> list[dict]:
    """The attendee roster — every designer with a live (not cancelled)
    booking, joined out to their public display fields. Callers in
    api/main.py gate who's allowed to call this at all (must themselves be
    attending); this function itself just returns the full roster."""
    c = _conn()
    rows = c.execute(
        """
        SELECT sb.status, d.id AS designer_id, d.display_name, d.handle, d.photo_path, d.headline
        FROM session_bookings sb JOIN designers d ON d.id = sb.designer_id
        WHERE sb.session_id = ? AND sb.status IN ('booked', 'waitlisted')
        ORDER BY sb.id
        """,
        (session_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def list_bookings_for_designer(designer_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        """
        SELECT sb.status, sb.session_id, cs.*
        FROM session_bookings sb JOIN community_sessions cs ON cs.id = sb.session_id
        WHERE sb.designer_id = ? AND sb.status IN ('booked', 'waitlisted')
        ORDER BY cs.session_date
        """,
        (designer_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def create_question(designer_id: int, topic: str, title: str, body: str) -> int:
    c = _conn()
    cur = c.execute(
        "INSERT INTO community_questions (designer_id, topic, title, body, status, created_at) "
        "VALUES (?, ?, ?, ?, 'visible', ?)",
        (designer_id, topic, title, body, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    question_id = cur.lastrowid
    c.close()
    return question_id


def list_questions(topic: str | None = None, status: str = "visible") -> list[dict]:
    c = _conn()
    if topic:
        rows = c.execute(
            "SELECT * FROM community_questions WHERE status = ? AND topic = ? ORDER BY created_at DESC",
            (status, topic),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM community_questions WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_question(question_id: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM community_questions WHERE id = ?", (question_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def list_questions_by_designer(designer_id: int, limit: int = 20) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM community_questions WHERE designer_id = ? AND status = 'visible' "
        "ORDER BY created_at DESC LIMIT ?",
        (designer_id, limit),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def list_replies_by_designer(designer_id: int, limit: int = 20) -> list[dict]:
    c = _conn()
    rows = c.execute(
        """
        SELECT r.*, q.title AS question_title
        FROM community_replies r JOIN community_questions q ON q.id = r.question_id
        WHERE r.designer_id = ? AND r.status = 'visible'
        ORDER BY r.created_at DESC LIMIT ?
        """,
        (designer_id, limit),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def set_accepted_reply(question_id: int, reply_id: int | None) -> None:
    c = _conn()
    c.execute("UPDATE community_questions SET accepted_reply_id = ? WHERE id = ?", (reply_id, question_id))
    c.commit()
    c.close()


def set_question_status(question_id: int, status: str) -> None:
    c = _conn()
    c.execute("UPDATE community_questions SET status = ? WHERE id = ?", (status, question_id))
    c.commit()
    c.close()


def create_reply(question_id: int, designer_id: int, body: str) -> int:
    c = _conn()
    cur = c.execute(
        "INSERT INTO community_replies (question_id, designer_id, body, status, created_at) "
        "VALUES (?, ?, ?, 'visible', ?)",
        (question_id, designer_id, body, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    reply_id = cur.lastrowid
    c.close()
    return reply_id


def list_replies(question_id: int, sort: str = "useful") -> list[dict]:
    c = _conn()
    order = "n DESC, r.id" if sort == "useful" else "r.id DESC"
    rows = c.execute(
        f"""
        SELECT r.*, d.display_name, d.handle, d.photo_path, d.headline,
               (SELECT COUNT(*) FROM reply_votes v WHERE v.reply_id = r.id) AS n
        FROM community_replies r JOIN designers d ON d.id = r.designer_id
        WHERE r.question_id = ? AND r.status = 'visible'
        ORDER BY {order}
        """,
        (question_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_reply(reply_id: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM community_replies WHERE id = ?", (reply_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def set_reply_status(reply_id: int, status: str) -> None:
    c = _conn()
    c.execute("UPDATE community_replies SET status = ? WHERE id = ?", (status, reply_id))
    c.commit()
    c.close()


def toggle_vote(reply_id: int, designer_id: int) -> bool:
    """Returns True if the vote is now on, False if it was just removed."""
    c = _conn()
    existing = c.execute(
        "SELECT id FROM reply_votes WHERE reply_id = ? AND designer_id = ?", (reply_id, designer_id)
    ).fetchone()
    if existing:
        c.execute("DELETE FROM reply_votes WHERE id = ?", (existing["id"],))
        c.commit()
        c.close()
        return False
    c.execute(
        "INSERT INTO reply_votes (reply_id, designer_id, created_at) VALUES (?, ?, ?)",
        (reply_id, designer_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()
    return True


def toggle_follow(question_id: int, designer_id: int) -> bool:
    c = _conn()
    existing = c.execute(
        "SELECT id FROM question_follows WHERE question_id = ? AND designer_id = ?", (question_id, designer_id)
    ).fetchone()
    if existing:
        c.execute("DELETE FROM question_follows WHERE id = ?", (existing["id"],))
        c.commit()
        c.close()
        return False
    c.execute(
        "INSERT INTO question_follows (question_id, designer_id, created_at) VALUES (?, ?, ?)",
        (question_id, designer_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()
    return True


def toggle_follow_target(follower_designer_id: int, target_type: str, target_id: int) -> bool:
    """Generic follow used by Profile/Company/People — target_type is
    'designer' or 'company'. Returns the new following state."""
    c = _conn()
    existing = c.execute(
        "SELECT id FROM follows WHERE follower_designer_id = ? AND target_type = ? AND target_id = ?",
        (follower_designer_id, target_type, target_id),
    ).fetchone()
    if existing:
        c.execute("DELETE FROM follows WHERE id = ?", (existing["id"],))
        c.commit()
        c.close()
        return False
    c.execute(
        "INSERT INTO follows (follower_designer_id, target_type, target_id, created_at) VALUES (?, ?, ?, ?)",
        (follower_designer_id, target_type, target_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()
    return True


def list_followed_target_ids(follower_designer_id: int, target_type: str) -> set[int]:
    """Batch-check helper for directory/listing pages — one query instead
    of N is-following checks per card."""
    c = _conn()
    rows = c.execute(
        "SELECT target_id FROM follows WHERE follower_designer_id = ? AND target_type = ?",
        (follower_designer_id, target_type),
    ).fetchall()
    c.close()
    return {r["target_id"] for r in rows}


def list_follows_for_designer(follower_designer_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT target_type, target_id, created_at FROM follows WHERE follower_designer_id = ? ORDER BY created_at DESC",
        (follower_designer_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def count_followers(target_type: str, target_id: int) -> int:
    c = _conn()
    n = c.execute(
        "SELECT COUNT(*) FROM follows WHERE target_type = ? AND target_id = ?", (target_type, target_id)
    ).fetchone()[0]
    c.close()
    return n


def count_stale_questions(days: int = 2) -> list[dict]:
    """Questions with zero visible replies, older than `days` — the
    admin Community view's "unanswered" flag."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    c = _conn()
    rows = c.execute(
        """
        SELECT q.* FROM community_questions q
        WHERE q.status = 'visible' AND q.created_at < ?
          AND NOT EXISTS (SELECT 1 FROM community_replies r WHERE r.question_id = q.id AND r.status = 'visible')
        ORDER BY q.created_at
        """,
        (cutoff,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_reply_leaderboard(days: int = 30, limit: int = 10) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    c = _conn()
    rows = c.execute(
        """
        SELECT d.id AS designer_id, d.display_name, d.handle, d.photo_path, COUNT(*) AS n
        FROM community_replies r JOIN designers d ON d.id = r.designer_id
        WHERE r.status = 'visible' AND r.created_at >= ? AND d.status = 'approved'
        GROUP BY r.designer_id ORDER BY n DESC LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def list_work_worth_reading(limit: int = 12) -> list[dict]:
    """One project per approved designer — their single most recent
    (highest-id) upload — ordered newest first. No admin curation: this is
    a real recency query, not an editorial pick (see Phase 8 Decision 1)."""
    c = _conn()
    rows = c.execute(
        """
        SELECT d.id AS designer_id, d.display_name, d.handle, d.photo_path, d.location, d.discipline,
               p.id AS project_id, p.title, p.description, p.category, p.image_path
        FROM designer_projects p
        JOIN designers d ON d.id = p.designer_id
        WHERE d.status = 'approved'
          AND p.id = (SELECT MAX(p2.id) FROM designer_projects p2 WHERE p2.designer_id = p.designer_id)
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_pay_median(discipline: str, level: str, market: str) -> float | None:
    """Median of accepted amount_monthly_usd for this exact group — used to
    compute a new submission's outlier_check at insert time. None means no
    accepted data yet for this group (first submission, nothing to compare)."""
    c = _conn()
    rows = c.execute(
        "SELECT amount_monthly_usd FROM pay_submissions WHERE status = 'accepted' AND discipline = ? AND level = ? AND market = ?",
        (discipline, level, market),
    ).fetchall()
    c.close()
    if not rows:
        return None
    return statistics.median(r["amount_monthly_usd"] for r in rows)


def create_pay_submission(designer_id: int, discipline: str, level: str, market: str,
                           raw_currency: str, raw_amount: float, amount_monthly_usd: float,
                           outlier_check: str) -> int:
    c = _conn()
    cur = c.execute(
        "INSERT INTO pay_submissions (designer_id, discipline, level, market, raw_currency, raw_amount, "
        "amount_monthly_usd, outlier_check, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (designer_id, discipline, level, market, raw_currency, raw_amount, amount_monthly_usd,
         outlier_check, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    submission_id = cur.lastrowid
    c.close()
    return submission_id


def list_pay_ranges(discipline: str | None = None, market: str | None = None) -> list[dict]:
    """Real aggregate over accepted submissions, grouped by
    (discipline, level, market) — never selects designer_id (Phase 8
    Decision 2: pay data stays anonymous in every response)."""
    c = _conn()
    query = "SELECT discipline, level, market, amount_monthly_usd FROM pay_submissions WHERE status = 'accepted'"
    params: list = []
    if discipline:
        query += " AND discipline = ?"
        params.append(discipline)
    if market:
        query += " AND market = ?"
        params.append(market)
    rows = c.execute(query, params).fetchall()
    c.close()
    groups: dict[tuple, list] = {}
    for r in rows:
        key = (r["discipline"], r["level"], r["market"])
        groups.setdefault(key, []).append(r["amount_monthly_usd"])
    out = []
    for (disc, level, mkt), amounts in groups.items():
        out.append({
            "discipline": disc, "level": level, "market": mkt, "count": len(amounts),
            "min": min(amounts), "max": max(amounts), "median": statistics.median(amounts),
        })
    out.sort(key=lambda g: (g["discipline"], g["level"]))
    return out


def list_pay_submissions(status: str | None = None) -> list[dict]:
    c = _conn()
    if status:
        rows = c.execute(
            "SELECT * FROM pay_submissions WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM pay_submissions ORDER BY created_at DESC").fetchall()
    c.close()
    return [dict(r) for r in rows]


def set_pay_submission_status(submission_id: int, status: str) -> None:
    c = _conn()
    c.execute("UPDATE pay_submissions SET status = ? WHERE id = ?", (status, submission_id))
    c.commit()
    c.close()


def create_report(kind: str, target_id: int, reporter_designer_id: int | None,
                   reporter_employer_id: int | None, summary: str) -> int:
    c = _conn()
    cur = c.execute(
        "INSERT INTO reports (kind, target_id, reporter_designer_id, reporter_employer_id, summary, "
        "status, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
        (kind, target_id, reporter_designer_id, reporter_employer_id, summary,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    c.commit()
    report_id = cur.lastrowid
    c.close()
    return report_id


def list_reports(status: str | None = None) -> list[dict]:
    c = _conn()
    if status:
        rows = c.execute("SELECT * FROM reports WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM reports ORDER BY id DESC").fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_report(report_id: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def resolve_report(report_id: int, resolution: str) -> None:
    c = _conn()
    c.execute(
        "UPDATE reports SET status = 'resolved', resolution = ?, resolved_at = ? WHERE id = ?",
        (resolution, datetime.now(timezone.utc).isoformat(timespec="seconds"), report_id),
    )
    c.commit()
    c.close()


def set_employer_suspended(employer_id: int, suspended: bool) -> None:
    c = _conn()
    c.execute("UPDATE employers SET suspended = ? WHERE id = ?", (1 if suspended else 0, employer_id))
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
