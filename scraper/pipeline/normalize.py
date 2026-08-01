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


# A bare currency symbol followed by a small number ("$22", "$250") matches
# all kinds of incidental dollar amounts in a job description that have
# nothing to do with pay — a fintech's product pricing, a stipend, a
# conference budget. Real salary disclosures are almost always either a
# range ("$45k-$70k", "KES 220,000 - 320,000") or use "k" shorthand
# ("$70k"), so require one of those two as a condition of a match,
# instead of accepting any lone currency+number pair.
CCY = r"(?:\$|€|£|KES|USD|NGN|ZAR|GHS)"
# Companies format the range separator inconsistently: hyphen, en dash (–),
# em dash (—, often what "&mdash;" unescapes to), or the word "to". Missing
# any one of these silently drops real salary data (seen live: Coinbase's
# "$207,485 — $244,100" used an em dash and matched nothing before this).
# \s* (not \s?): stripping adjacent HTML tags (e.g. "</span><span>") often
# collapses to two or more spaces, and a single optional \s? left the
# currency symbol on the far side of the range unreachable (seen live:
# GitLab's "$165,000  —  $200,000", two spaces on each side of the dash).
SALARY_RANGE_RE = re.compile(
    rf"{CCY}\s*[\d][\d,\.]*\s*k?\s*(?:–|—|-|to)\s*{CCY}?\s*[\d][\d,\.]*\s*k?",
    re.IGNORECASE,
)
SALARY_K_RE = re.compile(rf"{CCY}\s*[\d][\d,\.]*\s*k\b", re.IGNORECASE)


def extract_salary(*texts: str) -> str | None:
    for t in texts:
        if not t:
            continue
        m = SALARY_RANGE_RE.search(t) or SALARY_K_RE.search(t)
        if m:
            return re.sub(r"\s+", " ", m.group(0)).strip()
    return None
