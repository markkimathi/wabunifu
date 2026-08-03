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
    # Greenhouse's `content` field is double HTML-escaped: raw JSON has
    # "&lt;div&gt;...&amp;mdash;..." rather than "<div>...&mdash;...". A
    # single unescape() only reveals the tags/entities, it doesn't resolve
    # them, so tags never got stripped and leftover entities like &mdash;
    # broke pay-range extraction. unescape() twice, then strip tags.
    raw_content = raw.get("content", "") or ""
    unescaped = html.unescape(html.unescape(raw_content))
    content = re.sub(r"<[^>]+>", " ", unescaped)
    loc = (raw.get("location") or {}).get("name", "")
    return {
        "title": raw.get("title", "").strip(),
        "company": company,
        "department": " ".join(d.get("name", "") for d in raw.get("departments", [])),
        "location": loc,
        # Not truncated: pay-transparency sections are commonly the very
        # last thing in a long JD (after the role copy and EEO boilerplate),
        # so a 4000-char cap was silently cutting salary ranges out before
        # extract_salary() ever saw them. This body is only used internally
        # for classification/salary extraction, never shown to users.
        "body": content,
        # Real markup, kept intact for desc_format.py to turn into the
        # structured HTML actually shown to users (see body's comment
        # above for why this stays separate from the flattened text).
        "body_html": unescaped,
        "url": raw.get("absolute_url", ""),
        "source": "Greenhouse",
        "updated_at": raw.get("updated_at", "")[:10],
    }
