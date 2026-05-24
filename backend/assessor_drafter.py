"""Pure drafting helpers for the Clinical Supervisor workflow.

Takes a free-text supervisor "intent" (typed text or a transcribed voice
note) plus the assessor-side form schema and produces a structured
:class:`AssessorDraft` ready for Telegram preview. No I/O, no Kaizen
contact, no LLM calls — the module is intentionally deterministic so it
can be exercised offline. A future slice may replace
:func:`extract_field_values` with an LLM-backed extractor; the public
contract is shaped to allow that without changing call sites.

The draft preview is the only artefact this slice produces. Saving the
draft to Kaizen (Fill in / Save / Submit / Sign) is explicitly out of
scope and not implemented here — the safety contract in
``docs/clinical-supervisor-architecture.md`` keeps every write action
behind explicit per-ticket approval that does not exist yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from form_schemas import ASSESSOR_FORM_SCHEMAS

_BRIEF_FEEDBACK_THRESHOLD = 50
_RECOMMENDATION_HINTS = (
    "recommend", "recommendation", "next time", "next step",
    "should", "could improve", "improvement", "develop",
    "work on", "focus on", "consider", "suggest",
)
_ENTRUSTMENT_HINTS: tuple[tuple[str, str], ...] = (
    # Ordered: more specific phrases first so "did not need to be there"
    # outranks the bare "did not" check that might appear elsewhere.
    (r"\blevel\s*5\b|did(?:n'?t| not) need to be there", "Level 5 - I did not need to be there"),
    (r"\blevel\s*4\b|needed to be there.*did(?:n'?t| not) need to prompt", "Level 4 - I needed to be there but did not need to prompt"),
    (r"\blevel\s*3\b|had to prompt", "Level 3 - I had to prompt"),
    (r"\blevel\s*2\b|talk(?:ed)? them through", "Level 2 - I had to talk them through"),
    (r"\blevel\s*1\b|had to do (?:it )?myself", "Level 1 - I had to do"),
)


@dataclass
class AssessorDraft:
    """A reviewable assessor-side draft. Lives only in Telegram memory and disk cache."""

    form_type: str
    ticket_uuid: str | None
    values: dict[str, str] = field(default_factory=dict)
    missing_required: list[dict[str, Any]] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    source_intent: str = ""


def extract_field_values(intent: str, schema: dict[str, Any]) -> dict[str, str]:
    """Deterministic field extraction from a free-text intent.

    Current rules (no LLM yet):
    * The whole intent goes into the ``feedback`` field where the schema
      has one (CBD / DOPS / Mini-CEX) or ``feedback_on_performance`` for
      QIAT.
    * Entrustment Scale is filled when the intent contains a clear
      numeric or behavioural hint; otherwise left blank.
    * Every other field is left blank for the supervisor to confirm or
      provide in a later turn.
    """
    intent = (intent or "").strip()
    if not intent:
        return {}

    field_keys = {f.get("key") for f in schema.get("fields", []) if f.get("key")}
    values: dict[str, str] = {}

    if "feedback" in field_keys:
        values["feedback"] = intent
    elif "feedback_on_performance" in field_keys:
        values["feedback_on_performance"] = intent

    if "entrustment_scale" in field_keys:
        inferred = _infer_entrustment_scale(intent)
        if inferred:
            values["entrustment_scale"] = inferred

    return values


def _infer_entrustment_scale(intent: str) -> str | None:
    text = intent.lower()
    for pattern, label in _ENTRUSTMENT_HINTS:
        if re.search(pattern, text):
            return label
    return None


def missing_required_fields(
    values: dict[str, str], schema: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return schema fields whose ``required`` is true and whose value is empty."""
    missing: list[dict[str, Any]] = []
    for field_def in schema.get("fields", []):
        if not field_def.get("required"):
            continue
        key = field_def.get("key")
        value = (values.get(key) or "").strip() if key else ""
        if not value:
            missing.append(field_def)
    return missing


