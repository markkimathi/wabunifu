"""
Chipper Cash's careers page — plain server-rendered Webflow HTML, no public
API. Their robots.txt has no Disallow rules at all (just a sitemap), and this
is the company's own careers page (not a third-party aggregator), so reading
it is uncontroversial — unlike the Kenyan boards this project deliberately
skips. This is company-specific (tied to Chipper's exact Webflow markup) —
not a reusable ATS fetcher like the others in sources/.
"""
from __future__ import annotations
import requests
from bs4 import BeautifulSoup

URL = "https://www.chippercash.com/career-current-openings"
TIMEOUT = 20


def fetch(_token: str = "") -> list[dict]:
    """Return raw job dicts. `_token` is unused — kept so run.py's dispatch
    (which always calls fetch(c["token"])) doesn't need a special case."""
    r = requests.get(URL, timeout=TIMEOUT,
                     headers={"User-Agent": "Mozilla/5.0 (KaziBot/0.1; +https://kazi.africa)"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []
    for card in soup.select("a.career-card"):
        title_el = card.select_one(".career-title")
        loc_el = card.select_one(".job-location-wrapper")
        if not title_el:
            continue
        jobs.append({
            "title": title_el.get_text(strip=True),
            "location": loc_el.get_text(strip=True) if loc_el else "",
            "url": card.get("href", ""),
        })
    return jobs


def to_common(raw: dict, company: str, token: str) -> dict:
    return {
        "title": raw.get("title", ""),
        "company": company,
        "department": "",
        "location": raw.get("location", ""),
        "body": "",
        "url": raw.get("url", ""),
        "source": "company site",
        "updated_at": "",
    }
