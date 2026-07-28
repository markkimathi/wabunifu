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

# All 54 African countries, plus major business-hub cities for the ones most
# likely to show up in a location string without the country name attached
# (e.g. a listing that just says "Lagos" or "Cape Town"). Found via real
# scraped data that a short hand-picked list was missing common countries
# (Zambia, Malawi, Zimbabwe, ...) — a design role genuinely based in Lusaka
# was getting the honest-but-wrong "check" badge instead of "africa" simply
# because Zambia wasn't in the list. Err toward completeness here: a false
# "check" (undersells a role someone could actually take) is worse than a
# rare false "africa" match on an ambiguous city name.
AFRICA_COUNTRIES = [
    r"\balgeria\b", r"\bangola\b", r"\bbenin\b", r"\bbotswana\b", r"\bburkina faso\b",
    r"\bburundi\b", r"\bcabo verde\b", r"\bcape verde\b", r"\bcameroon\b",
    r"\bcentral african republic\b", r"\bchad\b", r"\bcomoros\b",
    r"\b(dr |democratic republic of the? )?congo\b", r"\bdjibouti\b", r"\begypt\b",
    r"\bequatorial guinea\b", r"\beritrea\b", r"\beswatini\b", r"\bswaziland\b",
    r"\bethiopia\b", r"\bgabon\b", r"\bgambia\b", r"\bghana\b", r"\bguinea[- ]bissau\b",
    r"\bguinea\b", r"\bivory coast\b", r"\bc[oô]te d.ivoire\b", r"\blesotho\b",
    r"\bliberia\b", r"\blibya\b", r"\bmadagascar\b", r"\bmalawi\b", r"\bmali\b",
    r"\bmauritania\b", r"\bmauritius\b", r"\bmorocco\b", r"\bmozambique\b",
    r"\bnamibia\b", r"\bniger\b", r"\bnigeria\b", r"\brwanda\b",
    r"\bs[aã]o tom[eé]( and pr[ií]ncipe)?\b", r"\bsenegal\b", r"\bseychelles\b",
    r"\bsierra leone\b", r"\bsomalia\b", r"\bsouth africa\b", r"\bsouth sudan\b",
    r"\bsudan\b", r"\btanzania\b", r"\btogo\b", r"\btunisia\b", r"\buganda\b",
    r"\bzambia\b", r"\bzimbabwe\b",
    # major cities, for listings that drop the country name
    r"\blagos\b", r"\babuja\b", r"\baccra\b", r"\bcape town\b", r"\bjohannesburg\b",
    r"\bpretoria\b", r"\bdurban\b", r"\bcairo\b", r"\balexandria\b", r"\bkigali\b",
    r"\bkampala\b", r"\bdar es salaam\b", r"\bdodoma\b", r"\baddis ababa\b",
    r"\bcasablanca\b", r"\brabat\b", r"\bdakar\b", r"\blusaka\b", r"\bharare\b",
    r"\bgaborone\b", r"\bwindhoek\b", r"\bmaputo\b", r"\bluanda\b", r"\bkinshasa\b",
    r"\bdouala\b", r"\byaound[eé]\b", r"\bam[aâ]n\b", r"\bfreetown\b", r"\bmonrovia\b",
    r"\bconakry\b", r"\bbamako\b", r"\bniamey\b", r"\bouagadougou\b", r"\bntoum\b",
    r"\bkhartoum\b", r"\bjuba\b", r"\btunis\b", r"\btripoli\b", r"\blom[eé]\b",
    r"\bcotonou\b", r"\bantananarivo\b", r"\bport louis\b",
    r"\bafrica\b", r"\bpan[- ]african\b", r"\bafrican union\b",
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
