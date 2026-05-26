"""Tests for guarded assessor Kaizen write-back planning and live execution."""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

import assessor_writeback
from assessor_drafter import AssessorDraft


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Replace asyncio.sleep in assessor_writeback with an immediate awaitable.

    The runner inserts deliberate ``await asyncio.sleep(...)`` pauses to let
    Angular re-render between navigation, Fill in, and Save as draft. Real
    sleeps would multiply test latency by ~13 seconds per execute test.
    """

    async def _instant(_):
        return None

    monkeypatch.setattr(assessor_writeback.asyncio, "sleep", _instant)


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


def _ticket_url_for(draft: AssessorDraft) -> str:
    return f"https://kaizenep.com/events/view-section/{draft.ticket_uuid}"


def _make_locator(
    *,
    count: int = 1,
    click: AsyncMock | None = None,
    fill: AsyncMock | None = None,
    select_option: AsyncMock | None = None,
) -> MagicMock:
    locator = MagicMock()
    locator.first = locator
    locator.count = AsyncMock(return_value=count)
    locator.click = click or AsyncMock()
    locator.fill = fill or AsyncMock()
    locator.select_option = select_option or AsyncMock()
    return locator


def _make_page(
    *,
    body_text: str = "Saved as draft.",
    text_locators: dict[str, MagicMock] | None = None,
    label_locators: dict[str, MagicMock] | None = None,
) -> MagicMock:
    """Build a Page-like mock for the save-draft runner."""
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.inner_text = AsyncMock(return_value=body_text)

    text_locators = text_locators or {}
    label_locators = label_locators or {}

    page.get_by_text = MagicMock(side_effect=lambda label, exact=False: text_locators.get(
        label, _make_locator(count=0)
    ))
    page.get_by_label = MagicMock(side_effect=lambda label, exact=False: label_locators.get(
        label, _make_locator(count=0)
    ))
    return page


def _all_labels_present_page(**overrides) -> MagicMock:
    """Page mock where Fill in / Save as draft buttons and each CBD field are present."""
    text_locators = {
        "Fill in": _make_locator(count=1),
        "Save as draft": _make_locator(count=1),
    }
    label_locators = {
        label: _make_locator(count=1)
        for label in assessor_writeback.CBD_ASSESSOR_FIELD_BINDINGS.values()
    }
    text_locators.update(overrides.pop("text_locators", {}))
    label_locators.update(overrides.pop("label_locators", {}))
    return _make_page(text_locators=text_locators, label_locators=label_locators, **overrides)


# ── plan-side behaviour ──────────────────────────────────────────────────────


def test_save_draft_plan_maps_cbd_fields_and_is_now_live_executable():
    draft = _complete_cbd_draft()

    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))

    assert plan.request.action is assessor_writeback.AssessorWriteAction.SAVE_DRAFT
    assert plan.touches_kaizen is True
    assert plan.is_final_action is False
    # Live execution is now available for an unblocked CBD save-draft plan.
    assert plan.live_execution_available is True
    assert plan.is_executable is True
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
    # Fill-fields is a planning-only action even when nothing is blocked — the
    # live runner only knows how to save-draft.
    assert plan.live_execution_available is False
    assert plan.is_executable is False


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
    assert plan.live_execution_available is False


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
    assert plan.live_execution_available is False


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
    assert plan.live_execution_available is False


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
    assert plan.live_execution_available is False


def test_write_plan_reports_missing_required_fields_before_save():
    draft = AssessorDraft(
        form_type="CBD",
        ticket_uuid="ticket-cbd",
        values={"feedback": "Good performance."},
    )

    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))

    assert "assessor_registration_number" in plan.missing_required
    assert any("required assessor fields" in reason for reason in plan.blocked_reasons)
    assert plan.live_execution_available is False


def test_render_write_plan_contains_safety_boundary_without_draft_values():
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))

    text = assessor_writeback.render_write_plan(plan)

    # Executable plan must advertise the explicit-confirmation gate and the
    # save-draft-only contract while keeping draft values out of the message.
    assert "Save as draft" in text
    assert "explicit confirmation" in text.lower()
    assert "never submit, sign, approve" in text.lower()
    assert "Clear clinical reasoning" not in text


def test_render_write_plan_blocked_plans_show_blocked_safety_text():
    draft = AssessorDraft(
        form_type="CBD",
        ticket_uuid="ticket-cbd",
        values={"feedback": "Good performance."},
    )
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))

    text = assessor_writeback.render_write_plan(plan)

    assert "Blocked:" in text
    assert "cannot be saved live" in text.lower()


# ── live execution ───────────────────────────────────────────────────────────


async def test_execute_write_plan_save_draft_happy_path_clicks_fill_in_fills_and_saves():
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))
    assert plan.is_executable
    page = _all_labels_present_page(body_text="Saved as draft.")

    result = await assessor_writeback.execute_write_plan(
        plan,
        draft,
        page=page,
        ticket_url=_ticket_url_for(draft),
    )

    assert result.status == "success"
    assert result.action is assessor_writeback.AssessorWriteAction.SAVE_DRAFT
    # The runner filled exactly the planned field writes (i.e. the draft
    # values that were populated — ``assessor_other_specify`` is blank here
    # because the job title was "Consultant", not "Other").
    expected_keys = {write.key for write in plan.field_writes}
    assert set(result.filled_fields) == expected_keys
    # Confirms that the runner navigated to the named ticket URL and only
    # clicked Fill in + Save as draft via the text locator path.
    page.goto.assert_awaited_once()
    assert _ticket_url_for(draft) == page.goto.await_args.args[0]
    # Save as draft locator must have been clicked exactly once.
    save_locator = page.get_by_text("Save as draft", exact=True)
    save_locator.click.assert_awaited_once()
    # Fill in must have been clicked exactly once.
    fill_locator = page.get_by_text("Fill in", exact=True)
    fill_locator.click.assert_awaited_once()


@pytest.mark.parametrize(
    "action",
    [
        assessor_writeback.AssessorWriteAction.FILL_FIELDS,
        assessor_writeback.AssessorWriteAction.SUBMIT,
        assessor_writeback.AssessorWriteAction.SIGN,
        assessor_writeback.AssessorWriteAction.APPROVE,
        assessor_writeback.AssessorWriteAction.CANCEL,
    ],
)
async def test_execute_write_plan_refuses_non_save_draft_actions(action):
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft, action))
    page = _all_labels_present_page()

    with pytest.raises(assessor_writeback.AssessorWriteBackUnavailable):
        await assessor_writeback.execute_write_plan(
            plan,
            draft,
            page=page,
            ticket_url=_ticket_url_for(draft),
        )

    page.goto.assert_not_awaited()


async def test_execute_write_plan_refuses_blocked_plans():
    # Missing required field → save-draft plan with blockers.
    draft = AssessorDraft(
        form_type="CBD",
        ticket_uuid="ticket-cbd",
        values={"feedback": "Good performance."},
    )
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))
    page = _all_labels_present_page()

    with pytest.raises(assessor_writeback.AssessorWriteBackUnavailable):
        await assessor_writeback.execute_write_plan(
            plan,
            draft,
            page=page,
            ticket_url=_ticket_url_for(draft),
        )

    page.goto.assert_not_awaited()


async def test_execute_write_plan_refuses_when_draft_hash_no_longer_matches():
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))
    # Mutate the draft after the plan was built — the runner must refuse
    # because the hash binding is now stale.
    draft.values["feedback"] = "Changed wording after planning."
    page = _all_labels_present_page()

    with pytest.raises(assessor_writeback.AssessorWriteBackUnavailable):
        await assessor_writeback.execute_write_plan(
            plan,
            draft,
            page=page,
            ticket_url=_ticket_url_for(draft),
        )

    page.goto.assert_not_awaited()


async def test_execute_write_plan_refuses_when_ticket_url_does_not_contain_uuid():
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))
    page = _all_labels_present_page()

    with pytest.raises(assessor_writeback.AssessorWriteBackUnavailable):
        await assessor_writeback.execute_write_plan(
            plan,
            draft,
            page=page,
            ticket_url="https://kaizenep.com/events/view-section/wrong-uuid",
        )

    page.goto.assert_not_awaited()


async def test_execute_write_plan_returns_failed_when_fill_in_missing():
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))
    page = _all_labels_present_page()
    # Override Fill in locator to be absent.
    fill_in = _make_locator(count=0)
    page.get_by_text = MagicMock(
        side_effect=lambda label, exact=False: {
            "Fill in": fill_in,
            "Save as draft": _make_locator(count=1),
        }.get(label, _make_locator(count=0))
    )

    result = await assessor_writeback.execute_write_plan(
        plan,
        draft,
        page=page,
        ticket_url=_ticket_url_for(draft),
    )

    assert result.status == "failed"
    assert "Fill in" in (result.error or "")
    # Save as draft must not have been clicked when Fill in was missing.
    save_locator = page.get_by_text("Save as draft", exact=True)
    save_locator.click.assert_not_awaited()


async def test_execute_write_plan_returns_failed_when_save_button_missing():
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))
    text_locators = {
        "Fill in": _make_locator(count=1),
        "Save as draft": _make_locator(count=0),
    }
    label_locators = {
        label: _make_locator(count=1)
        for label in assessor_writeback.CBD_ASSESSOR_FIELD_BINDINGS.values()
    }
    page = _make_page(text_locators=text_locators, label_locators=label_locators)

    result = await assessor_writeback.execute_write_plan(
        plan,
        draft,
        page=page,
        ticket_url=_ticket_url_for(draft),
    )

    assert result.status == "failed"
    assert "Save as draft" in (result.error or "")


async def test_execute_write_plan_returns_failed_when_field_label_missing():
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))
    label_locators = {
        label: _make_locator(count=1)
        for label in assessor_writeback.CBD_ASSESSOR_FIELD_BINDINGS.values()
    }
    # Remove Feedback so the runner cannot complete every planned write.
    label_locators["Feedback"] = _make_locator(count=0)
    page = _make_page(
        text_locators={
            "Fill in": _make_locator(count=1),
            "Save as draft": _make_locator(count=1),
        },
        label_locators=label_locators,
    )

    result = await assessor_writeback.execute_write_plan(
        plan,
        draft,
        page=page,
        ticket_url=_ticket_url_for(draft),
    )

    assert result.status == "failed"
    assert "Feedback" in (result.error or "")
    # Save as draft must not be clicked when a planned field could not be filled.
    save_locator = page.get_by_text("Save as draft", exact=True)
    save_locator.click.assert_not_awaited()


async def test_execute_write_plan_returns_failed_when_navigation_raises():
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))
    page = _all_labels_present_page()
    page.goto = AsyncMock(side_effect=RuntimeError("Connection reset"))

    result = await assessor_writeback.execute_write_plan(
        plan,
        draft,
        page=page,
        ticket_url=_ticket_url_for(draft),
    )

    assert result.status == "failed"
    assert "Execution error" in (result.error or "")
    save_locator = page.get_by_text("Save as draft", exact=True)
    save_locator.click.assert_not_awaited()


async def test_execute_write_plan_flags_missing_save_confirmation():
    draft = _complete_cbd_draft()
    plan = assessor_writeback.build_write_plan(draft, _request_for(draft))
    page = _all_labels_present_page(body_text="An unrelated page body.")

    result = await assessor_writeback.execute_write_plan(
        plan,
        draft,
        page=page,
        ticket_url=_ticket_url_for(draft),
    )

    assert result.status == "failed"
    assert "confirm" in (result.error or "").lower()


# ── source-scan boundary on assessor_writeback itself ────────────────────────


def test_assessor_writeback_only_targets_fill_in_and_save_as_draft_controls():
    """The runner must never click final assessor controls.

    The whitelist is intentionally tiny: ``Fill in`` and ``Save as draft``.
    Final assessor actions (Submit, Sign, Approve, Send, Reject, Delete) must
    not appear as click targets in this module. Enum identifiers like
    ``AssessorWriteAction.SUBMIT`` are still allowed in the source — the test
    only refuses click/locator patterns aimed at the forbidden labels.
    """
    source = inspect.getsource(assessor_writeback)
    forbidden_targets = ("Submit", "Sign", "Approve", "Send", "Reject", "Delete")
    forbidden_pattern_templates = (
        "get_by_text('{label}",
        'get_by_text("{label}',
        "get_by_label('{label}",
        'get_by_label("{label}',
        "has-text('{label}",
        'has-text("{label}',
        "text='{label}",
        'text="{label}',
        ":has-text('{label}",
        ':has-text("{label}',
    )
    for label in forbidden_targets:
        for template in forbidden_pattern_templates:
            snippet = template.format(label=label)
            assert snippet not in source, (
                f"assessor_writeback contains forbidden click target: {snippet!r}"
            )
    # The two whitelisted controls must actually be referenced — otherwise
    # the runner cannot perform its allowed save-draft behaviour.
    assert '"Fill in"' in source or "'Fill in'" in source
    assert '"Save as draft"' in source or "'Save as draft'" in source
