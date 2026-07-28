"""
Optional SQLite store. The static-JSON flow in run.py doesn't need this, but
once you want history ("when did this role first appear?", "hide roles gone for
14 days") a tiny DB helps. Import and call upsert_all() from run.py if you want it.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from models import Job

DB = Path(__file__).parent / "kazi.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  title TEXT, company TEXT, url TEXT, source TEXT,
  location TEXT, country TEXT, work_type TEXT, discipline TEXT,
  level TEXT, eligibility TEXT, salary TEXT,
  posted_at TEXT,
  first_seen TEXT DEFAULT (date('now')),
  last_seen  TEXT DEFAULT (date('now'))
);
"""


def _conn():
    c = sqlite3.connect(DB)
    c.execute(SCHEMA)
    return c


def upsert_all(jobs: list[Job]) -> None:
    c = _conn()
    for j in jobs:
        c.execute("""
          INSERT INTO jobs (id,title,company,url,source,location,country,work_type,
                            discipline,level,eligibility,salary,posted_at)
          VALUES (:id,:title,:company,:url,:source,:location,:country,:work_type,
                  :discipline,:level,:eligibility,:salary,:posted_at)
          ON CONFLICT(id) DO UPDATE SET last_seen=date('now'), salary=excluded.salary,
                                        eligibility=excluded.eligibility
        """, j.to_dict())
    c.commit()
    c.close()


def active(days: int = 30) -> list[dict]:
    c = _conn()
    cur = c.execute(
        "SELECT * FROM jobs WHERE last_seen >= date('now', ?) ORDER BY first_seen DESC",
        (f"-{days} days",)
    )
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    c.close()
    return [dict(zip(cols, r)) for r in rows]
