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
from datetime import datetime

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
)
from . import geoip  # noqa: E402
from . import ats_check  # noqa: E402
from . import photo as photo_module  # noqa: E402
from . import email as email_module  # noqa: E402

WEB_DIR = ROOT / "web"
JOBS_JSON = WEB_DIR / "jobs.json"
WORK_TYPES = {"Remote", "Hybrid", "On-site"}
LEVELS = {"Junior", "Mid", "Senior", "Lead"}
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
    delete_designer(designer["id"])
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
