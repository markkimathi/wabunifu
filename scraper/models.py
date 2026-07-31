"""Canonical job schema. Everything the pipeline produces conforms to this."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
import hashlib

# The disciplines Kazi cares about. Order = display priority.
DISCIPLINES = [
    "Product Design", "UX Design", "UI Design", "Brand Design",
    "Motion Design", "Design Systems", "UX Research", "Graphic Design",
    "Game Design", "Sound Design", "Instructional Design",
    "Fashion Design", "Interior Design",
]

# Eligibility badges. This is Kazi's signature feature; see pipeline/eligibility.py
ELIGIBILITY = {"kenya", "africa", "world", "check"}

# Listings older than this never show up, scraped or employer-submitted.
# Enforced both when the scraper writes web/jobs.json (run.py) and again on
# every /api/jobs request (api/main.py), so the cutoff holds live even if a
# scrape run is skipped or stale.
MAX_AGE_DAYS = 60


@dataclass
class Job:
    title: str
    company: str
    url: str                 # deep link to the ORIGINAL post; Kazi never hosts applications
    source: str              # "Greenhouse", "Lever", "BrighterMonday", ...
    location: str = ""       # human string, e.g. "Nairobi, Kenya" or "Remote"
    country: str = ""        # best-effort ISO-ish country name, "" if unknown
    work_type: str = "On-site"   # Remote | Hybrid | On-site
    discipline: str = ""     # one of DISCIPLINES
    level: str = "Mid"       # Junior | Mid | Senior | Lead
    eligibility: str = "check"   # one of ELIGIBILITY
    salary: str | None = None    # raw string if disclosed, else None
    desc: str | None = None      # role description, if the source provides one
    posted_at: str = ""      # ISO date "YYYY-MM-DD"
    id: str = field(default="")

    def __post_init__(self):
        if not self.id:
            self.id = self.stable_id(self.company, self.title, self.location)
        if not self.posted_at:
            self.posted_at = date.today().isoformat()

    @staticmethod
    def stable_id(company: str, title: str, location: str) -> str:
        """Deterministic id so the same role keeps one id across runs and sources."""
        key = f"{company.lower().strip()}::{title.lower().strip()}::{location.lower().strip()}"
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    def days_ago(self) -> int:
        try:
            d = datetime.fromisoformat(self.posted_at).date()
            return max(0, (date.today() - d).days)
        except Exception:
            return 0

    def to_web(self) -> dict:
        """Shape the frontend (web/index.html) reads."""
        return {
            "id": self.id,
            "t": self.title,
            "co": self.company,
            "city": self.location or "Remote",
            "country": self.country,
            "work": self.work_type,
            "cat": self.discipline,
            "level": self.level,
            "elig": self.eligibility,
            "pay": self.salary or "Not disclosed",
            "src": self.source,
            "days": self.days_ago(),
            "url": self.url,
            "desc": self.desc,
        }

    def to_dict(self) -> dict:
        return asdict(self)
