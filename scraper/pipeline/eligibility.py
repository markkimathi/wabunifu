"""
Kazi's whole reason to exist: answer "can a designer in Kenya actually apply?"
before the user clicks.

Returns one of four badges, plus a *scope* saying who the role is actually open
to when that is narrower than the badge alone implies:

  kenya  -> role is in Kenya (on-site/hybrid) OR remote explicitly open to Kenya
  africa -> open to more than one African country. scope "" means genuinely
            continent-wide (EMEA, pan-African, bare "Africa"); a non-empty scope
            names the specific country or sub-region it is limited to.
  world  -> remote, explicitly worldwide / anywhere / global
  check  -> remote but NO stated region, OR remote restricted to a non-African
            region. This is the honest fallback. Never pretend certainty you
            don't have.

The scope exists because the badge alone was lying. The platform defines
"Open across Africa" as "the employer can hire or contract someone in another
African country" (see the review copy in web/admin.html), but a role posted as
"Remote, Nigeria" matched the country list and got that badge — so a designer
in Nairobi was told a Nigeria-only role was open to them. That is precisely the
"line that quietly rules you out" this product exists to catch, so the country
now travels with the badge instead of being flattened into the continent.

Feed it whatever text you have: the location field, plus any "workplace / remote
region" copy from the listing body. More text = better inference.
"""
from __future__ import annotations
import re

KENYA = [r"\bkenya\b", r"\bnairobi\b", r"\bmombasa\b", r"\bkisumu\b"]

# Genuinely continent-wide. Bare "africa" is guarded so that "South Africa",
# "West Africa" and friends fall through to the country/region tables below
# rather than being read as the whole continent.
AFRICA_WIDE = [
    r"(?<!south )(?<!west )(?<!east )(?<!north )(?<!central )(?<!southern )\bafrica\b",
    r"\bpan[- ]african\b", r"\bafrican union\b", r"\bsub[- ]saharan\b", r"\bemea\b",
]

# Sub-regions are narrower than the continent but wider than one country. Kept
# as their own scope so the badge never claims more reach than the posting does.
AFRICA_REGIONS = {
    "East Africa": [r"\beast(ern)? africa\b"],
    "West Africa": [r"\bwest(ern)? africa\b"],
    "North Africa": [r"\bnorth(ern)? africa\b"],
    "Southern Africa": [r"\bsouthern africa\b"],
    "Central Africa": [r"\bcentral africa\b"],
}

