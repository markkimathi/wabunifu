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
import hashlib
import html as html_mod
import os
import re
from uuid import uuid4
from typing import Optional
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta, date

import bcrypt
from fastapi import (
    FastAPI, HTTPException, Header, Depends, Request, UploadFile, File, Form, BackgroundTasks,
)
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator, model_validator

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
from models import Job, DISCIPLINES, ELIGIBILITY, MAX_AGE_DAYS  # noqa: E402  (reuse the canonical schema)
from desc_format import format_description  # noqa: E402

from .db import (  # noqa: E402
    init_db, insert_submission, list_submissions, get_submission, set_status,
    log_pageview, log_search, log_apply_click, get_analytics_summary, count_profile_views,
    create_designer, get_designer, get_designer_by_email, get_designer_by_handle,
    record_designer_login_failure, reset_designer_login_failures,
    record_employer_login_failure, reset_employer_login_failures,
    LOGIN_LOCKOUT_THRESHOLD, LOGIN_LOCKOUT_MINUTES,
    update_designer_profile, update_designer_handle, HandleTaken, parse_multi_field,
    designers_missing_country, set_designer_country,
    shortlist_add, shortlist_remove, shortlist_ids, shortlist_entries,
    notify, list_notifications, count_unread_notifications, mark_notifications_read,
    list_employers_for_company, count_role_performance,
    mark_applied, list_applied_jobs, unmark_applied,
    save_search, list_saved_searches, delete_saved_search, set_search_alerts,
    searches_with_alerts, mark_search_notified,
    set_designer_photo, set_designer_email_verified, set_designer_password, set_designer_status,
    mark_onboarding_completed,
    set_designer_resume, clear_designer_resume, set_resume_visibility,
    list_designers, list_approved_designers, delete_designer, replace_designer_links,
    list_designer_links, create_session, get_session, delete_session, delete_sessions_for_designer,
    create_email_token, consume_email_token,
    list_designer_projects, count_designer_projects, create_designer_project,
    update_designer_project, set_project_image, delete_designer_project, reorder_designer_projects,
    update_project_story, set_project_status, list_project_images, add_project_image, delete_project_image, reorder_project_images,
    update_project_image_caption,
    list_role_history, create_role_history, update_role_history, delete_role_history, reorder_role_history,
    set_designer_company, list_designers_by_company,
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
    list_designers_at_company_name, list_followers_of, question_follower_ids,
    create_self_application, get_self_application, list_self_applications,
    withdraw_self_application,
    update_applicant, set_applicant_stage, delete_applicant, get_applicant,
    save_job, list_saved_jobs, unsave_job,
    get_or_create_conversation, get_or_create_peer_conversation, get_conversation, list_conversations_for_designer,
    list_conversations_for_company, list_messages, create_message, mark_conversation_read,
    set_message_status,
    create_community_session, list_community_sessions, get_community_session,
    update_community_session, set_session_status, count_session_seats, get_booking,
    book_session, cancel_booking, list_session_bookings, list_bookings_for_designer,
    create_question, list_questions, get_question, set_accepted_reply, set_question_status,
    list_questions_by_designer, list_replies_by_designer,
    create_reply, list_replies, get_reply, set_reply_status, toggle_vote, toggle_follow,
    toggle_follow_target, list_followed_target_ids, list_follows_for_designer, count_followers,
    count_stale_questions, get_reply_leaderboard, list_work_worth_reading,
    get_pay_median, create_pay_submission, list_pay_ranges, list_pay_submissions, set_pay_submission_status,
    get_message, create_report, list_reports, get_report, resolve_report, set_employer_suspended,
    override_submission_eligibility, suspend_designer, unsuspend_designer,
)
from . import geoip  # noqa: E402
from . import ats_check  # noqa: E402
from . import photo as photo_module  # noqa: E402
from . import project_image as project_image_module  # noqa: E402
from . import email as email_module  # noqa: E402
from .email import (send_saved_search_digest, send_designer_approved_email,  # noqa: E402
                    send_designer_rejected_email, send_company_approved_email,
                    send_company_rejected_email, send_designer_suspended_email,
                    send_designer_unsuspended_email, send_teammate_approved_email,
                    send_teammate_declined_email, send_teammate_removed_email,
                    send_content_removed_email, send_explain_yourself_email)

# The eligibility rules live with the classifier that produces them, and the
# digest has to apply exactly the same ones the board does — a second copy here
# is how five pages ended up disagreeing about what "Open across Africa" meant.
# scraper/ ships in the image (see Dockerfile), so import it rather than fork it.
sys.path.insert(0, str(ROOT / "scraper"))
from pipeline.eligibility import open_to_country as eligibility_open_to  # noqa: E402

WEB_DIR = ROOT / "web"
JOBS_JSON = WEB_DIR / "jobs.json"

# The one origin every canonical, og:url and sitemap entry points at. Override
# per-deployment if the public domain ever changes; set it empty to fall back
# to whatever host served the request.
PUBLIC_ORIGIN = os.environ.get("KAZI_PUBLIC_ORIGIN", "https://kazi.odana.design").rstrip("/")
WORK_TYPES = {"Remote", "Hybrid", "On-site"}
LEVELS = {"Junior", "Mid", "Senior", "Lead"}
APPLICANT_STAGES = ["Applied", "Reviewing", "Interviewing", "Offer"]
MAX_LINKS = 8
AVAILABILITY_STATUSES = ["Available", "Open to offers", "Not available"]
# What kind of work, which is a separate question from whether they're looking.
# An employer scanning the directory needs to tell a staff hire from a
# freelancer, and availability_status alone never said which.
OPEN_TO_OPTIONS = ["Full-time", "Contract", "Freelance", "Internship"]
MAX_DESIGNER_DISCIPLINES = 5
MAX_DESIGNER_SKILLS = 7
# Enough to be useful, few enough that a recruiter picks the ones that matter.
MAX_LISTING_SKILLS = 8

# Where a designer can work from, as a structured value the board matches
# against a listing's eligibility scope. Spelling has to line up exactly with
# scraper/pipeline/eligibility.py's country tables and web/pp-elig.js, or a
# role scoped to "Nigeria" won't match a designer who picked "Nigeria".
AFRICAN_COUNTRIES = [
    "Algeria", "Angola", "Benin", "Botswana", "Burkina Faso", "Burundi", "Cabo Verde",
    "Cameroon", "Central African Republic", "Chad", "Comoros", "Congo", "DR Congo",
    "Djibouti", "Egypt", "Equatorial Guinea", "Eritrea", "Eswatini", "Ethiopia",
    "Gabon", "Gambia", "Ghana", "Guinea", "Guinea-Bissau", "Ivory Coast", "Kenya",
    "Lesotho", "Liberia", "Libya", "Madagascar", "Malawi", "Mali", "Mauritania",
    "Mauritius", "Morocco", "Mozambique", "Namibia", "Niger", "Nigeria", "Rwanda",
    "Sao Tome and Principe", "Senegal", "Seychelles", "Sierra Leone", "Somalia",
    "South Africa", "South Sudan", "Sudan", "Tanzania", "Togo", "Tunisia", "Uganda",
    "Zambia", "Zimbabwe",
]
# Africa-first, but the directory is open to anyone, so the rest of the world
# needs somewhere to sit. Matches the non-African scope names the classifier
# produces, so "Open in the United States" resolves for someone who is there.
OTHER_COUNTRIES = [
    "the United States", "Canada", "Mexico", "the United Kingdom", "Ireland",
    "Germany", "France", "Spain", "Portugal", "Italy", "the Netherlands", "Belgium",
    "Poland", "Sweden", "Norway", "Denmark", "Finland", "Switzerland", "Austria",
    "Czechia", "Romania", "Greece", "Turkey", "Israel", "the UAE", "Saudi Arabia",
    "India", "Pakistan", "Bangladesh", "China", "Japan", "Singapore", "Malaysia",
    "Indonesia", "the Philippines", "Vietnam", "Thailand", "Australia",
    "New Zealand", "Brazil", "Argentina", "Colombia", "Chile", "Peru",
]
COUNTRIES = AFRICAN_COUNTRIES + OTHER_COUNTRIES

# Pay resources: a separate, deliberately simpler 2-value market split from
# job ELIGIBILITY's 3-tier vocabulary (kenya/africa/world/check) — pay data
# only needs to distinguish "paid by an African company" from "paid by a
# global/remote one", not the finer eligibility-for-applicants distinctions.
PAY_MARKETS = {"African company", "Global remote"}
# Static, approximate monthly conversion rates to USD — not a live FX feed.
# Good enough for a rough pay-transparency comparison; revisit periodically.
PAY_CURRENCY_TO_USD = {
    "USD": 1.0, "KES": 0.0077, "NGN": 0.00062, "ZAR": 0.055,
    "EUR": 1.09, "GBP": 1.27, "GHS": 0.067, "EGP": 0.020,
}

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
# A handle is the last segment of /designers/{handle}, so it cannot shadow a
# top-level route — but it is also the name shown beside anything that person
# says in messages and the community, and "admin" was takeable. These are the
# words someone could hide behind to look official, plus the product's own
# section names, which would read as a system page rather than a person.
RESERVED_HANDLES = {
    "me", "admin", "administrator", "root", "staff", "team", "support",
    "help", "moderator", "mod", "official", "system", "security", "billing",
    "kazi", "pathandpixel", "path_and_pixel", "pixel", "noreply", "no_reply",
    "about", "settings", "account", "profile", "dashboard", "employer",
    "jobs", "job", "people", "designers", "designer", "companies", "company",
    "community", "resources", "signin", "signup", "login", "logout", "new",
    "search", "invite", "terms", "privacy", "cookies", "api",
}

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
MAX_RESULT_STATS = 6
MAX_PROJECT_IMAGES = 8
MAX_ROLE_HISTORY = 10

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


def _backfill_designer_countries() -> None:
    """Derive the structured country from the free text people already typed, so
    the board can filter for existing designers without making them re-enter
    anything. Runs once — after this every row either has a country or has a
    location we couldn't place, and both are skipped on the next boot."""
    for d in designers_missing_country():
        loc = (d["location"] or "").lower()
        # Longest name first so "South Sudan" is never matched as "Sudan", and
        # "South Africa" never as the bare continent.
        for country in sorted(COUNTRIES, key=len, reverse=True):
            needle = country.lower()
            if needle.startswith("the "):
                needle = needle[4:]
            if re.search(r"\b" + re.escape(needle) + r"\b", loc):
                set_designer_country(d["id"], country)
                break


_backfill_designer_countries()


