"""Turn messy per-source fields into the clean values the schema expects."""
from __future__ import annotations
import re


def normalize_work_type(location: str, remote_flag: bool | None = None, body: str = "") -> str:
    text = f"{location} {body}".lower()
    if remote_flag is True or re.search(r"\bremote\b", text):
        if re.search(r"\bhybrid\b", text):
            return "Hybrid"
        return "Remote"
    if re.search(r"\bhybrid\b", text):
        return "Hybrid"
    return "On-site"


def clean_location(raw: str) -> str:
    if not raw:
        return "Remote"
    loc = re.sub(r"\s+", " ", raw).strip(" ,-")
    # collapse things like "Remote - Nigeria" -> "Remote" is wrong; keep country signal
    return loc


COUNTRY_HINTS = {
    "kenya": "Kenya", "nairobi": "Kenya", "nigeria": "Nigeria", "lagos": "Nigeria",
    "ghana": "Ghana", "accra": "Ghana", "south africa": "South Africa",
    "cape town": "South Africa", "johannesburg": "South Africa", "egypt": "Egypt",
    "cairo": "Egypt", "rwanda": "Rwanda", "kigali": "Rwanda", "uganda": "Uganda",
    "tanzania": "Tanzania",
}


def guess_country(location: str) -> str:
    loc = (location or "").lower()
    for hint, country in COUNTRY_HINTS.items():
        if hint in loc:
            return country
    return ""


SALARY_RE = re.compile(
    r"(\$|€|£|KES|USD|NGN|ZAR|GHS)\s?[\d][\d,\.]*\s?(k)?\s?(–|-|to)?\s?(\$|€|£|KES|USD)?\s?[\d,\.]*\s?(k)?",
    re.IGNORECASE,
)


def extract_salary(*texts: str) -> str | None:
    for t in texts:
        if not t:
            continue
        m = SALARY_RE.search(t)
        if m:
            return re.sub(r"\s+", " ", m.group(0)).strip()
    return None
