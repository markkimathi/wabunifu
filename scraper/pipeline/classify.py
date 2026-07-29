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
    ("UX Research",     [r"\buser research", r"\buxr\b", r"\bux research", r"\bresearcher\b.*design", r"\bdesign research"]),
    ("Design Systems",  [r"design system", r"\bdesign systems\b"]),
    ("Motion Design",   [r"\bmotion\b", r"\banimation\b", r"\bmotion design"]),
    ("Brand Design",    [r"\bbrand\b", r"\bvisual designer", r"\bvisual design\b", r"\bart director", r"\bcreative director"]),
    ("UX Design",       [r"\bux\b", r"\buser experience", r"\binteraction design", r"\bixd\b"]),
    ("UI Design",       [r"\bui\b", r"\buser interface", r"\bui/ux", r"\bux/ui"]),
    ("Graphic Design",  [r"\bgraphic design", r"\bgraphic designer"]),
    ("Product Design",  [r"\bproduct design", r"\bproduct designer", r"\bdesigner\b"]),  # generic fallback
]

# If the title matches design keywords BUT also one of these, it's almost certainly
# not the kind of role Kazi lists. Reject.
EXCLUDE = [
    r"\bdesign engineer\b", r"\bsolutions? design", r"\bcircuit\b", r"\bchip design",
    r"\bmechanical\b", r"\belectrical\b", r"\bstructural\b", r"\bhardware\b",
    r"\binstructional design", r"\bcurriculum\b", r"\bgame designer\b",
    r"\bsound design", r"\bsystem designer\b", r"\bnetwork design", r"\bfashion design",
    r"\binterior design", r"\bsales\b", r"\bmanager, design\b.*\bengineering\b",
]

LEVEL_RULES = [
    ("Lead",   [r"\blead\b", r"\bprincipal\b", r"\bstaff\b", r"\bhead of\b", r"\bdirector\b", r"\bmanager\b"]),
    ("Senior", [r"\bsenior\b", r"\bsr\.?\b", r"\bsnr\b"]),
    ("Junior", [r"\bjunior\b", r"\bjr\.?\b", r"\bgraduate\b", r"\bentry[- ]level\b", r"\bintern\b", r"\bassociate\b"]),
]


def _any(patterns, text) -> bool:
    return any(re.search(p, text) for p in patterns)


def classify_discipline(title: str, department: str = "") -> str | None:
    """Return a discipline string, or None if this isn't a design role we list."""
    text = f"{title} {department}".lower()
    if _any(EXCLUDE, text):
        return None
    # must contain some design signal at all
    if not re.search(r"design|\bux\b|\bui\b|\bresearch", text):
        return None
    for discipline, patterns in DISCIPLINE_RULES:
        if _any(patterns, text):
            return discipline
    return None


def classify_level(title: str) -> str:
    text = title.lower()
    for level, patterns in LEVEL_RULES:
        if _any(patterns, text):
            return level
    return "Mid"