# All 54 African countries, each with the business-hub cities most likely to
# appear in a location string without the country name attached (a listing that
# just says "Lagos" or "Cape Town"). Err toward completeness: a false "check"
# undersells a role someone could actually take.
#
# Order matters where one name contains another — "guinea-bissau" and
# "equatorial guinea" are listed before bare "guinea", and Python dicts keep
# insertion order, so the first match wins.
AFRICA_BY_COUNTRY = {
    "Algeria": [r"\balgeria\b", r"\balgiers\b"],
    "Angola": [r"\bangola\b", r"\bluanda\b"],
    "Benin": [r"\bbenin\b", r"\bcotonou\b"],
    "Botswana": [r"\bbotswana\b", r"\bgaborone\b"],
    "Burkina Faso": [r"\bburkina faso\b", r"\bouagadougou\b"],
    "Burundi": [r"\bburundi\b", r"\bbujumbura\b"],
    "Cabo Verde": [r"\bcabo verde\b", r"\bcape verde\b"],
    "Cameroon": [r"\bcameroon\b", r"\bdouala\b", r"\byaound[eé]\b"],
    "Central African Republic": [r"\bcentral african republic\b"],
    "Chad": [r"\bchad\b", r"\bn.?djamena\b"],
    "Comoros": [r"\bcomoros\b"],
    "DR Congo": [r"\b(dr|democratic republic of the)\s*congo\b", r"\bkinshasa\b"],
    "Congo": [r"\bcongo\b", r"\bbrazzaville\b"],
    "Djibouti": [r"\bdjibouti\b"],
    "Egypt": [r"\begypt\b", r"\bcairo\b", r"\balexandria\b"],
    "Equatorial Guinea": [r"\bequatorial guinea\b"],
    "Eritrea": [r"\beritrea\b", r"\basmara\b"],
    "Eswatini": [r"\beswatini\b", r"\bswaziland\b"],
    "Ethiopia": [r"\bethiopia\b", r"\baddis ababa\b"],
    "Gabon": [r"\bgabon\b", r"\blibreville\b", r"\bntoum\b"],
    "Gambia": [r"\bgambia\b", r"\bbanjul\b"],
    "Ghana": [r"\bghana\b", r"\baccra\b", r"\bkumasi\b"],
    "Guinea-Bissau": [r"\bguinea[- ]bissau\b"],
    "Guinea": [r"\bguinea\b", r"\bconakry\b"],
    "Ivory Coast": [r"\bivory coast\b", r"\bc[oô]te d.?ivoire\b", r"\babidjan\b"],
    "Lesotho": [r"\blesotho\b", r"\bmaseru\b"],
    "Liberia": [r"\bliberia\b", r"\bmonrovia\b"],
    "Libya": [r"\blibya\b", r"\btripoli\b"],
    "Madagascar": [r"\bmadagascar\b", r"\bantananarivo\b"],
    "Malawi": [r"\bmalawi\b", r"\blilongwe\b", r"\bblantyre\b"],
    "Mali": [r"\bmali\b", r"\bbamako\b"],
    "Mauritania": [r"\bmauritania\b", r"\bnouakchott\b"],
    "Mauritius": [r"\bmauritius\b", r"\bport louis\b"],
    "Morocco": [r"\bmorocco\b", r"\bcasablanca\b", r"\brabat\b", r"\bmarrakech\b"],
    "Mozambique": [r"\bmozambique\b", r"\bmaputo\b"],
    "Namibia": [r"\bnamibia\b", r"\bwindhoek\b"],
    "Niger": [r"\bniger\b(?!ia)", r"\bniamey\b"],
    "Nigeria": [r"\bnigeria\b", r"\blagos\b", r"\babuja\b", r"\bport harcourt\b", r"\bibadan\b"],
    "Rwanda": [r"\brwanda\b", r"\bkigali\b"],
    "São Tomé and Príncipe": [r"\bs[aã]o tom[eé]( and pr[ií]ncipe)?\b"],
    "Senegal": [r"\bsenegal\b", r"\bdakar\b"],
    "Seychelles": [r"\bseychelles\b"],
    "Sierra Leone": [r"\bsierra leone\b", r"\bfreetown\b"],
    "Somalia": [r"\bsomalia\b", r"\bmogadishu\b"],
    "South Africa": [r"\bsouth africa\b", r"\bcape town\b", r"\bjohannesburg\b",
                     r"\bpretoria\b", r"\bdurban\b"],
    "South Sudan": [r"\bsouth sudan\b", r"\bjuba\b"],
    "Sudan": [r"\bsudan\b", r"\bkhartoum\b"],
    "Tanzania": [r"\btanzania\b", r"\bdar es salaam\b", r"\bdodoma\b"],
    "Togo": [r"\btogo\b", r"\blom[eé]\b"],
    "Tunisia": [r"\btunisia\b", r"\btunis\b"],
    "Uganda": [r"\buganda\b", r"\bkampala\b"],
    "Zambia": [r"\bzambia\b", r"\blusaka\b"],
    "Zimbabwe": [r"\bzimbabwe\b", r"\bharare\b"],
}

WORLDWIDE = [r"\bworldwide\b", r"\banywhere\b", r"\bglobal(ly)?\b", r"\bany country\b",
             r"\bfully remote\b.*\bany", r"\bremote[- ]first\b"]
# Regions that, when they are the ONLY stated eligibility, exclude Africa.
NON_AFRICA_ONLY = [
    r"\bus[- ]only\b", r"\bunited states only\b", r"\bus[- ]based\b", r"\bmust be.*\bu\.?s\.?\b",
    r"\bcanada only\b", r"\bnorth america\b", r"\bamericas\b", r"\buk only\b",
    r"\beu only\b", r"\beurope only\b", r"\bmust reside in the (us|uk|eu)\b",
    r"\blatam\b", r"\basia[- ]pacific\b", r"\bapac\b",
]

