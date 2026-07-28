"""
Greenhouse job board API. Public, no key, structured JSON.
Endpoint:  https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

`token` is the board slug in a company's careers URL, e.g.
  boards.greenhouse.io/gitlab      -> token "gitlab"
Find it by visiting a company's Greenhouse careers page and reading the URL.
"""
from __future__ import annotations
import html
import re
import requests

BASE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
TIMEOUT = 20


def fetch(token: str) -> list[dict]:
    """Return raw job dicts for one company board. Network required."""
    url = BASE.format(token=token)
    r = requests.get(url, params={"content": "true"}, timeout=TIMEOUT,
                     headers={"User-Agent": "KaziBot/0.1 (+https://kazi.africa)"})
    r.raise_for_status()
    data = r.json()
    return data.get("jobs", [])


def to_common(raw: dict, company: str, token: str) -> dict:
    """Map a Greenhouse job into the neutral shape the pipeline consumes."""
    content = html.unescape(re.sub(r"<[^>]+>", " ", raw.get("content", "") or ""))
    loc = (raw.get("location") or {}).get("name", "")
    return {
        "title": raw.get("title", "").strip(),
        "company": company,
        "department": " ".join(d.get("name", "") for d in raw.get("departments", [])),
        "location": loc,
        "body": content[:4000],
        "url": raw.get("absolute_url", ""),
        "source": "Greenhouse",
        "updated_at": raw.get("updated_at", "")[:10],
    }
