"""
Lever postings API. Public, no key, JSON.
Endpoint:  https://api.lever.co/v0/postings/{company}?mode=json

`company` is the slug in jobs.lever.co/{company}.
"""
from __future__ import annotations
import html
import re
import requests

BASE = "https://api.lever.co/v0/postings/{company}"
TIMEOUT = 20


def fetch(company_slug: str) -> list[dict]:
    url = BASE.format(company=company_slug)
    r = requests.get(url, params={"mode": "json"}, timeout=TIMEOUT,
                     headers={"User-Agent": "KaziBot/0.1 (+https://kazi.africa)"})
    r.raise_for_status()
    return r.json()


def to_common(raw: dict, company: str, slug: str) -> dict:
    cats = raw.get("categories", {}) or {}
    desc = html.unescape(re.sub(r"<[^>]+>", " ", raw.get("descriptionPlain", raw.get("description", "")) or ""))
    workplace = cats.get("commitment", "")  # sometimes carries "Remote"/"Full-time"
    loc = cats.get("location", "")
    return {
        "title": raw.get("text", "").strip(),
        "company": company,
        "department": cats.get("team", "") or cats.get("department", ""),
        "location": loc,
        "body": (f"{workplace} {desc}")[:4000],
        "url": raw.get("hostedUrl", raw.get("applyUrl", "")),
        "source": "Lever",
        "updated_at": "",  # Lever createdAt is epoch ms; run.py fills a date if empty
        "created_ms": raw.get("createdAt"),
    }