# Places outside Africa named plainly, with no "only" attached. A location of
# "Remote - United States" is every bit as exclusionary as "US only", but the
# list above missed it: the location then read as inconclusive, the classifier
# fell through to the job body, and the word "global" in a company's marketing
# copy badged a US-only role "Global remote". Nearly half the live board was
# wrong this way, all of it pointing African designers at roles that would
# reject them — the precise failure this product exists to prevent.
# Named so the scope can say *where* the role is open instead of leaving the
# reader with a bare "worth checking". "Open in the United States" tells a
# designer in Nairobi what they need to know in three words.
NON_AFRICA_PLACES = {
    # "U.S." with the dots is common in ATS location strings and \bus\b does not
    # match it — the dots break the word boundary — so it needs its own pattern.
    "the United States": [r"\bunited states\b", r"\bu\.?s\.?a\b", r"\bus\b", r"\bu\.s\.?"],
    "Canada": [r"\bcanada\b"],
    "Mexico": [r"\bmexico\b"],
    "the United Kingdom": [r"\bunited kingdom\b", r"\bu\.?k\.?\b", r"\bengland\b",
                           r"\bscotland\b", r"\bwales\b"],
    "Ireland": [r"\bireland\b"],
    "Germany": [r"\bgermany\b"], "France": [r"\bfrance\b"], "Spain": [r"\bspain\b"],
    "Portugal": [r"\bportugal\b"], "Italy": [r"\bitaly\b"],
    "the Netherlands": [r"\bnetherlands\b"], "Belgium": [r"\bbelgium\b"],
    "Poland": [r"\bpoland\b"], "Sweden": [r"\bsweden\b"], "Norway": [r"\bnorway\b"],
    "Denmark": [r"\bdenmark\b"], "Finland": [r"\bfinland\b"],
    "Switzerland": [r"\bswitzerland\b"], "Austria": [r"\baustria\b"],
    "Czechia": [r"\bczech\b"], "Romania": [r"\bromania\b"], "Greece": [r"\bgreece\b"],
    "Europe": [r"\beurope\b", r"\beuropean union\b"],
    "India": [r"\bindia\b"], "Pakistan": [r"\bpakistan\b"],
    "Bangladesh": [r"\bbangladesh\b"], "China": [r"\bchina\b"], "Japan": [r"\bjapan\b"],
    "Singapore": [r"\bsingapore\b"], "Malaysia": [r"\bmalaysia\b"],
    "Indonesia": [r"\bindonesia\b"], "the Philippines": [r"\bphilippines\b"],
    "Vietnam": [r"\bvietnam\b"], "Thailand": [r"\bthailand\b"],
    "Australia": [r"\baustralia\b"], "New Zealand": [r"\bnew zealand\b"],
    "Brazil": [r"\bbrazil\b"], "Argentina": [r"\bargentina\b"],
    "Colombia": [r"\bcolombia\b"], "Chile": [r"\bchile\b"], "Peru": [r"\bperu\b"],
    "Turkey": [r"\bturkey\b"], "Israel": [r"\bisrael\b"],
    "the UAE": [r"\bu\.?a\.?e\.?\b", r"\bdubai\b"], "Saudi Arabia": [r"\bsaudi\b"],
    "North America": [r"\bnorth america\b", r"\bamericas\b"],
    "Latin America": [r"\blatam\b", r"\blatin america\b"],
    "Asia-Pacific": [r"\basia[- ]pacific\b", r"\bapac\b"],
}

# Location strings that genuinely say nothing about where you may sit. Only
# these are allowed to fall through to the job body for a verdict; anything
# else naming a place we can't place is "check", never an upgrade.
VAGUE_LOCATION = re.compile(
    r"^[\s,;/|()-]*(remote|fully remote|work from home|wfh|flexible|distributed|"
    r"multiple locations|various|other|n/?a|tbd)?[\s,;/|()-]*$"
)

# Sub-regions expanded to the countries a designer could be sitting in, so the
# board can answer "is this open to me?" for a region-scoped posting.
REGION_MEMBERS = {
    "East Africa": ["Kenya", "Tanzania", "Uganda", "Rwanda", "Burundi", "Ethiopia",
                    "Somalia", "South Sudan", "Djibouti", "Eritrea"],
    "West Africa": ["Nigeria", "Ghana", "Senegal", "Ivory Coast", "Mali", "Burkina Faso",
                    "Benin", "Togo", "Guinea", "Sierra Leone", "Liberia", "Niger",
                    "Gambia", "Guinea-Bissau", "Cabo Verde", "Mauritania"],
    "North Africa": ["Egypt", "Morocco", "Algeria", "Tunisia", "Libya", "Sudan"],
    "Southern Africa": ["South Africa", "Namibia", "Botswana", "Zimbabwe", "Zambia",
                        "Mozambique", "Lesotho", "Eswatini", "Malawi", "Angola"],
    "Central Africa": ["Cameroon", "Chad", "Central African Republic", "Gabon",
                       "DR Congo", "Congo", "Equatorial Guinea", "São Tomé and Príncipe"],
}


