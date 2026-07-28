"""
The same role shows up on the company site AND Greenhouse AND a job board.
Store it once. We key on (company, normalized-title, location) and keep the
record with the most information (salary present, real apply URL, etc.).
"""
from __future__ import annotations
import re
from models import Job


def _norm_title(t: str) -> str:
    t = t.lower()
    t = re.sub(r"\(.*?\)", "", t)                      # drop "(Remote)", "(Contract)"
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\b(sr|snr)\b", "senior", t)
    t = re.sub(r"\bjr\b", "junior", t)
    return re.sub(r"\s+", " ", t).strip()


def _completeness(j: Job) -> int:
    score = 0
    if j.salary:
        score += 2
    if j.url and j.url.startswith("http"):
        score += 1
    if j.eligibility != "check":
        score += 1
    return score


def dedupe(jobs: list[Job]) -> list[Job]:
    """
    Key on (company, normalized-title). Location is deliberately NOT in the key:
    the same req is mirrored across boards with inconsistent location strings
    ("Remote" vs "Remote - Africa"), so including it would let duplicates through.

    Tradeoff: a company with two genuinely different openings of the same title
    (e.g. two "Product Designer" reqs in different cities) collapses to one. At
    this scale that's rare and the right call; when it starts to bite, add a
    normalized country back into the key.
    """
    best: dict[tuple, Job] = {}
    for j in jobs:
        key = (j.company.lower().strip(), _norm_title(j.title))
        if key not in best or _completeness(j) > _completeness(best[key]):
            best[key] = j
    return list(best.values())
