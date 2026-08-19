"""
How the work is engaged, which is a different question from where it happens.

`work_type` already answers Remote / Hybrid / On-site. This answers Full-time /
Part-time / Contract / Freelance / Internship — the thing a designer filters on
first when they are, say, only after contract work, and which the board had no
way to represent at all.

Kept separate from eligibility on purpose: a Nigeria-only internship and a
worldwide permanent role are different in both dimensions, and collapsing them
into one badge is what made the eligibility label misleading in the first place.

Order matters. Internship is checked before everything because "Design Intern
(Full-time, 6 months)" is an internship, not a permanent role; contract is
checked before full-time for the same reason.
"""
from __future__ import annotations
import re

EMPLOYMENT_TYPES = ["Full-time", "Part-time", "Contract", "Freelance", "Internship"]

# Checked in this order — the first match wins.
PATTERNS = [
    ("Internship", [
        r"\bintern(ship)?\b", r"\bindustrial attachment\b", r"\battach[eé]\b",
        # Words often sit between: "Graduate Design Programme".
        r"\bgraduate\b[\w\s]{0,20}\b(programme|program|scheme)\b",
        r"\btrainee\b", r"\bapprentice(ship)?\b",
        r"\bearly careers?\b",
    ]),
    ("Freelance", [
        r"\bfreelance(r)?\b", r"\bper[- ]project\b", r"\bproject[- ]based\b",
        r"\bgig\b", r"\bcommission(ed)? (work|basis)\b",
    ]),
    ("Contract", [
        r"\bcontract(or|ing)?\b", r"\bfixed[- ]term\b", r"\btemporary\b", r"\btemp\b",
        r"\bconsultan(t|cy)\b", r"\b\d+[- ]month (contract|engagement)\b",
        r"\bsecondment\b", r"\bmaternity cover\b", r"\bb2b\b",
    ]),
    ("Part-time", [
        r"\bpart[- ]time\b", r"\bpart time\b", r"\bhalf[- ]time\b",
        r"\b(0\.[1-9]|[1-3])\s*days? (a|per) week\b",
    ]),
    ("Full-time", [
        r"\bfull[- ]time\b", r"\bfull time\b", r"\bpermanent\b", r"\bfte\b",
    ]),
]

# ATS fields that already state this outright. Values vary by provider, so map
# loosely rather than requiring an exact string.
ATS_FIELD_MAP = {
    "full-time": "Full-time", "fulltime": "Full-time", "full_time": "Full-time",
    "permanent": "Full-time", "regular": "Full-time",
    "part-time": "Part-time", "parttime": "Part-time", "part_time": "Part-time",
    "contract": "Contract", "contractor": "Contract", "fixed term": "Contract",
    "temporary": "Contract", "temp": "Contract",
    "freelance": "Freelance",
    "intern": "Internship", "internship": "Internship", "apprentice": "Internship",
    "graduate": "Internship", "trainee": "Internship",
}


def _first_match(text: str) -> str | None:
    for label, patterns in PATTERNS:
        for p in patterns:
            if re.search(p, text):
                return label
    return None


def infer_employment_type(title: str, body: str = "", ats_value: str = "") -> str:
    """Best-effort Full-time / Part-time / Contract / Freelance / Internship.

    Falls back to "Full-time" only when nothing says otherwise — the common case
    by a wide margin, and the assumption a reader already makes. It is recorded
    as an assumption, not a claim: `employment_stated` says whether anyone
    actually told us, so the UI can stay quiet rather than assert a guess.
    """
    # 1. The ATS field, when the provider gives one — it is the employer's own answer.
    if ats_value:
        key = ats_value.strip().lower()
        if key in ATS_FIELD_MAP:
            return ATS_FIELD_MAP[key]
        found = _first_match(key)
        if found:
            return found

    # 2. The title, which is where "Intern" or "(Contract)" almost always sits.
    found = _first_match((title or "").lower())
    if found:
        return found

    # 3. The body, last and least reliable — a permanent role's benefits section
    #    happily mentions contractors.
    found = _first_match((body or "").lower()[:4000])
    if found:
        return found

    return "Full-time"


def employment_was_stated(title: str, body: str = "", ats_value: str = "") -> bool:
    """Did anything actually say, or did we fall back? Lets the UI show a type
    only when it is the employer's word rather than our inference."""
    if ats_value and (ats_value.strip().lower() in ATS_FIELD_MAP or _first_match(ats_value.lower())):
        return True
    if _first_match((title or "").lower()):
        return True
    return bool(_first_match((body or "").lower()[:4000]))
