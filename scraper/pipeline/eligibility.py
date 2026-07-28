"""
Kazi's whole reason to exist: answer "can a designer in Kenya actually apply?"
before the user clicks.

Returns one of four badges:
  kenya  -> role is in Kenya (on-site/hybrid) OR remote explicitly open to Kenya
  africa -> remote open to Africa / a specific African country / EMEA that includes Africa
  world  -> remote, explicitly worldwide / anywhere / global
  check  -> remote but NO stated region, OR remote restricted to a non-African region.
            This is the honest fallback. Never pretend certainty you don't have.

Feed it whatever text you have: the location field, plus any "workplace / remote
region" copy from the listing body. More text = better inference.
"""
from __future__ import annotations
import re

KENYA = [r"\bkenya\b", r"\bnairobi\b", r"\bmombasa\b", r"\bkisumu\b"]
AFRICA_COUNTRIES = [
    r"\bnigeria\b", r"\blagos\b", r"\bghana\b", r"\baccra\b", r"\bsouth africa\b",
    r"\bcape town\b", r"\bjohannesburg\b", r"\begypt\b", r"\bcairo\b", r"\brwanda\b",
    r"\bkigali\b", r"\btanzania\b", r"\buganda\b", r"\bethiopia\b", r"\bmorocco\b",
    r"\bsenegal\b", r"\bafrica\b", r"\bpan[- ]african\b",
]
WORLDWIDE = [r"\bworldwide\b", r"\banywhere\b", r"\bglobal(ly)?\b", r"\bany country\b",
             r"\bfully remote\b.*\bany", r"\bremote[- ]first\b"]
# Regions that, when they are the ONLY stated eligibility, exclude Africa.
NON_AFRICA_ONLY = [
    r"\bus[- ]only\b", r"\bunited states only\b", r"\bus[- ]based\b", r"\bmust be.*\bu\.?s\.?\b",
    r"\bcanada only\b", r"\bnorth america\b", r"\bamericas\b", r"\buk only\b",
    r"\beu only\b", r"\beurope only\b", r"\bmust reside in the (us|uk|eu)\b",
    r"\blatam\b", r"\basia[- ]pacific\b", r"\bapac\b",
]
EMEA = [r"\bemea\b"]  # EMEA includes Africa -> treat as africa-eligible


def _any(patterns, text) -> bool:
    return any(re.search(p, text) for p in patterns)


def _classify(text: str) -> str | None:
    """Check one piece of text against the badge rules, in priority order."""
    if _any(KENYA, text):
        return "kenya"
    if _any(EMEA, text) or _any(AFRICA_COUNTRIES, text):
        return "africa"
    if _any(WORLDWIDE, text):
        return "world"
    if _any(NON_AFRICA_ONLY, text):
        return "check"   # remote but region-locked away from Africa
    return None          # this text didn't say anything conclusive


def infer_eligibility(location: str, work_type: str, region_text: str = "") -> str:
    loc = (location or "").lower()
    reg = (region_text or "").lower()
    remote = work_type.lower() == "remote"

    # On-site / hybrid: eligibility is just where the office is. Location only
    # — a job description's general "we operate across Africa" marketing copy
    # says nothing about where THIS office actually sits.
    if not remote:
        if _any(KENYA, loc):
            return "kenya"
        if _any(AFRICA_COUNTRIES, loc):
            return "africa"
        return "check"  # office outside Africa -> relocation/visa, flag it

    # Remote: the location field is what the ATS itself says this role is
    # scoped to (e.g. "Remote, Nigeria", "North America", "Worldwide"), so it
    # is authoritative and checked first. Body text is a fallback ONLY when
    # location itself is uninformative (blank, or bare "Remote") — otherwise
    # a company's boilerplate "we hire across Africa" copy in the description
    # could override an explicit location restriction (seen in the wild: an
    # Andela role scoped to "North America" whose about-us paragraph mentions
    # Africa repeatedly — that must not flip it to "africa").
    verdict = _classify(loc)
    if verdict:
        return verdict
    verdict = _classify(reg)
    if verdict:
        return verdict
    return "check"  # remote, no region stated anywhere — the honest default
