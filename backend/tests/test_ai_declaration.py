"""RCEM AI-use declaration must reach every entry Portfolio Guru drafts.

RCEM's "Position on the use of AI in Reflective Logs" (September 2025) s.5
requires AI assistance to be declared *within the log*. These tests guard the
three ways that can regress:

1. The declaration is appended to the entry's narrative field on the real
   filing path (not to the timeline summary, not to a date control).
2. The doctor sees the exact sentence in the draft preview, so approving the
   draft approves the declaration.
3. Every mapped form type has somewhere to put it — a new form cannot ship
   silently undeclared without this test failing.
"""

import pytest

import ai_declaration
from ai_declaration import (
    DECLARATION_FIELD_PRIORITY,
    DEFAULT_DECLARATION_LABEL,
    apply_ai_declaration,
    declaration_block,
    declaration_target_field,
    will_declare,
)
from kaizen_form_filer import (
    FORM_FIELD_MAP,
    apply_common_header_defaults,
    drop_consumed_unmapped_schema_fields,
    normalise_fields_for_deterministic_filing,
)

# Form types with no free-text control at all: teaching-attendance (STAT),
# journal club (JCF), and the audit/research registers. There is no reflection
# on these forms to declare against, and no honest field to put the sentence
# in. Adding a form here is a deliberate decision, not a default.
EXEMPT_FORM_TYPES = {
    "STAT", "STAT_2021",
    "JCF", "JCF_2021",
    "AUDIT", "AUDIT_2021",
    "RESEARCH",
}


@pytest.fixture(autouse=True)
def _clean_declaration_env(monkeypatch):
    """Each test starts from the shipped default wording, enabled."""
    for var in ("PG_AI_DECLARATION", "PG_AI_DECLARATION_LABEL", "PG_AI_DECLARATION_TEXT"):
        monkeypatch.delenv(var, raising=False)


def _file_as_kaizen_would(form_type: str, fields: dict) -> dict:
    """Replay the field pipeline both filing entrypoints run before writing."""
    field_map = FORM_FIELD_MAP[form_type]
    out = normalise_fields_for_deterministic_filing(form_type, fields)
    out, _ = apply_common_header_defaults(form_type, out, field_map)
    out = drop_consumed_unmapped_schema_fields(form_type, out)
    out, meta = apply_ai_declaration(form_type, out, field_map)
    return out, meta


# ── The declaration actually lands in the filed entry ────────────────────────


def test_cbd_entry_is_filed_with_the_declaration_in_its_reflection():
    fields, meta = _file_as_kaizen_would("CBD", {
        "date_of_encounter": "2026-08-01",
        "clinical_reasoning": "Discussed a chest pain presentation with the consultant.",
        "reflection": "I would request the second troponin earlier next time.",
    })

    assert meta["declared"] is True
    assert meta["field"] == "reflection"
    assert declaration_block() in fields["reflection"]
    # The doctor's own words survive intact alongside it.
    assert "I would request the second troponin earlier next time." in fields["reflection"]


def test_declaration_follows_lat_reflection_into_its_merged_target():
    """LAT has no `reflection` DOM field — normalisation merges it into
    clinical_reasoning, and the declaration must follow it there."""
    fields, meta = _file_as_kaizen_would("LAT", {
        "date_of_encounter": "2026-08-01",
        "leadership_context": "Led the shift after a major trauma call.",
        "reflection": "I delegated too late.",
    })

    assert meta["field"] == "clinical_reasoning"
    assert declaration_block() in fields["clinical_reasoning"]
    assert "reflection" not in fields


def test_declaration_never_lands_in_the_timeline_description_or_a_date_field():
    fields, meta = _file_as_kaizen_would("CRIT_INCIDENT", {
        "date_of_encounter": "2026-08-01",
        "description": "Missed sepsis on triage.",
        "reflection": "I now recheck the NEWS score myself.",
    })

    assert meta["field"] == "reflection"
    for key, value in fields.items():
        if key == "reflection":
            continue
        assert DEFAULT_DECLARATION_LABEL not in str(value), f"declaration leaked into {key}"


def test_procedural_log_declares_in_its_reflective_comments():
    fields, meta = _file_as_kaizen_would("PROC_LOG", {
        "date_of_activity": "2026-08-01",
        "higher_procedural_skill": "Chest drain insertion",
        "reflective_comments": "Ultrasound-guided landmarking made this easier.",
    })

    assert meta["field"] == "reflective_comments"
    assert declaration_block() in fields["reflective_comments"]


# ── Coverage: no form type ships silently undeclared ─────────────────────────


@pytest.mark.parametrize("form_type", sorted(set(FORM_FIELD_MAP) - EXEMPT_FORM_TYPES))
def test_every_mapped_form_type_has_a_declaration_target(form_type):
    field_map = FORM_FIELD_MAP[form_type]
    candidates = [key for key in DECLARATION_FIELD_PRIORITY if key in field_map]
    assert candidates, (
        f"{form_type} has no field able to carry the RCEM AI-use declaration. "
        "Add its narrative field key to DECLARATION_FIELD_PRIORITY, or add the "
        "form to EXEMPT_FORM_TYPES if it genuinely has no free-text control."
    )

    populated = {candidates[0]: "Some narrative content the doctor wrote."}
    assert declaration_target_field(form_type, populated, field_map) == candidates[0]


