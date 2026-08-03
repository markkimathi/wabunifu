"""
Pinpoint ATS RSS feed. Public, no key, XML: an RSS feed is explicitly meant
for external consumption, so this is the cleanest-conscience source in the
whole pipeline.
Endpoint:  https://{token}.pinpointhq.com/jobs.rss

`token` is the subdomain in a company's Pinpoint careers URL, e.g.
  sunking.pinpointhq.com   -> token "sunking"
"""
from __future__ import annotations
import html
import re
import xml.etree.ElementTree as ET
import requests

BASE = "https://{token}.pinpointhq.com/jobs.rss"
TIMEOUT = 20
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"


def fetch(token: str) -> list[dict]:
    """Return raw <item> elements (as dicts) for one company's feed."""
    r = requests.get(BASE.format(token=token), timeout=TIMEOUT,
                     headers={"User-Agent": "KaziBot/0.1 (+https://kazi.africa)"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall(".//item"):
        items.append({
            "title": (item.findtext("title") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "description": item.findtext("description") or "",
            "content": item.findtext(CONTENT_NS) or "",
            "pubDate": item.findtext("pubDate") or "",
        })
    return items


LOCATION_RE = re.compile(r"(?:job\s*)?location:\s*(.+?)(?:about|$)", re.IGNORECASE)


def _extract_location(description: str) -> str:
    """Pinpoint descriptions conventionally open with 'Job Location: City,
    Country' (or just 'Location: ...') before the role copy starts."""
    m = LOCATION_RE.search(description or "")
    return m.group(1).strip(" .") if m else ""


def to_common(raw: dict, company: str, token: str) -> dict:
    """Map a Pinpoint RSS item into the neutral shape the pipeline consumes."""
    raw_html = raw.get("content") or raw.get("description") or ""
    body = html.unescape(re.sub(r"<[^>]+>", " ", raw_html))
    return {
        "title": raw.get("title", ""),
        "company": company,
        "department": "",
        "location": _extract_location(raw.get("description", "")),
        # Not truncated — see greenhouse.py's to_common for why.
        "body": body,
        # Real markup (RSS content:encoded), kept intact for desc_format.py.
        "body_html": html.unescape(raw_html),
        "url": raw.get("link", ""),
        "source": "Pinpoint",
        "updated_at": _parse_rfc822_date(raw.get("pubDate", "")),
    }


def _parse_rfc822_date(s: str) -> str:
    """RSS pubDate is RFC 822 ('Mon, 21 Jul 2025 12:03:46 +0100') -> ISO date."""
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except Exception:
        return ""
