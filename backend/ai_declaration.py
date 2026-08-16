"""RCEM AI-use declaration for Portfolio Guru entries.

RCEM's "Position on the use of AI in Reflective Logs" (September 2025),
section 5: "If AI tools are used to support the reflective process, this
should be declared within the log (e.g., 'AI was used to help structure and
edit this reflection')." The same statement holds the resident doctor
responsible for the accuracy, authenticity and insightfulness of the
reflection.

Portfolio Guru drafts every entry with model assistance, so every entry it
saves carries a declaration. Two properties matter:

1. The declaration goes *inside the log* — appended to the entry's own
   narrative field — not into a covering message or the one-line timeline
   Description, which is a summary rather than part of the reflection.
2. The doctor sees it in the draft preview before approving, so approval
   covers the declaration as well as the content.

Scope is deliberately wider than RCEM's: the statement is written about
reflective logs, but any entry Portfolio Guru drafts was AI-assisted, so the
declaration is applied to every form type that has a narrative field. Forms
with no free-text field (attendance/output logs such as STAT, JCF, AUDIT,
RESEARCH) carry nothing, because there is no reflection to declare against
and no honest place to put the sentence.

Wording is not mandated by the College — the statement gives an example only.
Label and sentence are therefore runtime-overridable so preferred wording can
be adopted without a code change:

    PG_AI_DECLARATION        "0"/"false"/"no"/"off" disables the declaration
    PG_AI_DECLARATION_LABEL  overrides the section label
    PG_AI_DECLARATION_TEXT   overrides the declaration sentence
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_DECLARATION_LABEL = "AI use declaration"
DEFAULT_DECLARATION_TEXT = (
    "AI was used to help structure and edit this entry. "
    "The content, accuracy and reflective insight are my own."
)

_DISABLED_VALUES = {"0", "false", "no", "off"}

# Narrative fields that can carry the declaration, most reflective first. Only
# keys that map to a genuine Kaizen free-text control belong here — a
# declaration written into a dropdown or a date field would be a misfile.
DECLARATION_FIELD_PRIORITY: tuple[str, ...] = (
    "reflection",
    "reflective_comments",
    "reflective_notes",
    "reflection_on_learning",
    "learned",
    "learning_points",
    "lessons_learned",
    "learning_outcomes",
    "clinical_reasoning",
    "further_action",
    "other_comments",
    "general_comments",
    "session_description",
    "case_observed",
    "cases_observed",
    "patient_presentation",
    "clinical_scenario",
    "leadership_context",
    "project_description",
    "brief_description",
    "resource_details",
    "situation",
    "description",
)

# DOM targets that must never receive the declaration even if a priority key
# happens to map to them. `event-description` is Kaizen's one-line timeline
# summary; the date controls are self-explanatory.
_NON_NARRATIVE_DOM_IDS = frozenset({"event-description", "startDate", "endDate"})


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def is_enabled() -> bool:
    """True unless PG_AI_DECLARATION is explicitly switched off."""
    return _env("PG_AI_DECLARATION").lower() not in _DISABLED_VALUES


def declaration_label() -> str:
    return _env("PG_AI_DECLARATION_LABEL") or DEFAULT_DECLARATION_LABEL


def declaration_text() -> str:
    return _env("PG_AI_DECLARATION_TEXT") or DEFAULT_DECLARATION_TEXT


def declaration_block() -> str:
    """The exact text appended to the entry's narrative field."""
    return f"{declaration_label()}: {declaration_text()}"


def contains_declaration(value: Any) -> bool:
    """True if `value` already carries a declaration.

    Matches on the label as well as the sentence so a draft declared under
    previous wording is not declared twice when it is re-filed.
    """
    text = str(value or "")
    if not text.strip():
        return False
    lowered = text.lower()
    return (
        f"{declaration_label().lower()}:" in lowered
        or declaration_text().lower() in lowered
        or DEFAULT_DECLARATION_LABEL.lower() + ":" in lowered
    )


def fields_carry_declaration(fields: dict) -> bool:
    return any(contains_declaration(value) for value in (fields or {}).values())


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return False
    return bool(str(value).strip())


def _field_dom_id(field_target: Any) -> str:
    """Mirror kaizen_form_filer._field_dom_id without importing it."""
    if isinstance(field_target, dict):
        return str(field_target.get("dom_id") or "")
    return str(field_target or "")


def _resolve_field_map(form_type: str, field_map: Optional[dict]) -> dict:
    if field_map is not None:
        return field_map
    try:
        from kaizen_form_filer import FORM_FIELD_MAP, canonical_form_type
        return FORM_FIELD_MAP.get(canonical_form_type(form_type), {})
    except Exception:  # pragma: no cover - defensive; filer always importable
        logger.warning("ai_declaration: could not load FORM_FIELD_MAP for %s", form_type)
        return {}


def declaration_target_field(
    form_type: str,
    fields: dict,
    field_map: Optional[dict] = None,
    *,
    require_mapped: bool = True,
) -> Optional[str]:
    """Return the field key that should carry the declaration, or None.

    A key qualifies when it is a known narrative field, is mapped to a real
    Kaizen free-text control, and already holds content. The content
    requirement is deliberate: writing the declaration into an otherwise-empty
    reflection would turn a field the doctor still needs to complete into one
    that looks filled.

    `require_mapped=False` is for the browser-use bridge, which fills by field
    name on forms that have no DOM map to check against.
    """
    resolved_map = _resolve_field_map(form_type, field_map) if require_mapped else {}
    fields = fields or {}
    for key in DECLARATION_FIELD_PRIORITY:
        if require_mapped:
            if key not in resolved_map:
                continue
            if _field_dom_id(resolved_map[key]) in _NON_NARRATIVE_DOM_IDS:
                continue
        if _has_content(fields.get(key)):
            return key
    return None


def apply_ai_declaration(
    form_type: str,
    fields: dict,
    field_map: Optional[dict] = None,
    *,
    require_mapped: bool = True,
) -> tuple[dict, dict]:
    """Append the RCEM AI-use declaration to the entry's narrative field.

    Idempotent: a draft that already carries a declaration (including one
    re-filed from Kaizen) is returned unchanged.

    Returns `(fields, meta)` where meta is
    `{"declared": bool, "field": key_or_None, "reason": str}`.
    """
    out = dict(fields or {})

    if not is_enabled():
        return out, {"declared": False, "field": None, "reason": "disabled"}
    if fields_carry_declaration(out):
        return out, {"declared": False, "field": None, "reason": "already_declared"}

    target = declaration_target_field(form_type, out, field_map, require_mapped=require_mapped)
    if not target:
        logger.info(
            "ai_declaration: no narrative field available on %s — entry filed without a declaration",
            form_type,
        )
        return out, {"declared": False, "field": None, "reason": "no_narrative_field"}

    existing = str(out[target]).strip()
    out[target] = f"{existing}\n\n{declaration_block()}"
    return out, {"declared": True, "field": target, "reason": "appended"}


def will_declare(form_type: str, fields: dict) -> bool:
    """Whether filing this draft will add a declaration — used by the preview.

    Resolves against the same normalisation filing uses, so the preview does
    not promise a declaration the filer cannot place (and vice versa).
    """
    if not is_enabled():
        return False
    fields = fields or {}
    if fields_carry_declaration(fields):
        return True
    try:
        from kaizen_form_filer import normalise_fields_for_deterministic_filing
        resolved = normalise_fields_for_deterministic_filing(form_type, fields)
    except Exception:
        logger.warning("ai_declaration: normalisation failed for %s; using raw fields", form_type)
        resolved = fields
    return declaration_target_field(form_type, resolved) is not None
