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
from typing import Optional
import sys
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
from models import Job, DISCIPLINES, ELIGIBILITY, MAX_AGE_DAYS  # noqa: E402  (reuse the canonical schema)

from .db import init_db, insert_submission, list_submissions, get_submission, set_status  # noqa: E402

WEB_DIR = ROOT / "web"
JOBS_JSON = WEB_DIR / "jobs.json"
WORK_TYPES = {"Remote", "Hybrid", "On-site"}
LEVELS = {"Junior", "Mid", "Senior", "Lead"}

# Dev default so `uvicorn api.main:app` works out of the box. Set a real
# KAZI_ADMIN_TOKEN env var before deploying anywhere reachable; anyone with
# this token can approve/reject submissions.
ADMIN_TOKEN = os.environ.get("KAZI_ADMIN_TOKEN", "dev-only-change-me")

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


def require_admin(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(401, "invalid or missing admin token")


@app.post("/api/submissions")
def submit_job(payload: JobSubmission):
    sub_id = insert_submission(payload.model_dump())
    return {"ok": True, "id": sub_id, "status": "pending"}


@app.get("/api/jobs")
def get_jobs():
    scraped: list[dict] = []
    generated_at = None
    if JOBS_JSON.exists():
        data = json.loads(JOBS_JSON.read_text())
        scraped = data.get("jobs", [])
        generated_at = data.get("generated_at")

    approved = list_submissions(status="approved")
    employer_jobs = [
        Job(
            title=s["title"], company=s["company"], url=s["url"], source="Direct",
            location=s["location"], work_type=s["work_type"], discipline=s["discipline"],
            level=s["level"], eligibility=s["eligibility"], salary=s.get("salary"),
            desc=s.get("description"), posted_at=s["created_at"][:10],
        ).to_web()
        for s in approved
    ]

    # Enforced again here (not just at scrape time) so the cutoff holds live
    # even if a scrape run is skipped, and so it also applies to employer
    # submissions, which run.py never sees.
    combined = [j for j in employer_jobs + scraped if j["days"] <= MAX_AGE_DAYS]
    combined.sort(key=lambda j: j["days"])
    return {
        "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
        "count": len(combined),
        "jobs": combined,
    }


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


# Clean URLs: serve the .html files without the extension...
CLEAN_PAGES = ["post", "admin", "terms", "privacy", "cookies"]
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

# Static site last: /api/* and the routes above take priority, everything
# else falls through to web/ (jobs.json, css, js, images, ...).
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