def _any(patterns, text) -> bool:
    return any(re.search(p, text) for p in patterns)


def _countries_in(text: str) -> list[str]:
    """Every African country named in this text, in table order."""
    return [c for c, pats in AFRICA_BY_COUNTRY.items() if _any(pats, text)]


def _regions_in(text: str) -> list[str]:
    return [r for r, pats in AFRICA_REGIONS.items() if _any(pats, text)]


def _classify(text: str) -> tuple[str, str] | None:
    """Check one piece of text against the badge rules, in priority order.

    Returns (badge, scope) or None when this text says nothing conclusive.
    """
    if _any(KENYA, text):
        return "kenya", "Kenya"
    # Continent-wide beats a named country: "Remote across Africa (Lagos hub)"
    # really is open continent-wide, and the country is incidental.
    if _any(AFRICA_WIDE, text):
        return "africa", ""
    regions = _regions_in(text)
    countries = _countries_in(text)
    named = regions + countries
    if named:
        return "africa", ", ".join(named)
    if _any(WORLDWIDE, text):
        return "world", ""
    # Region-locked away from Africa. The scope names where it *is* open, so the
    # badge can say "Open in the United States" rather than a vague "check".
    elsewhere = [n for n, pats in NON_AFRICA_PLACES.items() if _any(pats, text)]
    if elsewhere:
        return "check", ", ".join(elsewhere)
    if _any(NON_AFRICA_ONLY, text):
        return "check", ""
    return None              # this text didn't say anything conclusive


def infer_eligibility_detail(location: str, work_type: str, region_text: str = "") -> tuple[str, str]:
    """Return (badge, scope). See module docstring for what each badge means."""
    loc = (location or "").lower()
    reg = (region_text or "").lower()
    remote = work_type.lower() == "remote"

    # On-site / hybrid: eligibility is just where the office is. Location only:
    # a job description's general "we operate across Africa" marketing copy
    # says nothing about where THIS office actually sits. An office is always
    # in exactly one country, so a continent-wide reading is never right here.
    if not remote:
        if _any(KENYA, loc):
            return "kenya", "Kenya"
        countries = _countries_in(loc)
        if countries:
            return "africa", ", ".join(countries)
        return "check", ""  # office outside Africa -> relocation/visa, flag it

    # Remote: the location field is what the ATS itself says this role is
    # scoped to (e.g. "Remote, Nigeria", "North America", "Worldwide"), so it
    # is authoritative and checked first. Body text is a fallback ONLY when
    # location itself is uninformative (blank, or bare "Remote"); otherwise
    # a company's boilerplate "we hire across Africa" copy in the description
    # could override an explicit location restriction (seen in the wild: an
    # Andela role scoped to "North America" whose about-us paragraph mentions
    # Africa repeatedly, which must not flip it to "africa").
    verdict = _classify(loc)
    if verdict:
        return verdict

    # The body is consulted ONLY when the location genuinely says nothing. If it
    # names a place we failed to recognise, that is a restriction we cannot
    # verify, and marketing copy must never be allowed to talk us up out of it —
    # that leak is what badged US-only roles "Global remote".
    if VAGUE_LOCATION.match(loc):
        verdict = _classify(reg)
        if verdict:
            return verdict
    return "check", ""  # nothing conclusive: the honest default


def infer_eligibility(location: str, work_type: str, region_text: str = "") -> str:
    """Badge only, for callers that don't care who the role is scoped to."""
    return infer_eligibility_detail(location, work_type, region_text)[0]


def open_to_country(badge: str, scope: str, country: str) -> bool | None:
    """Can someone sitting in `country` take this role?

    True / False where we can say honestly; None when we genuinely don't know,
    which the UI must surface as "worth checking" rather than a verdict.
    """
    if not country:
        return None
    # A scope is a definite list of where the role is open, whatever the badge:
    # "Nigeria" on an africa badge, "the United States" on a check badge. Either
    # way the question is the same — are you in it?
    if scope:
        allowed: list[str] = []
        for part in (p.strip() for p in scope.split(",")):
            allowed.extend(REGION_MEMBERS.get(part, [part]))
        return country in allowed
    if badge == "world":
        return True
    if badge in ("kenya", "africa"):
        return True              # continent-wide, no narrowing stated
    return None                  # bare "check": genuinely nothing stated
