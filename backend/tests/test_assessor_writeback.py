"""Tests for guarded assessor Kaizen write-back planning."""

from __future__ import annotations

import pytest

import assessor_writeback
from assessor_drafter import AssessorDraft


def _complete_cbd_draft(ticket_uuid: str = "ticket-123") -> AssessorDraft:
    return AssessorDraft(
        form_type="CBD",
        ticket_uuid=ticket_uuid,
        values={
            "assessor_registration_number": "GMC1234567",
            "assessor_job_title": "Consultant",
            "entrustment_scale": "Level 4 - I needed to be there but did not need to prompt",
            "feedback": "Clear clinical reasoning and appropriate escalation.",
            "recommendation": "Continue documenting differential diagnosis and escalation triggers.",
        },
        missing_required=[],
        risk_notes=[],
        source_intent="Reviewed wording",
    )


def _request_for(draft: AssessorDraft, action=assessor_writeback.AssessorWriteAction.SAVE_DRAFT):
    return assessor_writeback.build_write_request(
        action=action,
        ticket_uuid=draft.ticket_uuid,
        form_type=draft.form_type,
        reviewed_draft_hash=assessor_writeback.draft_review_hash(draft),
    )


def test_save_draft_plan_maps_cbd_fields_but_is_not_live_executable():
    draft = _complete_cbd_draft()

    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))

    assert plan.request.action is assessor_writeback.AssessorWriteAction.SAVE_DRAFT
    assert plan.touches_kaizen is True
    assert plan.is_final_action is False
    assert plan.live_execution_available is False
    assert plan.is_executable is False
    labels = {field.label for field in plan.field_writes}
    assert "Assessor Registration Number" in labels
    assert "Feedback" in labels
    assert any(step.kind == "open_completion_surface" for step in plan.browser_steps)
    assert any(step.kind == "save_draft" for step in plan.browser_steps)


def test_fill_fields_plan_is_distinct_from_save_and_final_actions():
    draft = _complete_cbd_draft()
    request = _request_for(draft, assessor_writeback.AssessorWriteAction.FILL_FIELDS)

    plan = assessor_writeback.build_write_plan(draft, request)

    assert plan.request.action is assessor_writeback.AssessorWriteAction.FILL_FIELDS
    assert any(step.kind == "fill_field" for step in plan.browser_steps)
    assert not any(step.kind == "save_draft" for step in plan.browser_steps)
    assert not plan.is_final_action


@pytest.mark.parametrize(
    "action",
    [
        assessor_writeback.AssessorWriteAction.SUBMIT,
        assessor_writeback.AssessorWriteAction.SIGN,
        assessor_writeback.AssessorWriteAction.APPROVE,
    ],
)
def test_final_actions_are_distinguished_and_blocked(action):
    draft = _complete_cbd_draft()

    plan = assessor_writeback.build_write_plan(draft, _request_for(draft, action))

    assert plan.is_final_action is True
    assert any(action.value in reason for reason in plan.blocked_reasons)
    assert plan.is_executable is False


def test_cancel_plan_does_not_require_ticket_identity_or_touch_kaizen():
    draft = _complete_cbd_draft()
    request = assessor_writeback.build_write_request(
        action=assessor_writeback.AssessorWriteAction.CANCEL,
        ticket_uuid=None,
        form_type=None,
        reviewed_draft_hash=None,
    )

    plan = assessor_writeback.build_write_plan(draft, request)

    assert plan.touches_kaizen is False
    assert plan.browser_steps[0].kind == "cancel_local"
    assert plan.blocked_reasons == []


def test_write_plan_requires_matching_ticket_and_reviewed_draft_hash():
    draft = _complete_cbd_draft("ticket-a")
    request = assessor_writeback.build_write_request(
        action=assessor_writeback.AssessorWriteAction.SAVE_DRAFT,
        ticket_uuid="ticket-b",
        form_type="CBD",
        reviewed_draft_hash="wrong",
    )

    plan = assessor_writeback.build_write_plan(draft, request)

    assert "ticket_uuid does not match" in " ".join(plan.blocked_reasons)
    assert "reviewed_draft_hash" in " ".join(plan.blocked_reasons)
    assert plan.is_executable is False


def test_write_plan_blocks_unmapped_forms():
    draft = AssessorDraft(
        form_type="DOPS",
        ticket_uuid="ticket-dops",
        values={"feedback": "Good performance."},
    )
    request = assessor_writeback.build_write_request(
        action=assessor_writeback.AssessorWriteAction.SAVE_DRAFT,
        ticket_uuid="ticket-dops",
        form_type="DOPS",
        reviewed_draft_hash=assessor_writeback.draft_review_hash(draft),
    )

    plan = assessor_writeback.build_write_plan(draft, request)

    assert any("not mapped" in reason for reason in plan.blocked_reasons)
    assert plan.field_writes == []


def test_write_plan_reports_missing_required_fields_before_save():
    draft = AssessorDraft(
        form_type="CBD",
        ticket_uuid="ticket-cbd",
        values={"feedback": "Good performance."},
    )

    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))

    assert "assessor_registration_number" in plan.missing_required
    assert any("required assessor fields" in reason for reason in plan.blocked_reasons)


def test_render_write_plan_contains_safety_boundary_without_draft_values():
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))

    text = assessor_writeback.render_write_plan(plan)

    assert "Live Kaizen execution is unavailable" in text
    assert "did not open, fill, save, submit, sign, or approve" in text
    assert "Clear clinical reasoning" not in text


async def test_execute_write_plan_is_unavailable_even_with_a_valid_plan():
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))

    with pytest.raises(assessor_writeback.AssessorWriteBackUnavailable):
        await assessor_writeback.execute_write_plan(plan)
