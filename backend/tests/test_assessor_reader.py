"""Tests for read-only assessor ticket reader.

The reader transforms a raw ``AssessorTicketDetail`` (from ``assessor_mapper``)
into structured ``AssessorTicketData`` keyed by form type, so the Telegram
layer can render the ticket and prompt the supervisor for the missing fields.
"""

from __future__ import annotations

import inspect

import pytest

import assessor_reader
from assessor_mapper import AssessorTicketDetail, AssessorTicketSummary


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("CBD - Case Based Discussion", "CBD"),
        ("CBD - Case Based Discussion (2025 update)", "CBD"),
        ("DOPS - Direct Observation of Procedural Skills", "DOPS"),
        ("Mini-CEX - Mini Clinical Evaluation Exercise", "MINI_CEX"),
        ("Mini CEX", "MINI_CEX"),
        ("QIAT - Quality Improvement Assessment Tool", "QIAT"),
        ("ESLE - Extended Supervised Learning Event", "ESLE"),
        ("Some unrelated event", None),
        (None, None),
    ],
)
def test_detect_form_type_handles_known_titles(title, expected):
    assert assessor_reader.detect_form_type(title) == expected


def test_get_assessor_schema_returns_schema_for_known_type():
    schema = assessor_reader.get_assessor_schema("CBD")

    assert schema is not None
    assert schema["name"].startswith("Case-Based Discussion")
    assert any(f["label"] == "Feedback" for f in schema["fields"])


def test_get_assessor_schema_returns_none_for_unknown_type():
    assert assessor_reader.get_assessor_schema("UNKNOWN") is None
    assert assessor_reader.get_assessor_schema(None) is None


def test_build_ticket_data_from_cbd_detail():
    summary = AssessorTicketSummary(
        title="CBD - Case Based Discussion",
        href="https://kaizenep.com/events/view-section/abc-uuid",
        uuid="abc-uuid",
        section_view=True,
    )
    detail = AssessorTicketDetail(
        summary=summary,
        event_type="CBD - Case Based Discussion",
        state="Pending",
        fields=[
            {"label": "Date occurred on", "value": "17 May, 2026"},
            {"label": "Patient Presentation", "value": "Chest pain"},
            {"label": "Clinical Reasoning", "value": "ACS rule-out"},
        ],
        available_buttons=["Fill in", "Save", "View profile"],
        url="https://kaizenep.com/events/view-section/abc-uuid",
    )

    data = assessor_reader.build_ticket_data(detail)

    assert data.form_type == "CBD"
    assert data.ticket_uuid == "abc-uuid"
    assert data.ticket_url == "https://kaizenep.com/events/view-section/abc-uuid"
    assert data.needs_write_side_mapping is True
    # Trainee section preserved (label+value)
    assert {"label": "Patient Presentation", "value": "Chest pain"} in data.trainee_section
    # Pending assessor fields come from schema
    assessor_labels = [f["label"] for f in data.pending_assessor_fields]
    assert "Feedback" in assessor_labels
    assert "Entrustment Scale" in assessor_labels


def test_build_ticket_data_from_esle_detail_has_full_assessor_schema():
    summary = AssessorTicketSummary(
        title="ESLE - Extended Supervised Learning Event",
        href="https://kaizenep.com/events/view-section/esle-uuid",
        uuid="esle-uuid",
    )
    detail = AssessorTicketDetail(summary=summary, event_type="ESLE - Extended Supervised Learning Event")

    data = assessor_reader.build_ticket_data(detail)

    assert data.form_type == "ESLE"
    assert len(data.pending_assessor_fields) >= 15


def test_build_ticket_data_unknown_form_type_returns_empty_assessor_fields():
    summary = AssessorTicketSummary(title="Unknown form", href=None, uuid=None)
    detail = AssessorTicketDetail(summary=summary, event_type="Unknown form")

    data = assessor_reader.build_ticket_data(detail)

    assert data.form_type is None
    assert data.pending_assessor_fields == []


def test_build_ticket_data_handles_no_write_controls():
    summary = AssessorTicketSummary(
        title="CBD - Case Based Discussion",
        href="https://kaizenep.com/events/view-section/abc-uuid",
        uuid="abc-uuid",
    )
    detail = AssessorTicketDetail(
        summary=summary,
        event_type="CBD - Case Based Discussion",
        available_buttons=["View profile", "Logout"],
    )

    data = assessor_reader.build_ticket_data(detail)

    assert data.needs_write_side_mapping is False


def test_build_ticket_data_strips_empty_field_rows():
    summary = AssessorTicketSummary(title="CBD - Case Based Discussion", href=None, uuid=None)
    detail = AssessorTicketDetail(
        summary=summary,
        event_type="CBD - Case Based Discussion",
        fields=[
            {"label": "Date", "value": "17/05/2026"},
            {"label": None, "value": "orphan"},
            {"label": "Clinical Reasoning", "value": None},
        ],
    )

    data = assessor_reader.build_ticket_data(detail)

    # Only rows with labels are kept; null-value rows still keep the label for UX
    labels = [row["label"] for row in data.trainee_section]
    assert "Date" in labels
    assert "Clinical Reasoning" in labels
    assert None not in labels


def test_reader_module_never_clicks_write_controls():
    """The reader must remain read-only — no write actions in the source.

    Mirrors the test in test_assessor_mapper.py.
    """
    source = inspect.getsource(assessor_reader)
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
    ]

    for snippet in forbidden_snippets:
        assert snippet not in source, f"Reader source contains forbidden write action: {snippet}"
