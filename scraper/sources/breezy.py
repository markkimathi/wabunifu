"""
Breezy HR job board API. Public, no key, structured JSON.
Endpoint:  https://{token}.breezy.hr/json?verbose=true

`token` is the subdomain in a company's Breezy careers URL, e.g.
  wasoko.breezy.hr   -> token "wasoko"
Find it by visiting a company's careers page; Breezy boards are usually
embedded or linked from there.
"""
from __future__ import annotations
import html
import re
import requests

BASE = "https://{token}.breezy.hr/json"
TIMEOUT = 20


def fetch(token: str) -> list[dict]:
    """Return raw job dicts for one company board. Network required."""
    url = BASE.format(token=token)
    r = requests.get(url, params={"verbose": "true"}, timeout=TIMEOUT,
                     headers={"User-Agent": "KaziBot/0.1 (+https://kazi.africa)"})
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def to_common(raw: dict, company: str, token: str) -> dict:
    """Map a Breezy job into the neutral shape the pipeline consumes."""
    raw_html = raw.get("description", "") or ""
    desc = html.unescape(re.sub(r"<[^>]+>", " ", raw_html))
    loc = (raw.get("location") or {}).get("name", "")
    return {
        "title": raw.get("name", "").strip(),
        "company": company,
        "department": raw.get("department", ""),
        "location": loc,
        # Not truncated — see greenhouse.py's to_common for why.
        "body": desc,
        # Real markup, kept intact for desc_format.py.
        "body_html": html.unescape(raw_html),
        "url": raw.get("url", ""),
        "source": "Breezy",
        "updated_at": (raw.get("published_date") or "")[:10],
    }
