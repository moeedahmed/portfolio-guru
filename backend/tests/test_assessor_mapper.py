"""Tests for read-only assessor workflow mapping."""

from __future__ import annotations

import inspect

import assessor_mapper


def test_event_uuid_from_href_handles_view_routes():
    uuid = "12345678-1234-1234-1234-123456789abc"

    assert assessor_mapper._event_uuid_from_href(f"https://kaizenep.com/events/view/{uuid}") == (uuid, False)
    assert assessor_mapper._event_uuid_from_href(f"https://kaizenep.com/events/view-section/{uuid}") == (uuid, True)
    assert assessor_mapper._event_uuid_from_href(None) == (None, None)


def test_normalise_summary_extracts_uuid_and_state():
    row = {
        "title": "CBD - Case Based Discussion",
        "href": "https://kaizenep.com/events/view-section/12345678-1234-1234-1234-123456789abc",
        "state": "Pending",
    }

    summary = assessor_mapper._normalise_summary(row)

    assert summary.title == "CBD - Case Based Discussion"
    assert summary.uuid == "12345678-1234-1234-1234-123456789abc"
    assert summary.section_view is True
    assert summary.state == "Pending"


def test_redact_ticket_title_removes_owner_suffix():
    assert (
        assessor_mapper.redact_ticket_title("CBD - Case Based Discussion (2025 update) for Jane Example")
        == "CBD - Case Based Discussion (2025 update)"
    )
    assert assessor_mapper.redact_ticket_title("DOPS - Direct Observation") == "DOPS - Direct Observation"


def test_mapper_keeps_write_actions_deny_listed():
    labels = assessor_mapper.WRITE_ACTION_LABELS

    assert {"approve", "delete", "fill in", "save", "send", "sign", "submit"} <= set(labels)


def test_classify_controls_separates_safe_navigation_from_write_controls():
    write_controls, safe_controls = assessor_mapper.classify_controls(
        ["View profile", "Fill in", "Save", "Show more", "Logout"]
    )

    assert write_controls == ["Fill in", "Save"]
    assert safe_controls == ["View profile", "Show more", "Logout"]


def test_summarise_ticket_shape_redacts_field_values():
    summary = assessor_mapper.AssessorTicketSummary(
        title="CBD - Case Based Discussion",
        href="https://kaizenep.com/events/view-section/12345678-1234-1234-1234-123456789abc",
        section_view=True,
    )
    detail = assessor_mapper.AssessorTicketDetail(
        summary=summary,
        event_type="CBD - Case Based Discussion",
        fields=[
            {"label": "Case to be discussed", "value": "Patient-specific narrative"},
            {"label": "Date occurred on", "value": "17 May, 2026"},
        ],
        available_buttons=["Fill in", "Save", "View profile"],
    )

    shape = assessor_mapper.summarise_ticket_shape(detail)

    assert shape.event_type == "CBD - Case Based Discussion"
    assert shape.field_labels == ["Case to be discussed", "Date occurred on"]
    assert shape.write_controls == ["Fill in", "Save"]
    assert shape.safe_controls == ["View profile"]
    assert shape.needs_write_side_mapping is True
    assert shape.route_kind == "view-section"
    assert "Patient-specific narrative" not in repr(shape)


def test_completion_shape_defaults_to_not_saved_or_submitted():
    shape = assessor_mapper.AssessorCompletionShape(
        ticket_type="CBD - Case Based Discussion",
        post_fill_heading="CBD - Case Based Discussion",
        route_kind="/events/fillin/<uuid>",
        field_labels=["Feedback"],
        input_shapes=[{"tag": "textarea", "type": None, "id_present": True, "name_present": True}],
        write_controls=["Submit", "Save as draft"],
        safe_controls=["View profile"],
    )

    assert shape.saved_or_submitted is False


def test_mapper_does_not_click_write_controls():
    source = inspect.getsource(assessor_mapper)
    forbidden_snippets = [
        "click('text=Sign",
        "click('text=Submit",
        "click('text=Approve",
        "click('text=Delete",
        "click('text=Save",
        "click('text=Send",
        "get_by_text('Sign",
        "get_by_text('Submit",
        "get_by_text('Approve",
        "get_by_text('Delete",
        "get_by_text('Save",
        "get_by_text('Send",
    ]

    for snippet in forbidden_snippets:
        assert snippet not in source
