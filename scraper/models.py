"""Canonical job schema. Everything the pipeline produces conforms to this."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
import hashlib

# The disciplines Kazi cares about. Order = display priority.
DISCIPLINES = [
    "Product Design", "UX Design", "UI Design", "Brand Design",
    "Motion Design", "Design Systems", "UX Research", "Graphic Design",
    # Digital-creative roles that sit next to design and were previously
    # unreachable: "Design Engineer" was on the exclude list, and creative
    # technology / content design had no rule at all.
    "Design Engineering", "Content Design", "Creative Technology",
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
    work_type: str = "On-site"   # Remote | Hybrid | On-site — where the work happens
    # How the work is engaged, which is a separate question. See
    # pipeline/employment.py. `employment_stated` records whether anyone
    # actually said, so the UI can stay quiet rather than assert a guess.
    employment_type: str = "Full-time"
    employment_stated: bool = False
    discipline: str = ""     # one of DISCIPLINES
    level: str = "Mid"       # Junior | Mid | Senior | Lead
    eligibility: str = "check"   # one of ELIGIBILITY
    # Who an "africa"/"kenya" badge is actually limited to. "" means genuinely
    # continent-wide; otherwise a country or sub-region ("Nigeria", "West
    # Africa"), comma-joined when a posting names several. Without this the
    # badge claimed a Nigeria-only role was open across Africa.
    eligibility_scope: str = ""
    salary: str | None = None    # raw string if disclosed, else None
    desc: str | None = None      # role description as safe structured HTML — see desc_format.py
    desc_text: str | None = None # short plain-text teaser derived from desc, for card previews
    posted_at: str = ""      # ISO date "YYYY-MM-DD"
    cross_border_note: str = ""  # employer's own words on how they hire outside their home country
    # Employer-posted roles only: whether applications are taken on Kazi rather
    # than on the employer's own site. Scraped roles can never be True — there
    # is no employer on the other end to receive one.
    accepts_applications: bool = False
    submission_id: int | None = None
    # ISO date applications close. Employer-posted roles only — no ATS we
    # scrape publishes one, so a scraped listing stays open until it ages out.
    closes_at: str = ""
    # Employer-posted extras. Scraped listings have none of these: an ATS
    # feed carries no structured skill list and no screening questions.
    skills: list[str] = field(default_factory=list)
    screening: list[str] = field(default_factory=list)
    portfolio_required: bool = False
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
            "etype": self.employment_type,
            "etype_stated": self.employment_stated,
            "cat": self.discipline,
            "level": self.level,
            "elig": self.eligibility,
            "elig_scope": self.eligibility_scope,
            "pay": self.salary or "Not disclosed",
            "src": self.source,
            "days": self.days_ago(),
            "url": self.url,
            "desc": self.desc,
            "desc_text": self.desc_text,
            "cross_border_note": self.cross_border_note,
            "closes": self.closes_at,
            "skills": self.skills,
            "screening": self.screening,
            "portfolio_required": self.portfolio_required,
            "accepts_applications": self.accepts_applications,
            "submission_id": self.submission_id,
        }

    def to_dict(self) -> dict:
        return asdict(self)
