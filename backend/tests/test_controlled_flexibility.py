"""Telegram integration checks for the controlled-flexibility boundary."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from tests.bot_simulator import BotSimulator


def _draft_payload():
    return {
        "_type": "FORM",
        "form_type": "CBD",
        "fields": {
            "patient_presentation": "Chest pain",
            "clinical_reasoning": "Managed as ACS.",
            "reflection": "I will escalate earlier.",
        },
        "uuid": "uuid-cbd",
    }


@pytest.mark.asyncio
async def test_side_question_keeps_active_draft_and_returns_to_approval():
    from bot import AWAIT_APPROVAL, handle_mid_conversation_text

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(
        {
            "draft_data": _draft_payload(),
            "chosen_form": "CBD",
            "case_text": "Original source-backed case.",
        }
    )
    before = deepcopy(context.user_data)

    with patch("bot.classify_intent", new=AsyncMock(return_value="question_general")), patch(
        "bot.answer_question",
        new=AsyncMock(return_value="A CBD explores reasoning; a mini-CEX observes a clinical encounter."),
    ):
        result = await handle_mid_conversation_text(
            sim._make_text_update("What is the difference between a CBD and a mini-CEX?"),
            context,
        )

    assert result == AWAIT_APPROVAL
    assert context.user_data == before
    assert "A CBD explores reasoning" in sim.get_last_text()
    assert "draft is ready" in sim.get_last_text()


@pytest.mark.asyncio
async def test_classifier_failure_does_not_regenerate_or_leave_active_draft_state():
    from bot import AWAIT_APPROVAL, handle_mid_conversation_text

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(
        {
            "draft_data": _draft_payload(),
            "chosen_form": "CBD",
            "case_text": "Original source-backed case.",
        }
    )
    before = deepcopy(context.user_data)

    with patch("bot.classify_intent", new=AsyncMock(side_effect=TimeoutError)), patch(
        "bot._regenerate_active_draft_with_feedback",
        new=AsyncMock(),
    ) as regenerate:
        result = await handle_mid_conversation_text(
            sim._make_text_update("Please do something with that"),
            context,
        )

    assert result == AWAIT_APPROVAL
    assert context.user_data == before
    regenerate.assert_not_awaited()
    assert "didn't change your draft" in sim.get_last_text().lower()


@pytest.mark.asyncio
async def test_template_review_classifier_failure_does_not_refresh_pending_draft():
    from bot import AWAIT_TEMPLATE_REVIEW, handle_template_review_text

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(
        {
            "pending_draft_data": _draft_payload(),
            "chosen_form": "CBD",
            "case_text": "Original source-backed case.",
        }
    )
    before = deepcopy(context.user_data)

    with patch("bot.classify_intent", new=AsyncMock(side_effect=TimeoutError)), patch(
        "bot._accumulate_and_refresh",
        new=AsyncMock(),
    ) as refresh:
        result = await handle_template_review_text(
            sim._make_text_update("Please do something with that"),
            context,
        )

    assert result == AWAIT_TEMPLATE_REVIEW
    assert context.user_data == before
    refresh.assert_not_awaited()
    assert "didn't change your case" in sim.get_last_text().lower()
    assert ("✅ Show me the draft", "ACTION|continue_thin") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_unclear_text_on_explicit_form_choice_restores_form_choice_buttons():
    from bot import AWAIT_FORM_CHOICE, handle_mid_conversation_text

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(
        {
            "case_text": "Original source-backed case asking for a CBD.",
            "chosen_form": "CBD",
            "explicit_form_choice": "CBD",
        }
    )
    before = deepcopy(context.user_data)

    with patch("bot.classify_intent", new=AsyncMock(side_effect=TimeoutError)):
        result = await handle_mid_conversation_text(
            sim._make_text_update("Please do something with that"),
            context,
        )

    assert result == AWAIT_FORM_CHOICE
    assert context.user_data == before
    assert ("✅ Draft Case-Based Discussion", "FORM|CBD") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_ambiguous_cancel_language_requires_button_confirmation():
    from bot import AWAIT_APPROVAL, handle_mid_conversation_text

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(
        {
            "draft_data": _draft_payload(),
            "chosen_form": "CBD",
            "case_text": "Original source-backed case.",
        }
    )
    before = deepcopy(context.user_data)

    with patch("bot.classify_intent", new=AsyncMock(return_value="new_case")):
        result = await handle_mid_conversation_text(
            sim._make_text_update("Forget it"),
            context,
        )

    assert result == AWAIT_APPROVAL
    assert context.user_data == before
    assert "haven't cancelled" in sim.get_last_text().lower()
    assert ("❌ Cancel", "CANCEL|draft") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_explicit_new_case_with_open_draft_uses_choice_gate():
    from bot import AWAIT_TEMPLATE_REVIEW, handle_mid_conversation_text

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(
        {
            "draft_data": _draft_payload(),
            "chosen_form": "CBD",
            "case_text": "Original source-backed case.",
        }
    )

    with patch("bot.classify_intent", new=AsyncMock(return_value="new_case")):
        result = await handle_mid_conversation_text(
            sim._make_text_update("Start a new case"),
            context,
        )

    assert result == AWAIT_TEMPLATE_REVIEW
    assert context.user_data["draft_data"] == _draft_payload()
    assert "current draft is still open" in sim.get_last_text().lower()
    assert ("📋 Start new case", "CASE|new") in sim.get_last_buttons()
    assert ("✏️ Add to current draft", "CASE|improve") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_mixed_case_detail_and_question_keeps_step_and_context():
    from bot import AWAIT_FORM_CHOICE, handle_mid_conversation_text

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(
        {
            "case_text": "A patient presented in shock and I led the initial assessment.",
            "case_input_source": "text",
        }
    )
    mixed = "His BP was 80/40 and I escalated early — does this count for SLO3?"

    with patch("bot.classify_intent", new=AsyncMock(return_value="question_about_case")), patch(
        "bot.answer_question",
        new=AsyncMock(return_value="That can support SLO3 when you describe the leadership evidence."),
    ):
        result = await handle_mid_conversation_text(sim._make_text_update(mixed), context)

    assert result == AWAIT_FORM_CHOICE
    assert "His BP was 80/40 and I escalated early" in context.user_data["case_text"]
    assert "does this count for SLO3" not in context.user_data["case_text"]
    assert "support SLO3" in sim.get_last_text()
    assert "case is still in progress" in sim.get_last_text()


@pytest.mark.asyncio
async def test_side_question_while_document_choice_is_pending_does_not_pollute_context():
    from bot import AWAIT_DOC_INTENT, handle_mid_conversation_text

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(
        {
            "_pending_doc": {"kind": "document"},
            "_pending_doc_context": "Original source-backed context.",
            "case_text": "Original source-backed case.",
        }
    )
    before = deepcopy(context.user_data)

    with patch(
        "bot.answer_question",
        new=AsyncMock(return_value="A CBD focuses on reasoning; DOPS focuses on a procedure."),
    ):
        result = await handle_mid_conversation_text(
            sim._make_text_update("What is the difference between CBD and DOPS?"),
            context,
        )

    assert result == AWAIT_DOC_INTENT
    assert context.user_data == before
    assert "A CBD focuses on reasoning" in sim.get_last_text()
    assert "document choice is still waiting" in sim.get_last_text().lower()
