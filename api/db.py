"""
SQLite storage for employer-submitted job postings (the "post a job" journey)
and site analytics (admin dashboard). Raw sqlite3, no ORM, matching
scraper/store.py's style.

Every submission starts as 'pending' and is invisible to the public feed until
an admin approves it via /api/admin/submissions/{id}/approve. This is the only
gate against spam/fake listings, since submission itself is unauthenticated.

Analytics tables (pageviews, search_events, apply_clicks) store no personal
data — no IP addresses, no cookies, no identifiers that could tie two visits
to the same person. Just: what page/search/apply, roughly when, device class
(mobile/tablet/desktop) and country (resolved from the request IP via a local
GeoIP lookup, see geoip.py, then the IP itself is discarded). Rows older than
ANALYTICS_RETENTION_DAYS are deleted on startup; see cleanup_stale_analytics.
"""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone

ANALYTICS_RETENTION_DAYS = 90

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
"""

# Columns added after the table first shipped. A fresh database gets them
# via SCHEMA above; an existing one (e.g. the persistent volume already
# running in production) needs them backfilled here instead, or every
# insert/select against the new columns breaks.
_MIGRATIONS = [
    "ALTER TABLE submissions ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE submissions ADD COLUMN agreed_to_terms INTEGER NOT NULL DEFAULT 0",
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


def insert_submission(data: dict) -> int:
    c = _conn()
    row = {**data, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    cur = c.execute("""
        INSERT INTO submissions
          (title, company, url, contact_email, location, work_type,
           discipline, level, eligibility, salary, description, agreed_to_terms,
           status, created_at)
        VALUES
          (:title, :company, :url, :contact_email, :location, :work_type,
           :discipline, :level, :eligibility, :salary, :description, :agreed_to_terms,
           'pending', :created_at)
    """, row)
    c.commit()
    sub_id = cur.lastrowid
    c.close()
    return sub_id


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
