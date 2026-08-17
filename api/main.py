"""
Kazi API: serves the job feed and the "post a job" employer journey.

Runs the whole site: mounts web/ as static files AND exposes /api/* on the
same origin, so there's one process to run and no CORS to configure.

  pip install -r api/requirements.txt
  KAZI_ADMIN_TOKEN=choose-something-long uvicorn api.main:app --reload --port 8000

Then open http://localhost:8000: the board, /post.html (submit a job), and
/admin.html (review queue, needs the admin token) all come from this one server.

The public board (GET /api/jobs) merges two sources:
  - scraped listings already written to web/jobs.json by scraper/run.py
  - employer submissions that an admin has approved (see db.py)
Nothing an employer submits goes live until it's approved; see db.py's note.
"""
from __future__ import annotations
import json
import os
import re
from typing import Optional
import sys
from pathlib import Path
from datetime import datetime, timezone

import bcrypt
from fastapi import (
    FastAPI, HTTPException, Header, Depends, Request, UploadFile, File, Form, BackgroundTasks,
)
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
from models import Job, DISCIPLINES, ELIGIBILITY, MAX_AGE_DAYS  # noqa: E402  (reuse the canonical schema)
from desc_format import format_description  # noqa: E402

from .db import (  # noqa: E402
    init_db, insert_submission, list_submissions, get_submission, set_status,
    log_pageview, log_search, log_apply_click, get_analytics_summary, count_profile_views,
    create_designer, get_designer, get_designer_by_email, get_designer_by_handle,
    update_designer_profile, update_designer_handle, HandleTaken, parse_multi_field,
    set_designer_photo, set_designer_email_verified, set_designer_password, set_designer_status,
    mark_onboarding_completed,
    set_designer_resume, clear_designer_resume, set_resume_visibility,
    list_designers, list_approved_designers, delete_designer, replace_designer_links,
    list_designer_links, create_session, get_session, delete_session, delete_sessions_for_designer,
    create_email_token, consume_email_token,
    list_designer_projects, count_designer_projects, create_designer_project,
    update_designer_project, set_project_image, delete_designer_project, reorder_designer_projects,
    create_company, get_company, get_company_by_slug, get_company_by_domain,
    update_company, set_company_logo, list_companies, set_company_status,
    create_employer, get_employer, get_employer_by_email, list_employers_for_company,
    count_company_owners, set_employer_email_verified, set_employer_password,
    update_employer_profile, approve_pending_employer, delete_employer,
    create_employer_session, get_employer_session, delete_employer_session,
    delete_employer_sessions_for_employer,
    create_employer_email_token, consume_employer_email_token,
    update_submission, list_submissions_for_company,
    create_team_invite, get_team_invite_by_token, list_pending_team_invites, set_invite_status,
    create_applicant, list_applicants_for_submission, count_applicants_by_submission,
    update_applicant, set_applicant_stage, delete_applicant,
    save_job, list_saved_jobs, unsave_job,
    get_or_create_conversation, get_conversation, list_conversations_for_designer,
    list_conversations_for_company, list_messages, create_message, mark_conversation_read,
)
from . import geoip  # noqa: E402
from . import ats_check  # noqa: E402
from . import photo as photo_module  # noqa: E402
from . import project_image as project_image_module  # noqa: E402
from . import email as email_module  # noqa: E402

WEB_DIR = ROOT / "web"
JOBS_JSON = WEB_DIR / "jobs.json"
WORK_TYPES = {"Remote", "Hybrid", "On-site"}
LEVELS = {"Junior", "Mid", "Senior", "Lead"}
APPLICANT_STAGES = ["Applied", "Reviewing", "Interviewing", "Offer"]
MAX_LINKS = 8
AVAILABILITY_STATUSES = ["Available", "Open to offers", "Not available"]
MAX_DESIGNER_DISCIPLINES = 5
MAX_DESIGNER_SKILLS = 7

# Built-in avatars a designer can pick instead of uploading a photo — served
# straight out of web/avatars (avatar-1.png .. avatar-28.png). avatar-1.png
# doubles as the default shown everywhere a designer hasn't set photo_path
# at all (frontend applies that fallback; the backend just stores whatever
# path, uploaded or picked, was last set).
AVATAR_COUNT = 28
DEFAULT_AVATAR_PATH = "/avatars/avatar-1.png"


def _valid_avatar_path(path: str) -> bool:
    if not path.startswith("/avatars/avatar-") or not path.endswith(".png"):
        return False
    middle = path[len("/avatars/avatar-"):-len(".png")]
    return middle.isdigit() and 1 <= int(middle) <= AVATAR_COUNT

# Designer profile handles (the @name used in public profile URLs instead of
# a numeric id). Must start with a letter — this also guarantees a handle
# can never be all-digits, so a lookup can always tell a handle from an id
# just by checking .isdigit(). "me" is reserved because /api/designers/me
# is a literal route registered ahead of the dynamic /{identifier} one.
HANDLE_RE = re.compile(r"^[a-z][a-z0-9_]{2,29}$")
RESERVED_HANDLES = {"me"}

# Dev default so `uvicorn api.main:app` works out of the box. Set a real
# KAZI_ADMIN_TOKEN env var before deploying anywhere reachable; anyone with
# this token can approve/reject submissions.
ADMIN_TOKEN = os.environ.get("KAZI_ADMIN_TOKEN", "dev-only-change-me")

# Profile photos live next to the SQLite file (same persistent volume in
# production, /data — see fly.toml), so they survive redeploys the same way
# employer submissions already do.
PHOTOS_DIR = Path(os.environ.get("KAZI_DB_PATH", str(Path(__file__).parent / "kazi_submissions.db"))).parent / "photos"

# Resumes live alongside photos on the same persistent volume, same reason.
# Unlike photos, the original bytes are kept as-is (no re-encoding — the
# stored file is what ats_check.analyze() reads text out of on every future
# "Check Against Your Resume" click, so it has to stay a real PDF/DOCX).
RESUMES_DIR = Path(os.environ.get("KAZI_DB_PATH", str(Path(__file__).parent / "kazi_submissions.db"))).parent / "resumes"
MAX_RESUME_BYTES = 5 * 1024 * 1024

# Featured-project cover images, same volume, same reason — unlike photos
# and resumes (one file per designer, fixed name) a designer can have up to
# MAX_PROJECTS covers at once, so files are named "{designer_id}_{project_id}.jpg".
PROJECTS_DIR = Path(os.environ.get("KAZI_DB_PATH", str(Path(__file__).parent / "kazi_submissions.db"))).parent / "projects"
MAX_PROJECTS = 6

# Company logos, same volume, same reason — one file per company, fixed name.
LOGOS_DIR = Path(os.environ.get("KAZI_DB_PATH", str(Path(__file__).parent / "kazi_submissions.db"))).parent / "logos"


def classify_device(user_agent: str) -> str:
    """mobile | tablet | desktop, from a plain User-Agent string check —
    phones and tablets both say "Android"/mention touch, so the standard
    signal is that phone UAs additionally include the literal "Mobile"
    token and tablet UAs don't."""
    ua = user_agent or ""
    if "iPad" in ua or ("Android" in ua and "Mobile" not in ua):
        return "tablet"
    if "iPhone" in ua or "iPod" in ua or "Windows Phone" in ua or "Mobi" in ua:
        return "mobile"
    return "desktop"


def client_ip(request: Request) -> str:
    """Best-effort real visitor IP behind Fly's edge proxy. Used only for an
    in-process GeoIP lookup at request time — never stored (see db.py)."""
    fly_ip = request.headers.get("fly-client-ip")
    if fly_ip:
        return fly_ip
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""

app = FastAPI(title="Kazi API")
init_db()


