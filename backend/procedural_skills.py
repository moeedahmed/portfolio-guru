"""Procedural-skills dropdowns — never leave the required control blank.

Kaizen renders a "Procedural skills list" dropdown on several curriculum-bearing
forms, including ESLE and CBD. It is required, and it offers an explicit
"Not applicable" option, so a blank is never a correct answer: either the
session evidences a procedural skill, or the answer is "Not applicable".

The filer used to look for an option spelled ``n/a`` and give up otherwise. The
real ESLE control spells it ``Not applicable``, which matched nothing, so the
dropdown was skipped and the draft saved with a required question unanswered.

Option matching lives here, in Python, rather than in a regex embedded in a
page script, so it is testable without a browser. Nothing here guesses: a real
skill is only chosen when the session's own words name one of the options the
page actually rendered.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional, Sequence

# Draft fields that carry an explicit procedural-skill choice on the forms that
# ask for one directly. Checked before the free text, because a chosen value
# outranks an inferred one.
PROCEDURAL_SKILL_FIELD_KEYS: tuple[str, ...] = (
    "procedural_skill",
    "procedure_name",
    "higher_procedural_skill",
    "intermediate_procedural_skill",
    "accs_procedural_skill",
)

# Free-text fields whose words describe what actually happened in the session.
_EVIDENCE_FIELD_KEYS: tuple[str, ...] = (
    "reflection",
    "case_description",
    "description",
    "clinical_reasoning",
    "what_happened",
    "summary",
    "learning_points",
    "feedback",
)

# Option labels that are a prompt rather than an answer.
_PLACEHOLDER_OPTIONS = frozenset({
    "", "?", "please select", "please select...", "select", "select...",
    "choose", "choose...", "-- select --",
})

# Option labels too generic to infer from prose without a direct instruction.
_UNINFERRABLE_OPTIONS = frozenset({"other", "others", "other (please specify)"})

# Leading numbering Kaizen sometimes prefixes onto option labels ("3. Chest
# drain insertion", "12 - Lumbar puncture").
_OPTION_PREFIX = re.compile(r"^\s*\d+\s*[.)\-:]\s*")

_NA_EXACT = re.compile(r"^[\s\-–—]*n\s*[/\\.]?\s*a[\s\-–—.]*$", re.IGNORECASE)
_NA_PHRASE = re.compile(r"\bnot\s+applicable\b", re.IGNORECASE)

# The shortest option label worth inferring from prose. Below this, a chance
# substring ("ECG" inside a word) starts choosing skills for the doctor.
_MIN_INFERABLE_OPTION_LENGTH = 6


def clean_option_label(text: Any) -> str:
    """Rendered option text with numbering and stray whitespace removed."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return _OPTION_PREFIX.sub("", cleaned).strip()


def is_not_applicable_option(text: Any) -> bool:
    """True for every spelling of the "no procedure here" option seen so far.

    Covers ``- n/a -`` (the spelling the old code assumed), ``N/A``, ``NA`` and
    ``Not applicable`` (the spelling the live ESLE form actually uses).
    """
    cleaned = clean_option_label(text)
    if not cleaned:
        return False
    if _NA_EXACT.match(cleaned):
        return True
    if _NA_PHRASE.search(cleaned):
        return True
    return cleaned.lower() in {"none", "none of the above", "not relevant"}


def find_not_applicable_option(options: Iterable[Any]) -> Optional[str]:
    """The exact rendered label to select when no procedure was performed."""
    for option in options or []:
        text = str(option or "")
        if is_not_applicable_option(text):
            return text.strip()
    return None


def _is_selectable(option: str) -> bool:
    cleaned = clean_option_label(option)
    if not cleaned or cleaned.lower() in _PLACEHOLDER_OPTIONS:
        return False
    return not is_not_applicable_option(cleaned)


def _stated_skill(fields: Mapping[str, Any]) -> str:
    for key in PROCEDURAL_SKILL_FIELD_KEYS:
        value = fields.get(key) if hasattr(fields, "get") else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _evidence_text(fields: Mapping[str, Any]) -> str:
    parts = []
    for key in _EVIDENCE_FIELD_KEYS:
        value = fields.get(key) if hasattr(fields, "get") else None
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


def _match_option(needle: str, options: Sequence[str]) -> Optional[str]:
    """The rendered option a stated skill name refers to, if any."""
    wanted = clean_option_label(needle).lower()
    if not wanted:
        return None
    selectable = [option for option in options if _is_selectable(option)]
    for option in selectable:
        if clean_option_label(option).lower() == wanted:
            return str(option).strip()
    # A stated value is often a shorter or longer phrasing of the rendered
    # label ("chest drain" vs "Chest drain insertion"). Longest containment
    # wins, so the most specific option is chosen rather than the first.
    contained = [
        option for option in selectable
        if wanted in clean_option_label(option).lower()
        or clean_option_label(option).lower() in wanted
    ]
    if contained:
        return max(contained, key=lambda option: len(clean_option_label(option))).strip()
    return None


def resolve_procedural_skill(
    options: Sequence[Any],
    fields: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """The exact option label to select on a procedural-skills dropdown.

    Returns the rendered label of a real skill when the draft states one or the
    session's own words clearly name one of the rendered options; otherwise the
    "Not applicable" option. Returns ``None`` only when the page offers neither
    a matching skill nor any not-applicable option — a real gap, which the
    caller must report rather than paper over.
    """
    rendered = [str(option) for option in (options or []) if str(option or "").strip()]
    fields = fields or {}

    stated = _stated_skill(fields)
    if stated and not is_not_applicable_option(stated):
        match = _match_option(stated, rendered)
        if match:
            return match

    text = _evidence_text(fields)
    if text:
        named = []
        for option in rendered:
            if not _is_selectable(option):
                continue
            label = clean_option_label(option)
            if label.lower() in _UNINFERRABLE_OPTIONS:
                continue
            if len(label) < _MIN_INFERABLE_OPTION_LENGTH:
                continue
            if label.lower() in text:
                named.append(option)
        if named:
            # Most specific wins: "Chest drain insertion" over "Chest drain".
            return max(named, key=lambda option: len(clean_option_label(option))).strip()

    return find_not_applicable_option(rendered)
