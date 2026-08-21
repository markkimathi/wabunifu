"""
Decide whether a scraped listing is actually a DESIGN role, and if so tag its
discipline and seniority. This is the layer that stops the board filling up with
"design engineer" (usually SWE) or "designer of database schemas" noise.

Everything here is rule-based and fast. When you outgrow rules, this is the clean
seam to drop in an embedding classifier: the interface (classify -> Discipline?)
stays the same.
"""
from __future__ import annotations
import re

# Ordered: first match wins, so put specific disciplines before generic ones.
DISCIPLINE_RULES = [
    ("Design Engineering", [r"\bdesign engineer", r"\bdesign technologist", r"\bui engineer\b",
                           r"\bfront[- ]?end designer\b"]),
    ("Creative Technology", [r"\bcreative technolog", r"\bcreative engineer", r"\bcreative developer"]),
    ("Content Design",   [r"\bcontent design", r"\bux writ", r"\bcontent strateg", r"\bcopywriter\b.*\bproduct\b"]),
    ("UX Research",     [r"\buser research", r"\buxr\b", r"\bux research", r"\bresearcher\b.*design", r"\bdesign research"]),
    ("Design Systems",  [r"design system", r"\bdesign systems\b"]),
    ("Motion Design",   [r"\bmotion\b", r"\banimation\b", r"\bmotion design"]),
    ("Game Design",     [r"\bgame design", r"\bgame designer\b"]),
    ("Sound Design",    [r"\bsound design", r"\baudio design"]),
    ("Instructional Design", [r"\binstructional design", r"\blearning design", r"\bcurriculum design"]),
    ("Fashion Design",  [r"\bfashion design"]),
    ("Interior Design", [r"\binterior design"]),
    ("Brand Design",    [r"\bbrand\b", r"\bvisual designer", r"\bvisual design\b", r"\bart director", r"\bcreative director"]),
    ("UX Design",       [r"\bux\b", r"\buser experience", r"\binteraction design", r"\bixd\b"]),
    ("UI Design",       [r"\bui\b", r"\buser interface", r"\bui/ux", r"\bux/ui"]),
    ("Graphic Design",  [r"\bgraphic design", r"\bgraphic designer"]),
    ("Product Design",  [r"\bproduct design", r"\bproduct designer", r"\bdesigner\b"]),  # generic fallback
]

# If the title matches design keywords BUT also one of these, it's almost certainly
# not a design role at all (an engineering discipline that happens to use the
# word "design") rather than a design role Kazi has simply chosen to exclude.
EXCLUDE = [
    r"\bsolutions? design", r"\bcircuit\b", r"\bchip design",
    r"\bmechanical\b", r"\belectrical\b", r"\bstructural\b", r"\bhardware\b",
    # Engineering roles that merely mention UX or a design system in passing —
    # "Senior Software Engineer, Mobile (Repayment UX)", "Senior Android
    # Engineer, Design System". The parenthetical names the team they sit
    # beside, not the job. Predates the Design Engineering rule below and was
    # never caught, because none of these say "design engineer".
    #
    # Design Engineer and UX Engineer are deliberately NOT here: those are real
    # design-led titles and the whole point of adding the discipline.
    r"\b(software|android|ios|mobile|backend|back[- ]end|frontend|front[- ]end|fullstack|"
    r"full[- ]stack|platform|data|infrastructure|devops|security|qa|test)\s+engineer",
    r"\bengineering manager\b", r"\bmanager,\s*software engineering\b",
    # ...but the engineering senses of "design engineer" still go: these are
    # the ones that made it an exclude in the first place.
    r"\b(mechanical|electrical|hardware|firmware|rf|asic|verification|manufacturing|civil)\s+design engineer\b",
    r"\bdesign engineer\b.*\b(mechanical|electrical|hardware|firmware|asic|plant|civil)\b",
    r"\bsystem designer\b", r"\bnetwork design",
    r"\bsales\b", r"\bmanager, design\b.*\bengineering\b",
]

LEVEL_RULES = [
    ("Lead",   [r"\blead\b", r"\bprincipal\b", r"\bstaff\b", r"\bhead of\b", r"\bdirector\b", r"\bmanager\b"]),
    ("Senior", [r"\bsenior\b", r"\bsr\.?\b", r"\bsnr\b"]),
    ("Junior", [r"\bjunior\b", r"\bjr\.?\b", r"\bgraduate\b", r"\bentry[- ]level\b", r"\bintern\b", r"\bassociate\b"]),
]


def _any(patterns, text) -> bool:
    return any(re.search(p, text) for p in patterns)


# Matches any designer at all, which is what makes it the fallback for
# Product Design — and also why it must not outrank a department. A "Learning
# Experience Designer" matches this and nothing else; the department is what
# says Instructional Design, and it is right.
GENERIC_PATTERNS = {r"\bdesigner\b"}

# Checked against the title only, never the department. Both of these were on
# the live board: "Senior Product Manager, Design Systems" (owns the design
# system as a product) and "UX Product Manager" (sat in Commercial HQ). A
# designer opening either spends an afternoon on a role that was never a design
# job, which is the exact thing this board exists to prevent.
#
# Title-only because Duolingo files "Senior Learning Designer, Indian
# Languages" under a department called "Product Manager". The department says
# where a role sits; the title says what it is, and it is a design job.
#
# The adjacency matters too: this catches "Product Manager" but not "Product
# Design Manager" or "Senior Manager, Product Design", which manage designers
# and belong here.
TITLE_EXCLUDE = [r"\bproduct\s+manager\b"]


def _match(text: str, precise_only: bool = False) -> str | None:
    for discipline, patterns in DISCIPLINE_RULES:
        pats = [p for p in patterns if not (precise_only and p in GENERIC_PATTERNS)]
        if pats and _any(pats, text):
            return discipline
    return None


def classify_discipline(title: str, department: str = "") -> str | None:
    """Return a discipline string, or None if this isn't a design role we list.

    Title first, department second. Both used to be concatenated and matched in
    rule order, and Product Design is the last rule — so any other discipline
    named anywhere outranked it. "Director, Product Design" in a design-systems
    department came out as Design Systems, and three roles literally titled
    "Product Designer" were filed under UX, which is a miss for anyone
    filtering the board by the discipline they actually do.

    Only a *precise* title match wins that way. A title matching nothing but
    the generic "designer" still defers to the department, because that is the
    case where the department genuinely knows better.
    """
    text = f"{title} {department}".lower()
    if _any(EXCLUDE, text) or _any(TITLE_EXCLUDE, title.lower()):
        return None
    # must contain some design signal at all
    if not re.search(
        r"design|\bux\b|\bui\b|\bresearch|\bcreative director\b|\bart director\b"
        r"|\bcreative technolog|\bmotion graphics\b|\billustrat",
        text,
    ):
        return None
    return _match(title.lower(), precise_only=True) or _match(text)


def classify_level(title: str) -> str:
    text = title.lower()
    for level, patterns in LEVEL_RULES:
        if _any(patterns, text):
            return level
    return "Mid"
