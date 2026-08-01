"""
HiBob careers-site API. Public, no key, structured JSON, but requires a
Referer header matching the career site's own origin, or it 401s.
Endpoint:  https://{token}.careers.hibob.com/api/job-ad

`token` is the subdomain in a company's HiBob careers URL, e.g.
  yoco.careers.hibob.com   -> token "yoco"
Find it by visiting a company's careers page and following the "Apply" link,
which typically points at {token}.careers.hibob.com.
"""
from __future__ import annotations
import html
import re
import requests

BASE = "https://{token}.careers.hibob.com/api/job-ad"
TIMEOUT = 20


def fetch(token: str) -> list[dict]:
    """Return raw job dicts for one company board. Network required."""
    referer = f"https://{token}.careers.hibob.com/"
    r = requests.get(BASE.format(token=token), timeout=TIMEOUT, headers={
        "User-Agent": "Mozilla/5.0 (KaziBot/0.1; +https://kazi.africa)",
        "Referer": referer,
    })
    r.raise_for_status()
    data = r.json()
    return data.get("jobAdDetails", [])


def to_common(raw: dict, company: str, token: str) -> dict:
    """Map a HiBob job into the neutral shape the pipeline consumes."""
    parts = [raw.get(k, "") for k in ("description", "requirements", "responsibilities")]
    body = html.unescape(re.sub(r"<[^>]+>", " ", " ".join(p or "" for p in parts)))
    workspace = raw.get("workspaceType", "") or ""
    return {
        "title": raw.get("title", "").strip(),
        "company": company,
        "department": raw.get("department", ""),
        "location": raw.get("site", "") or raw.get("country", ""),
        # Not truncated — see greenhouse.py's to_common for why.
        "body": f"{workspace} {body}",
        "url": f"https://{token}.careers.hibob.com/jobs/{raw.get('id', '')}/apply",
        "source": "HiBob",
        "updated_at": (raw.get("publishedAt") or "")[:10],
        "remote_flag": workspace.lower() == "remote",
    }
