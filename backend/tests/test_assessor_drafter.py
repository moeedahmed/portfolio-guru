"""Tests for the assessor draft service used by Clinical Supervisor mode.

The drafter is pure: it takes a supervisor's free-text intent plus the
assessor-side schema and returns a structured :class:`AssessorDraft`.
No Kaizen contact, no LLM calls in this slice — the tests pin down the
deterministic behaviour:

* Whole intent lands in ``feedback`` for CBD / DOPS / Mini-CEX schemas
  (or ``feedback_on_performance`` for QIAT).
* Entrustment Scale is inferred from numeric or behavioural hints.
* Required schema fields surface in ``missing_required``.
* Risk notes flag brief feedback, missing recommendation phrasing,
  missing entrustment, and missing assessor identity.
* ``render_preview`` produces a Markdown preview with field labels,
  missing-required block, and a safety footer.
* Module source contains no Kaizen write-action references (defence in
  depth — the safety contract keeps Fill in / Save / Submit / Sign out
  of every Clinical Supervisor module).
"""

from __future__ import annotations

import inspect

import pytest

import assessor_drafter
from assessor_drafter import (
    AssessorDraft,
    draft_from_intent,
    extract_field_values,
    missing_required_fields,
    render_preview,
    risk_notes_for,
)
from form_schemas import ASSESSOR_FORM_SCHEMAS


# ── extract_field_values ────────────────────────────────────────────────────


def test_extract_field_values_puts_intent_into_feedback_for_cbd():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    intent = "Excellent clinical reasoning, identified ACS rule-out promptly."

    values = extract_field_values(intent, schema)

    assert values["feedback"] == intent


def test_extract_field_values_uses_feedback_on_performance_for_qiat():
    schema = ASSESSOR_FORM_SCHEMAS["QIAT"]
    intent = "Strong QI project, clear measurement plan."

    values = extract_field_values(intent, schema)

    assert "feedback_on_performance" in values
    assert values["feedback_on_performance"] == intent
    assert "feedback" not in values


@pytest.mark.parametrize(
    ("intent", "expected_level_prefix"),
    [
        ("I needed level 4 supervision.", "Level 4"),
        ("Did not need to be there, very independent.", "Level 5"),
        ("Had to prompt them on the airway plan.", "Level 3"),
        ("Talked them through every step.", "Level 2"),
        ("Had to do it myself.", "Level 1"),
    ],
)
def test_extract_field_values_infers_entrustment_when_hints_present(intent, expected_level_prefix):
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]

    values = extract_field_values(intent, schema)

    assert values.get("entrustment_scale", "").startswith(expected_level_prefix)


def test_extract_field_values_leaves_entrustment_blank_when_no_hint():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    intent = "Good case, well managed."

    values = extract_field_values(intent, schema)

    assert "entrustment_scale" not in values


def test_extract_field_values_returns_empty_for_blank_intent():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]

    assert extract_field_values("", schema) == {}
    assert extract_field_values("   ", schema) == {}


# ── missing_required_fields ─────────────────────────────────────────────────


def test_missing_required_lists_unfilled_required_fields():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    values = {"feedback": "Great work"}

    missing = missing_required_fields(values, schema)
    missing_keys = {m["key"] for m in missing}

    assert "assessor_registration_number" in missing_keys
    assert "assessor_job_title" in missing_keys
    assert "entrustment_scale" in missing_keys
    assert "recommendation" in missing_keys
    assert "feedback" not in missing_keys


def test_missing_required_ignores_optional_fields():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    values = {
        "feedback": "Great work",
        "recommendation": "Consider revision of X protocol",
        "assessor_registration_number": "1234567",
        "assessor_job_title": "Consultant",
        "entrustment_scale": "Level 4 - I needed to be there but did not need to prompt",
    }

    missing = missing_required_fields(values, schema)

    # All required filled — only optional ones remain (assessor_other_specify is conditional).
    assert all(not m["required"] or False for m in missing) or missing == []


def test_missing_required_treats_whitespace_only_as_empty():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    values = {"feedback": "   "}

    missing = missing_required_fields(values, schema)
    missing_keys = {m["key"] for m in missing}

    assert "feedback" in missing_keys


# ── risk_notes_for ──────────────────────────────────────────────────────────


def test_risk_notes_flag_brief_feedback():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    intent = "Good."
    values = {"feedback": intent}

    notes = risk_notes_for(intent=intent, values=values, schema=schema, missing=[])

    assert any("brief" in note.lower() for note in notes)


def test_risk_notes_flag_missing_recommendation_phrasing():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    intent = "Patient was managed appropriately, good documentation throughout the encounter."
    values = {"feedback": intent}

    notes = risk_notes_for(intent=intent, values=values, schema=schema, missing=[])

    assert any("recommendation" in note.lower() for note in notes)