class JobSubmission(BaseModel):
    title: str
    company: str
    url: str                 # where candidates actually apply; Kazi never hosts applications
    contact_email: str       # ours only, never shown on the public board
    description: str
    location: str = ""
    work_type: str = "On-site"
    discipline: str
    level: str = "Mid"
    eligibility: str
    salary: Optional[str] = None
    agreed_to_terms: bool = False

    @field_validator("title", "company", "url", "contact_email", "discipline", "eligibility", "description")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("work_type")
    @classmethod
    def _valid_work_type(cls, v: str) -> str:
        if v not in WORK_TYPES:
            raise ValueError(f"work_type must be one of {sorted(WORK_TYPES)}")
        return v

    @field_validator("level")
    @classmethod
    def _valid_level(cls, v: str) -> str:
        if v not in LEVELS:
            raise ValueError(f"level must be one of {sorted(LEVELS)}")
        return v

    @field_validator("discipline")
    @classmethod
    def _valid_discipline(cls, v: str) -> str:
        if v not in DISCIPLINES:
            raise ValueError(f"discipline must be one of {DISCIPLINES}")
        return v

    @field_validator("eligibility")
    @classmethod
    def _valid_eligibility(cls, v: str) -> str:
        if v not in ELIGIBILITY:
            raise ValueError(f"eligibility must be one of {sorted(ELIGIBILITY)}")
        return v

    @field_validator("agreed_to_terms")
    @classmethod
    def _must_agree(cls, v: bool) -> bool:
        if not v:
            raise ValueError("must agree to the posting guidelines")
        return v


class PageviewIn(BaseModel):
    path: str


class SearchIn(BaseModel):
    query: str


class ApplyIn(BaseModel):
    job_id: str
    job_title: str
    company: str


