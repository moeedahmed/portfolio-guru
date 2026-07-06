"""Regression tests for CBD draft quality fixes.

Covers the beta-blocking issues found during the June 2026 live Kaizen test:
1. Reflection of event was blank even when clinical facts were present.
2. Case text contained broken grammar ("I safe discharge with his daughter").
3. Procedural skill should not be a CBD content field, but Kaizen's visible
   procedural-skill dropdown still needs an explicit n/a default when rendered.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from extractor import _polish_cbd_fields, _portfolio_quality_polish
from form_schemas import FORM_SCHEMAS
from kaizen_form_filer import FORM_FIELD_MAP


# ─── _polish_cbd_fields: reflection synthesis from clinical_reasoning ─────────


DISCHARGE_CASE = (
    "ST5 EM trainee. 76-year-old man with confusion and urinary symptoms. "
    "I reviewed, suspected UTI with mild delirium. Bloods, urinalysis and CXR "
    "requested. Antibiotics started. Discussed with the medical team. "
    "I safe discharge with his daughter with safety-netting advice and GP follow-up."
)

ESCALATION_CASE = (
    "ST4 in majors. 58-year-old with worsening chest pain and diaphoresis. "
    "ECG showed new LBBB pattern. I involved the registrar immediately. "
    "Patient escalated to the cath lab via the cardiac team."
)

SEPSIS_CASE = (
    "62-year-old presenting with fever 38.9, HR 118, BP 94/62 and confusion. "
    "I recognised sepsis. I took blood cultures, started IV antibiotics and "
    "gave 500ml fluid bolus within the hour. I escalated to the medical team."
)


def test_polish_cbd_fills_reflection_from_discharge_case():
    """Empty reflection synthesised when clinical_reasoning mentions discharge."""
    fields = {
        "clinical_reasoning": (
            "I reviewed the patient, confirmed UTI with delirium features, "
            "started antibiotics, and arranged safe discharge home with his daughter."
        ),
        "reflection": "",
    }
    out = _polish_cbd_fields(fields, DISCHARGE_CASE)
    assert out["reflection"], "reflection must not be blank after polish"
    assert len(out["reflection"]) > 20


def test_polish_cbd_fills_reflection_from_escalation_case():
    """Empty reflection synthesised from escalation clinical context."""
    fields = {
        "clinical_reasoning": "I escalated immediately to the registrar and ITU team.",
        "reflection": "",
    }
    out = _polish_cbd_fields(fields, ESCALATION_CASE)
    assert out["reflection"], "reflection must not be blank for escalation case"
    assert "escalat" in out["reflection"].lower() or "senior" in out["reflection"].lower()


def test_polish_cbd_fills_reflection_from_sepsis_case():
    """Empty reflection synthesised from sepsis-pattern case."""
    fields = {
        "clinical_reasoning": "I recognised sepsis, started IV antibiotics, took cultures.",
        "reflection": "",
    }
    out = _polish_cbd_fields(fields, SEPSIS_CASE)
    assert out["reflection"], "reflection must not be blank for sepsis case"
    assert "sepsis" in out["reflection"].lower()


def test_polish_cbd_preserves_existing_reflection():
    """Polish must not overwrite a reflection the LLM already populated."""
    original = "I found this case reinforced my approach to geriatric delirium."
    fields = {
        "clinical_reasoning": "I reviewed a 76-year-old with confusion.",
        "reflection": original,
    }
    out = _polish_cbd_fields(fields, DISCHARGE_CASE)
    assert out["reflection"] == original


def test_polish_cbd_returns_blank_when_no_clinical_context():
    """When both case and clinical_reasoning are empty, reflection stays blank."""
    fields = {"clinical_reasoning": "", "reflection": ""}
    out = _polish_cbd_fields(fields, "")
    assert out["reflection"] == ""


# ─── _portfolio_quality_polish: grammar repair ────────────────────────────────


def test_grammar_fix_safe_discharge_repaired():
    """'I safe discharge' must be repaired to 'I safely discharged'."""
    text = "I safe discharge with his daughter with safety-netting and GP follow-up."
    result = _portfolio_quality_polish(text)
    assert "I safe discharge" not in result
    assert "I safely discharged" in result


def test_grammar_fix_safe_discharged_repaired():
    """Handles the variant 'I safe discharged' as well."""
    text = "I safe discharged the patient after discussion with the team."
    result = _portfolio_quality_polish(text)
    assert "I safe discharged" not in result
    assert "I safely discharged" in result


def test_grammar_fix_does_not_alter_safe_discharge_used_correctly():
    """'safe discharge' as a noun phrase (not a verb) must survive."""
    text = "I planned a safe discharge with input from the medical team."
    result = _portfolio_quality_polish(text)
    # The noun-phrase form must not be corrupted; only the verb form is fixed.
    assert "safe discharge" in result


def test_grammar_fix_missing_ensure_in_future_reflection():
    """Repair missing verb in 'In future, I will...' reflection phrasing."""
    text = (
        "In future, I will these elements are clearly detailed in my initial "
        "documentation."
    )
    result = _portfolio_quality_polish(text)
    assert "I will these elements" not in result
    assert (
        "In future, I will ensure these elements are clearly documented in my "
        "initial documentation."
    ) == result


# ─── Procedural skills: no CBD content field, but DOM defaults apply ─────────


def test_cbd_schema_has_no_procedural_skill_field():
    """CBD has no procedural_skill field — it is a clinical reasoning form, not
    a procedure assessment. The DOM-level Kaizen procedural dropdown is handled
    by the filer n/a default, not exposed as a CBD draft field."""
    cbd_schema_keys = {f["key"] for f in FORM_SCHEMAS["CBD"]["fields"]}
    assert "procedural_skill" not in cbd_schema_keys
    assert "procedure_name" not in cbd_schema_keys


def test_cbd_field_map_has_no_procedural_skill_dom_entry():
    """CBD does not ask the extractor/user for a procedural skill value."""
    cbd_dom_map = FORM_FIELD_MAP["CBD"]
    assert "procedural_skill" not in cbd_dom_map
    assert "procedure_name" not in cbd_dom_map


def test_cbd_field_map_has_reflection_dom_entry():
    """Regression: CBD FORM_FIELD_MAP must expose a reflection DOM UUID so the
    field is actually written to Kaizen when reflection is non-empty."""
    cbd_dom_map = FORM_FIELD_MAP["CBD"]
    assert "reflection" in cbd_dom_map
    uuid = cbd_dom_map["reflection"]
    assert len(uuid) == 36, f"Expected UUID, got: {uuid!r}"