@pytest.mark.parametrize("form_type", sorted(EXEMPT_FORM_TYPES))
def test_exempt_form_types_are_still_genuinely_free_text_free(form_type):
    """If Kaizen adds a narrative field to one of these, stop exempting it."""
    field_map = FORM_FIELD_MAP[form_type]
    assert not [key for key in DECLARATION_FIELD_PRIORITY if key in field_map]


def test_form_with_no_narrative_field_is_filed_without_a_declaration():
    fields, meta = _file_as_kaizen_would("STAT", {
        "date_of_encounter": "2026-08-01",
        "session_title": "Airway teaching",
        "number_of_learners": "8",
    })

    assert meta["declared"] is False
    assert meta["reason"] == "no_narrative_field"
    assert not any(DEFAULT_DECLARATION_LABEL in str(value) for value in fields.values())


# ── Idempotency and guards ───────────────────────────────────────────────────


def test_refiling_an_already_declared_draft_does_not_declare_twice():
    once, _ = _file_as_kaizen_would("CBD", {
        "date_of_encounter": "2026-08-01",
        "reflection": "I would escalate sooner.",
    })
    twice, meta = _file_as_kaizen_would("CBD", once)

    assert meta["declared"] is False
    assert meta["reason"] == "already_declared"
    assert twice["reflection"].count(DEFAULT_DECLARATION_LABEL) == 1


def test_declaration_is_not_written_into_an_empty_reflection():
    """An empty narrative field is one the doctor still has to complete —
    filling it with only a declaration would make it look answered."""
    fields = {"date_of_encounter": "2026-08-01", "reflection": "   "}
    _, meta = apply_ai_declaration("CBD", fields, FORM_FIELD_MAP["CBD"])

    assert meta["declared"] is False
    assert meta["reason"] == "no_narrative_field"


# ── Configurability (wording is not mandated by the College) ─────────────────


def test_declaration_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("PG_AI_DECLARATION", "0")
    fields, meta = _file_as_kaizen_would("CBD", {
        "date_of_encounter": "2026-08-01",
        "reflection": "I would escalate sooner.",
    })

    assert meta["reason"] == "disabled"
    assert fields["reflection"] == "I would escalate sooner."


def test_college_preferred_wording_can_replace_the_default(monkeypatch):
    monkeypatch.setenv("PG_AI_DECLARATION_LABEL", "Declaration")
    monkeypatch.setenv("PG_AI_DECLARATION_TEXT", "AI was used to help structure and edit this reflection.")

    fields, meta = _file_as_kaizen_would("CBD", {
        "date_of_encounter": "2026-08-01",
        "reflection": "I would escalate sooner.",
    })

    assert meta["declared"] is True
    assert "Declaration: AI was used to help structure and edit this reflection." in fields["reflection"]
    assert ai_declaration.DEFAULT_DECLARATION_TEXT not in fields["reflection"]


# ── The doctor approves the declaration, not just the content ────────────────


def test_draft_preview_shows_the_exact_declaration_before_approval():
    from bot import _format_draft_preview
    from models import FormDraft

    draft = FormDraft(form_type="CBD", fields={
        "date_of_encounter": "2026-08-01",
        "clinical_reasoning": "Discussed a chest pain presentation.",
        "reflection": "I would request the second troponin earlier next time.",
    })
    preview = _format_draft_preview(draft)

    assert ai_declaration.declaration_text() in preview
    assert ai_declaration.declaration_label() in preview


def test_regeneration_prompt_does_not_carry_the_declaration_boilerplate():
    """The preview doubles as model input when a draft is regenerated. Feeding
    the declaration back in invites the model to write it into a field."""
    from bot import _format_draft_preview
    from models import FormDraft

    draft = FormDraft(form_type="CBD", fields={
        "date_of_encounter": "2026-08-01",
        "reflection": "I would request the second troponin earlier next time.",
    })
    prompt_view = _format_draft_preview(draft, include_safety_layer=False)

    assert ai_declaration.declaration_text() not in prompt_view
    assert DEFAULT_DECLARATION_LABEL not in prompt_view


def test_draft_preview_omits_the_declaration_when_nothing_will_be_declared():
    from bot import _format_draft_preview
    from models import FormDraft

    draft = FormDraft(form_type="STAT", fields={
        "date_of_encounter": "2026-08-01",
        "session_title": "Airway teaching",
    })
    preview = _format_draft_preview(draft)

    assert ai_declaration.declaration_text() not in preview


def test_preview_promise_matches_what_filing_does():
    """will_declare drives the preview; it must not promise what filing skips."""
    declared_cases = [
        ("CBD", {"reflection": "I would escalate sooner."}),
        ("LAT", {"reflection": "I delegated too late."}),
        ("PROC_LOG", {"reflective_comments": "Landmarking with ultrasound helped."}),
    ]
    for form_type, fields in declared_cases:
        _, meta = _file_as_kaizen_would(form_type, dict(fields, date_of_encounter="2026-08-01"))
        assert will_declare(form_type, fields) is meta["declared"], form_type

    skipped_cases = [
        ("STAT", {"session_title": "Airway teaching"}),
        ("CBD", {"reflection": ""}),
    ]
    for form_type, fields in skipped_cases:
        _, meta = _file_as_kaizen_would(form_type, dict(fields, date_of_encounter="2026-08-01"))
        assert will_declare(form_type, fields) is meta["declared"] is False, form_type