def test_risk_notes_do_not_flag_recommendation_when_intent_mentions_it():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    intent = (
        "Patient was managed well. Recommend revising the documentation "
        "macro to capture pre-test probability for chest pain."
    )
    values = {"feedback": intent}

    notes = risk_notes_for(intent=intent, values=values, schema=schema, missing=[])

    assert not any(
        "no recommendation phrasing detected" in note.lower() for note in notes
    )


def test_risk_notes_flag_missing_entrustment():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    intent = "Strong performance. Recommend writing up the case for grand rounds discussion."
    values = {"feedback": intent}

    notes = risk_notes_for(intent=intent, values=values, schema=schema, missing=[])

    assert any("entrustment" in note.lower() for note in notes)


def test_risk_notes_flag_missing_assessor_identity():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    intent = "Solid case."
    values = {"feedback": intent}
    missing = missing_required_fields(values, schema)

    notes = risk_notes_for(intent=intent, values=values, schema=schema, missing=missing)

    assert any("identity" in note.lower() for note in notes)


# ── draft_from_intent ───────────────────────────────────────────────────────


def test_draft_from_intent_returns_full_draft_for_cbd():
    intent = (
        "Patient with chest pain managed appropriately, good initial assessment "
        "and clear documentation."
    )

    draft = draft_from_intent(intent, form_type="CBD", ticket_uuid="ticket-1")

    assert draft.form_type == "CBD"
    assert draft.ticket_uuid == "ticket-1"
    assert draft.values["feedback"] == intent
    assert draft.source_intent == intent
    assert any(m["key"] == "assessor_registration_number" for m in draft.missing_required)


def test_draft_from_intent_handles_unknown_form_type_gracefully():
    draft = draft_from_intent("Some intent", form_type="UNKNOWN_TYPE")

    assert draft.form_type == "UNKNOWN_TYPE"
    assert draft.values == {}
    assert draft.missing_required == []
    # Risk note explains why no draft was produced.
    assert any("schema available" in note.lower() for note in draft.risk_notes)


def test_draft_from_intent_with_explicit_schema_overrides_default():
    custom = {
        "name": "Custom",
        "fields": [
            {"key": "feedback", "label": "Feedback", "type": "text", "required": True},
        ],
    }

    draft = draft_from_intent("My note here", form_type="CBD", schema=custom)

    assert draft.values == {"feedback": "My note here"}
    assert draft.missing_required == []


# ── render_preview ──────────────────────────────────────────────────────────


def test_render_preview_includes_field_labels_and_safety_footer():
    draft = draft_from_intent(
        "Excellent clinical reasoning throughout the encounter, level 4 supervision.",
        form_type="CBD",
        ticket_uuid="ticket-2",
    )

    preview = render_preview(draft)

    assert "Assessor draft — CBD" in preview
    assert "Feedback" in preview
    assert "Entrustment Scale" in preview
    assert "Recommendation for further learning or development" in preview
    assert "review only" in preview.lower() or "does not save" in preview.lower()


def test_render_preview_lists_missing_required_block():
    draft = draft_from_intent("Short.", form_type="CBD")

    preview = render_preview(draft)

    assert "Missing required" in preview
    assert "Assessor Registration Number" in preview


def test_render_preview_lists_risk_notes_block():
    draft = draft_from_intent("Short.", form_type="CBD")

    preview = render_preview(draft)

    assert "Risk notes" in preview


def test_render_preview_truncates_long_field_values():
    long_intent = "x" * 500
    draft = draft_from_intent(long_intent, form_type="CBD")

    preview = render_preview(draft)

    # 280-char truncation guard keeps the preview Telegram-safe.
    assert "x" * 500 not in preview
    assert "…" in preview


def test_render_preview_handles_unknown_form_type_with_only_intent():
    draft = AssessorDraft(form_type="UNKNOWN", ticket_uuid=None, source_intent="hello")

    preview = render_preview(draft)

    assert "UNKNOWN" in preview
    assert "hello" in preview


# ── safety: no write-side Kaizen actions referenced ─────────────────────────


def test_drafter_module_never_clicks_write_controls():
    source = inspect.getsource(assessor_drafter)
    forbidden_snippets = [
        "click('text=Sign",
        "click('text=Submit",
        "click('text=Approve",
        "click('text=Delete",
        "click('text=Save",
        "click('text=Send",
        "click('text=Fill",
        'click("text=Sign',
        'click("text=Submit',
        'click("text=Approve',
        'click("text=Delete',
        'click("text=Save',
        'click("text=Send',
        'click("text=Fill',
        "get_by_text('Sign",
        "get_by_text('Submit",
        "get_by_text('Approve",
        "get_by_text('Delete",
        "get_by_text('Save",
        "get_by_text('Send",
        "get_by_text('Fill in",
        ".fill(",
        "extract_assessor_completion_shape",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source, (
            f"assessor_drafter source contains forbidden write action: {snippet}"
        )
