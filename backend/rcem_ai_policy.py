"""Deterministic controls for RCEM-compliant AI-supported reflection.

RCEM permits AI to structure and edit a resident doctor's reflection, but not
to replace the reflective act. Model output is therefore never used as proof
that the doctor reflected: the source supplied by the doctor must contain an
explicit learning, interpretation, reaction, or intended practice change.
"""

from __future__ import annotations

import re


AI_USE_DECLARATION = "AI was used to help structure and edit this reflection."


_PERSONAL_REFLECTION_PATTERNS = tuple(
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


def has_personal_reflective_input(text: str | None) -> bool:
    """Return whether the doctor's source contains explicit reflective input.

    The gate is intentionally transparent and conservative. It does not score
    educational quality, and it does not ask an LLM to judge its own output.
    """
    source = " ".join(str(text or "").split())
    if len(source.split()) < 5:
        return False
    return any(pattern.search(source) for pattern in _PERSONAL_REFLECTION_PATTERNS)


def with_ai_use_declaration(text: str | None) -> str:
    """Append the RCEM AI-use declaration once to non-empty reflection text."""
    reflection = str(text or "").strip()
    if not reflection:
        return ""
    if AI_USE_DECLARATION.casefold() in reflection.casefold():
        return reflection
    return f"{reflection}\n\n{AI_USE_DECLARATION}"
