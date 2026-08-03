"""
Ashby job board API. Public, no key, structured JSON.
Endpoint:  https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true

`token` is the slug in a company's Ashby careers URL, e.g.
  jobs.ashbyhq.com/andela   -> token "andela"
Find it by visiting a company's Ashby careers page and reading the URL.
"""
from __future__ import annotations
import html
import re
import requests

BASE = "https://api.ashbyhq.com/posting-api/job-board/{token}"
TIMEOUT = 20


def fetch(token: str) -> list[dict]:
    """Return raw job dicts for one company board. Network required."""
    url = BASE.format(token=token)
    r = requests.get(url, params={"includeCompensation": "true"}, timeout=TIMEOUT,
                     headers={"User-Agent": "KaziBot/0.1 (+https://kazi.africa)"})
    r.raise_for_status()
    data = r.json()
    return data.get("jobs", [])


def to_common(raw: dict, company: str, token: str) -> dict:
    """Map an Ashby job into the neutral shape the pipeline consumes."""
    raw_html = raw.get("descriptionHtml", "") or ""
    desc = html.unescape(re.sub(r"<[^>]+>", " ", raw_html))
    workplace = raw.get("workplaceType", "") or ""
    remote_flag = bool(raw.get("isRemote"))
    return {
        "title": raw.get("title", "").strip(),
        "company": company,
        "department": " ".join(filter(None, [raw.get("department", ""), raw.get("team", "")])),
        "location": raw.get("location", ""),
        # Not truncated — see greenhouse.py's to_common for why.
        "body": f"{workplace} {desc}",
        # Real markup, kept intact for desc_format.py.
        "body_html": html.unescape(raw_html),
        "url": raw.get("jobUrl", raw.get("applyUrl", "")),
        "source": "Ashby",
        "updated_at": (raw.get("publishedAt") or "")[:10],
        "remote_flag": remote_flag,
    }