# Nothing here is content-hashed — pages ask for "pp-nav.js", not
# "pp-nav.a1b2c3.js" — and StaticFiles only sends etag/last-modified, no
# Cache-Control. Browsers fall back to *heuristic* freshness for that, which
# means a plain refresh can serve a stale copy for hours without ever asking
# us, so a deploy quietly fails to reach people who already had the page open.
# "no-cache" is not "don't cache": it stores the file but revalidates first,
# and the existing etag turns that into a cheap 304. Media keeps a real
# max-age since those bytes are big and change only by being replaced.
_REVALIDATE_SUFFIXES = (".html", ".js", ".css", ".json", ".map")
_MEDIA_PREFIXES = ("/avatars/", "/photos/", "/project-images/", "/logos/", "/video/", "/brand/", "/fonts/")


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        return response
    path = request.url.path
    if path.startswith(_MEDIA_PREFIXES):
        response.headers.setdefault("Cache-Control", "public, max-age=86400")
    elif path.endswith(_REVALIDATE_SUFFIXES) or "." not in path.rsplit("/", 1)[-1]:
        # Extensionless paths are the clean-URL page routes (/jobs, /people, ...),
        # which serve HTML and must revalidate for the same reason.
        response.headers["Cache-Control"] = "no-cache"
    return response


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
    cross_border_note: str = ""
    # ISO date (YYYY-MM-DD). Optional: a role with no stated close is left
    # open rather than given an invented deadline.
    closes_at: str = ""
    # Structured, so matching can be sharper than discipline alone.
    skills: list[str] = []
    # What an applicant should be ready to answer. Shown on the listing as
    # preparation when the employer takes applications on their own site, and
    # asked directly when they take them here.
    screening: list[str] = []
    portfolio_required: bool = False
    # The employer's choice: applications through Kazi, or a link to their own
    # site. Defaults to their own site, which is how every listing behaved
    # before this existed and what every existing employer agreed to.
    accepts_applications: bool = False

    @field_validator("skills", "screening")
    @classmethod
    def _clean_list(cls, v: list[str]) -> list[str]:
        out = []
        for item in v or []:
            item = (item or "").strip()[:120]
            if item and item not in out:
                out.append(item)
        return out[:MAX_LISTING_SKILLS]

    @field_validator("closes_at")
    @classmethod
    def _valid_closes_at(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            return ""
        try:
            when = date.fromisoformat(v)
        except ValueError:
            raise ValueError("closing date must be YYYY-MM-DD")
        if when < date.today():
            raise ValueError("closing date can't be in the past")
        return v

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
    """Every field except the name defaults to None meaning "not sent", and the
    endpoint keeps whatever is already stored for those.

    This used to default to "" instead, which made a partial form indistinguishable
    from a deliberate clear — so the availability form, which posts a whole profile
    payload with only its own fields filled, silently wiped the country the job
    board filters on the moment that field was added. Three forms post this model
    and each would have to be updated by hand for every new field; making absence
    mean "leave it alone" removes the trap instead of re-setting it each time.
    """
    display_name: str
    bio: Optional[str] = None
    discipline: Optional[list] = None
    skills: Optional[list] = None
    open_to: Optional[list] = None
    location: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    contact_email: Optional[str] = None
    headline: Optional[str] = None
    years_experience: Optional[str] = None
    availability_status: Optional[str] = None

    @field_validator("display_name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("enter your name")
        return v.strip()

    @field_validator("discipline")
    @classmethod
    def _valid_discipline_list(cls, v):
        if v is None:
            return None          # not sent — the endpoint keeps what's stored
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
    def _valid_skills_list(cls, v):
        if v is None:
            return None          # not sent — the endpoint keeps what's stored
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
        if not HANDLE_RE.match(v):
            raise ValueError("handle must be 3-30 characters: lowercase letters, numbers, and underscores, starting with a letter")
        # Told apart from the format rule on purpose: "admin" satisfies every
        # rule that message lists, so answering with it would read as nonsense.
        if v in RESERVED_HANDLES:
            raise ValueError("that word is reserved — try another")
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


class ResultStat(BaseModel):
    value: str
    label: str

    @field_validator("value", "label")
    @classmethod
    def _clean(cls, v: str) -> str:
        return (v or "").strip()[:60]


class ProjectStoryIn(BaseModel):
    problem: str = ""
    results: list[ResultStat] = []
    credits: str = ""

    @field_validator("problem", "credits")
    @classmethod
    def _clean_text(cls, v: str) -> str:
        return (v or "").strip()[:2000]

    @field_validator("results")
    @classmethod
    def _cap_results(cls, v: list) -> list:
        return v[:MAX_RESULT_STATS]


class ProjectImageCaption(BaseModel):
    caption: str = ""

    @field_validator("caption")
    @classmethod
    def _clean(cls, v: str) -> str:
        return (v or "").strip()[:140]


class RoleHistoryIn(BaseModel):
    company: str
    title: str
    start_date: str = ""
    end_date: str = ""
    is_current: bool = False
    description: str = ""

    @field_validator("company", "title")
    @classmethod
    def _required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("company and title are both required")
        return v[:120]

    @field_validator("start_date", "end_date")
    @classmethod
    def _clean_date(cls, v: str) -> str:
        return (v or "").strip()[:40]

    @field_validator("description")
    @classmethod
    def _clean_description(cls, v: str) -> str:
        return (v or "").strip()[:1000]


class RoleHistoryReorder(BaseModel):
    ids: list[int]


class DesignerCompanySet(BaseModel):
    company_id: Optional[int] = None


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
    company_id: Optional[int] = None
    peer_designer_id: Optional[int] = None
    body: str

    @field_validator("body")
    @classmethod
    def _valid_body(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("a message can't be empty")
        return v[:4000]

    @model_validator(mode="after")
    def _exactly_one_target(self):
        if (self.company_id is None) == (self.peer_designer_id is None):
            raise ValueError("exactly one of company_id or peer_designer_id is required")
        return self


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


class CommunitySessionIn(BaseModel):
    title: str
    kind: str = ""
    session_date: str
    time: str = ""
    length: str = ""
    blurb: str = ""
    host: str = ""
    host_initials: str = ""
    host_bg: str = ""
    host_fg: str = ""
    reviewer_bio: str = ""
    seats: int = 6
    joining_link: str = ""
    bring_list: list[str] = []
    agenda: list[dict] = []

    @field_validator("title", "session_date")
    @classmethod
    def _required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("required")
        return v


class CommunitySessionUpdate(BaseModel):
    title: Optional[str] = None
    kind: Optional[str] = None
    session_date: Optional[str] = None
    time: Optional[str] = None
    length: Optional[str] = None
    blurb: Optional[str] = None
    host: Optional[str] = None
    host_initials: Optional[str] = None
    host_bg: Optional[str] = None
    host_fg: Optional[str] = None
    reviewer_bio: Optional[str] = None
    seats: Optional[int] = None
    joining_link: Optional[str] = None
    bring_list: Optional[list[str]] = None
    agenda: Optional[list[dict]] = None


class QuestionIn(BaseModel):
    topic: str = ""
    title: str
    body: str

    @field_validator("title", "body")
    @classmethod
    def _required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("required")
        return v[:8000]


class ReplyIn(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def _valid_body(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("a reply can't be empty")
        return v[:8000]


class ReplyAccept(BaseModel):
    reply_id: Optional[int] = None


class ReportIn(BaseModel):
    summary: str = ""

    @field_validator("summary")
    @classmethod
    def _clip_summary(cls, v: str) -> str:
        return (v or "").strip()[:1000]


class ReportResolve(BaseModel):
    action: str

    @field_validator("action")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        if v not in {"remove", "suspend", "keep", "ask"}:
            raise ValueError("action must be one of remove, suspend, keep, ask")
        return v


class DesignerSuspend(BaseModel):
    rule: str
    reason: str

    @field_validator("rule")
    @classmethod
    def _valid_rule(cls, v: str) -> str:
        if v not in {"eligibility", "work", "conduct", "spam"}:
            raise ValueError("rule must be one of eligibility, work, conduct, spam")
        return v

    @field_validator("reason")
    @classmethod
    def _valid_reason(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError("reason must be at least 10 characters")
        return v.strip()


class EligibilityOverride(BaseModel):
    eligibility: str
    reason: str = ""

    @field_validator("eligibility")
    @classmethod
    def _valid_eligibility(cls, v: str) -> str:
        if v not in ELIGIBILITY:
            raise ValueError(f"eligibility must be one of {ELIGIBILITY}")
        return v


class PaySubmissionIn(BaseModel):
    discipline: str
    level: str
    market: str
    currency: str
    amount: float

    @field_validator("discipline")
    @classmethod
    def _valid_discipline(cls, v: str) -> str:
        if v not in DISCIPLINES:
            raise ValueError(f"discipline must be one of {DISCIPLINES}")
        return v

    @field_validator("level")
    @classmethod
    def _valid_level(cls, v: str) -> str:
        if v not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        return v

    @field_validator("market")
    @classmethod
    def _valid_market(cls, v: str) -> str:
        if v not in PAY_MARKETS:
            raise ValueError(f"market must be one of {PAY_MARKETS}")
        return v

    @field_validator("currency")
    @classmethod
    def _valid_currency(cls, v: str) -> str:
        v = (v or "").upper().strip()
        if v not in PAY_CURRENCY_TO_USD:
            raise ValueError(f"currency must be one of {sorted(PAY_CURRENCY_TO_USD)}")
        return v

    @field_validator("amount")
    @classmethod
    def _valid_amount(cls, v: float) -> float:
        if v <= 0 or v > 10_000_000:
            raise ValueError("amount must be a positive, realistic monthly figure")
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
    if designer["status"] == "suspended":
        raise HTTPException(403, "this account has been suspended")
    return designer


def require_designer_any_status(authorization: str = Header(default="")) -> dict:
    """require_designer with the suspension gate removed, for the one endpoint
    that has to work while suspended: reading your own account.

    Without this a suspended designer signed in fine, then every call 403'd —
    including /me — so the dashboard sat on "Loading…" forever and the banner
    written to explain the suspension could never render. Being blocked from
    acting is the point; being unable to find out why is not."""
    token = authorization.removeprefix("Bearer ").strip()
    session = get_session(token) if token else None
    if not session:
        raise HTTPException(401, "invalid or expired session")
    designer = get_designer(session["designer_id"])
    if not designer:
        raise HTTPException(401, "invalid session")
    return designer


def optional_designer(authorization: str = Header(default="")) -> Optional[dict]:
    """Like require_designer, but returns None instead of 401ing — for
    endpoints that are publicly readable but personalize their response
    (e.g. a session's your_status) when the caller happens to be signed in."""
    token = authorization.removeprefix("Bearer ").strip()
    session = get_session(token) if token else None
    if not session:
        return None
    return get_designer(session["designer_id"])


def require_employer(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    session = get_employer_session(token) if token else None
    if not session:
        raise HTTPException(401, "invalid or expired session")
    employer = get_employer(session["employer_id"])
    if not employer:
        raise HTTPException(401, "invalid session")
    if employer["suspended"]:
        raise HTTPException(403, "this account has been suspended")
    return employer


def require_employer_any_status(authorization: str = Header(default="")) -> dict:
    """require_employer without the suspension gate, for reading your own
    account. Mirrors require_designer_any_status and exists for the same
    reason: an employer can be suspended by a report resolution, and without
    this their dashboard could never load the status that explains why."""
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
    # Went through model_dump() raw, which hands sqlite Python lists for skills
    # and screening — it has been returning 500 for anything posted to it. The
    # dashboard path already had _listing_row() to do this conversion; this now
    # uses the same one rather than a second, broken copy of the idea.
    sub_id = insert_submission(_listing_row(payload))
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
                cross_border_note=s.get("cross_border_note", ""),
                accepts_applications=bool(s.get("accepts_applications")),
                submission_id=s["id"],
                closes_at=s.get("closes_at", "") or "",
                skills=parse_multi_field(s.get("skills")),
                screening=parse_multi_field(s.get("screening")),
                portfolio_required=bool(s.get("portfolio_required")),
            ).to_web()
        )

    # Enforced again here (not just at scrape time) so the cutoff holds live
    # even if a scrape run is skipped, and so it also applies to employer
    # submissions, which run.py never sees.
    # A role past its stated closing date is not open, whatever its age. Only
    # employer-posted listings carry one, so this never touches scraped roles.
    today = date.today().isoformat()
    combined = [j for j in employer_jobs + scraped
                if j["days"] <= MAX_AGE_DAYS and not (j.get("closes") and j["closes"] < today)]
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


class DesignerReview(BaseModel):
    """Optional note from the reviewer. On a rejection this is the only thing
    that tells the designer what to change — same shape as SubmissionReview."""
    reason: str = ""


class SubmissionReview(BaseModel):
    """Optional note from the reviewer. On a rejection this is the only thing
    that tells the employer what to change, so it is passed straight through."""
    note: str = ""


def _tell_employer(sub: dict, kind: str, title: str, body: str) -> None:
    """Notify whoever posted a listing about a review decision.

    Scraped listings have no employer behind them, and the older employer-posted
    rows predate submissions.employer_id, so a missing id is normal rather than
    an error — there is simply nobody to tell."""
    employer_id = sub.get("employer_id")
    if employer_id:
        notify("employer", int(employer_id), kind, title, body, "/employer")


@app.post("/api/admin/submissions/{sub_id}/approve")
def admin_approve(sub_id: int, payload: SubmissionReview = SubmissionReview(),
                  _: None = Depends(require_admin)):
    sub = get_submission(sub_id)
    if not sub:
        raise HTTPException(404, "no such submission")
    set_status(sub_id, "approved", payload.note.strip())
    _tell_employer(
        sub, "listing_approved",
        f"{sub['title']} is live on the board",
        payload.note.strip() or "Designers can see and apply to it now.",
    )
    return {"ok": True}


@app.post("/api/admin/submissions/{sub_id}/reject")
def admin_reject(sub_id: int, payload: SubmissionReview = SubmissionReview(),
                 _: None = Depends(require_admin)):
    sub = get_submission(sub_id)
    if not sub:
        raise HTTPException(404, "no such submission")
    note = payload.note.strip()
    set_status(sub_id, "rejected", note)
    _tell_employer(
        sub, "listing_rejected",
        f"{sub['title']} wasn't approved",
        note or "Open the listing in your dashboard to edit and resubmit it.",
    )
    return {"ok": True}


@app.post("/api/admin/submissions/{sub_id}/eligibility")
def admin_override_eligibility(sub_id: int, payload: EligibilityOverride, _: None = Depends(require_admin)):
    """Correcting the employer's own eligibility claim during review — the
    review modal calls this before approve/reject when the admin picked a
    different tier than what was submitted. Recorded with a reason so the
    employer can be told why, and eligibility_source flips to 'admin-set' so
    every listing surface can show the tier was corrected, not claimed."""
    if not get_submission(sub_id):
        raise HTTPException(404, "no such submission")
    override_submission_eligibility(sub_id, payload.eligibility, payload.reason)
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

def _designer_public(d: dict, include_drafts: bool = False) -> dict:
    """Strip private fields (login email, password_hash) before this ever
    reaches a public response — used for both the directory and
    single-profile endpoints so there's exactly one place that decides
    what's public. phone/contact_email ARE included here on purpose: unlike
    the login email, they're an opt-in field a designer fills in specifically
    so employers can reach them, same as the rest of the profile."""
    out = {
        "id": d["id"], "display_name": d["display_name"], "bio": d["bio"],
        "discipline": parse_multi_field(d["discipline"]), "location": d["location"],
        "country": d.get("country", ""),
        "photo_path": d["photo_path"], "created_at": d["created_at"],
        "phone": d.get("phone", ""), "contact_email": d.get("contact_email", ""),
        "handle": d.get("handle", ""),
        "headline": d.get("headline", ""), "years_experience": d.get("years_experience", ""),
        "availability_status": d.get("availability_status", ""),
        "open_to": parse_multi_field(d.get("open_to")),
        "skills": parse_multi_field(d.get("skills")),
        "links": list_designer_links(d["id"]),
        # Drafts are the owner's own view only. Every public caller leaves
        # include_drafts off, so an unpublished project can't reach the
        # directory, a profile page, or search.
        "projects": list_designer_projects(d["id"], published_only=not include_drafts),
        "role_history": list_role_history(d["id"]),
        "followers_count": count_followers("designer", d["id"]),
    }
    company_id = d.get("company_id")
    if company_id:
        company = get_company(company_id)
        out["company"] = { "id": company["id"], "name": company["name"], "slug": company["slug"] } if company else None
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


def _lockout_minutes_left(locked_until: str) -> int:
    """Whole minutes remaining on a lockout, rounded up so "1 minute left"
    never reads as "0 minutes left" while still actually locked."""
    try:
        until = datetime.fromisoformat(locked_until)
    except ValueError:
        return 0
    seconds_left = (until - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(seconds_left + 59) // 60)


@app.post("/api/designers/login")
def designer_login(payload: DesignerLogin):
    designer = get_designer_by_email(payload.email.strip().lower())
    if designer and designer.get("locked_until"):
        minutes_left = _lockout_minutes_left(designer["locked_until"])
        if minutes_left > 0:
            raise HTTPException(423, {"message": f"Too many attempts. Try again in {minutes_left} minutes.", "locked": True})
    if not designer or not bcrypt.checkpw(payload.password.encode(), designer["password_hash"].encode()):
        if designer:
            result = record_designer_login_failure(designer["id"])
            if result["locked_until"]:
                raise HTTPException(423, {"message": f"Too many attempts. Try again in {LOGIN_LOCKOUT_MINUTES} minutes.", "locked": True})
            remaining = LOGIN_LOCKOUT_THRESHOLD - result["attempts"]
            if remaining <= 2:
                raise HTTPException(401, {
                    "message": "That password doesn't match this email",
                    "attempts_remaining": remaining,
                })
        raise HTTPException(401, "Incorrect email or password.")
    reset_designer_login_failures(designer["id"])
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
    # Then issue one for the person standing here, who just proved they
    # control the address. Returning only {ok:true} was why the reset page
    # sent them to a sign-in form to retype what they'd just chosen, under a
    # button that says "Save and sign in".
    return {"ok": True, "token": create_session(designer_id)}


@app.get("/api/designers/me")
def designer_me(designer: dict = Depends(require_designer_any_status)):
    return {**_designer_public(designer, include_drafts=True), "email": designer["email"],
            "email_verified": bool(designer["email_verified"]), "status": designer["status"],
            # Reason included so the suspended banner can say what happened
            # rather than leaving them to guess. Only ever their own.
            "suspend_reason": designer.get("suspend_reason") or "",
            "onboarding_completed": bool(designer["onboarding_completed"]),
            # Always present for the owner's own view, regardless of
            # resume_public — _designer_public() only includes these when
            # public, since that copy is also used for the public directory.
            "resume_filename": designer.get("resume_filename", ""),
            "resume_uploaded_at": designer.get("resume_uploaded_at", ""),
            "resume_url": "/api/designers/me/resume/download" if designer.get("resume_path") else "",
            "resume_public": bool(designer.get("resume_public"))}


class AppliedIn(BaseModel):
    job_id: str
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""


@app.get("/api/designers/me/applied")
def designer_list_applied(designer: dict = Depends(require_designer)):
    return {"jobs": list_applied_jobs(designer["id"])}


@app.post("/api/designers/me/applied")
def designer_mark_applied(payload: AppliedIn, designer: dict = Depends(require_designer)):
    """Self-reported. Path & Pixel deliberately never touches the application —
    it hands you to the company's own page — so this records that you said you
    applied and nothing more. We don't claim to know the outcome, because we
    genuinely don't."""
    if not payload.job_id.strip():
        raise HTTPException(400, "job_id is required")
    mark_applied(designer["id"], payload.job_id.strip(), payload.title[:200],
                 payload.company[:200], payload.location[:200], payload.url[:500])
    return {"ok": True}


@app.delete("/api/designers/me/applied/{job_id}")
def designer_unmark_applied(job_id: str, designer: dict = Depends(require_designer)):
    if not unmark_applied(designer["id"], job_id):
        raise HTTPException(404, "not marked as applied")
    return {"ok": True}


class SavedSearchIn(BaseModel):
    name: str
    filters: dict = {}
    alerts: bool = False

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("give this search a name")
        if len(v) > 60:
            raise ValueError("keep the name under 60 characters")
        return v


class SearchAlertsIn(BaseModel):
    alerts: bool


MAX_SAVED_SEARCHES = 10


@app.get("/api/designers/me/searches")
def designer_list_searches(designer: dict = Depends(require_designer)):
    return {"searches": [_search_out(s) for s in list_saved_searches(designer["id"])]}


def _search_out(s: dict) -> dict:
    try:
        filters = json.loads(s.get("filters") or "{}")
    except Exception:
        filters = {}
    return {"id": s["id"], "name": s["name"], "filters": filters,
            "alerts": bool(s["alerts"]), "created_at": s["created_at"]}


@app.post("/api/designers/me/searches")
def designer_save_search(payload: SavedSearchIn, designer: dict = Depends(require_designer)):
    existing = list_saved_searches(designer["id"])
    # Only a new name counts against the cap; re-saving one is an edit.
    if len(existing) >= MAX_SAVED_SEARCHES and payload.name not in [e["name"] for e in existing]:
        raise HTTPException(400, f"you can keep up to {MAX_SAVED_SEARCHES} saved searches")
    return _search_out(save_search(designer["id"], payload.name, payload.filters, payload.alerts))


@app.delete("/api/designers/me/searches/{search_id}")
def designer_delete_search(search_id: int, designer: dict = Depends(require_designer)):
    if not delete_saved_search(designer["id"], search_id):
        raise HTTPException(404, "no such saved search")
    return {"ok": True}


@app.put("/api/designers/me/searches/{search_id}/alerts")
def designer_search_alerts(search_id: int, payload: SearchAlertsIn,
                           designer: dict = Depends(require_designer)):
    if not set_search_alerts(designer["id"], search_id, payload.alerts):
        raise HTTPException(404, "no such saved search")
    return {"ok": True}


def _search_matches(j: dict, filters: dict, country: str) -> bool:
    """The board's own filter rules, server side. Kept deliberately narrow: only
    the filters that survive a week (discipline, level, pay, text) plus the
    eligibility rule, because an alert has to honour the same promise the board
    does — never surface a role that would turn this person down."""
    if country and eligibility_open_to(j.get("elig", ""), j.get("elig_scope", ""), country) is False:
        return False
    disciplines = filters.get("disciplines") or []
    if disciplines and j.get("cat") not in disciplines:
        return False
    levels = filters.get("levels") or []
    if levels and j.get("level") not in levels:
        return False
    if filters.get("payOnly") and (not j.get("pay") or j.get("pay") == "Not disclosed"):
        return False
    q = (filters.get("q") or "").strip().lower()
    if q:
        haystack = " ".join([j.get("t", ""), j.get("co", ""), j.get("cat", ""), j.get("city", "")]).lower()
        if q not in haystack:
            return False
    return True


@app.post("/api/admin/send-search-digests")
def admin_send_search_digests(dry_run: bool = False, _: None = Depends(require_admin)):
    """Weekly digest for alert-enabled saved searches. Triggered by a scheduled
    workflow rather than a timer in-process, matching how the scraper runs.

    Each search only reports roles posted since its own last_notified_at, so a
    run that sends nothing costs nothing and a missed week is caught up rather
    than skipped. Nobody is emailed an empty digest."""
    combined, _gen = _combined_jobs()
    now = datetime.now(timezone.utc)
    sent, skipped, errors = 0, 0, 0
    for s in searches_with_alerts():
        try:
            filters = json.loads(s.get("filters") or "{}")
        except Exception:
            filters = {}
        since = s.get("last_notified_at") or ""
        fresh = []
        for j in combined:
            posted = (j.get("posted_at") or "")[:10] if j.get("posted_at") else ""
            if not posted:
                # jobs.json exposes age in days, not a date; derive one so the
                # high-water mark still works for scraped listings.
                posted = (now - timedelta(days=int(j.get("days") or 0))).date().isoformat()
            if since and posted <= since[:10]:
                continue
            if _search_matches(j, filters, s.get("country") or ""):
                fresh.append(j)
        if not fresh:
            skipped += 1
            continue
        if dry_run:
            sent += 1
            continue
        ok = send_saved_search_digest(
            s["email"], s.get("display_name") or "there", s["name"],
            fresh[:10], s.get("country") or "",
        )
        if ok:
            mark_search_notified(s["id"], now.isoformat(timespec="seconds"))
            sent += 1
        else:
            errors += 1
    return {"sent": sent, "nothing_to_send": skipped, "failed": errors, "dry_run": dry_run}


@app.get("/api/search")
def unified_search(q: str = "", limit: int = 5):
    """One search across the things the site actually holds.

    The header search box existed on every page and went nowhere — pressing
    Enter saved the term to recent searches and nothing else. This gives it
    somewhere to go.

    Grouped by type rather than blended into one ranked list: "roles",
    "people" and "companies" are different intents, and a mixed list makes the
    reader do the sorting. Public data only, so it works signed out — which is
    most of the traffic on a job board."""
    needle = (q or "").strip().lower()
    if len(needle) < 2:
        return {"query": q, "roles": [], "people": [], "companies": [], "total": 0}

    combined, _gen = _combined_jobs()
    roles = []
    for j in combined:
        hay = " ".join([j.get("t", ""), j.get("co", ""), j.get("cat", ""), j.get("city", "")]).lower()
        if needle in hay:
            roles.append({"id": j["id"], "title": j.get("t", ""), "company": j.get("co", ""),
                          "place": j.get("city", "Remote"),
                          "elig": j.get("elig", ""), "elig_scope": j.get("elig_scope", ""),
                          "href": "/jobs/" + j["id"]})
        if len(roles) >= limit:
            break

    people = []
    for d in list_approved_designers():
        pub = _designer_public(d)
        hay = " ".join([pub.get("display_name", ""), pub.get("headline", ""), pub.get("location", ""),
                        " ".join(pub.get("skills") or []), " ".join(pub.get("discipline") or [])]).lower()
        if needle in hay:
            people.append({"name": pub["display_name"],
                           "sub": pub.get("headline") or (pub.get("discipline") or ["Designer"])[0],
                           "photo": pub.get("photo_path", ""),
                           "href": "/designers/" + (pub.get("handle") or str(pub["id"]))})
        if len(people) >= limit:
            break

    companies = []
    for co in list_companies(status="approved"):
        hay = " ".join([co.get("name", ""), co.get("blurb", "")]).lower()
        if needle in hay:
            companies.append({"name": co["name"], "sub": (co.get("blurb") or "")[:80],
                              "logo": co.get("logo_path", ""),
                              "href": "/companies/" + (co.get("slug") or str(co["id"]))})
        if len(companies) >= limit:
            break

    return {"query": q, "roles": roles, "people": people, "companies": companies,
            "total": len(roles) + len(people) + len(companies)}


@app.get("/api/countries")
def get_countries():
    """The country picker's options. Served rather than duplicated into the
    page so the list can never drift from what the profile endpoint validates."""
    return {"african": AFRICAN_COUNTRIES, "other": OTHER_COUNTRIES}


@app.get("/api/designers/me/stats")
def designer_stats(designer: dict = Depends(require_designer)):
    """Lightweight dashboard-stats sibling of /api/designers/me, same idea as
    /api/jobs/count next to /api/jobs — a later phase can add more keys here
    (click counts, visitor breakdowns, ...) without breaking existing callers."""
    return {"profile_views": count_profile_views(designer.get("handle", ""), designer["id"])}


@app.put("/api/designers/me")
def designer_update_me(payload: ProfileUpdate, designer: dict = Depends(require_designer)):
    # A field the form didn't send keeps whatever is stored. Sending "" is still
    # a deliberate clear — absence and emptiness are different answers.
    def kept(sent, column, parse_list=False):
        if sent is not None:
            return sent
        return parse_multi_field(designer.get(column)) if parse_list else designer.get(column, "")

    discipline = kept(payload.discipline, "discipline", parse_list=True)
    skills = kept(payload.skills, "skills", parse_list=True)
    open_to = kept(payload.open_to, "open_to", parse_list=True)
    country = kept(payload.country, "country")
    availability = kept(payload.availability_status, "availability_status")

    bad = [d for d in discipline if d not in DISCIPLINES]
    if bad:
        raise HTTPException(400, f"discipline must be one of {DISCIPLINES}")
    if availability and availability not in AVAILABILITY_STATUSES:
        raise HTTPException(400, f"availability_status must be one of {AVAILABILITY_STATUSES}")
    # Must be an exact match or the board can't line it up with a listing's
    # eligibility scope; "" stays allowed so the field is never a blocker.
    if country and country not in COUNTRIES:
        raise HTTPException(400, "country must be one we recognise")
    bad_open = [o for o in open_to if o not in OPEN_TO_OPTIONS]
    if bad_open:
        raise HTTPException(400, f"open_to must be from {OPEN_TO_OPTIONS}")

    update_designer_profile(
        designer["id"], display_name=payload.display_name,
        bio=kept(payload.bio, "bio").strip()[:2000],
        discipline=discipline,
        location=kept(payload.location, "location").strip()[:200],
        country=country,
        phone=kept(payload.phone, "phone").strip()[:40],
        contact_email=kept(payload.contact_email, "contact_email").strip()[:200],
        headline=kept(payload.headline, "headline"),
        years_experience=kept(payload.years_experience, "years_experience"),
        availability_status=availability, skills=skills, open_to=open_to,
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


class ProjectStatusIn(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _valid(cls, v: str) -> str:
        if v not in ("draft", "published"):
            raise ValueError("status must be draft or published")
        return v


@app.put("/api/designers/me/projects/{project_id}/status")
def designer_set_project_status(project_id: int, payload: ProjectStatusIn,
                                designer: dict = Depends(require_designer)):
    """Publishing is the designer's own call, not a review step — this is their
    work, and the admin queue is for profiles, not for each project."""
    was = next((p for p in list_designer_projects(designer["id"]) if p["id"] == project_id), None)
    if not set_project_status(designer["id"], project_id, payload.status):
        raise HTTPException(404, "No such project.")

    # Going live is the moment worth telling people about, and following a
    # designer had meant nothing until now — the follows table was only ever
    # read to decide whether a button looked pressed. Only on the transition,
    # so re-publishing something already public doesn't notify twice.
    if payload.status == "published" and was and was.get("status") != "published":
        pub = _designer_public(designer)
        who = pub.get("display_name") or "A designer"
        handle = pub.get("handle") or designer["id"]
        for follower_id in list_followers_of("designer", designer["id"]):
            notify("designer", follower_id, "new_work",
                   f"{who} published {was.get('title') or 'a new project'}",
                   (was.get("description") or "")[:200],
                   f"/designers/{handle}/{project_id}")
    return {"ok": True, "status": payload.status}


@app.put("/api/designers/me/projects/{project_id}/story")
def designer_update_project_story(project_id: int, payload: ProjectStoryIn, designer: dict = Depends(require_designer)):
    updated = update_project_story(
        designer["id"], project_id, payload.problem, [r.model_dump() for r in payload.results], payload.credits
    )
    if not updated:
        raise HTTPException(404, "No such project.")
    project = next(p for p in list_designer_projects(designer["id"]) if p["id"] == project_id)
    return {"ok": True, **project}


@app.post("/api/designers/me/projects/{project_id}/images")
async def designer_upload_project_gallery_image(project_id: int, file: UploadFile = File(...),
                                                  designer: dict = Depends(require_designer)):
    if not any(p["id"] == project_id for p in list_designer_projects(designer["id"])):
        raise HTTPException(404, "No such project.")
    if len(list_project_images(project_id)) >= MAX_PROJECT_IMAGES:
        raise HTTPException(400, f"You can add up to {MAX_PROJECT_IMAGES} images per project.")
    data = await file.read()
    try:
        jpeg_bytes = project_image_module.process_project_image(data)
    except project_image_module.UnsupportedImage as e:
        raise HTTPException(status_code=400, detail=str(e))
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{designer['id']}_{project_id}_{uuid4().hex[:8]}.jpg"
    (PROJECTS_DIR / stored_name).write_bytes(jpeg_bytes)
    image_path = f"/project-images/{stored_name}"
    image_id = add_project_image(designer["id"], project_id, image_path)
    if image_id is None:
        raise HTTPException(404, "No such project.")
    return {"ok": True, "id": image_id, "image_path": image_path, "caption": ""}


@app.put("/api/designers/me/projects/{project_id}/images/{image_id}")
def designer_caption_project_image(project_id: int, image_id: int, payload: ProjectImageCaption,
                                    designer: dict = Depends(require_designer)):
    updated = update_project_image_caption(designer["id"], project_id, image_id, payload.caption)
    if not updated:
        raise HTTPException(404, "No such image.")
    return {"ok": True}


@app.delete("/api/designers/me/projects/{project_id}/images/{image_id}")
def designer_delete_project_image(project_id: int, image_id: int, designer: dict = Depends(require_designer)):
    project = next((p for p in list_designer_projects(designer["id"]) if p["id"] == project_id), None)
    if not project:
        raise HTTPException(404, "No such project.")
    image = next((im for im in list_project_images(project_id) if im["id"] == image_id), None)
    if image and image.get("image_path"):
        f = PROJECTS_DIR / Path(image["image_path"]).name
        if f.exists():
            f.unlink()
    deleted = delete_project_image(designer["id"], project_id, image_id)
    if not deleted:
        raise HTTPException(404, "No such image.")
    return {"ok": True}


@app.put("/api/designers/me/projects/{project_id}/images/reorder")
def designer_reorder_project_images(project_id: int, payload: ProjectReorder, designer: dict = Depends(require_designer)):
    ok = reorder_project_images(designer["id"], project_id, payload.ids)
    if not ok:
        raise HTTPException(404, "No such project.")
    return {"ok": True}


@app.get("/api/designers/me/role-history")
def designer_list_role_history(designer: dict = Depends(require_designer)):
    return {"role_history": list_role_history(designer["id"])}


@app.post("/api/designers/me/role-history")
def designer_create_role_history(payload: RoleHistoryIn, designer: dict = Depends(require_designer)):
    if len(list_role_history(designer["id"])) >= MAX_ROLE_HISTORY:
        raise HTTPException(400, f"You can add up to {MAX_ROLE_HISTORY} roles.")
    role_id = create_role_history(
        designer["id"], payload.company, payload.title, payload.start_date,
        payload.end_date, payload.is_current, payload.description,
    )
    return {"ok": True, "id": role_id, **payload.model_dump()}


@app.put("/api/designers/me/role-history/reorder")
def designer_reorder_role_history(payload: RoleHistoryReorder, designer: dict = Depends(require_designer)):
    reorder_role_history(designer["id"], payload.ids)
    return {"ok": True}


@app.put("/api/designers/me/role-history/{role_id}")
def designer_update_role_history(role_id: int, payload: RoleHistoryIn, designer: dict = Depends(require_designer)):
    updated = update_role_history(
        designer["id"], role_id, payload.company, payload.title, payload.start_date,
        payload.end_date, payload.is_current, payload.description,
    )
    if not updated:
        raise HTTPException(404, "No such role.")
    return {"ok": True, "id": role_id, **payload.model_dump()}


@app.delete("/api/designers/me/role-history/{role_id}")
def designer_delete_role_history(role_id: int, designer: dict = Depends(require_designer)):
    deleted = delete_role_history(designer["id"], role_id)
    if not deleted:
        raise HTTPException(404, "No such role.")
    return {"ok": True}


@app.put("/api/designers/me/company")
def designer_set_company(payload: DesignerCompanySet, designer: dict = Depends(require_designer)):
    if payload.company_id is not None:
        company = get_company(payload.company_id)
        if not company or company["status"] != "approved":
            raise HTTPException(404, "no such company")
    set_designer_company(designer["id"], payload.company_id)
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

class ApplicationIn(BaseModel):
    """What a designer sends with an application. Deliberately short: the
    employer already has their profile, and asking for a cover letter is how
    you get a worse version of what is already on the page."""
    note: str = ""
    answers: list[str] = []

    @field_validator("note")
    @classmethod
    def _clean_note(cls, v: str) -> str:
        return (v or "").strip()[:2000]

    @field_validator("answers")
    @classmethod
    def _clean_answers(cls, v: list) -> list:
        return [str(a or "").strip()[:1000] for a in (v or [])][:3]


def _applications_open(sub: dict) -> bool:
    """A listing takes applications here only if its employer chose that, it is
    approved, and it hasn't closed. Scraped listings never qualify — there is no
    employer on the other end to receive anything."""
    if not sub or not sub.get("accepts_applications"):
        return False
    if sub.get("status") != "approved":
        return False
    closes = (sub.get("closes_at") or "").strip()
    return not (closes and closes < date.today().isoformat())


@app.post("/api/designers/me/applications/{submission_id}")
def designer_apply(submission_id: int, payload: ApplicationIn,
                   designer: dict = Depends(require_designer)):
    sub = get_submission(submission_id)
    if not _applications_open(sub):
        raise HTTPException(404, "This role isn't taking applications through Kazi.")
    if designer["status"] != "approved":
        raise HTTPException(
            403,
            "Your profile is still in review. Employers see your profile with an application, "
            "so it has to be live first.",
        )
    if get_self_application(submission_id, designer["id"]):
        raise HTTPException(400, "You've already applied to this role.")

    pub = _designer_public(designer)
    applicant_id = create_self_application(
        submission_id, sub["company_id"], designer["id"],
        pub.get("display_name") or "", designer.get("email") or "",
        pub.get("location") or pub.get("country") or "",
        f"{PUBLIC_ORIGIN}/designers/{pub.get('handle') or designer['id']}",
        payload.note, json.dumps(payload.answers),
    )
    if applicant_id is None:
        raise HTTPException(400, "You've already applied to this role.")

    if sub.get("employer_id"):
        notify("employer", int(sub["employer_id"]), "application",
               f"{pub.get('display_name') or 'A designer'} applied to {sub['title']}",
               (payload.note or "")[:200], "/employer?tab=applicants")
    return {"ok": True, "id": applicant_id}


@app.get("/api/designers/me/applications")
def designer_list_applications(designer: dict = Depends(require_designer)):
    return {"applications": [
        {
            "id": a["id"], "submission_id": a["submission_id"], "title": a["title"],
            "company": a["company"], "location": a.get("role_location") or "",
            "note": a.get("note") or "", "stage": a.get("stage") or 0,
            "created_at": a["created_at"],
            # The employer moves this; the designer only ever reads it.
            "stage_label": APPLICANT_STAGES[a["stage"]] if 0 <= (a.get("stage") or 0) < len(APPLICANT_STAGES) else "Applied",
            "listing_status": a.get("listing_status") or "",
        }
        for a in list_self_applications(designer["id"])
    ]}


@app.delete("/api/designers/me/applications/{applicant_id}")
def designer_withdraw_application(applicant_id: int, designer: dict = Depends(require_designer)):
    if not withdraw_self_application(designer["id"], applicant_id):
        raise HTTPException(404, "No such application.")
    return {"ok": True}


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
    """A real, plain overlap filter — no scoring model, no fabricated "why you
    matched" copy beyond what actually overlaps.

    Discipline is the gate, as before. Shared skills only reorder what already
    matched: a role wanting three skills you list should sit above one wanting
    none, but a skill in common is not on its own a reason to show someone a
    role in a different discipline. Each match carries the overlap so the UI
    can say why, rather than asserting a match and leaving the reader to
    guess."""
    disciplines = set(parse_multi_field(designer.get("discipline")))
    if not disciplines:
        return {"jobs": []}
    my_skills = {s.lower() for s in parse_multi_field(designer.get("skills"))}
    combined, _ = _combined_jobs()

    matches = []
    for j in combined:
        if j["cat"] not in disciplines:
            continue
        shared = [s for s in (j.get("skills") or []) if s.lower() in my_skills]
        matches.append({**j, "matched_skills": shared})
    matches.sort(key=lambda j: (-len(j["matched_skills"]), j["days"]))
    return {"jobs": matches[:20]}


def _messages_out(conversation_id: int) -> list[dict]:
    """A removed message keeps its place in the thread rather than vanishing:
    the other side needs to see that something was there and is gone, or the
    conversation stops making sense. The body is dropped here rather than in
    the database, so moderation keeps what it acted on."""
    out = []
    for m in list_messages(conversation_id):
        if (m.get("status") or "visible") == "removed":
            m = {**m, "body": "", "removed": True}
        out.append(m)
    return out


def _designer_conversation_out(conv: dict, viewer_designer_id: int) -> dict:
    if conv.get("peer_designer_id") is not None:
        other_id = conv["peer_designer_id"] if conv["designer_id"] == viewer_designer_id else conv["designer_id"]
        other = get_designer(other_id)
        return {
            **conv, "peer": _designer_public(other) if other else None,
            "company_name": "", "company_slug": "",
        }
    company = get_company(conv["company_id"])
    return {**conv, "company_name": company["name"] if company else "", "company_slug": company["slug"] if company else ""}


@app.get("/api/designers/me/conversations")
def designer_list_conversations(designer: dict = Depends(require_designer)):
    convs = list_conversations_for_designer(designer["id"])
    return {"conversations": [_designer_conversation_out(c, designer["id"]) for c in convs]}


@app.post("/api/designers/me/conversations")
def designer_start_conversation(payload: DesignerConversationStart, designer: dict = Depends(require_designer)):
    if payload.peer_designer_id is not None:
        if payload.peer_designer_id == designer["id"]:
            raise HTTPException(400, "can't message yourself")
        peer = get_designer(payload.peer_designer_id)
        if not peer or peer["status"] != "approved":
            raise HTTPException(404, "no such designer")
        conversation_id = get_or_create_peer_conversation(designer["id"], payload.peer_designer_id, designer["id"])
    else:
        company = get_company(payload.company_id)
        if not company or company["status"] != "approved":
            raise HTTPException(404, "no such company")
        conversation_id = get_or_create_conversation(designer["id"], payload.company_id, "designer")
    create_message(conversation_id, "designer", designer["id"], None, payload.body)
    _notify_other_party(conversation_id, "designer", designer["id"],
                        designer["display_name"], payload.body)
    return {"ok": True, "conversation_id": conversation_id}


def _require_own_conversation(conversation_id: int, designer_id: int) -> dict:
    conv = get_conversation(conversation_id)
    if not conv or (conv["designer_id"] != designer_id and conv.get("peer_designer_id") != designer_id):
        raise HTTPException(404, "no such conversation")
    return conv


@app.get("/api/designers/me/conversations/{conversation_id}/messages")
def designer_get_messages(conversation_id: int, designer: dict = Depends(require_designer)):
    _require_own_conversation(conversation_id, designer["id"])
    mark_conversation_read(conversation_id, "designer", designer["id"])
    return {"messages": _messages_out(conversation_id)}


@app.post("/api/designers/me/conversations/{conversation_id}/messages")
def designer_send_message(conversation_id: int, payload: MessageIn, designer: dict = Depends(require_designer)):
    _require_own_conversation(conversation_id, designer["id"])
    create_message(conversation_id, "designer", designer["id"], None, payload.body)
    _notify_other_party(conversation_id, "designer", designer["id"],
                        designer["display_name"], payload.body)
    return {"ok": True}


@app.post("/api/designers/me/conversations/{conversation_id}/messages/{message_id}/report")
def designer_report_message(conversation_id: int, message_id: int, payload: ReportIn,
                             designer: dict = Depends(require_designer)):
    _require_own_conversation(conversation_id, designer["id"])
    message = get_message(message_id)
    if not message or message["conversation_id"] != conversation_id:
        raise HTTPException(404, "no such message")
    create_report("message", message_id, designer["id"], None, payload.summary)
    return {"ok": True}


FOLLOW_TARGET_TYPES = {"designer", "company"}


@app.get("/api/designers/me/follows")
def designer_list_follows(designer: dict = Depends(require_designer)):
    """Each row carries enough to render without a lookup per follow. The
    target_type/target_id pair is unchanged, since the Follow buttons match on
    it; name, handle and photo are additions."""
    out = []
    for f in list_follows_for_designer(designer["id"]):
        row = {**f, "name": "", "handle": "", "photo_path": ""}
        if f["target_type"] == "designer":
            d = get_designer(f["target_id"])
            if d and d["status"] == "approved":
                pub = _designer_public(d)
                row.update(name=pub.get("display_name") or "", handle=pub.get("handle") or "",
                           photo_path=pub.get("photo_path") or "")
        elif f["target_type"] == "company":
            co = get_company(f["target_id"])
            if co and co["status"] == "approved":
                row.update(name=co["name"], handle=co["slug"], photo_path=co.get("logo_path") or "")
        out.append(row)
    return {"follows": out}


@app.post("/api/designers/me/follows/{target_type}/{target_id}")
def designer_toggle_follow(target_type: str, target_id: int, designer: dict = Depends(require_designer)):
    if target_type not in FOLLOW_TARGET_TYPES:
        raise HTTPException(400, "unknown follow target type")
    if target_type == "designer":
        if target_id == designer["id"]:
            raise HTTPException(400, "can't follow yourself")
        if not get_designer(target_id):
            raise HTTPException(404, "no such designer")
    else:
        if not get_company(target_id):
            raise HTTPException(404, "no such company")
    following = toggle_follow_target(designer["id"], target_type, target_id)
    # Being followed was completely silent: the follower got a pressed button,
    # the person followed got nothing at all. Only on the follow, never the
    # unfollow — nobody needs to be told they lost a follower.
    if following and target_type == "designer":
        pub = _designer_public(designer)
        who = pub.get("display_name") or "Someone"
        notify("designer", target_id, "new_follower",
               f"{who} is following your work",
               "They'll see it when you publish something new.",
               f"/designers/{pub.get('handle') or designer['id']}")
    return {"ok": True, "following": following, "followers": count_followers(target_type, target_id)}


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
    if employer and employer.get("locked_until"):
        minutes_left = _lockout_minutes_left(employer["locked_until"])
        if minutes_left > 0:
            raise HTTPException(423, {"message": f"Too many attempts. Try again in {minutes_left} minutes.", "locked": True})
    if not employer or not bcrypt.checkpw(payload.password.encode(), employer["password_hash"].encode()):
        if employer:
            result = record_employer_login_failure(employer["id"])
            if result["locked_until"]:
                raise HTTPException(423, {"message": f"Too many attempts. Try again in {LOGIN_LOCKOUT_MINUTES} minutes.", "locked": True})
            remaining = LOGIN_LOCKOUT_THRESHOLD - result["attempts"]
            if remaining <= 2:
                raise HTTPException(401, {
                    "message": "That password doesn't match this email",
                    "attempts_remaining": remaining,
                })
        raise HTTPException(401, "Incorrect email or password.")
    reset_employer_login_failures(employer["id"])
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
    return {"ok": True, "token": create_employer_session(employer_id)}


@app.get("/api/employers/me")
def employer_me(employer: dict = Depends(require_employer_any_status)):
    company = get_company(employer["company_id"])
    return {
        **_employer_public(employer), "email": employer["email"],
        "email_verified": bool(employer["email_verified"]),
        "is_pending_approval": bool(employer["is_pending_approval"]),
        "suspended": bool(employer["suspended"]),
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
                cross_border_note=s.get("cross_border_note", ""),
                accepts_applications=bool(s.get("accepts_applications")),
                submission_id=s["id"],
            ).to_web()
        )
    return out


def _company_slug(name: str) -> str:
    """Mirror of guessSlug() in pp-company.html, so a company that only exists
    as a name on a scraped listing still gets one stable, shareable URL that
    both sides derive the same way."""
    base = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if not base or not base[:1].isalpha():
        base = "company" + base
    base = base[:30]
    while len(base) < 3:
        base += "0"
    return base


@app.get("/api/companies")
def companies_directory():
    """Every company currently hiring on the board.

    Built from the board rather than the companies table on purpose: the table
    only holds employers who registered here (one, today), while the board
    carries roles from dozens. A directory that showed only the former would be
    accurate and useless. Registered companies are merged in on top, so they
    bring their logo and blurb with them."""
    combined, _ = _combined_jobs()
    by_name: dict[str, dict] = {}
    for j in combined:
        name = (j.get("co") or "").strip()
        if not name:
            continue
        entry = by_name.setdefault(name, {
            "name": name, "slug": _company_slug(name), "roles": 0,
            "disciplines": [], "open_to_africa": 0, "locations": [],
            "logo_path": "", "blurb": "", "registered": False,
        })
        entry["roles"] += 1
        if j.get("elig") in ("kenya", "africa"):
            entry["open_to_africa"] += 1
        for key, field in (("cat", "disciplines"), ("city", "locations")):
            v = (j.get(key) or "").strip()
            if v and v not in entry[field]:
                entry[field].append(v)

    for c in list_companies(status="approved"):
        entry = by_name.get(c["name"])
        if entry is None:
            entry = by_name.setdefault(c["name"], {
                "name": c["name"], "slug": c["slug"], "roles": 0, "disciplines": [],
                "open_to_africa": 0, "locations": [], "logo_path": "", "blurb": "",
                "registered": False,
            })
        entry.update({"slug": c["slug"], "registered": True,
                      "logo_path": c.get("logo_path") or "",
                      "blurb": c.get("blurb") or ""})

    out = sorted(by_name.values(), key=lambda e: (-e["roles"], e["name"].lower()))
    return {"count": len(out), "companies": out}


@app.get("/api/companies/{slug}/team")
def company_team(slug: str):
    """The designers who say they work here, for any company on the board.

    Separate from /api/companies/{identifier} because that one only knows
    companies registered on Kazi and 404s for the rest — which is almost all
    of them. A company page for Moniepoint has to be able to ask this too."""
    company = get_company_by_slug(slug)
    name = company["name"] if company and company["status"] == "approved" else ""
    if not name:
        combined, _ = _combined_jobs()
        name = next((j["co"] for j in combined
                     if _company_slug(j.get("co") or "") == slug), "")
    if not name:
        return {"count": 0, "team": []}

    members = {}
    if company and company["status"] == "approved":
        members = {d["id"]: d for d in list_designers_by_company(company["id"])}
    for d in list_designers_at_company_name(name):
        members.setdefault(d["id"], d)
    team = [_designer_public(d) for d in members.values()]
    return {"count": len(team), "team": team}


@app.get("/api/companies/{identifier}")
def company_public_page(identifier: str):
    company = get_company(int(identifier)) if identifier.isdigit() else None
    if not company:
        company = get_company_by_slug(identifier)
    if not company or company["status"] != "approved":
        raise HTTPException(404, "no such company")
    # Two sources, because company_id only exists for companies registered on
    # Kazi and almost none are: designers explicitly attached to this company,
    # plus anyone whose current Experience entry names it. Deduped on id.
    members = {d["id"]: d for d in list_designers_by_company(company["id"])}
    for d in list_designers_at_company_name(company["name"]):
        members.setdefault(d["id"], d)
    team = [_designer_public(d) for d in members.values()]
    return {**company, "listings": _company_listings(company), "design_team": team,
            "followers_count": count_followers("company", company["id"])}


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
    sub_id = insert_submission(_listing_row(payload), company_id=employer["company_id"], employer_id=employer["id"])
    return {"ok": True, "id": sub_id, "status": "pending"}


def _listing_row(payload: JobSubmission) -> dict:
    """Pydantic gives lists and a bool; sqlite wants JSON text and an int."""
    row = payload.model_dump()
    row["skills"] = json.dumps(row.get("skills") or [])
    row["screening"] = json.dumps(row.get("screening") or [])
    row["portfolio_required"] = 1 if row.get("portfolio_required") else 0
    row["accepts_applications"] = 1 if row.get("accepts_applications") else 0
    return row


@app.get("/api/employers/me/listings/{sub_id}")
def employer_get_listing(sub_id: int, employer: dict = Depends(require_employer)):
    return _owned_submission(sub_id, employer["company_id"])


@app.put("/api/employers/me/listings/{sub_id}")
def employer_update_listing(sub_id: int, payload: JobSubmission, employer: dict = Depends(require_employer_role("owner", "can_post"))):
    _owned_submission(sub_id, employer["company_id"])
    update_submission(sub_id, **_listing_row(payload))
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
    before = get_applicant(applicant_id, employer["company_id"])
    updated = set_applicant_stage(applicant_id, employer["company_id"], payload.stage)
    if not updated:
        raise HTTPException(404, "no such applicant")

    # Being moved to Interviewing is the most consequential thing that happens
    # to a designer on this site, and it was completely silent — visible only
    # if they happened to open the Applications tab and notice the word had
    # changed. Only forward: an employer correcting a mis-click backwards is
    # not news worth sending, and "you've been moved back to Reviewing" is a
    # message nobody needs.
    if before and before.get("designer_id") and payload.stage > (before.get("stage") or 0):
        sub = get_submission(before["submission_id"]) or {}
        company = get_company(employer["company_id"]) or {}
        notify("designer", int(before["designer_id"]), "application_stage",
               f"{company.get('name') or 'An employer'} moved you to "
               f"{APPLICANT_STAGES[payload.stage]}",
               sub.get("title") or "", "/dashboard?tab=applications")
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

    # The whole invite lifecycle used to be silent. Two people needed telling
    # here: whoever sent the invite, and — when the new account needs an
    # owner's approval — the owners, who otherwise had no idea anyone was
    # waiting on them. That's how someone ends up pending indefinitely.
    who = payload.full_name or invite["invited_email"]
    company = get_company(invite["company_id"])
    company_name = (company or {}).get("name") or "your company"
    if invite["invited_by_employer_id"]:
        notify("employer", int(invite["invited_by_employer_id"]), "invite_accepted",
               f"{who} accepted your invite",
               f"They've joined {company_name}.", "/employer?tab=team")
    if invite["needs_approval"]:
        for e in list_employers_for_company(invite["company_id"]):
            if e["team_role"] == "owner" and e["id"] != employer_id:
                notify("employer", e["id"], "teammate_pending",
                       f"{who} is waiting for you to approve them",
                       "They can't post or manage anything until an owner approves.",
                       "/employer?tab=team")
    return {"ok": True, "token": create_employer_session(employer_id), "needs_approval": bool(invite["needs_approval"])}


@app.post("/api/employers/invites/{token}/decline")
def decline_team_invite(token: str):
    invite = get_team_invite_by_token(token)
    if not invite or invite["status"] != "pending":
        raise HTTPException(404, "This invite is no longer valid.")
    set_invite_status(token, "declined")
    if invite["invited_by_employer_id"]:
        notify("employer", int(invite["invited_by_employer_id"]), "invite_declined",
               f"{invite['invited_email']} declined your invite",
               "Nothing else happens — you can invite someone else whenever.",
               "/employer?tab=team")
    return {"ok": True}


@app.post("/api/employers/me/team/{target_id}/approve")
def employer_approve_teammate(target_id: int, employer: dict = Depends(require_employer_role("owner"))):
    target = get_employer(target_id)
    if not target or target["company_id"] != employer["company_id"]:
        raise HTTPException(404, "no such teammate")
    was_pending = bool(target["is_pending_approval"])
    approve_pending_employer(target_id)
    # They've been looking at a banner saying someone needs to approve them.
    # This is that someone doing it — worth saying so.
    if was_pending:
        company_name = (get_company(employer["company_id"]) or {}).get("name") or "your company"
        notify("employer", target_id, "teammate_approved",
               "You're approved", f"You can post roles for {company_name} now.", "/employer")
        if target.get("email"):
            send_teammate_approved_email(target["email"], target.get("full_name") or "Hello", company_name)
    return {"ok": True}


@app.post("/api/employers/me/team/{target_id}/decline")
def employer_decline_teammate(target_id: int, employer: dict = Depends(require_employer_role("owner"))):
    # "Decline says nothing was shared" — the pending account is removed
    # outright, same as it never having been created.
    target = get_employer(target_id)
    if not target or target["company_id"] != employer["company_id"] or not target["is_pending_approval"]:
        raise HTTPException(404, "no such pending teammate")
    # Read before deleting: the account is about to stop existing, so email is
    # the only channel left. Without this someone signs up through an invite
    # and then simply finds their brand-new password doesn't work.
    company_name = (get_company(employer["company_id"]) or {}).get("name") or "the company"
    email, name = target.get("email"), target.get("full_name") or "Hello"
    delete_employer(target_id)
    if email:
        send_teammate_declined_email(email, name, company_name)
    return {"ok": True}


@app.delete("/api/employers/me/team/{target_id}")
def employer_remove_teammate(target_id: int, employer: dict = Depends(require_employer_role("owner"))):
    target = get_employer(target_id)
    if not target or target["company_id"] != employer["company_id"]:
        raise HTTPException(404, "no such teammate")
    if target["team_role"] == "owner" and count_company_owners(employer["company_id"], exclude_id=target_id) == 0:
        raise HTTPException(400, "Can't remove the only owner — add another owner first.")
    company_name = (get_company(employer["company_id"]) or {}).get("name") or "the company"
    email, name = target.get("email"), target.get("full_name") or "Hello"
    delete_employer(target_id)
    if email:
        send_teammate_removed_email(email, name, company_name)
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
    _company = get_company(employer["company_id"])
    _notify_other_party(conversation_id, "employer", None,
                        (_company or {}).get("name", "A company"), payload.body)
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
    return {"messages": _messages_out(conversation_id)}


@app.post("/api/employers/me/conversations/{conversation_id}/messages")
def employer_send_message(conversation_id: int, payload: MessageIn, employer: dict = Depends(require_employer)):
    _require_company_conversation(conversation_id, employer["company_id"])
    create_message(conversation_id, "employer", None, employer["id"], payload.body)
    _company = get_company(employer["company_id"])
    _notify_other_party(conversation_id, "employer", None,
                        (_company or {}).get("name", "A company"), payload.body)
    return {"ok": True}


@app.post("/api/employers/me/conversations/{conversation_id}/messages/{message_id}/report")
def employer_report_message(conversation_id: int, message_id: int, payload: ReportIn,
                             employer: dict = Depends(require_employer)):
    _require_company_conversation(conversation_id, employer["company_id"])
    message = get_message(message_id)
    if not message or message["conversation_id"] != conversation_id:
        raise HTTPException(404, "no such message")
    create_report("message", message_id, None, employer["id"], payload.summary)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Community: sessions (fixed-capacity seat reservations — an admin pastes in
# a joining_link string, there's no real video/calendar integration, same
# non-transactional treatment as the employer dashboard's Billing tab) and
# a designer-run Q&A board. Public reads, designer-authenticated writes;
# admin session-creation lands in a later milestone.
# ---------------------------------------------------------------------------

def _session_out(session: dict, designer_id: int | None = None) -> dict:
    seats = count_session_seats(session["id"])
    out = {
        **session,
        "bring_list": json.loads(session["bring_list"] or "[]"),
        "agenda": json.loads(session["agenda"] or "[]"),
        "seats_taken": seats["taken"],
        "seats_waitlisted": seats["waitlisted"],
    }
    booking = get_booking(session["id"], designer_id) if designer_id is not None else None
    if designer_id is not None:
        out["your_status"] = booking["status"] if booking else None

    # The joining link goes to people who took a seat, and nobody else. The
    # detail page has always gated it on your_status === "booked", but the API
    # handed it to anyone who asked — including anonymously, and including
    # someone who had cancelled. A six-seat room whose link is public isn't a
    # six-seat room.
    if not (booking and booking["status"] == "booked"):
        out["joining_link"] = ""
    return out


@app.get("/api/community/sessions")
def community_list_sessions(status: str = "upcoming", designer: Optional[dict] = Depends(optional_designer)):
    rows = list_community_sessions(status=status if status != "all" else None)
    return {"sessions": [_session_out(s, designer["id"] if designer else None) for s in rows]}


@app.get("/api/community/sessions/{session_id}")
def community_get_session(session_id: int, designer: Optional[dict] = Depends(optional_designer)):
    session = get_community_session(session_id)
    if not session:
        raise HTTPException(404, "no such session")
    return _session_out(session, designer["id"] if designer else None)


@app.post("/api/community/sessions/{session_id}/book")
def community_book_session(session_id: int, designer: dict = Depends(require_designer)):
    session = get_community_session(session_id)
    if not session or session["status"] != "scheduled":
        raise HTTPException(404, "no such session")
    # A scheduled session whose date has passed is still "scheduled" — nothing
    # sweeps the status — so this checked the flag and happily took a booking
    # for a meeting that already happened, handing over its joining link. The
    # list endpoints have always filtered on the date; this did not.
    if (session.get("session_date") or "") < date.today().isoformat():
        raise HTTPException(400, "That session has already happened.")
    status = book_session(session_id, designer["id"], session["seats"])
    return {"ok": True, "status": status}


@app.post("/api/community/sessions/{session_id}/cancel-booking")
def community_cancel_booking(session_id: int, designer: dict = Depends(require_designer)):
    if not get_community_session(session_id):
        raise HTTPException(404, "no such session")
    cancel_booking(session_id, designer["id"])
    return {"ok": True}


@app.get("/api/community/sessions/{session_id}/attendees")
def community_session_attendees(session_id: int, designer: dict = Depends(require_designer)):
    if not get_community_session(session_id):
        raise HTTPException(404, "no such session")
    if not get_booking(session_id, designer["id"]):
        raise HTTPException(403, "you're not attending this session")
    return {"attendees": list_session_bookings(session_id)}


@app.get("/api/designers/me/community-sessions")
def designer_my_sessions(designer: dict = Depends(require_designer)):
    rows = list_bookings_for_designer(designer["id"])
    for r in rows:
        r["bring_list"] = json.loads(r["bring_list"] or "[]")
        r["agenda"] = json.loads(r["agenda"] or "[]")
    return {"sessions": rows}


def _question_out(question: dict) -> dict:
    designer = get_designer(question["designer_id"])
    return {
        **question,
        "author_name": designer["display_name"] if designer else "",
        "author_handle": designer["handle"] if designer else "",
        "author_photo": designer["photo_path"] if designer else "",
        "reply_count": len(list_replies(question["id"])),
    }


@app.get("/api/community/questions")
def community_list_questions(topic: str = ""):
    rows = list_questions(topic=topic or None)
    return {"questions": [_question_out(q) for q in rows]}


@app.get("/api/community/questions/{question_id}")
def community_get_question(question_id: int, sort: str = "useful"):
    question = get_question(question_id)
    if not question or question["status"] != "visible":
        raise HTTPException(404, "no such question")
    return {**_question_out(question), "replies": list_replies(question_id, sort=sort)}


@app.post("/api/community/questions")
def community_create_question(payload: QuestionIn, designer: dict = Depends(require_designer)):
    question_id = create_question(designer["id"], payload.topic, payload.title, payload.body)
    return {"ok": True, "question_id": question_id}


@app.post("/api/community/questions/{question_id}/replies")
def community_create_reply(question_id: int, payload: ReplyIn, designer: dict = Depends(require_designer)):
    question = get_question(question_id)
    if not question or question["status"] != "visible":
        raise HTTPException(404, "no such question")
    reply_id = create_reply(question_id, designer["id"], payload.body)

    # An answer nobody is told about is the whole failure mode of a Q&A: you
    # ask, you leave, and you never learn that someone replied. The author
    # first, then anyone watching the thread — deduped, and never the person
    # who just wrote it.
    pub = _designer_public(designer)
    who = pub.get("display_name") or "Someone"
    told = {designer["id"]}
    author_id = question["designer_id"]
    if author_id not in told:
        told.add(author_id)
        notify("designer", author_id, "reply",
               f"{who} answered your question",
               payload.body[:200], f"/community/q-{question_id}")
    for watcher in question_follower_ids(question_id):
        if watcher in told:
            continue
        told.add(watcher)
        notify("designer", watcher, "reply",
               f"{who} replied to a question you follow",
               payload.body[:200], f"/community/q-{question_id}")
    return {"ok": True, "reply_id": reply_id}


@app.post("/api/community/questions/{question_id}/accept-reply")
def community_accept_reply(question_id: int, reply: ReplyAccept, designer: dict = Depends(require_designer)):
    question = get_question(question_id)
    if not question:
        raise HTTPException(404, "no such question")
    if question["designer_id"] != designer["id"]:
        raise HTTPException(403, "only the question's author can accept a reply")
    if reply.reply_id is not None:
        reply_row = get_reply(reply.reply_id)
        if not reply_row or reply_row["question_id"] != question_id:
            raise HTTPException(404, "no such reply")
    set_accepted_reply(question_id, reply.reply_id)

    # Being marked as the answer is the only reward this place offers, so it is
    # worth saying out loud. Not to yourself, if you accepted your own.
    if reply.reply_id is not None:
        answerer = reply_row["designer_id"]
        if answerer != designer["id"]:
            asker = (_designer_public(designer).get("display_name") or "The asker")
            notify("designer", answerer, "accepted",
                   f"{asker} marked your reply as the answer",
                   (question.get("title") or "")[:200], f"/community/q-{question_id}")
    return {"ok": True}


@app.post("/api/community/questions/{question_id}/follow")
def community_follow_question(question_id: int, designer: dict = Depends(require_designer)):
    if not get_question(question_id):
        raise HTTPException(404, "no such question")
    following = toggle_follow(question_id, designer["id"])
    return {"ok": True, "following": following}


@app.post("/api/community/replies/{reply_id}/vote")
def community_vote_reply(reply_id: int, designer: dict = Depends(require_designer)):
    reply = get_reply(reply_id)
    if not reply or reply["status"] != "visible":
        raise HTTPException(404, "no such reply")
    voted = toggle_vote(reply_id, designer["id"])
    return {"ok": True, "voted": voted}


@app.post("/api/community/questions/{question_id}/report")
def community_report_question(question_id: int, payload: ReportIn, designer: dict = Depends(require_designer)):
    if not get_question(question_id):
        raise HTTPException(404, "no such question")
    create_report("community_question", question_id, designer["id"], None, payload.summary)
    return {"ok": True}


@app.post("/api/community/replies/{reply_id}/report")
def community_report_reply(reply_id: int, payload: ReportIn, designer: dict = Depends(require_designer)):
    if not get_reply(reply_id):
        raise HTTPException(404, "no such reply")
    create_report("community_reply", reply_id, designer["id"], None, payload.summary)
    return {"ok": True}


@app.get("/api/community/leaderboard")
def community_leaderboard():
    return {"leaderboard": get_reply_leaderboard()}


@app.get("/api/community/work-worth-reading")
def community_work_worth_reading():
    return {"work": list_work_worth_reading()}


# ---------------------------------------------------------------------------
# Resources: anonymous pay-range aggregates. designer_id is stored on
# pay_submissions purely for anti-abuse/dedup and must never appear in any
# response here — list_pay_ranges() enforces that by never selecting it.
# ---------------------------------------------------------------------------

@app.get("/api/resources/pay-ranges")
def resources_pay_ranges(discipline: str = "", market: str = ""):
    return {"ranges": list_pay_ranges(discipline=discipline or None, market=market or None)}


@app.post("/api/resources/pay-submissions")
def resources_submit_pay(payload: PaySubmissionIn, designer: dict = Depends(require_designer)):
    usd = payload.amount * PAY_CURRENCY_TO_USD[payload.currency]
    median = get_pay_median(payload.discipline, payload.level, payload.market)
    if median is None:
        outlier_check = "First submission for this group — nothing to compare yet."
    elif usd > median * 1.6:
        outlier_check = f"Well above the current median (${median:,.0f}/mo) for this group."
    elif usd < median * 0.5:
        outlier_check = f"Well below the current median (${median:,.0f}/mo) for this group."
    else:
        outlier_check = f"Within range of the current median (${median:,.0f}/mo)."
    create_pay_submission(designer["id"], payload.discipline, payload.level, payload.market,
                           payload.currency, payload.amount, usd, outlier_check)
    return {"ok": True}


def _notify_other_party(conversation_id: int, sender_type: str, sender_designer_id,
                        sender_name: str, preview: str) -> None:
    """Tell whoever didn't send. Called after the message is already stored, so
    a failure here costs a notification and never the message itself.

    An employer-side conversation notifies every teammate at that company: the
    person who started the thread may not be the one who reads it, and a
    message that only reached one inbox is how candidates get ignored."""
    conv = get_conversation(conversation_id)
    if not conv:
        return
    body = (preview or "").strip().replace("\n", " ")[:120]

    if sender_type == "employer":
        notify("designer", conv["designer_id"], "message",
               sender_name + " sent you a message", body, "/dashboard?tab=messages")
        return

    # Designer sent. Peer conversations go to the other designer; company
    # conversations go to the whole team.
    peer = conv.get("peer_designer_id")
    if peer is not None:
        other = peer if conv["designer_id"] == sender_designer_id else conv["designer_id"]
        notify("designer", other, "message", sender_name + " sent you a message", body, "/dashboard?tab=messages")
        return
    for emp in list_employers_for_company(conv["company_id"]):
        notify("employer", emp["id"], "message",
               sender_name + " replied", body, "/employer?tab=messages")


def _notif_out(n: dict) -> dict:
    return {"id": n["id"], "kind": n["kind"], "title": n["title"], "body": n["body"],
            "href": n["href"], "read": bool(n["read_at"]), "created_at": n["created_at"]}


@app.get("/api/designers/me/notifications")
def designer_notifications(designer: dict = Depends(require_designer)):
    rows = list_notifications("designer", designer["id"])
    return {"unread": count_unread_notifications("designer", designer["id"]),
            "notifications": [_notif_out(n) for n in rows]}


@app.post("/api/designers/me/notifications/read")
def designer_notifications_read(notification_id: Optional[int] = None,
                                designer: dict = Depends(require_designer)):
    mark_notifications_read("designer", designer["id"], notification_id)
    return {"ok": True}


@app.get("/api/employers/me/notifications")
def employer_notifications(employer: dict = Depends(require_employer)):
    rows = list_notifications("employer", employer["id"])
    return {"unread": count_unread_notifications("employer", employer["id"]),
            "notifications": [_notif_out(n) for n in rows]}


@app.post("/api/employers/me/notifications/read")
def employer_notifications_read(notification_id: Optional[int] = None,
                                employer: dict = Depends(require_employer)):
    mark_notifications_read("employer", employer["id"], notification_id)
    return {"ok": True}


class ShortlistIn(BaseModel):
    designer_id: int
    note: str = ""


@app.get("/api/employers/me/role-performance")
def employer_role_performance(employer: dict = Depends(require_employer)):
    """Views and applies per live role. The only metric here that answers a
    question a recruiter can act on: a role people open and don't apply to is
    telling you something about the description or the pay. Deliberately not a
    dashboard — three numbers per role, no charts, nothing that needs
    interpreting."""
    combined, _gen = _combined_jobs()
    mine = [j for j in combined if j.get("src") == "Direct"
            and j.get("co") == (get_company(employer["company_id"]) or {}).get("name")]
    perf = count_role_performance([j["id"] for j in mine])
    out = []
    for j in mine:
        p = perf.get(j["id"], {"views": 0, "applies": 0})
        out.append({"id": j["id"], "title": j["t"], "views": p["views"], "applies": p["applies"]})
    out.sort(key=lambda r: -r["views"])
    return {"roles": out}


@app.get("/api/employers/me/talent")
def employer_talent_search(
    q: str = "", discipline: str = "", country: str = "",
    open_to: str = "", availability: str = "",
    employer: dict = Depends(require_employer),
):
    """Let a company find designers. The directory was public all along; what
    was missing was any way for the other half of the marketplace to reach it,
    so an employer could post a role but never look for anyone to fill it.

    Deliberately the same corpus and the same _designer_public() shape the
    public directory serves — this exposes nothing a visitor couldn't already
    see, it just makes it searchable by the people doing the hiring."""
    rows = list_approved_designers(discipline=discipline or None)
    saved = set(shortlist_ids(employer["company_id"]))

    needle = q.strip().lower()
    out = []
    for d in rows:
        pub = _designer_public(d)
        if country and (pub.get("country") or "") != country:
            continue
        if availability and (pub.get("availability_status") or "") != availability:
            continue
        if open_to and open_to not in (pub.get("open_to") or []):
            continue
        if needle:
            hay = " ".join([
                pub.get("display_name", ""), pub.get("headline", ""), pub.get("bio", ""),
                pub.get("location", ""), " ".join(pub.get("skills") or []),
                " ".join(pub.get("discipline") or []),
            ]).lower()
            if needle not in hay:
                continue
        pub["shortlisted"] = d["id"] in saved
        out.append(pub)
    return {"count": len(out), "designers": out}


@app.get("/api/employers/me/shortlist")
def employer_shortlist(employer: dict = Depends(require_employer)):
    entries = {e["designer_id"]: e for e in shortlist_entries(employer["company_id"])}
    out = []
    for did, entry in entries.items():
        d = get_designer(did)
        # A designer who has since been suspended or removed drops out rather
        # than lingering as a broken row on someone's shortlist.
        if not d or d["status"] != "approved":
            continue
        pub = _designer_public(d)
        pub["shortlisted"] = True
        pub["note"] = entry["note"]
        pub["saved_at"] = entry["saved_at"]
        out.append(pub)
    return {"count": len(out), "designers": out}


@app.post("/api/employers/me/shortlist")
def employer_shortlist_add(payload: ShortlistIn, employer: dict = Depends(require_employer)):
    d = get_designer(payload.designer_id)
    if not d or d["status"] != "approved":
        raise HTTPException(404, "no such designer")
    already = payload.designer_id in set(shortlist_ids(employer["company_id"]))
    shortlist_add(employer["company_id"], payload.designer_id, payload.note.strip()[:500], employer["id"])
    if not already:
        # Only on the first save — re-saving to edit a note is not news, and the
        # private note itself is never shown to the designer.
        company = get_company(employer["company_id"])
        notify("designer", payload.designer_id, "shortlisted",
               (company or {}).get("name", "A company") + " saved your profile",
               "They're looking at designers for a role.", "/dashboard")
    return {"ok": True}


@app.delete("/api/employers/me/shortlist/{designer_id}")
def employer_shortlist_remove(designer_id: int, employer: dict = Depends(require_employer)):
    if not shortlist_remove(employer["company_id"], designer_id):
        raise HTTPException(404, "not on the shortlist")
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
    d = get_designer(designer_id)
    if not d:
        raise HTTPException(404, "no such designer")
    was = d.get("status")
    set_designer_status(designer_id, "approved")
    # Someone signs up, sees "in review", and the review finishing was the one
    # moment nothing was sent. Only on the transition, so re-approving an
    # already-approved account doesn't send a second "you're live" email.
    if was != "approved":
        handle = d.get("handle") or designer_id
        notify("designer", designer_id, "profile_approved",
               "Your profile is live",
               "You can be found in the directory and apply to roles here.",
               f"/designers/{handle}")
        if d.get("email"):
            send_designer_approved_email(d["email"], d.get("display_name") or "Hello", str(handle))
    return {"ok": True}


@app.post("/api/admin/designers/{designer_id}/reject")
def admin_reject_designer(designer_id: int, payload: DesignerReview = DesignerReview(),
                          _: None = Depends(require_admin)):
    d = get_designer(designer_id)
    if not d:
        raise HTTPException(404, "no such designer")
    was = d.get("status")
    set_designer_status(designer_id, "rejected")
    # Silence is the worst option here: someone never told waits indefinitely
    # for a decision that has already been made.
    if was != "rejected":
        notify("designer", designer_id, "profile_rejected",
               "Your profile hasn't been published",
               payload.reason.strip() or "Have a look at the house rules and edit it from your dashboard.",
               "/dashboard?tab=profile")
        if d.get("email"):
            send_designer_rejected_email(d["email"], d.get("display_name") or "Hello", payload.reason)
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


@app.get("/api/admin/designers/{designer_id}")
def admin_get_designer(designer_id: int, _: None = Depends(require_admin)):
    """The designer detail page: full profile plus the moderation-relevant
    surface — activity feed and reports against them — a plain list row
    doesn't carry. Same activity-merge shape as the public /activity
    endpoint, just without the approved-only gate."""
    d = get_designer(designer_id)
    if not d:
        raise HTTPException(404, "no such designer")
    activity = []
    for q in list_questions_by_designer(designer_id):
        activity.append({"type": "question", "text": q["title"], "when": q["created_at"]})
    for r in list_replies_by_designer(designer_id):
        activity.append({"type": "reply", "text": r["question_title"], "when": r["created_at"]})
    for b in list_bookings_for_designer(designer_id):
        activity.append({"type": "session", "text": b["title"], "when": b["session_date"]})
    activity.sort(key=lambda i: i["when"], reverse=True)
    reports = [r for r in list_reports() if r["kind"] == "profile" and r["target_id"] == designer_id]
    return {
        **d,
        "discipline": parse_multi_field(d["discipline"]),
        "skills": parse_multi_field(d.get("skills")),
        "links": list_designer_links(designer_id),
        "projects": list_designer_projects(designer_id),
        "role_history": list_role_history(designer_id),
        "followers_count": count_followers("designer", designer_id),
        "activity": activity[:20],
        "reports": reports,
    }


@app.post("/api/admin/designers/{designer_id}/suspend")
def admin_suspend_designer(designer_id: int, payload: DesignerSuspend, _: None = Depends(require_admin)):
    """Her profile comes down and she can't message or apply — status flips
    to 'suspended', which the public directory/profile queries and
    require_designer() already treat as invisible/blocked. Community posts
    stay up, credited to the account as-is."""
    if not get_designer(designer_id):
        raise HTTPException(404, "no such designer")
    _suspend_designer_and_tell(designer_id, payload.rule, payload.reason)
    return {"ok": True}


def _suspend_designer_and_tell(designer_id: int, rule: str, reason: str) -> None:
    """The only way a designer should ever reach 'suspended'.

    There are two routes to this state — the suspend dialog and resolving a
    report with action='suspend' — and the second one called
    set_designer_status() directly, so it recorded no rule, no reason, and
    sent nothing. Someone suspended that way saw a banner with no explanation
    and got no email. Same bug as the dialog had, surviving on the path that
    wasn't the one it was found on.
    """
    d = get_designer(designer_id)
    if not d or d.get("status") == "suspended":
        return
    suspend_designer(designer_id, rule, reason)
    # Email, not a notification: a suspended account is blocked from the
    # endpoints an in-app notification would be read through.
    if d.get("email"):
        send_designer_suspended_email(d["email"], d.get("display_name") or "Hello", reason)


@app.post("/api/admin/designers/{designer_id}/unsuspend")
def admin_unsuspend_designer(designer_id: int, _: None = Depends(require_admin)):
    d = get_designer(designer_id)
    if not d:
        raise HTTPException(404, "no such designer")
    was = d.get("status")
    unsuspend_designer(designer_id)
    if was == "suspended" and d.get("email"):
        send_designer_unsuspended_email(d["email"], d.get("display_name") or "Hello")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin: companies. Same review-queue shape as designers above — a company
# and everyone on its team stays invisible (no public page, no listings)
# until an admin approves it here.
# ---------------------------------------------------------------------------

@app.get("/api/admin/companies")
def admin_list_companies(status: str = "pending", _: None = Depends(require_admin)):
    rows = list_companies(status=status if status != "all" else None)
    return [{**c, "team_size": len(list_employers_for_company(c["id"]))} for c in rows]


@app.get("/api/admin/companies/{company_id}")
def admin_get_company(company_id: int, _: None = Depends(require_admin)):
    company = get_company(company_id)
    if not company:
        raise HTTPException(404, "no such company")
    return {**company, "team": [_employer_public(e) for e in list_employers_for_company(company_id)]}


def _tell_company(company_id: int, kind: str, title: str, body: str, href: str,
                  email_fn=None, *email_args) -> None:
    """Everyone on the team, not just the owner — any of them may be the one
    waiting to post, and the dashboard's review banner is the only other place
    this decision shows up. Emailed too: someone waiting to hear isn't sitting
    on the dashboard watching for a banner to change."""
    for e in list_employers_for_company(company_id):
        notify("employer", e["id"], kind, title, body, href)
        if email_fn and e.get("email"):
            email_fn(e["email"], *email_args)


@app.post("/api/admin/companies/{company_id}/approve")
def admin_approve_company(company_id: int, _: None = Depends(require_admin)):
    co = get_company(company_id)
    if not co:
        raise HTTPException(404, "no such company")
    was = co.get("status")
    set_company_status(company_id, "approved")
    # Same gap the designer side had: a company sat in review and the review
    # finishing sent nothing. The dashboard banner told them only if they
    # happened to come back and look.
    if was != "approved":
        _tell_company(company_id, "company_approved", "Your company page is live",
                      "You can post roles now, and your page is public.", "/employer?tab=company",
                      send_company_approved_email, co.get("name") or "Your company")
    return {"ok": True}


@app.post("/api/admin/companies/{company_id}/reject")
def admin_reject_company(company_id: int, payload: DesignerReview = DesignerReview(),
                         _: None = Depends(require_admin)):
    co = get_company(company_id)
    if not co:
        raise HTTPException(404, "no such company")
    was = co.get("status")
    set_company_status(company_id, "rejected")
    if was != "rejected":
        _tell_company(company_id, "company_rejected", "Your company page hasn't been published",
                      payload.reason.strip() or "Have a look at what's on it and edit it from your dashboard.",
                      "/employer?tab=company",
                      send_company_rejected_email, co.get("name") or "Your company", payload.reason)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin: reports. Polymorphic — the report row only stores kind + target_id,
# so every read resolves the actual content (and its author) live via joins
# rather than duplicating it onto the report row, which would go stale the
# moment the content changed.
# ---------------------------------------------------------------------------

def _report_target(report: dict) -> dict:
    kind, target_id = report["kind"], report["target_id"]
    if kind == "message":
        message = get_message(target_id)
        if not message:
            return {"content_deleted": True}
        if message["sender_type"] == "designer":
            author = get_designer(message["sender_designer_id"])
            by = author["display_name"] if author else "(deleted designer)"
        else:
            employer = get_employer(message["sender_employer_id"])
            by = employer["full_name"] if employer else "(deleted employer)"
        return {"by": by, "content": message["body"], "sender_type": message["sender_type"]}
    if kind == "community_reply":
        reply = get_reply(target_id)
        if not reply:
            return {"content_deleted": True}
        author = get_designer(reply["designer_id"])
        question = get_question(reply["question_id"])
        return {
            "by": author["display_name"] if author else "(deleted designer)",
            "content": reply["body"],
            "about": question["title"] if question else "",
            "reply_status": reply["status"],
        }
    if kind == "community_question":
        question = get_question(target_id)
        if not question:
            return {"content_deleted": True}
        author = get_designer(question["designer_id"])
        return {
            "by": author["display_name"] if author else "(deleted designer)",
            "content": question["title"] + " — " + question["body"],
            "question_status": question["status"],
        }
    return {"content_deleted": True}


def _report_out(report: dict) -> dict:
    reporter = "Anonymous"
    if report["reporter_designer_id"]:
        d = get_designer(report["reporter_designer_id"])
        reporter = d["display_name"] if d else "(deleted designer)"
    elif report["reporter_employer_id"]:
        e = get_employer(report["reporter_employer_id"])
        reporter = e["full_name"] if e else "(deleted employer)"
    return {**report, "reporter": reporter, "target": _report_target(report)}


@app.get("/api/admin/reports")
def admin_list_reports(status: str = "open", _: None = Depends(require_admin)):
    rows = list_reports(status=status if status != "all" else None)
    return [_report_out(r) for r in rows]


@app.get("/api/admin/reports/{report_id}")
def admin_get_report(report_id: int, _: None = Depends(require_admin)):
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "no such report")
    return _report_out(report)


def _report_author(kind: str, target_id: int):
    """The account behind a reported item, whichever kind it is. Returns
    (designer_dict | None, employer_dict | None)."""
    if kind == "message":
        m = get_message(target_id)
        if not m:
            return None, None
        if m["sender_type"] == "designer" and m["sender_designer_id"]:
            return get_designer(m["sender_designer_id"]), None
        if m["sender_type"] == "employer" and m["sender_employer_id"]:
            return None, get_employer(m["sender_employer_id"])
        return None, None
    if kind == "community_reply":
        r = get_reply(target_id)
        return (get_designer(r["designer_id"]) if r else None), None
    if kind == "community_question":
        q = get_question(target_id)
        return (get_designer(q["designer_id"]) if q else None), None
    return None, None


def _warn_report_author(kind: str, target_id: int, what: str, ask: bool = False) -> None:
    designer, employer = _report_author(kind, target_id)
    account = designer or employer
    if not account or not account.get("email"):
        return
    name = account.get("display_name") or account.get("full_name") or "Hello"
    if ask:
        send_explain_yourself_email(account["email"], name, what)
    else:
        send_content_removed_email(account["email"], name, what)
        if designer:
            notify("designer", designer["id"], "content_removed",
                   "Something you posted was removed",
                   f"{what.capitalize()} was reported and taken down.", "/community/rules")


@app.post("/api/admin/reports/{report_id}/resolve")
def admin_resolve_report(report_id: int, payload: ReportResolve, _: None = Depends(require_admin)):
    report = get_report(report_id)
    if not report:
        raise HTTPException(404, "no such report")
    action = payload.action
    kind, target_id = report["kind"], report["target_id"]

    WHAT = {"message": "a message you sent", "community_reply": "a reply you posted",
            "community_question": "a question you posted"}
    what = WHAT.get(kind, "something you posted")

    if action == "remove":
        if kind == "community_reply":
            set_reply_status(target_id, "removed")
        elif kind == "community_question":
            set_question_status(target_id, "removed")
        elif kind == "message":
            # Messages had no status at all, so this option removed nothing on
            # a message report and only marked the report resolved — the one
            # place removal matters most. The row is kept and its body dropped
            # on the way out, so the thread still shows something was there.
            set_message_status(target_id, "removed")
        # "Remove the content and warn the account" — the warning half never
        # happened either.
        _warn_report_author(kind, target_id, what)

    if action == "ask":
        # This did nothing whatsoever: the report was marked resolved and
        # nobody was asked anything.
        _warn_report_author(kind, target_id, what, ask=True)

    if action == "suspend":
        # Reason recorded and sent, same as a suspension from the dialog. The
        # reporter's own words aren't repeated back — a report is one side of
        # a story — so this says what was found and where, and invites a reply.
        why = f"A report about {what} was reviewed and found to break the house rules."
        author_designer, author_employer = _report_author(kind, target_id)
        if author_designer:
            _suspend_designer_and_tell(author_designer["id"], "conduct", why)
        elif author_employer:
            set_employer_suspended(author_employer["id"], True)
        # Suspending is also a removal — leaving the reported thing up while
        # its author is suspended for posting it makes no sense.
        if kind == "community_reply":
            set_reply_status(target_id, "removed")
        elif kind == "community_question":
            set_question_status(target_id, "removed")
        elif kind == "message":
            set_message_status(target_id, "removed")

    # Whoever reported it hears that it was looked at. Deliberately without
    # the outcome: what happened to someone else's account isn't the
    # reporter's to know, and saying nothing at all is what makes reporting
    # feel like shouting into a hole.
    if report.get("reporter_designer_id"):
        notify("designer", int(report["reporter_designer_id"]), "report_reviewed",
               "Thanks — we looked at what you reported",
               "Someone read it and acted on it. We can't share what happened to "
               "another account, but the report wasn't ignored.", "/community/rules")
    elif report.get("reporter_employer_id"):
        notify("employer", int(report["reporter_employer_id"]), "report_reviewed",
               "Thanks — we looked at what you reported",
               "Someone read it and acted on it.", "/employer")

    resolve_report(report_id, action)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin: community sessions (scheduling) + a stale-questions flag for the
# Q&A board. Mirrors the designer-facing session shape exactly — an admin
# is just writing the same fields a designer reads.
# ---------------------------------------------------------------------------

@app.post("/api/admin/community/sessions")
def admin_create_session(payload: CommunitySessionIn, _: None = Depends(require_admin)):
    session_id = create_community_session(
        title=payload.title, kind=payload.kind, session_date=payload.session_date,
        time=payload.time, length=payload.length, blurb=payload.blurb, host=payload.host,
        host_initials=payload.host_initials, host_bg=payload.host_bg, host_fg=payload.host_fg,
        reviewer_bio=payload.reviewer_bio, seats=payload.seats, joining_link=payload.joining_link,
        bring_list=json.dumps(payload.bring_list), agenda=json.dumps(payload.agenda),
    )
    return {"ok": True, "session_id": session_id}


@app.patch("/api/admin/community/sessions/{session_id}")
def admin_update_session(session_id: int, payload: CommunitySessionUpdate, _: None = Depends(require_admin)):
    if not get_community_session(session_id):
        raise HTTPException(404, "no such session")
    fields = payload.model_dump(exclude_none=True)
    if "bring_list" in fields:
        fields["bring_list"] = json.dumps(fields["bring_list"])
    if "agenda" in fields:
        fields["agenda"] = json.dumps(fields["agenda"])
    if fields:
        update_community_session(session_id, **fields)
    return {"ok": True}


@app.post("/api/admin/community/sessions/{session_id}/cancel")
def admin_cancel_session(session_id: int, _: None = Depends(require_admin)):
    if not get_community_session(session_id):
        raise HTTPException(404, "no such session")
    set_session_status(session_id, "cancelled")
    return {"ok": True}


@app.get("/api/admin/community/questions")
def admin_flagged_questions(flagged: str = "", _: None = Depends(require_admin)):
    if flagged == "stale":
        return {"questions": count_stale_questions()}
    return {"questions": list_questions()}


# ---------------------------------------------------------------------------
# Admin: pay-data moderation queue. Same review-queue shape used everywhere
# else in this file — a submission stays out of the public aggregate until
# accepted here. outlier_check was computed once, at submission time in
# api/resources/pay-submissions, against whatever the median was then.
# ---------------------------------------------------------------------------

@app.get("/api/admin/pay-submissions")
def admin_list_pay_submissions(status: str = "pending", _: None = Depends(require_admin)):
    rows = list_pay_submissions(status=status if status != "all" else None)
    return [{k: v for k, v in r.items() if k != "designer_id"} for r in rows]


@app.post("/api/admin/pay-submissions/{submission_id}/accept")
def admin_accept_pay_submission(submission_id: int, _: None = Depends(require_admin)):
    if not set_pay_submission_status(submission_id, "accepted"):
        raise HTTPException(404, "no such pay submission")
    return {"ok": True}


@app.post("/api/admin/pay-submissions/{submission_id}/reject")
def admin_reject_pay_submission(submission_id: int, _: None = Depends(require_admin)):
    if not set_pay_submission_status(submission_id, "rejected"):
        raise HTTPException(404, "no such pay submission")
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


@app.get("/api/designers/{identifier}/activity")
def designer_public_activity(identifier: str):
    """Community activity feed for a public profile's Community tab —
    questions asked, replies posted, and sessions attended, merged and
    sorted newest-first. Capped per source in db.py, then capped again here
    after merging so the feed itself stays short."""
    designer = get_designer(int(identifier)) if identifier.isdigit() else None
    if not designer:
        designer = get_designer_by_handle(identifier)
    if not designer or designer["status"] != "approved":
        raise HTTPException(404, "no such designer")
    items = []
    for q in list_questions_by_designer(designer["id"]):
        items.append({"type": "question", "text": q["title"], "when": q["created_at"]})
    for r in list_replies_by_designer(designer["id"]):
        items.append({"type": "reply", "text": r["question_title"], "when": r["created_at"]})
    for b in list_bookings_for_designer(designer["id"]):
        items.append({"type": "session", "text": b["title"], "when": b["session_date"]})
    items.sort(key=lambda i: i["when"], reverse=True)
    return {"activity": items[:20]}


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


# Path & Pixel cutover: clean URLs now serve the pp-*.html pages instead of
# the pre-migration ones (retired to web/legacy/, not deleted, not routed to).
# admin.html is the one holdout — an internal tool, not the public site, so
# it keeps its old chrome and its slot in this same clean-page mechanism.
PP_CLEAN_PAGES = {
    "": "pp-homepage.html",
    "jobs": "pp-jobs.html",
    "people": "pp-people.html",
    "signin": "pp-auth.html",
    "onboarding": "pp-onboarding.html",
    "dashboard": "pp-dashboard.html",
    # The employer dashboard was the one signed-in surface with no clean path,
    # so links to it had to name the .html file directly. /employer matches
    # /dashboard on the designer side.
    "employer": "pp-employer.html",
    "post-a-role": "pp-employer-onboarding.html",
    "terms": "terms.html",
    "privacy": "privacy.html",
    "cookies": "cookies.html",
    "admin": "admin.html",
}
# These three get a share card built from live data, so they are registered
# explicitly below instead of by the generic loop.
_RICH_META_PAGES = {"", "jobs", "people"}

for _path, _file in PP_CLEAN_PAGES.items():
    if _path in _RICH_META_PAGES:
        continue
    def _make_page_route(file: str):
        def _serve():
            return FileResponse(WEB_DIR / file)
        return _serve
    app.get(f"/{_path}", include_in_schema=False)(_make_page_route(_file))

def _board_summary() -> tuple[int, int]:
    """(roles listed, roles open somewhere in Africa). Used in share cards, so
    it has to be the real number — an inflated one is the fastest way to lose
    the trust this board is built on."""
    combined, _ = _combined_jobs()
    african = sum(1 for j in combined if j.get("elig") in ("kenya", "africa"))
    return len(combined), african


@app.get("/", include_in_schema=False)
def homepage(request: Request):
    base = _site_base(request)
    total, african = _board_summary()
    return _page_with_head(
        "pp-homepage.html",
        title="Path & Pixel — design jobs you can actually apply to",
        description=(f"{total} design roles, {african} of them open to designers across Africa. "
                     "Every listing says who it can hire before you spend an evening applying."),
        canonical=f"{base}/", image=f"{base}/logo.png", request=request)


@app.get("/jobs", include_in_schema=False)
def jobs_page(request: Request):
    base = _site_base(request)
    total, african = _board_summary()
    return _page_with_head(
        "pp-jobs.html", title="Design jobs · Path & Pixel",
        description=(f"{total} design roles across product, brand, research and motion — "
                     f"{african} open to designers in Africa. Filter by where you can work from."),
        canonical=f"{base}/jobs", image=f"{base}/logo.png", request=request)


@app.get("/people", include_in_schema=False)
def people_page(request: Request):
    base = _site_base(request)
    return _page_with_head(
        "pp-people.html", title="Designers · Path & Pixel",
        description="Portfolios from designers working across Africa — the work, not just a CV.",
        canonical=f"{base}/people", image=f"{base}/logo.png", request=request)


# 301s for every old bookmark/link that no longer matches a same-named clean
# path — either because the old page was retired (post, cv-check, account,
# onboarding, login, signup, designers, index) or renamed outright (join).
_PP_REDIRECTS = {
    "post": "/post-a-role", "post.html": "/post-a-role",
    "cv-check": "/resources", "cv-check.html": "/resources",
    "account": "/dashboard", "account.html": "/dashboard",
    "onboarding.html": "/onboarding",
    "login": "/signin", "login.html": "/signin",
    "signup": "/signin?view=signup", "signup.html": "/signin?view=signup",
    "join": "/signin?view=signup",
    "designers": "/people",
    "index.html": "/",
}
for _name, _target in _PP_REDIRECTS.items():
    def _make_redirect_route(target: str):
        # Carry the query string across. Dropping it silently broke every
        # password-reset link that pointed at /login?reset=… — the token was
        # gone by the time the page loaded. Old links are still in inboxes, so
        # this matters beyond the sender being fixed.
        def _redirect(request: Request):
            q = request.url.query
            if not q:
                return RedirectResponse(url=target, status_code=301)
            joiner = "&" if "?" in target else "?"
            return RedirectResponse(url=f"{target}{joiner}{q}", status_code=301)
        return _redirect
    app.get(f"/{_name}", include_in_schema=False)(_make_redirect_route(_target))

# One dynamic page route: /designers/{id-or-handle} always serves the same
# static file — pp-profile.html reads the identifier from location.pathname
# client-side and fetches GET /api/designers/{identifier} itself.
@app.get("/designers/{identifier}", include_in_schema=False)
def designer_profile_page(identifier: str, request: Request):
    base = _site_base(request)
    # The pitch to designers is "a link to send instead of a PDF nobody opens".
    # A link that unfurls into a blank card is not that link.
    designer = None
    try:
        for d in list_approved_designers():
            if str(d.get("handle") or "") == identifier or str(d.get("id")) == identifier:
                designer = d
                break
    except Exception:
        designer = None
    if designer is None:
        return FileResponse(WEB_DIR / "pp-profile.html")

    name = (designer.get("display_name") or "").strip() or "A designer"
    disciplines = parse_multi_field(designer.get("discipline"))
    where = (designer.get("country") or designer.get("location") or "").strip()
    subtitle = ", ".join(disciplines[:2]) or "Designer"
    description = (designer.get("headline") or "").strip() or (designer.get("bio") or "").strip()
    if not description:
        description = f"{subtitle} on Path & Pixel" + (f", based in {where}." if where else ".")
    photo = (designer.get("photo_path") or "").strip()
    return _page_with_head(
        "pp-profile.html",
        title=f"{name} — {subtitle}" + (f", {where}" if where else ""),
        description=_clip(description),
        canonical=f"{base}/designers/{designer.get('handle') or designer.get('id')}",
        image=f"{base}{photo}" if photo.startswith("/") else f"{base}/logo.png", request=request)


# The company pages had no routes at all: pp-company.html was built, had a
# working API behind it, and was reachable from nowhere. /companies is the
# directory; /companies/{slug} is one company, resolved from the path.
@app.get("/how-it-works", include_in_schema=False)
def how_it_works_page(request: Request):
    base = _site_base(request)
    return _page_with_head(
        "pp-how-it-works.html", title="How roles are checked · Path & Pixel",
        description="Where the roles come from, how we decide which are design roles, and "
                    "how we work out which countries each one can actually hire from.",
        canonical=f"{base}/how-it-works", image=f"{base}/logo.png", request=request)


@app.get("/companies", include_in_schema=False)
def companies_page(request: Request):
    base = _site_base(request)
    return _page_with_head(
        "pp-companies.html", title="Companies hiring designers · Path & Pixel",
        description="Every company with a design role open on Path & Pixel, and how many "
                    "of those roles are open to designers in Africa.",
        canonical=f"{base}/companies", image=f"{base}/logo.png", request=request)


@app.get("/companies/{slug}", include_in_schema=False)
def company_page(slug: str, request: Request):
    base = _site_base(request)
    combined, _ = _combined_jobs()
    name = next((j["co"] for j in combined if _company_slug(j.get("co") or "") == slug), "")
    record = get_company_by_slug(slug)
    if record and record.get("status") == "approved":
        name = record["name"]
    if not name:
        return FileResponse(WEB_DIR / "pp-company.html")
    roles = sum(1 for j in combined if (j.get("co") or "") == name)
    african = sum(1 for j in combined
                  if (j.get("co") or "") == name and j.get("elig") in ("kenya", "africa"))
    desc = f"{roles} design role{'' if roles == 1 else 's'} open at {name}"
    desc += f", {african} open to designers in Africa." if african else "."
    return _page_with_head(
        "pp-company.html", title=f"Design jobs at {name}", description=desc,
        canonical=f"{base}/companies/{slug}",
        image=f"{base}{record['logo_path']}" if record and record.get("logo_path") else f"{base}/logo.png", request=request)


# The invite link goes in an email to someone who has never used the product,
# so it was the last raw .html URL handed to a person. ?token= still resolves,
# since links already sent have to keep working.
@app.get("/invite/{token}", include_in_schema=False)
def invite_page(token: str):
    return FileResponse(WEB_DIR / "pp-invite.html")


# A case study is the thing a designer actually sends someone — "a link
# instead of a PDF nobody opens" is the promise on the homepage. It lived at
# /pp-case-study.html?designer=&project=, the only raw .html URL left in the
# product, and unfurled into a blank card. Nested under the designer because
# that is what it belongs to.
@app.get("/designers/{identifier}/{project_id}", include_in_schema=False)
def case_study_page(identifier: str, project_id: str, request: Request):
    base = _site_base(request)
    designer = None
    try:
        for d in list_approved_designers():
            if str(d.get("handle") or "") == identifier or str(d.get("id")) == identifier:
                designer = d
                break
    except Exception:
        designer = None
    if designer is None:
        return FileResponse(WEB_DIR / "pp-case-study.html")

    pub = _designer_public(designer)
    project = next((p for p in (pub.get("projects") or [])
                    if str(p.get("id")) == str(project_id)), None)
    if project is None:
        return _page_with_head(
            "pp-case-study.html", title="This project isn't available · Path & Pixel",
            description="The project you're looking for isn't published.",
            canonical=f"{base}/designers/{identifier}", image=f"{base}/logo.png",
            extra_head='<meta name="robots" content="noindex">\n', request=request)

    name = (pub.get("display_name") or "").strip()
    title = (project.get("title") or "Case study").strip()
    desc = (project.get("description") or "").strip()
    if not desc:
        disciplines = parse_multi_field(designer.get("discipline"))
        desc = f"A case study by {name}" + (f", {disciplines[0]}." if disciplines else ".")
    image = (project.get("image_path") or "").strip() or (pub.get("photo_path") or "").strip()
    return _page_with_head(
        "pp-case-study.html", title=f"{title} — {name}", description=_clip(desc),
        canonical=f"{base}/designers/{identifier}/{project_id}",
        image=f"{base}{image}" if image.startswith("/") else f"{base}/logo.png", request=request)


# Same pattern for a single job: /jobs/{id} always serves pp-job.html, which
# reads the id from location.pathname and fetches GET /api/jobs/{id} itself.
# --- What a crawler or a link preview actually sees -------------------------
# Every page on this site renders client-side, so the HTML that reaches a
# search crawler or a WhatsApp/LinkedIn link unfurler contains no job at all —
# just an empty shell whose <title> is the same "Job · Path & Pixel" on all
# fifty roles. Sharing a role produced a blank card, and nothing was indexable.
#
# Rendering these pages server-side would be a rewrite. Injecting the <head>
# is not, and the head is the entire thing a preview or a search result reads.
# The body stays exactly as it was and still fetches its own data.

def _site_base(request: Request) -> str:
    """Absolute origin for this request, so links work on fly.dev and on the
    custom domain without either being hardcoded here.

    TLS terminates at Fly's proxy, so the app sees plain http and base_url
    reports it. An http canonical on an https page is a different URL to a
    search engine, and an http og:image is blocked as mixed content by most
    link unfurlers — so trust X-Forwarded-Proto, which the proxy sets."""
    base = str(request.base_url).rstrip("/")
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    if proto in ("http", "https"):
        base = re.sub(r"^https?://", proto + "://", base)

    # The same site answers on both wabunifu.fly.dev and the custom domain, and
    # a self-canonicalising page on each means a search engine indexes two
    # complete copies and splits every ranking signal between them — with the
    # infrastructure hostname free to outrank the brand one. Every public URL
    # we emit points at one origin, whichever host actually served the request.
    # Local development is left alone so its links stay clickable.
    host = request.url.hostname or ""
    if PUBLIC_ORIGIN and host not in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return PUBLIC_ORIGIN
    return base


def _clip(text: str, limit: int = 300) -> str:
    """Trim to a word boundary. A link card truncates anyway, but cutting
    mid-word is visible and looks like a bug rather than an ellipsis."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.—-")
    return (cut or text[:limit]) + "…"


def _page_with_head(filename: str, *, title: str, description: str,
                    canonical: str, image: str, extra_head: str = "",
                    request: Optional[Request] = None) -> Response:
    """Serve a static page with its <title> replaced and social/meta tags added.

    Values are escaped for an HTML attribute context. extra_head is trusted
    caller-built markup (JSON-LD), never anything a user typed."""
    def esc(v: str) -> str:
        return html_mod.escape((v or "").strip(), quote=True)

    doc = (WEB_DIR / filename).read_text()
    head = (
        f'<meta name="description" content="{esc(description)}">\n'
        f'<link rel="canonical" href="{esc(canonical)}">\n'
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:site_name" content="Path &amp; Pixel">\n'
        f'<meta property="og:title" content="{esc(title)}">\n'
        f'<meta property="og:description" content="{esc(description)}">\n'
        f'<meta property="og:url" content="{esc(canonical)}">\n'
        f'<meta property="og:image" content="{esc(image)}">\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
        f'<meta name="twitter:title" content="{esc(title)}">\n'
        f'<meta name="twitter:description" content="{esc(description)}">\n'
        f'<meta name="twitter:image" content="{esc(image)}">\n'
        + extra_head
    )
    doc = re.sub(r"<title>.*?</title>", f"<title>{esc(title)}</title>", doc, count=1, flags=re.S)
    doc = doc.replace("</head>", head + "</head>", 1)

    # StaticFiles gives every file it serves an ETag, so a repeat visit costs a
    # 304 and no body. These pages are assembled here instead, so without this
    # they were the only ones re-downloading their whole HTML on every visit —
    # and they are the most visited ones. The tag covers the finished document,
    # so it changes when the file changes or when the injected values do.
    etag = '"' + hashlib.md5(doc.encode("utf-8")).hexdigest() + '"'
    if request is not None and request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"etag": etag})
    return HTMLResponse(doc, headers={"etag": etag})


def _job_share_text(job: dict) -> tuple[str, str]:
    """(title, description) for one role, written the way a person scanning a
    shared link reads it: who is hiring, for what, from where."""
    # The title stays short enough to survive a search result and a link card:
    # role and company only. Everything else earns its place in the description.
    title = f"{job['t']} at {job['co']}"

    where = job.get("city") or job.get("country") or ""
    # "Remote, Nigeria" is already a whole answer — prefixing it with "Remote"
    # again reads as a bug to anyone who sees the card.
    if job.get("work") == "Remote" and where and "remote" not in where.lower():
        where = f"Remote · {where}"
    elif job.get("work") == "Remote" and not where:
        where = "Remote"

    bits = [b for b in (job.get("level"), job.get("cat"), job.get("etype")) if b]
    lead = ", ".join(bits)
    pay = job.get("pay") or ""
    # "Not disclosed" is true but it is not worth one of the ~150 characters a
    # link card gets. Say nothing about pay rather than saying nothing useful.
    if pay.strip().lower() in ("not disclosed", "undisclosed", "n/a"):
        pay = ""
    # The eligibility line is the reason this board exists, so it leads the
    # description rather than being buried after the pay.
    elig = {"kenya": "Open to designers in Kenya",
            "africa": "Open to designers across Africa",
            "world": "Open worldwide"}.get(job.get("elig"), "")
    if job.get("elig") == "africa" and job.get("elig_scope"):
        elig = "Open to designers in " + job["elig_scope"]
    if job.get("elig") == "check" and job.get("elig_scope"):
        elig = "Open in " + job["elig_scope"]
    desc = " · ".join(b for b in (elig, where, lead, pay) if b)
    return title, _clip(desc or job.get("desc_text") or "")


@app.get("/jobs/{job_id}", include_in_schema=False)
def job_details_page(job_id: str, request: Request):
    base = _site_base(request)
    combined, _ = _combined_jobs()
    job = next((j for j in combined if j["id"] == job_id), None)
    if job is None:
        # Still the same page — it renders its own "this role is gone" state —
        # but nothing here should invite a crawler to index a dead listing.
        return _page_with_head(
            "pp-job.html", title="This role is no longer listed · Path & Pixel",
            description="This listing has closed or expired. See what else is open.",
            canonical=f"{base}/jobs", image=f"{base}/logo.png",
            extra_head='<meta name="robots" content="noindex">\n', request=request)

    title, description = _job_share_text(job)
    posted = (date.today() - timedelta(days=int(job.get("days") or 0))).isoformat()
    # JobPosting is what puts a role into Google Jobs, which for a board this
    # size is a bigger door than the site's own search ranking.
    ld = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": job["t"],
        "description": job.get("desc") or job.get("desc_text") or description,
        "datePosted": posted,
        "employmentType": job.get("etype") or None,
        "hiringOrganization": {"@type": "Organization", "name": job["co"]},
        "directApply": False,
    }
    if job.get("work") == "Remote":
        ld["jobLocationType"] = "TELECOMMUTE"
    if job.get("city") or job.get("country"):
        ld["jobLocation"] = {"@type": "Place", "address": {
            "@type": "PostalAddress",
            "addressLocality": job.get("city") or "",
            "addressCountry": job.get("country") or "",
        }}
    if job.get("closes"):
        ld["validThrough"] = job["closes"]
    ld = {k: v for k, v in ld.items() if v is not None}
    extra = ('<script type="application/ld+json">'
             + json.dumps(ld).replace("</", "<\\/") + "</script>\n")
    return _page_with_head(
        "pp-job.html", title=f"{title} · Path & Pixel", description=description,
        canonical=f"{base}/jobs/{job_id}", image=f"{base}/logo.png", extra_head=extra, request=request)


# Community: /community lists sessions/questions/work, /community/{id}
# serves one item — the id is prefixed ("s-3" / "q-17") so the client knows
# session vs. question without a lookup round-trip. Still unwired into
# pp-nav.js's placeholder ROUTES cutover like the rest of this migration —
# reachable directly for now, same as every other pp-*.html page so far.
# Nothing told a crawler these pages existed: no robots.txt, no sitemap, and
# fifty role pages reachable only by clicking through a client-rendered board.
@app.get("/robots.txt", include_in_schema=False)
def robots_txt(request: Request):
    base = _site_base(request)
    # Signed-in surfaces and the staff console are not secrets, but they are
    # useless in a search result and would only dilute what is worth indexing.
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Disallow: /dashboard",
        "Disallow: /employer",
        "Disallow: /onboarding",
        "Disallow: /api/",
        f"Sitemap: {base}/sitemap.xml",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(request: Request):
    base = _site_base(request)
    urls: list[tuple[str, str]] = [(f"{base}/", "daily"), (f"{base}/jobs", "daily"),
                                   (f"{base}/people", "weekly"), (f"{base}/companies", "daily"),
                                   (f"{base}/community", "weekly"),
                                   (f"{base}/resources", "monthly"), (f"{base}/how-it-works", "monthly"),
                                   (f"{base}/terms", "yearly"),
                                   (f"{base}/privacy", "yearly")]
    combined, _ = _combined_jobs()
    urls += [(f"{base}/jobs/{j['id']}", "weekly") for j in combined]
    for name in sorted({(j.get("co") or "").strip() for j in combined} - {""}):
        urls.append((f"{base}/companies/{_company_slug(name)}", "weekly"))
    # Only designers who are actually public — a sitemap pointing at a profile
    # the directory itself won't show is a 404 waiting to be crawled.
    try:
        for d in list_approved_designers():
            handle = d.get("handle") or d.get("id")
            if not handle:
                continue
            urls.append((f"{base}/designers/{handle}", "weekly"))
            for p in (_designer_public(d).get("projects") or []):
                urls.append((f"{base}/designers/{handle}/{p['id']}", "monthly"))
    except Exception:
        pass
    body = "".join(
        f"<url><loc>{html_mod.escape(u, quote=True)}</loc><changefreq>{f}</changefreq></url>"
        for u, f in urls
    )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + body + "</urlset>")
    return Response(content=xml, media_type="application/xml")


@app.get("/community", include_in_schema=False)
def community_page():
    return FileResponse(WEB_DIR / "pp-community.html")


# Registered before /community/{item_id} so "rules" is never swallowed as
# a session/question id — same care as /api/jobs/count vs /api/jobs/{job_id}.
@app.get("/community/rules", include_in_schema=False)
def community_rules_page():
    return FileResponse(WEB_DIR / "pp-house-rules.html")


@app.get("/community/{item_id}", include_in_schema=False)
def community_detail_page(item_id: str):
    return FileResponse(WEB_DIR / "pp-community-detail.html")


@app.get("/resources", include_in_schema=False)
def resources_page():
    return FileResponse(WEB_DIR / "pp-resources.html")


@app.get("/resources/{slug}", include_in_schema=False)
def resources_guide_page(slug: str):
    return FileResponse(WEB_DIR / "pp-guide.html")


# Static site last: /api/* and the routes above take priority, everything
# else falls through to web/ (jobs.json, css, js, images, ...).
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
