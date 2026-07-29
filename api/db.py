"""
SQLite storage for employer-submitted job postings: the "post a job" journey.
Raw sqlite3, no ORM, matching scraper/store.py's style.

Every submission starts as 'pending' and is invisible to the public feed until
an admin approves it via /api/admin/submissions/{id}/approve. This is the only
gate against spam/fake listings, since submission itself is unauthenticated.
"""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

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
    c.execute(SCHEMA)
    for stmt in _MIGRATIONS:
        try:
            c.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    c.commit()
    return c


def init_db() -> None:
    _conn().close()


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