def risk_notes_for(
    *,
    intent: str,
    values: dict[str, str],
    schema: dict[str, Any],
    missing: Iterable[dict[str, Any]],
) -> list[str]:
    """Surface low-confidence or low-substance observations for the supervisor."""
    notes: list[str] = []
    field_keys = {f.get("key") for f in schema.get("fields", []) if f.get("key")}

    feedback_key = "feedback" if "feedback" in field_keys else (
        "feedback_on_performance" if "feedback_on_performance" in field_keys else None
    )
    if feedback_key:
        feedback_value = (values.get(feedback_key) or "").strip()
        if feedback_value and len(feedback_value) < _BRIEF_FEEDBACK_THRESHOLD:
            notes.append("Feedback is brief — consider adding clinical detail before saving.")

    if "recommendation" in field_keys:
        intent_lower = (intent or "").lower()
        has_recommendation_hint = any(hint in intent_lower for hint in _RECOMMENDATION_HINTS)
        if not has_recommendation_hint and not (values.get("recommendation") or "").strip():
            notes.append("No recommendation phrasing detected — add one before saving.")

    if "entrustment_scale" in field_keys and not values.get("entrustment_scale"):
        notes.append("Entrustment level not inferred from your words — pick it manually before saving.")

    required_missing_keys = {m.get("key") for m in missing if m.get("key")}
    identity_keys = {
        "assessor_registration_number",
        "assessor_name",
        "assessor_email",
        "assessor_job_title",
    }
    if identity_keys & required_missing_keys:
        notes.append("Assessor identity fields are blank — Kaizen will reject the draft without them.")

    return notes


def draft_from_intent(
    intent: str,
    *,
    form_type: str,
    ticket_uuid: str | None = None,
    schema: dict[str, Any] | None = None,
) -> AssessorDraft:
    """Build a reviewable assessor draft from a single supervisor utterance."""
    resolved_schema = schema or ASSESSOR_FORM_SCHEMAS.get(form_type)
    if resolved_schema is None:
        return AssessorDraft(
            form_type=form_type,
            ticket_uuid=ticket_uuid,
            source_intent=intent or "",
            risk_notes=[
                f"No assessor schema available for form type {form_type!r}. "
                "Draft fields cannot be populated."
            ],
        )

    values = extract_field_values(intent, resolved_schema)
    missing = missing_required_fields(values, resolved_schema)
    notes = risk_notes_for(
        intent=intent,
        values=values,
        schema=resolved_schema,
        missing=missing,
    )
    return AssessorDraft(
        form_type=form_type,
        ticket_uuid=ticket_uuid,
        values=values,
        missing_required=missing,
        risk_notes=notes,
        source_intent=intent or "",
    )


def _format_value_for_preview(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return "—"
    if len(cleaned) > 280:
        return cleaned[:277].rstrip() + "…"
    return cleaned


def render_preview(draft: AssessorDraft, *, schema: dict[str, Any] | None = None) -> str:
    """Render a Telegram-safe Markdown preview of the assessor draft."""
    resolved_schema = schema or ASSESSOR_FORM_SCHEMAS.get(draft.form_type)
    lines: list[str] = [f"📝 *Assessor draft — {draft.form_type}*"]

    if resolved_schema is None:
        lines.append("\nNo schema available for this form type. Showing raw intent only.")
        if draft.source_intent:
            lines.append(f"\n_Your words:_\n{_format_value_for_preview(draft.source_intent)}")
        return "\n".join(lines)

    lines.append("\n*Fields*")
    for field_def in resolved_schema.get("fields", []):
        key = field_def.get("key")
        label = field_def.get("label") or key or ""
        value = draft.values.get(key, "") if key else ""
        lines.append(f"• {label}: {_format_value_for_preview(value)}")

    if draft.missing_required:
        lines.append("\n*Missing required*")
        for missing in draft.missing_required:
            label = missing.get("label") or missing.get("key") or ""
            lines.append(f"• {label}")

    if draft.risk_notes:
        lines.append("\n⚠️ *Risk notes*")
        for note in draft.risk_notes:
            lines.append(f"• {note}")

    lines.append(
        "\n_This is a local draft. The bot does not save, submit, or sign anything in Kaizen._"
    )
    return "\n".join(lines)
