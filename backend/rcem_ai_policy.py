"""Deterministic controls for RCEM-compliant AI-supported reflection.

RCEM permits AI to structure and edit a resident doctor's reflection, but not
to replace the reflective act. Model output is therefore never used as proof
that the doctor reflected: the source supplied by the doctor must contain an
explicit learning, interpretation, reaction, or intended practice change.
"""

from __future__ import annotations

import re


AI_USE_DECLARATION = "AI was used to help structure and edit this reflection."


# Strong: first-person reflective phrasing. These alone are sufficient
# evidence of a doctor's own reflective input, regardless of what else the
# source contains.
_STRONG_REFLECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bI\s+(?:learned|learnt|realised|recognized|recognised|understood|noticed|reflected)\b",
        r"\bI\s+(?:felt|found|struggled|was surprised|was challenged)\b",
        r"\bI\s+(?:will|would|need to|plan to|intend to|should|could|must)\s+"
        r"(?!(?:like|want|ask|send|upload|file|create|draft)\b)",
        r"\bI(?:'ll|'d)\s+(?!(?:like|want|ask|send|upload|file|create|draft)\b)",
        r"\b(?:on reflection|reflected on|my reflection|my learning point|what I learned|what I learnt)\b",
        r"\b(?:next time|in future|in the future|what I would do differently)\b",
        r"\bthis\s+(?:taught|showed|reminded)\s+me\b",
    )
)

# Weak: explicit learning stated without first-person phrasing, e.g. a
# doctor's "Learning points: ..." note or "Learning that X is important when
# Y", or "This/the case reinforced/highlighted ...". These read as headings
# or narrative connectors rather than a first-person admission, so they are
# only accepted when they are not themselves a request for fabricated
# content and not an explicit statement that no learning was supplied.
# "showed" is deliberately excluded: it is the most common verb for plain
# diagnostic narrative ("it showed a fracture"), not for a stated lesson.
_WEAK_REFLECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:key |main )?learning points?\b",
        r"\blearning that\b",
        r"\b(?:this|it|the case|the encounter)\s+"
        r"(?:reinforced|highlighted|emphasi[sz]ed|taught|reminded)\b",
    )
)

# A weak match next to language asking someone (the AI) to originate the
# learning, rather than report it, is a request for fabricated content.
_FABRICATION_REQUEST_PATTERN = re.compile(
    r"\b(?:invent|make up|fabricat\w*|come up with|generate|write)\b"
    r"(?:(?!\.).){0,40}\blearning\b",
    re.IGNORECASE,
)

# A weak "learning point(s)" heading immediately followed by an explicit
# statement that none was supplied is not itself a reported learning.
_NO_LEARNING_SUPPLIED_PATTERN = re.compile(
    r"\blearning points?\b\s*[:\-]*\s*"
    r"(?:none|n\W?a|nil|nothing|not\s+(?:supplied|provided|applicable))\b",
    re.IGNORECASE,
)


def has_personal_reflective_input(text: str | None) -> bool:
    """Return whether the doctor's source contains explicit reflective input.

    The gate is intentionally transparent and conservative. It does not score
    educational quality, and it does not ask an LLM to judge its own output.
    """
    source = " ".join(str(text or "").split())
    if len(source.split()) < 5:
        return False
    if any(pattern.search(source) for pattern in _STRONG_REFLECTION_PATTERNS):
        return True
    if _NO_LEARNING_SUPPLIED_PATTERN.search(source):
        return False
    if _FABRICATION_REQUEST_PATTERN.search(source):
        return False
    return any(pattern.search(source) for pattern in _WEAK_REFLECTION_PATTERNS)


def with_ai_use_declaration(text: str | None) -> str:
    """Append the RCEM AI-use declaration once to non-empty reflection text."""
    reflection = str(text or "").strip()
    if not reflection:
        return ""
    if AI_USE_DECLARATION.casefold() in reflection.casefold():
        return reflection
    return f"{reflection}\n\n{AI_USE_DECLARATION}"