class DesignerSignup(BaseModel):
    email: str
    password: str
    display_name: str
    honeypot: str = ""  # left blank by real users; a filled-in value means a bot

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("enter a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def _valid_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v

    @field_validator("display_name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("enter your name")
        return v.strip()


class DesignerLogin(BaseModel):
    email: str
    password: str


class ForgotPasswordIn(BaseModel):
    email: str


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _valid_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


class VerifyCodeIn(BaseModel):
    code: str


class ProfileUpdate(BaseModel):
    display_name: str
    bio: str = ""
    discipline: list[str] = []
    skills: list[str] = []
    location: str = ""
    phone: str = ""
    contact_email: str = ""
    headline: str = ""
    years_experience: str = ""
    availability_status: str = ""

    @field_validator("display_name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("enter your name")
        return v.strip()

    @field_validator("discipline")
    @classmethod
    def _valid_discipline_list(cls, v: list[str]) -> list[str]:
        cleaned = []
        for item in v:
            item = item.strip()
            if item and item not in cleaned:
                cleaned.append(item)
        if len(cleaned) > MAX_DESIGNER_DISCIPLINES:
            raise ValueError(f"choose up to {MAX_DESIGNER_DISCIPLINES} job functions")
        return cleaned

    @field_validator("skills")
    @classmethod
    def _valid_skills_list(cls, v: list[str]) -> list[str]:
        cleaned = []
        seen = set()
        for item in v:
            item = item.strip()[:40]
            key = item.lower()
            if item and key not in seen:
                seen.add(key)
                cleaned.append(item)
        if len(cleaned) > MAX_DESIGNER_SKILLS:
            raise ValueError(f"choose up to {MAX_DESIGNER_SKILLS} skills")
        return cleaned

    @field_validator("phone")
    @classmethod
    def _clean_phone(cls, v: str) -> str:
        # Belt-and-suspenders: the wizard now composes this from a country
        # code select + digits-only number field, but strip anything that
        # isn't a digit/+/space anyway in case a client ever sends something
        # freeform (or an old stray value gets re-submitted unchanged).
        return re.sub(r"[^\d+ ]", "", v).strip()

    @field_validator("headline")
    @classmethod
    def _clean_headline(cls, v: str) -> str:
        return v.strip()[:120]

    @field_validator("years_experience")
    @classmethod
    def _clean_years_experience(cls, v: str) -> str:
        return v.strip()[:20]


class AvatarSelect(BaseModel):
    photo_path: str


class ResumeVisibility(BaseModel):
    public: bool


class AtsCheckIn(BaseModel):
    job_description: str = ""


class HandleUpdate(BaseModel):
    handle: str

    @field_validator("handle")
    @classmethod
    def _valid_handle(cls, v: str) -> str:
        v = v.strip().lstrip("@").lower()
        if not HANDLE_RE.match(v) or v in RESERVED_HANDLES:
            raise ValueError("handle must be 3-30 characters: lowercase letters, numbers, and underscores, starting with a letter")
        return v


class LinkItem(BaseModel):
    label: str
    url: str

    @field_validator("label")
    @classmethod
    def _valid_label(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("every link needs a label")
        return v.strip()[:60]

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("http://") and not v.startswith("https://"):
            raise ValueError("links must start with http:// or https://")
        return v[:500]


class LinksUpdate(BaseModel):
    links: list[LinkItem]

    @field_validator("links")
    @classmethod
    def _valid_count(cls, v: list[LinkItem]) -> list[LinkItem]:
        if len(v) > MAX_LINKS:
            raise ValueError(f"no more than {MAX_LINKS} links")
        return v


class ProjectIn(BaseModel):
    title: str
    description: str = ""
    url: str = ""
    category: str = ""

    @field_validator("title")
    @classmethod
    def _valid_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("every project needs a title")
        return v.strip()[:80]

    @field_validator("description")
    @classmethod
    def _clean_description(cls, v: str) -> str:
        return (v or "").strip()[:300]

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not v.startswith("http://") and not v.startswith("https://"):
            raise ValueError("project links must start with http:// or https://")
        return v[:500]

    @field_validator("category")
    @classmethod
    def _clean_category(cls, v: str) -> str:
        return (v or "").strip()[:40]


class ProjectReorder(BaseModel):
    ids: list[int]


# ---------------------------------------------------------------------------
# Employer accounts. Mirrors the designer models above field-for-field where
# the shape matches (email/password validation, honeypot); EmployerSignup
# is a single flat payload for the whole onboarding wizard (company + first
# employer are created together, since one can't exist without the other)
# rather than a bare display_name like DesignerSignup — everything else on a
# designer account is added later via PUT /me, but a company has no
# "unowned" state to sit in between signup and the first PUT.
# ---------------------------------------------------------------------------

class EmployerSignup(BaseModel):
    email: str
    password: str
    full_name: str
    role_title: str = ""
    company_name: str
    company_website: str = ""
    company_blurb: str = ""
    eligibility: str = ""
    eligibility_note: str = ""
    honeypot: str = ""

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("enter a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def _valid_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v

    @field_validator("full_name")
    @classmethod
    def _valid_full_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("enter your name")
        return v.strip()

    @field_validator("company_name")
    @classmethod
    def _valid_company_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("enter the company name")
        return v.strip()[:120]

    @field_validator("eligibility")
    @classmethod
    def _valid_eligibility(cls, v: str) -> str:
        if v and v not in ELIGIBILITY:
            raise ValueError(f"eligibility must be one of {sorted(ELIGIBILITY)}")
        return v


class EmployerLogin(BaseModel):
    email: str
    password: str


class EmployerProfileUpdate(BaseModel):
    full_name: str
    role_title: str = ""

    @field_validator("full_name")
    @classmethod
    def _valid_full_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("enter your name")
        return v.strip()


class CompanyUpdate(BaseModel):
    name: str
    website: str = ""
    blurb: str = ""
    eligibility: str = ""
    eligibility_note: str = ""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("enter the company name")
        return v.strip()[:120]

    @field_validator("eligibility")
    @classmethod
    def _valid_eligibility(cls, v: str) -> str:
        if v and v not in ELIGIBILITY:
            raise ValueError(f"eligibility must be one of {sorted(ELIGIBILITY)}")
        return v


# Invite-able roles only — "owner" is never proposed through an invite, only
# created at signup (or, in a later version, an explicit ownership transfer
# that doesn't exist yet).
INVITE_TEAM_ROLES = {"can_post", "can_view"}


class TeamInviteIn(BaseModel):
    email: str
    team_role: str = "can_post"

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("enter a valid email address")
        return v

    @field_validator("team_role")
    @classmethod
    def _valid_team_role(cls, v: str) -> str:
        if v not in INVITE_TEAM_ROLES:
            raise ValueError(f"team_role must be one of {sorted(INVITE_TEAM_ROLES)}")
        return v


class TeamInviteAccept(BaseModel):
    full_name: str
    password: str

    @field_validator("full_name")
    @classmethod
    def _valid_full_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("enter your name")
        return v.strip()

    @field_validator("password")
    @classmethod
    def _valid_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


class ApplicantIn(BaseModel):
    full_name: str
    email: str = ""
    location: str = ""
    portfolio_url: str = ""
    note: str = ""

    @field_validator("full_name")
    @classmethod
    def _valid_full_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("enter a name")
        return v.strip()[:120]

    @field_validator("email", "location", "note")
    @classmethod
    def _trim(cls, v: str) -> str:
        return (v or "").strip()[:300]

    @field_validator("portfolio_url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not v.startswith("http://") and not v.startswith("https://"):
            raise ValueError("portfolio link must start with http:// or https://")
        return v[:500]


class ApplicantStageUpdate(BaseModel):
    stage: int

    @field_validator("stage")
    @classmethod
    def _valid_stage(cls, v: int) -> int:
        if v < 0 or v >= len(APPLICANT_STAGES):
            raise ValueError(f"stage must be 0-{len(APPLICANT_STAGES) - 1}")
        return v


class SaveJobIn(BaseModel):
    job_id: str
    title: str
    company: str
    location: str = ""
    eligibility: str = ""
    url: str = ""


class MessageIn(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def _valid_body(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("a message can't be empty")
        return v[:4000]


class DesignerConversationStart(BaseModel):
    company_id: int
    body: str

    @field_validator("body")
    @classmethod
    def _valid_body(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("a message can't be empty")
        return v[:4000]


class EmployerConversationStart(BaseModel):
    designer_id: int
    body: str

    @field_validator("body")
    @classmethod
    def _valid_body(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("a message can't be empty")
        return v[:4000]


def require_admin(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(401, "invalid or missing admin token")


def require_designer(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    session = get_session(token) if token else None
    if not session:
        raise HTTPException(401, "invalid or expired session")
    designer = get_designer(session["designer_id"])
    if not designer:
        raise HTTPException(401, "invalid session")
    return designer


def require_employer(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    session = get_employer_session(token) if token else None
    if not session:
        raise HTTPException(401, "invalid or expired session")
    employer = get_employer(session["employer_id"])
    if not employer:
        raise HTTPException(401, "invalid session")
    return employer


def require_employer_role(*allowed: str):
    """Gates write endpoints by team_role. A pending (not-yet-approved)
    teammate is blocked from every write regardless of the proposed role
    they were invited at — approval has to land first. Permission model:
    owner does everything; can_post manages listings/applicants and can send
    invites (their invitees always need an owner's approval) but can't touch
    the company record or approve/remove teammates; can_view is read-only
    everywhere, enforced simply by never being in an `allowed` list."""
    def _dep(employer: dict = Depends(require_employer)) -> dict:
        if employer["is_pending_approval"] or employer["team_role"] not in allowed:
            raise HTTPException(403, "not permitted for this account")
        return employer
    return _dep


@app.post("/api/submissions")
def submit_job(payload: JobSubmission):
    sub_id = insert_submission(payload.model_dump())
    return {"ok": True, "id": sub_id, "status": "pending"}


def _combined_jobs() -> tuple[list[dict], str | None]:
    scraped: list[dict] = []
    generated_at = None
    if JOBS_JSON.exists():
        data = json.loads(JOBS_JSON.read_text())
        scraped = data.get("jobs", [])
        generated_at = data.get("generated_at")

    approved = list_submissions(status="approved")
    employer_jobs = []
    for s in approved:
        # Employer submissions come from a plain <textarea> — always
        # heuristically reformatted, never treated as real HTML.
        desc_html, desc_text = format_description(s.get("description", ""), is_html=False)
        employer_jobs.append(
            Job(
                title=s["title"], company=s["company"], url=s["url"], source="Direct",
                location=s["location"], work_type=s["work_type"], discipline=s["discipline"],
                level=s["level"], eligibility=s["eligibility"], salary=s.get("salary"),
                desc=desc_html or None, desc_text=desc_text or None, posted_at=s["created_at"][:10],
            ).to_web()
        )

    # Enforced again here (not just at scrape time) so the cutoff holds live
    # even if a scrape run is skipped, and so it also applies to employer
    # submissions, which run.py never sees.
    combined = [j for j in employer_jobs + scraped if j["days"] <= MAX_AGE_DAYS]
    combined.sort(key=lambda j: j["days"])
    return combined, generated_at


@app.get("/api/jobs")
def get_jobs():
    combined, generated_at = _combined_jobs()
    return {
        "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
        "count": len(combined),
        "jobs": combined,
    }


@app.get("/api/jobs/count")
def get_jobs_count():
    """Lightweight sibling of /api/jobs for the header's live role count,
    which every page shows (not just the jobs listing) — avoids shipping the
    full jobs payload site-wide just to read its length."""
    combined, generated_at = _combined_jobs()
    return {
        "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
        "count": len(combined),
    }


# Registered after /api/jobs/count (a literal path) so "count" is never
# mistaken for a job id, same "/me before /{identifier}" pattern used for
# designers below.
@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    combined, _ = _combined_jobs()
    for j in combined:
        if j["id"] == job_id:
            return j
    raise HTTPException(404, "no such job")


@app.get("/api/admin/submissions")
def admin_list(status: str = "pending", _: None = Depends(require_admin)):
    return list_submissions(status=status if status != "all" else None)


@app.post("/api/admin/submissions/{sub_id}/approve")
def admin_approve(sub_id: int, _: None = Depends(require_admin)):
    if not get_submission(sub_id):
        raise HTTPException(404, "no such submission")
    set_status(sub_id, "approved")
    return {"ok": True}


@app.post("/api/admin/submissions/{sub_id}/reject")
def admin_reject(sub_id: int, _: None = Depends(require_admin)):
    if not get_submission(sub_id):
        raise HTTPException(404, "no such submission")
    set_status(sub_id, "rejected")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Analytics: three fire-and-forget beacons the frontend calls (see nav.js /
# index.html), plus the aggregate dashboard the admin page reads. No auth on
# the beacons — they're called from every visitor's browser, not just admins.
# ---------------------------------------------------------------------------

@app.post("/api/track/pageview")
def track_pageview(payload: PageviewIn, request: Request):
    device = classify_device(request.headers.get("user-agent", ""))
    country = geoip.lookup_country(client_ip(request))
    log_pageview(payload.path.strip()[:200] or "/", device, country)
    return {"ok": True}


@app.post("/api/track/search")
def track_search(payload: SearchIn, request: Request):
    query = payload.query.strip()
    if not query:
        return {"ok": True}
    device = classify_device(request.headers.get("user-agent", ""))
    country = geoip.lookup_country(client_ip(request))
    log_search(query[:200], device, country)
    return {"ok": True}


@app.post("/api/track/apply")
def track_apply(payload: ApplyIn, request: Request):
    device = classify_device(request.headers.get("user-agent", ""))
    country = geoip.lookup_country(client_ip(request))
    log_apply_click(
        payload.job_id.strip()[:100],
        payload.job_title.strip()[:200],
        payload.company.strip()[:200],
        device, country,
    )
    return {"ok": True}


@app.get("/api/admin/analytics")
def admin_analytics(days: int = 30, _: None = Depends(require_admin)):
    return get_analytics_summary(days=max(1, min(days, 365)))


@app.post("/api/ats/check")
async def ats_check_endpoint(file: UploadFile = File(...), job_description: str = Form("")):
    # Parsed entirely in memory and never persisted — see ats_check.py's
    # module docstring. `data` goes out of scope (and is garbage collected)
    # once this request finishes.
    data = await file.read()
    try:
        return ats_check.analyze(file.filename or "", data, job_description)
    except ats_check.UnsupportedFile as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Designer accounts: signup/login/password-reset, profile editing, and the
# public directory. Real per-account auth (require_designer) sits alongside
# the single shared admin token — the two are unrelated. Literal /me routes
# are registered before the dynamic /{designer_id} route further down so
# "me" is never mistaken for an id.
# ---------------------------------------------------------------------------

def _designer_public(d: dict) -> dict:
    """Strip private fields (login email, password_hash) before this ever
    reaches a public response — used for both the directory and
    single-profile endpoints so there's exactly one place that decides
    what's public. phone/contact_email ARE included here on purpose: unlike
    the login email, they're an opt-in field a designer fills in specifically
    so employers can reach them, same as the rest of the profile."""
    out = {
        "id": d["id"], "display_name": d["display_name"], "bio": d["bio"],
        "discipline": parse_multi_field(d["discipline"]), "location": d["location"],
        "photo_path": d["photo_path"], "created_at": d["created_at"],
        "phone": d.get("phone", ""), "contact_email": d.get("contact_email", ""),
        "handle": d.get("handle", ""),
        "headline": d.get("headline", ""), "years_experience": d.get("years_experience", ""),
        "availability_status": d.get("availability_status", ""),
        "skills": parse_multi_field(d.get("skills")),
        "links": list_designer_links(d["id"]),
        "projects": list_designer_projects(d["id"]),
    }
    # Resumes are opt-in public (default private) — only surface them here
    # (the directory/public-profile view) when the designer has switched
    # resume_public on. designer_me() below always includes them regardless,
    # since the owner can always see their own.
    if d.get("resume_public") and d.get("resume_filename"):
        out["resume_filename"] = d["resume_filename"]
        out["resume_uploaded_at"] = d["resume_uploaded_at"]
        out["resume_url"] = f"/api/designers/{d.get('handle') or d['id']}/resume/download"
    return out


@app.post("/api/designers/signup")
def designer_signup(payload: DesignerSignup, background: BackgroundTasks):
    if payload.honeypot:
        # Silently succeed so a bot can't tell it was rejected, without
        # actually creating an account.
        return {"ok": True}
    if get_designer_by_email(payload.email):
        raise HTTPException(400, "An account with this email already exists.")
    password_hash = bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode()
    designer_id = create_designer(payload.email, password_hash, payload.display_name)
    token = create_email_token(designer_id, "verify")
    background.add_task(email_module.send_verification_email, payload.email, token)
    session_token = create_session(designer_id)
    return {"ok": True, "token": session_token}


@app.post("/api/designers/login")
def designer_login(payload: DesignerLogin):
    designer = get_designer_by_email(payload.email.strip().lower())
    if not designer or not bcrypt.checkpw(payload.password.encode(), designer["password_hash"].encode()):
        raise HTTPException(401, "Incorrect email or password.")
    return {"ok": True, "token": create_session(designer["id"])}


@app.post("/api/designers/logout")
def designer_logout(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    if token:
        delete_session(token)
    return {"ok": True}


@app.post("/api/designers/me/verify-email")
def designer_verify_email(payload: VerifyCodeIn, designer: dict = Depends(require_designer)):
    # Scoped to the logged-in session (rather than a bare anonymous token
    # lookup) since a 6-digit code has a much smaller guess-space than the
    # 256-bit link tokens used elsewhere — this closes off anonymous
    # brute-forcing: an attacker needs their own valid session first, and
    # can only guess against the one account it belongs to.
    designer_id = consume_email_token(payload.code.strip(), "verify")
    if not designer_id or designer_id != designer["id"]:
        raise HTTPException(400, "That code is incorrect or has expired.")
    set_designer_email_verified(designer_id)
    return {"ok": True}


@app.post("/api/designers/me/resend-verification")
def designer_resend_verification(background: BackgroundTasks, designer: dict = Depends(require_designer)):
    if designer["email_verified"]:
        return {"ok": True}
    token = create_email_token(designer["id"], "verify")
    background.add_task(email_module.send_verification_email, designer["email"], token)
    return {"ok": True}


@app.post("/api/designers/forgot-password")
def designer_forgot_password(payload: ForgotPasswordIn, background: BackgroundTasks):
    designer = get_designer_by_email(payload.email.strip().lower())
    if designer:
        token = create_email_token(designer["id"], "reset")
        background.add_task(email_module.send_password_reset_email, designer["email"], token)
    # Same response either way — don't leak whether an email is registered.
    return {"ok": True}


@app.post("/api/designers/reset-password")
def designer_reset_password(payload: ResetPasswordIn):
    designer_id = consume_email_token(payload.token, "reset")
    if not designer_id:
        raise HTTPException(400, "This reset link is invalid or has expired.")
    password_hash = bcrypt.hashpw(payload.new_password.encode(), bcrypt.gensalt()).decode()
    set_designer_password(designer_id, password_hash)
    # A password reset might mean the account was compromised — invalidate
    # every other active session rather than leaving a stolen token valid.
    delete_sessions_for_designer(designer_id)
    return {"ok": True}


@app.get("/api/designers/me")
def designer_me(designer: dict = Depends(require_designer)):
    return {**_designer_public(designer), "email": designer["email"],
            "email_verified": bool(designer["email_verified"]), "status": designer["status"],
            "onboarding_completed": bool(designer["onboarding_completed"]),
            # Always present for the owner's own view, regardless of
            # resume_public — _designer_public() only includes these when
            # public, since that copy is also used for the public directory.
            "resume_filename": designer.get("resume_filename", ""),
            "resume_uploaded_at": designer.get("resume_uploaded_at", ""),
            "resume_url": "/api/designers/me/resume/download" if designer.get("resume_path") else "",
            "resume_public": bool(designer.get("resume_public"))}


@app.get("/api/designers/me/stats")
def designer_stats(designer: dict = Depends(require_designer)):
    """Lightweight dashboard-stats sibling of /api/designers/me, same idea as
    /api/jobs/count next to /api/jobs — a later phase can add more keys here
    (click counts, visitor breakdowns, ...) without breaking existing callers."""
    return {"profile_views": count_profile_views(designer.get("handle", ""), designer["id"])}


@app.put("/api/designers/me")
def designer_update_me(payload: ProfileUpdate, designer: dict = Depends(require_designer)):
    bad = [d for d in payload.discipline if d not in DISCIPLINES]
    if bad:
        raise HTTPException(400, f"discipline must be one of {DISCIPLINES}")
    if payload.availability_status and payload.availability_status not in AVAILABILITY_STATUSES:
        raise HTTPException(400, f"availability_status must be one of {AVAILABILITY_STATUSES}")
    update_designer_profile(
        designer["id"], display_name=payload.display_name, bio=payload.bio.strip()[:2000],
        discipline=payload.discipline, location=payload.location.strip()[:200],
        phone=payload.phone.strip()[:40], contact_email=payload.contact_email.strip()[:200],
        headline=payload.headline, years_experience=payload.years_experience,
        availability_status=payload.availability_status, skills=payload.skills,
    )
    return {"ok": True}


@app.put("/api/designers/me/handle")
def designer_update_handle(payload: HandleUpdate, designer: dict = Depends(require_designer)):
    try:
        update_designer_handle(designer["id"], payload.handle)
    except HandleTaken:
        raise HTTPException(400, "That handle is already taken.")
    return {"ok": True, "handle": payload.handle}


@app.put("/api/designers/me/links")
def designer_update_links(payload: LinksUpdate, designer: dict = Depends(require_designer)):
    replace_designer_links(designer["id"], [link.model_dump() for link in payload.links])
    return {"ok": True}


@app.post("/api/designers/me/projects")
def designer_create_project(payload: ProjectIn, designer: dict = Depends(require_designer)):
    if count_designer_projects(designer["id"]) >= MAX_PROJECTS:
        raise HTTPException(400, f"You can feature up to {MAX_PROJECTS} projects.")
    project_id = create_designer_project(
        designer["id"], payload.title, payload.description, payload.url, payload.category
    )
    return {"ok": True, "id": project_id, "title": payload.title, "description": payload.description,
            "url": payload.url, "category": payload.category, "image_path": ""}


# Registered before /projects/{project_id} (a literal path) so "reorder"
# is never mistaken for a project id, same "/me before /{identifier}"
# pattern used throughout this file.
@app.put("/api/designers/me/projects/reorder")
def designer_reorder_projects(payload: ProjectReorder, designer: dict = Depends(require_designer)):
    reorder_designer_projects(designer["id"], payload.ids)
    return {"ok": True}


@app.put("/api/designers/me/projects/{project_id}")
def designer_update_project(project_id: int, payload: ProjectIn, designer: dict = Depends(require_designer)):
    updated = update_designer_project(
        designer["id"], project_id, payload.title, payload.description, payload.url, payload.category
    )
    if not updated:
        raise HTTPException(404, "No such project.")
    project = next(p for p in list_designer_projects(designer["id"]) if p["id"] == project_id)
    return {"ok": True, **project}


@app.delete("/api/designers/me/projects/{project_id}")
def designer_delete_project(project_id: int, designer: dict = Depends(require_designer)):
    project = next((p for p in list_designer_projects(designer["id"]) if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "No such project.")
    if project.get("image_path"):
        f = PROJECTS_DIR / Path(project["image_path"]).name
        if f.exists():
            f.unlink()
    delete_designer_project(designer["id"], project_id)
    return {"ok": True}


@app.post("/api/designers/me/projects/{project_id}/image")
async def designer_upload_project_image(project_id: int, file: UploadFile = File(...), designer: dict = Depends(require_designer)):
    if not any(p["id"] == project_id for p in list_designer_projects(designer["id"])):
        raise HTTPException(404, "No such project.")
    data = await file.read()
    try:
        jpeg_bytes = project_image_module.process_project_image(data)
    except project_image_module.UnsupportedImage as e:
        raise HTTPException(status_code=400, detail=str(e))
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{designer['id']}_{project_id}.jpg"
    (PROJECTS_DIR / stored_name).write_bytes(jpeg_bytes)
    image_path = f"/project-images/{stored_name}"
    set_project_image(designer["id"], project_id, image_path)
    return {"ok": True, "image_path": image_path}


@app.post("/api/designers/me/photo")
async def designer_upload_photo(file: UploadFile = File(...), designer: dict = Depends(require_designer)):
    data = await file.read()
    try:
        jpeg_bytes = photo_module.process_photo(data)
    except photo_module.UnsupportedPhoto as e:
        raise HTTPException(status_code=400, detail=str(e))
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    photo_path = f"{designer['id']}.jpg"
    (PHOTOS_DIR / photo_path).write_bytes(jpeg_bytes)
    set_designer_photo(designer["id"], f"/photos/{photo_path}")
    return {"ok": True, "photo_path": f"/photos/{photo_path}"}


@app.post("/api/designers/me/avatar")
def designer_select_avatar(payload: AvatarSelect, designer: dict = Depends(require_designer)):
    """Picking a built-in avatar reuses set_designer_photo() — same column,
    same re-review-on-approved-edit behavior as an uploaded photo. Selecting
    one here naturally supersedes a previously uploaded photo (and vice
    versa) since photo_path only ever holds one value at a time."""
    if not _valid_avatar_path(payload.photo_path):
        raise HTTPException(400, "Not a valid avatar.")
    set_designer_photo(designer["id"], payload.photo_path)
    return {"ok": True, "photo_path": payload.photo_path}


@app.post("/api/designers/me/resume")
async def designer_upload_resume(file: UploadFile = File(...), designer: dict = Depends(require_designer)):
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    if ext not in ats_check.ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Please upload a .pdf or .docx file.")
    data = await file.read()
    if len(data) > MAX_RESUME_BYTES:
        raise HTTPException(400, "Resume must be under 5MB.")
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{designer['id']}.{ext}"
    (RESUMES_DIR / stored_name).write_bytes(data)
    uploaded_at = datetime.now().isoformat(timespec="seconds")
    set_designer_resume(designer["id"], f"/resumes/{stored_name}", file.filename or stored_name, uploaded_at)
    return {"ok": True, "resume_filename": file.filename or stored_name, "resume_uploaded_at": uploaded_at}


@app.delete("/api/designers/me/resume")
def designer_delete_resume(designer: dict = Depends(require_designer)):
    if designer.get("resume_path"):
        f = RESUMES_DIR / Path(designer["resume_path"]).name
        if f.exists():
            f.unlink()
    clear_designer_resume(designer["id"])
    return {"ok": True}


@app.put("/api/designers/me/resume/visibility")
def designer_set_resume_visibility(payload: ResumeVisibility, designer: dict = Depends(require_designer)):
    set_resume_visibility(designer["id"], payload.public)
    return {"ok": True, "resume_public": payload.public}


def _resume_file_response(d: dict) -> FileResponse:
    f = RESUMES_DIR / Path(d["resume_path"]).name
    if not d.get("resume_path") or not f.exists():
        raise HTTPException(404, "No resume on file.")
    return FileResponse(f, filename=d["resume_filename"] or f.name)


@app.get("/api/designers/me/resume/download")
def designer_download_own_resume(designer: dict = Depends(require_designer)):
    return _resume_file_response(designer)


@app.get("/api/designers/{identifier}/resume/download")
def designer_download_public_resume(identifier: str):
    designer = get_designer(int(identifier)) if identifier.isdigit() else None
    if not designer:
        designer = get_designer_by_handle(identifier)
    if not designer or designer["status"] != "approved" or not designer.get("resume_public"):
        raise HTTPException(404, "No public resume for this designer.")
    return _resume_file_response(designer)


@app.post("/api/designers/me/ats-check")
def designer_ats_check(payload: AtsCheckIn, designer: dict = Depends(require_designer)):
    """The authenticated 'use my saved resume' counterpart to the anonymous
    /api/ats/check above — reads the stored file straight off disk and runs
    it through the exact same scoring function, so results are identical in
    shape regardless of which path produced them."""
    if not designer.get("resume_path"):
        raise HTTPException(404, "No saved resume. Upload one first.")
    f = RESUMES_DIR / Path(designer["resume_path"]).name
    if not f.exists():
        raise HTTPException(404, "No saved resume. Upload one first.")
    data = f.read_bytes()
    try:
        return ats_check.analyze(designer["resume_filename"] or f.name, data, payload.job_description)
    except ats_check.UnsupportedFile as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/designers/me/submit")
def designer_submit(designer: dict = Depends(require_designer)):
    if not designer["email_verified"]:
        raise HTTPException(400, "Please verify your email before submitting your profile for review.")
    set_designer_status(designer["id"], "pending")
    return {"ok": True}


@app.post("/api/designers/me/complete-onboarding")
def designer_complete_onboarding(designer: dict = Depends(require_designer)):
    """Called once, from the last step of the onboarding wizard — flips the
    routing flag that keeps /account and /onboarding pointed at the right
    place on future logins. Never called again after that (Edit Profile
    doesn't touch it)."""
    mark_onboarding_completed(designer["id"])
    return {"ok": True}


@app.delete("/api/designers/me")
def designer_delete_me(designer: dict = Depends(require_designer)):
    photo_file = PHOTOS_DIR / f"{designer['id']}.jpg"
    if photo_file.exists():
        photo_file.unlink()
    if designer.get("resume_path"):
        resume_file = RESUMES_DIR / Path(designer["resume_path"]).name
        if resume_file.exists():
            resume_file.unlink()
    for project in list_designer_projects(designer["id"]):
        if project.get("image_path"):
            image_file = PROJECTS_DIR / Path(project["image_path"]).name
            if image_file.exists():
                image_file.unlink()
    delete_designer(designer["id"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Saved roles, messages and matches: the genuinely new Dashboard tabs that
# have real backend behind them (Overview/Profile/Availability/Account are
# just this file's existing designer endpoints, reused as-is).
# ---------------------------------------------------------------------------

@app.get("/api/designers/me/saved-jobs")
def designer_list_saved_jobs(designer: dict = Depends(require_designer)):
    return {"jobs": list_saved_jobs(designer["id"])}


@app.post("/api/designers/me/saved-jobs")
def designer_save_job(payload: SaveJobIn, designer: dict = Depends(require_designer)):
    save_job(designer["id"], payload.job_id, payload.title, payload.company,
              payload.location, payload.eligibility, payload.url)
    return {"ok": True}


@app.delete("/api/designers/me/saved-jobs/{job_id}")
def designer_unsave_job(job_id: str, designer: dict = Depends(require_designer)):
    unsave_job(designer["id"], job_id)
    return {"ok": True}


@app.get("/api/designers/me/matches")
def designer_matches(designer: dict = Depends(require_designer)):
    """A real, plain overlap filter against the designer's own listed
    disciplines — no scoring model, no fabricated "why you matched" copy
    beyond the discipline that actually overlaps."""
    disciplines = set(parse_multi_field(designer.get("discipline")))
    combined, _ = _combined_jobs()
    if not disciplines:
        return {"jobs": []}
    matches = [j for j in combined if j["cat"] in disciplines]
    return {"jobs": matches[:20]}


def _designer_conversation_out(conv: dict) -> dict:
    company = get_company(conv["company_id"])
    return {**conv, "company_name": company["name"] if company else "", "company_slug": company["slug"] if company else ""}


@app.get("/api/designers/me/conversations")
def designer_list_conversations(designer: dict = Depends(require_designer)):
    convs = list_conversations_for_designer(designer["id"])
    return {"conversations": [_designer_conversation_out(c) for c in convs]}


@app.post("/api/designers/me/conversations")
def designer_start_conversation(payload: DesignerConversationStart, designer: dict = Depends(require_designer)):
    company = get_company(payload.company_id)
    if not company or company["status"] != "approved":
        raise HTTPException(404, "no such company")
    conversation_id = get_or_create_conversation(designer["id"], payload.company_id, "designer")
    create_message(conversation_id, "designer", designer["id"], None, payload.body)
    return {"ok": True, "conversation_id": conversation_id}


def _require_own_conversation(conversation_id: int, designer_id: int) -> dict:
    conv = get_conversation(conversation_id)
    if not conv or conv["designer_id"] != designer_id:
        raise HTTPException(404, "no such conversation")
    return conv


@app.get("/api/designers/me/conversations/{conversation_id}/messages")
def designer_get_messages(conversation_id: int, designer: dict = Depends(require_designer)):
    _require_own_conversation(conversation_id, designer["id"])
    mark_conversation_read(conversation_id, "designer")
    return {"messages": list_messages(conversation_id)}


@app.post("/api/designers/me/conversations/{conversation_id}/messages")
def designer_send_message(conversation_id: int, payload: MessageIn, designer: dict = Depends(require_designer)):
    _require_own_conversation(conversation_id, designer["id"])
    create_message(conversation_id, "designer", designer["id"], None, payload.body)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Employer accounts: signup/login/password-reset, profile editing. Real
# per-account auth (require_employer), same shape as the designer section
# above but sitting on the companies/employers tables instead. Company CRUD,
# team invites, listings and applicants land in later milestones — this
# section is just an employer's own account lifecycle.
# ---------------------------------------------------------------------------

def _employer_public(e: dict) -> dict:
    """Strip the login email/password_hash before this reaches a response
    that isn't the owner's own /me view — mirrors _designer_public()."""
    return {
        "id": e["id"], "full_name": e["full_name"], "role_title": e.get("role_title", ""),
        "team_role": e["team_role"], "created_at": e["created_at"],
    }


@app.post("/api/employers/signup")
def employer_signup(payload: EmployerSignup, background: BackgroundTasks):
    if payload.honeypot:
        return {"ok": True}
    if get_employer_by_email(payload.email):
        raise HTTPException(400, "An account with this email already exists.")
    domain = re.sub(r"^https?://", "", payload.company_website or "", flags=re.I)
    domain = re.sub(r"^www\.", "", domain, flags=re.I).split("/")[0].lower()
    if domain:
        existing = get_company_by_domain(domain)
        if existing:
            raise HTTPException(
                400,
                f"{existing['name']} is already on Kazi. Ask a teammate there to invite you instead.",
            )
    company_id = create_company(
        payload.company_name, payload.company_website, payload.company_blurb,
        payload.eligibility, payload.eligibility_note,
    )
    password_hash = bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode()
    employer_id = create_employer(
        company_id, payload.email, password_hash, payload.full_name, payload.role_title,
        team_role="owner", is_pending_approval=False,
    )
    token = create_employer_email_token(employer_id, "verify")
    background.add_task(email_module.send_employer_verification_email, payload.email, token)
    session_token = create_employer_session(employer_id)
    return {"ok": True, "token": session_token}


@app.post("/api/employers/login")
def employer_login(payload: EmployerLogin):
    employer = get_employer_by_email(payload.email.strip().lower())
    if not employer or not bcrypt.checkpw(payload.password.encode(), employer["password_hash"].encode()):
        raise HTTPException(401, "Incorrect email or password.")
    return {"ok": True, "token": create_employer_session(employer["id"])}


@app.post("/api/employers/logout")
def employer_logout(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    if token:
        delete_employer_session(token)
    return {"ok": True}


@app.post("/api/employers/me/verify-email")
def employer_verify_email(payload: VerifyCodeIn, employer: dict = Depends(require_employer)):
    employer_id = consume_employer_email_token(payload.code.strip(), "verify")
    if not employer_id or employer_id != employer["id"]:
        raise HTTPException(400, "That code is incorrect or has expired.")
    set_employer_email_verified(employer_id)
    return {"ok": True}


@app.post("/api/employers/me/resend-verification")
def employer_resend_verification(background: BackgroundTasks, employer: dict = Depends(require_employer)):
    if employer["email_verified"]:
        return {"ok": True}
    token = create_employer_email_token(employer["id"], "verify")
    background.add_task(email_module.send_employer_verification_email, employer["email"], token)
    return {"ok": True}


@app.post("/api/employers/forgot-password")
def employer_forgot_password(payload: ForgotPasswordIn, background: BackgroundTasks):
    employer = get_employer_by_email(payload.email.strip().lower())
    if employer:
        token = create_employer_email_token(employer["id"], "reset")
        background.add_task(email_module.send_employer_password_reset_email, employer["email"], token)
    return {"ok": True}


@app.post("/api/employers/reset-password")
def employer_reset_password(payload: ResetPasswordIn):
    employer_id = consume_employer_email_token(payload.token, "reset")
    if not employer_id:
        raise HTTPException(400, "This reset link is invalid or has expired.")
    password_hash = bcrypt.hashpw(payload.new_password.encode(), bcrypt.gensalt()).decode()
    set_employer_password(employer_id, password_hash)
    delete_employer_sessions_for_employer(employer_id)
    return {"ok": True}


@app.get("/api/employers/me")
def employer_me(employer: dict = Depends(require_employer)):
    company = get_company(employer["company_id"])
    return {
        **_employer_public(employer), "email": employer["email"],
        "email_verified": bool(employer["email_verified"]),
        "is_pending_approval": bool(employer["is_pending_approval"]),
        "company": company,
    }


@app.put("/api/employers/me")
def employer_update_me(payload: EmployerProfileUpdate, employer: dict = Depends(require_employer)):
    update_employer_profile(employer["id"], payload.full_name, payload.role_title)
    return {"ok": True}


@app.delete("/api/employers/me")
def employer_delete_me(employer: dict = Depends(require_employer)):
    # No ownership-transfer flow exists yet, so the last owner of a company
    # can't delete their own account out from under a company with no one
    # left to run it — a deliberate wall, not a gap.
    if employer["team_role"] == "owner" and count_company_owners(employer["company_id"], exclude_id=employer["id"]) == 0:
        raise HTTPException(400, "You're the only owner here — add another owner or contact us before closing this account.")
    delete_employer(employer["id"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Companies: the public page, and the owning employer's own edit/logo
# endpoints. Same review-queue re-review-on-edit rule as designer profiles.
# ---------------------------------------------------------------------------

def _company_listings(company: dict) -> list[dict]:
    """This company's own approved submissions, run through the same Job()
    construction _combined_jobs() uses for employer submissions generally —
    scraped jobs.json listings have no company_id at all, so they can never
    appear here; this is a company's real self-submitted listings only."""
    out = []
    for s in list_submissions_for_company(company["id"]):
        if s["status"] != "approved":
            continue
        desc_html, desc_text = format_description(s.get("description", ""), is_html=False)
        out.append(
            Job(
                title=s["title"], company=s["company"], url=s["url"], source="Direct",
                location=s["location"], work_type=s["work_type"], discipline=s["discipline"],
                level=s["level"], eligibility=s["eligibility"], salary=s.get("salary"),
                desc=desc_html or None, desc_text=desc_text or None, posted_at=s["created_at"][:10],
            ).to_web()
        )
    return out


@app.get("/api/companies/{identifier}")
def company_public_page(identifier: str):
    company = get_company(int(identifier)) if identifier.isdigit() else None
    if not company:
        company = get_company_by_slug(identifier)
    if not company or company["status"] != "approved":
        raise HTTPException(404, "no such company")
    return {**company, "listings": _company_listings(company)}


@app.put("/api/employers/me/company")
def employer_update_company(payload: CompanyUpdate, employer: dict = Depends(require_employer_role("owner"))):
    update_company(
        employer["company_id"], payload.name, payload.website, payload.blurb,
        payload.eligibility, payload.eligibility_note,
    )
    return {"ok": True}


@app.post("/api/employers/me/company/logo")
async def employer_upload_company_logo(file: UploadFile = File(...), employer: dict = Depends(require_employer_role("owner"))):
    data = await file.read()
    try:
        jpeg_bytes = photo_module.process_photo(data)
    except photo_module.UnsupportedPhoto as e:
        raise HTTPException(status_code=400, detail=str(e))
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    logo_path = f"{employer['company_id']}.jpg"
    (LOGOS_DIR / logo_path).write_bytes(jpeg_bytes)
    set_company_logo(employer["company_id"], f"/logos/{logo_path}")
    return {"ok": True, "logo_path": f"/logos/{logo_path}"}


# ---------------------------------------------------------------------------
# Listings: an employer's own submissions. POST goes through the exact same
# insert_submission() the anonymous /post.html flow uses, just stamped with
# company_id/employer_id — one review queue, not a parallel employer-only
# path. Literal ordering doesn't matter here (no "me" vs dynamic-id clash
# the way /designers/me does, since these all sit under /me/listings).
# ---------------------------------------------------------------------------

def _owned_submission(sub_id: int, company_id: int) -> dict:
    sub = get_submission(sub_id)
    if not sub or sub.get("company_id") != company_id:
        raise HTTPException(404, "no such listing")
    return sub


@app.get("/api/employers/me/listings")
def employer_list_listings(employer: dict = Depends(require_employer)):
    counts = count_applicants_by_submission(employer["company_id"])
    listings = list_submissions_for_company(employer["company_id"])
    for listing in listings:
        listing["applicant_count"] = counts.get(listing["id"], 0)
    return {"listings": listings}


@app.post("/api/employers/me/listings")
def employer_create_listing(payload: JobSubmission, employer: dict = Depends(require_employer_role("owner", "can_post"))):
    sub_id = insert_submission(payload.model_dump(), company_id=employer["company_id"], employer_id=employer["id"])
    return {"ok": True, "id": sub_id, "status": "pending"}


@app.get("/api/employers/me/listings/{sub_id}")
def employer_get_listing(sub_id: int, employer: dict = Depends(require_employer)):
    return _owned_submission(sub_id, employer["company_id"])


@app.put("/api/employers/me/listings/{sub_id}")
def employer_update_listing(sub_id: int, payload: JobSubmission, employer: dict = Depends(require_employer_role("owner", "can_post"))):
    _owned_submission(sub_id, employer["company_id"])
    update_submission(sub_id, **payload.model_dump())
    return {"ok": True}


@app.delete("/api/employers/me/listings/{sub_id}")
def employer_close_listing(sub_id: int, employer: dict = Depends(require_employer_role("owner", "can_post"))):
    # Closing, not deleting — a closed listing keeps its history (views,
    # applicants tracked against it) rather than disappearing outright.
    _owned_submission(sub_id, employer["company_id"])
    set_status(sub_id, "closed")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Applicants: a manual per-listing tracker, not real application collection
# (see api/db.py's module note above create_applicant). Nested under a
# listing's own id so ownership is checked once, at the listing, before
# ever touching a row.
# ---------------------------------------------------------------------------

def _applicant_out(row: dict) -> dict:
    return {**row, "stage_name": APPLICANT_STAGES[row["stage"]]}


@app.get("/api/employers/me/listings/{sub_id}/applicants")
def employer_list_applicants(sub_id: int, employer: dict = Depends(require_employer)):
    _owned_submission(sub_id, employer["company_id"])
    rows = list_applicants_for_submission(sub_id, employer["company_id"])
    return {"applicants": [_applicant_out(r) for r in rows], "stages": APPLICANT_STAGES}


@app.post("/api/employers/me/listings/{sub_id}/applicants")
def employer_add_applicant(sub_id: int, payload: ApplicantIn, employer: dict = Depends(require_employer_role("owner", "can_post"))):
    _owned_submission(sub_id, employer["company_id"])
    applicant_id = create_applicant(
        sub_id, employer["company_id"], payload.full_name, payload.email, payload.location,
        payload.portfolio_url, payload.note, employer["id"],
    )
    return {"ok": True, "id": applicant_id}


@app.put("/api/employers/me/applicants/{applicant_id}")
def employer_update_applicant(applicant_id: int, payload: ApplicantIn, employer: dict = Depends(require_employer_role("owner", "can_post"))):
    updated = update_applicant(
        applicant_id, employer["company_id"], payload.full_name, payload.email,
        payload.location, payload.portfolio_url, payload.note,
    )
    if not updated:
        raise HTTPException(404, "no such applicant")
    return {"ok": True}


@app.put("/api/employers/me/applicants/{applicant_id}/stage")
def employer_move_applicant(applicant_id: int, payload: ApplicantStageUpdate, employer: dict = Depends(require_employer_role("owner", "can_post"))):
    updated = set_applicant_stage(applicant_id, employer["company_id"], payload.stage)
    if not updated:
        raise HTTPException(404, "no such applicant")
    return {"ok": True}


@app.delete("/api/employers/me/applicants/{applicant_id}")
def employer_remove_applicant(applicant_id: int, employer: dict = Depends(require_employer_role("owner", "can_post"))):
    deleted = delete_applicant(applicant_id, employer["company_id"])
    if not deleted:
        raise HTTPException(404, "no such applicant")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Team: the roster plus pending invites (owner side), and the invite link
# itself (public — a colleague hasn't signed in yet when they open it). The
# real backend counterpart to the client-side-only pp-invite.html demo: the
# link now carries a server-verified token instead of spoofable
# ?company=&inviter=&email= query params.
# ---------------------------------------------------------------------------

@app.get("/api/employers/me/team")
def employer_team(employer: dict = Depends(require_employer)):
    members = [_employer_public(e) | {"is_pending_approval": bool(e["is_pending_approval"])}
               for e in list_employers_for_company(employer["company_id"])]
    pending_invites = list_pending_team_invites(employer["company_id"])
    return {"members": members, "pending_invites": pending_invites}


@app.post("/api/employers/me/team/invite")
def employer_send_invite(payload: TeamInviteIn, background: BackgroundTasks,
                          employer: dict = Depends(require_employer_role("owner", "can_post"))):
    if get_employer_by_email(payload.email):
        raise HTTPException(400, "That email already has an account somewhere on Kazi.")
    company = get_company(employer["company_id"])
    needs_approval = employer["team_role"] != "owner"
    token = create_team_invite(
        employer["company_id"], payload.email, employer["id"], payload.team_role, needs_approval,
    )
    background.add_task(email_module.send_team_invite_email, payload.email, company["name"], employer["full_name"], token)
    return {"ok": True}


@app.get("/api/employers/invites/{token}")
def get_team_invite(token: str):
    invite = get_team_invite_by_token(token)
    if not invite or invite["status"] != "pending" or invite["expires_at"] < datetime.now(timezone.utc).isoformat(timespec="seconds"):
        raise HTTPException(404, "This invite is no longer valid.")
    company = get_company(invite["company_id"])
    inviter = get_employer(invite["invited_by_employer_id"])
    return {
        "company_name": company["name"] if company else "",
        "inviter_name": inviter["full_name"] if inviter else "",
        "invited_email": invite["invited_email"],
        "needs_approval": bool(invite["needs_approval"]),
    }


@app.post("/api/employers/invites/{token}/accept")
def accept_team_invite(token: str, payload: TeamInviteAccept):
    invite = get_team_invite_by_token(token)
    if not invite or invite["status"] != "pending" or invite["expires_at"] < datetime.now(timezone.utc).isoformat(timespec="seconds"):
        raise HTTPException(404, "This invite is no longer valid.")
    if get_employer_by_email(invite["invited_email"]):
        raise HTTPException(400, "That email already has an account somewhere on Kazi.")
    password_hash = bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode()
    employer_id = create_employer(
        invite["company_id"], invite["invited_email"], password_hash, payload.full_name,
        team_role=invite["proposed_team_role"], is_pending_approval=bool(invite["needs_approval"]),
        invited_by_employer_id=invite["invited_by_employer_id"],
    )
    set_invite_status(token, "accepted")
    return {"ok": True, "token": create_employer_session(employer_id), "needs_approval": bool(invite["needs_approval"])}


@app.post("/api/employers/invites/{token}/decline")
def decline_team_invite(token: str):
    invite = get_team_invite_by_token(token)
    if not invite or invite["status"] != "pending":
        raise HTTPException(404, "This invite is no longer valid.")
    set_invite_status(token, "declined")
    return {"ok": True}


@app.post("/api/employers/me/team/{target_id}/approve")
def employer_approve_teammate(target_id: int, employer: dict = Depends(require_employer_role("owner"))):
    target = get_employer(target_id)
    if not target or target["company_id"] != employer["company_id"]:
        raise HTTPException(404, "no such teammate")
    approve_pending_employer(target_id)
    return {"ok": True}


@app.post("/api/employers/me/team/{target_id}/decline")
def employer_decline_teammate(target_id: int, employer: dict = Depends(require_employer_role("owner"))):
    # "Decline says nothing was shared" — the pending account is removed
    # outright, same as it never having been created.
    target = get_employer(target_id)
    if not target or target["company_id"] != employer["company_id"] or not target["is_pending_approval"]:
        raise HTTPException(404, "no such pending teammate")
    delete_employer(target_id)
    return {"ok": True}


@app.delete("/api/employers/me/team/{target_id}")
def employer_remove_teammate(target_id: int, employer: dict = Depends(require_employer_role("owner"))):
    target = get_employer(target_id)
    if not target or target["company_id"] != employer["company_id"]:
        raise HTTPException(404, "no such teammate")
    if target["team_role"] == "owner" and count_company_owners(employer["company_id"], exclude_id=target_id) == 0:
        raise HTTPException(400, "Can't remove the only owner — add another owner first.")
    delete_employer(target_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Messages, employer side: mirrors the designer side, scoped by company_id
# so any teammate can read/reply to a thread, not just whoever started it.
# ---------------------------------------------------------------------------

def _employer_conversation_out(conv: dict) -> dict:
    designer = get_designer(conv["designer_id"])
    return {**conv, "designer_name": designer["display_name"] if designer else "",
            "designer_handle": designer.get("handle", "") if designer else ""}


@app.get("/api/employers/me/conversations")
def employer_list_conversations(employer: dict = Depends(require_employer)):
    convs = list_conversations_for_company(employer["company_id"])
    return {"conversations": [_employer_conversation_out(c) for c in convs]}


@app.post("/api/employers/me/conversations")
def employer_start_conversation(payload: EmployerConversationStart, employer: dict = Depends(require_employer)):
    designer = get_designer(payload.designer_id)
    if not designer or designer["status"] != "approved":
        raise HTTPException(404, "no such designer")
    conversation_id = get_or_create_conversation(payload.designer_id, employer["company_id"], "employer")
    create_message(conversation_id, "employer", None, employer["id"], payload.body)
    return {"ok": True, "conversation_id": conversation_id}


def _require_company_conversation(conversation_id: int, company_id: int) -> dict:
    conv = get_conversation(conversation_id)
    if not conv or conv["company_id"] != company_id:
        raise HTTPException(404, "no such conversation")
    return conv


@app.get("/api/employers/me/conversations/{conversation_id}/messages")
def employer_get_messages(conversation_id: int, employer: dict = Depends(require_employer)):
    _require_company_conversation(conversation_id, employer["company_id"])
    mark_conversation_read(conversation_id, "employer")
    return {"messages": list_messages(conversation_id)}


@app.post("/api/employers/me/conversations/{conversation_id}/messages")
def employer_send_message(conversation_id: int, payload: MessageIn, employer: dict = Depends(require_employer)):
    _require_company_conversation(conversation_id, employer["company_id"])
    create_message(conversation_id, "employer", None, employer["id"], payload.body)
    return {"ok": True}


@app.get("/api/designers")
def designers_directory(discipline: str = ""):
    rows = list_approved_designers(discipline=discipline or None)
    return {"count": len(rows), "designers": [_designer_public(d) for d in rows]}


@app.get("/api/admin/designers")
def admin_list_designers(status: str = "pending", _: None = Depends(require_admin)):
    rows = list_designers(status=status if status != "all" else None)
    return [{
        **d,
        "discipline": parse_multi_field(d["discipline"]),
        "skills": parse_multi_field(d.get("skills")),
        "links": list_designer_links(d["id"]),
        "projects": list_designer_projects(d["id"]),
    } for d in rows]


@app.post("/api/admin/designers/{designer_id}/approve")
def admin_approve_designer(designer_id: int, _: None = Depends(require_admin)):
    if not get_designer(designer_id):
        raise HTTPException(404, "no such designer")
    set_designer_status(designer_id, "approved")
    return {"ok": True}


@app.post("/api/admin/designers/{designer_id}/reject")
def admin_reject_designer(designer_id: int, _: None = Depends(require_admin)):
    if not get_designer(designer_id):
        raise HTTPException(404, "no such designer")
    set_designer_status(designer_id, "rejected")
    return {"ok": True}


@app.post("/api/admin/designers/{designer_id}/verify-email")
def admin_verify_designer_email(designer_id: int, _: None = Depends(require_admin)):
    """Manual fallback for when the verification email can't be delivered
    (e.g. Resend isn't configured/working) — an admin can unblock a real
    designer without them being stuck waiting on an email that never arrives."""
    if not get_designer(designer_id):
        raise HTTPException(404, "no such designer")
    set_designer_email_verified(designer_id)
    return {"ok": True}


# Public single-profile lookup — registered after the /me routes above so
# "me" is never routed here as if it were a numeric id. Accepts either the
# numeric id (old-style links, and any profile that hasn't set a handle
# yet) or a handle (the canonical form once a designer has one) — a handle
# can never be all-digits (HANDLE_RE requires a leading letter), so
# .isdigit() alone is enough to tell the two apart.
@app.get("/api/designers/{identifier}")
def designer_public_profile(identifier: str):
    designer = get_designer(int(identifier)) if identifier.isdigit() else None
    if not designer:
        designer = get_designer_by_handle(identifier)
    if not designer or designer["status"] != "approved":
        raise HTTPException(404, "no such designer")
    return _designer_public(designer)


# Serve processed profile photos. Registered before the catch-all web/ mount
# further down so it isn't shadowed. StaticFiles needs the directory to
# exist at mount time, same reason db.py's _conn() creates DB.parent first.
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/photos", StaticFiles(directory=str(PHOTOS_DIR)), name="photos")

# Same reasoning for featured-project cover images — public by default
# (unlike resumes), so a plain static mount is fine.
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/project-images", StaticFiles(directory=str(PROJECTS_DIR)), name="project-images")

# Same reasoning for company logos.
LOGOS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/logos", StaticFiles(directory=str(LOGOS_DIR)), name="logos")


# Clean URLs: serve the .html files without the extension...
CLEAN_PAGES = ["post", "cv-check", "account", "onboarding", "login", "signup", "designers", "admin", "terms", "privacy", "cookies"]
for _page in CLEAN_PAGES:
    def _make_page_route(page: str):
        def _serve():
            return FileResponse(WEB_DIR / f"{page}.html")
        return _serve
    app.get(f"/{_page}", include_in_schema=False)(_make_page_route(_page))

# ...and 301 old .html links (bookmarks, external links) to the clean path.
_HTML_REDIRECTS = {"index": "/", **{p: f"/{p}" for p in CLEAN_PAGES}}
for _name, _target in _HTML_REDIRECTS.items():
    def _make_redirect_route(target: str):
        def _redirect():
            return RedirectResponse(url=target, status_code=301)
        return _redirect
    app.get(f"/{_name}.html", include_in_schema=False)(_make_redirect_route(_target))

# One dynamic page route: /designers/{id-or-handle} always serves the same
# static file — designer.html reads the identifier from location.pathname
# client-side and fetches GET /api/designers/{identifier} itself, same
# pattern index.html already uses to fetch jobs.json.
@app.get("/designers/{identifier}", include_in_schema=False)
def designer_profile_page(identifier: str):
    return FileResponse(WEB_DIR / "designer.html")


# Same pattern for a single job: /jobs/{id} always serves job.html, which
# reads the id from location.pathname and fetches GET /api/jobs/{id} itself.
@app.get("/jobs/{job_id}", include_in_schema=False)
def job_details_page(job_id: str):
    return FileResponse(WEB_DIR / "job.html")


# Static site last: /api/* and the routes above take priority, everything
# else falls through to web/ (jobs.json, css, js, images, ...).
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
