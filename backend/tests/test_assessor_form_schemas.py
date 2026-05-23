"""Tests for assessor-section form schemas used by Clinical Supervisor mode.

These schemas describe the fields the *assessor* (supervisor) fills in. They
are separate from FORM_SCHEMAS, which describes the trainee-side fields.
"""

from __future__ import annotations

import pytest

from form_schemas import ASSESSOR_FORM_SCHEMAS


SUPPORTED_FORM_TYPES = ("CBD", "DOPS", "MINI_CEX", "QIAT", "ESLE")


def test_all_five_form_types_have_assessor_schemas():
    for form_type in SUPPORTED_FORM_TYPES:
        assert form_type in ASSESSOR_FORM_SCHEMAS, f"Missing assessor schema for {form_type}"


@pytest.mark.parametrize("form_type", SUPPORTED_FORM_TYPES)
def test_assessor_schema_has_required_metadata(form_type):
    schema = ASSESSOR_FORM_SCHEMAS[form_type]

    assert "name" in schema
    assert "fields" in schema
    assert isinstance(schema["fields"], list)
    assert len(schema["fields"]) > 0


@pytest.mark.parametrize("form_type", SUPPORTED_FORM_TYPES)
def test_each_assessor_field_has_key_label_type(form_type):
    schema = ASSESSOR_FORM_SCHEMAS[form_type]
    for field in schema["fields"]:
        assert "key" in field
        assert "label" in field
        assert "type" in field


def test_cbd_assessor_schema_matches_discovery():
    """CBD assessor section matches the DOPS-evidenced shape (architecture brief)."""
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    labels = {f["label"] for f in schema["fields"]}

    assert "Assessor Registration Number" in labels
    assert "Job title" in labels
    assert "Entrustment Scale" in labels
    assert "Feedback" in labels
    assert "Recommendation for further learning or development" in labels


def test_dops_assessor_schema_matches_discovery():
    schema = ASSESSOR_FORM_SCHEMAS["DOPS"]
    labels = {f["label"] for f in schema["fields"]}

    assert "Assessor Registration Number" in labels
    assert "Job title" in labels
    assert "Entrustment Scale" in labels
    assert "Feedback" in labels
    assert "Recommendation for further learning or development" in labels


def test_mini_cex_assessor_schema_matches_discovery():
    schema = ASSESSOR_FORM_SCHEMAS["MINI_CEX"]
    labels = {f["label"] for f in schema["fields"]}

    assert "Assessor Registration Number" in labels
    assert "Job title" in labels
    assert "Entrustment Scale" in labels
    assert "Feedback" in labels
    assert "Recommendation for further learning or development" in labels


@pytest.mark.parametrize("form_type", ("CBD", "DOPS", "MINI_CEX"))
def test_assessor_schema_has_conditional_other_specify(form_type):
    """DOPS evidence (raw-output.json index 8) shows a conditional 'If other, please specify'
    field after Job title; the architecture brief asserts CBD/Mini-CEX share the shape.
    """
    schema = ASSESSOR_FORM_SCHEMAS[form_type]
    other = next(
        (f for f in schema["fields"] if f["key"] == "assessor_other_specify"),
        None,
    )

    assert other is not None, f"{form_type} schema missing assessor_other_specify"
    assert other["required"] is False
    assert other["conditional_required_when"] == {"assessor_job_title": "Other"}


def test_entrustment_scale_is_dropdown_with_levels():
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    entrustment = next(f for f in schema["fields"] if f["label"] == "Entrustment Scale")

    assert entrustment["type"] == "dropdown"
    assert "options" in entrustment
    assert len(entrustment["options"]) >= 4


def test_qiat_assessor_schema_has_slo11_field():
    schema = ASSESSOR_FORM_SCHEMAS["QIAT"]
    labels = {f["label"] for f in schema["fields"]}

    assert "SLO 11 performance level" in labels
    assert "Feedback on clinician performance" in labels
    assert "Learning points" in labels
    assert "Recommendation" in labels


def test_qiat_assessor_schema_has_assessor_contact_block():
    schema = ASSESSOR_FORM_SCHEMAS["QIAT"]
    labels = {f["label"] for f in schema["fields"]}

    assert "Assessor name" in labels
    assert "Assessor Registration Number" in labels
    assert "Assessor email" in labels
    assert "Job title" in labels


def test_esle_assessor_schema_has_minimum_complexity():
    """ESLE is the most complex assessor form. Architecture brief says 30+ fields."""
    schema = ASSESSOR_FORM_SCHEMAS["ESLE"]

    assert len(schema["fields"]) >= 15, "ESLE assessor section should have at least 15 fields"


def test_esle_assessor_schema_includes_nts_ratings():
    """ESLE includes non-technical skills (NTS) ratings."""
    schema = ASSESSOR_FORM_SCHEMAS["ESLE"]
    labels = {f["label"] for f in schema["fields"]}

    # NTS rating fields per architecture doc
    assert "Maintenance of standards" in labels
    assert "Workload management" in labels
    assert "Team building" in labels
    assert "Communication quality" in labels
    assert "Summary of NTS evaluation" in labels


def test_esle_assessor_schema_includes_event_sequence_and_cases():
    schema = ASSESSOR_FORM_SCHEMAS["ESLE"]
    labels = {f["label"] for f in schema["fields"]}

    assert "Records event sequence" in labels
    assert "Clinical cases covered" in labels
    assert "Key learning points" in labels
    assert "Learning Objectives" in labels


def test_assessor_schema_does_not_overlap_with_trainee_schema():
    """ASSESSOR_FORM_SCHEMAS describes only assessor-side fields, not trainee-side.

    For CBD, the trainee fills 'Patient Presentation', 'Clinical Reasoning', etc.
    The assessor should NOT see those as part of their schema.
    """
    schema = ASSESSOR_FORM_SCHEMAS["CBD"]
    labels = {f["label"] for f in schema["fields"]}

    assert "Patient Presentation" not in labels
    assert "Reflection of event" not in labels
    assert "Case to be discussed" not in labels
