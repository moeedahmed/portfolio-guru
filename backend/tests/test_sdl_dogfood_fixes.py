"""Regression coverage for the Self-Directed Learning dogfood fixes.

Covers four product gaps surfaced in Haris' SDL dogfood report:

1. SDL initial form-intent copy is short, action-forward, channel-safe
   (no raw markdown) and does not ask "would you like to start?" after the
   user has already asked to file.
2. Self-Directed Learning is never relabelled "Reflective Practice Log".
3. Date handling is deterministic: a "Date of Event" shown in a reflection
   preview maps into the Kaizen draft.
4. Missing-field recovery: complaining that a saved draft is incomplete
   preserves the filing context and asks for the missing fields instead of
   resetting to generic idle copy.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import bot
from bot import AWAIT_APPROVAL, AWAIT_CASE_INPUT, ConversationHandler
from models import FormDraft
from tests.bot_simulator import BotSimulator


def _last_text(sim: BotSimulator) -> str:
    return sim.get_last_text() or ""


def _filed_sdl_context(sim: BotSimulator, *, reflection: str = "", resource_details: str = "") -> None:
    """Simulate the user-data state left behind after a successful SDL save.

    Mirrors handle_approval_approve's post-success bookkeeping: the live draft
    is cleared but the amend snapshot + last_filing_* markers survive.
    """
    draft = FormDraft(
        form_type="SDL",
        uuid="uuid-sdl",
        fields={
            "reflection_title": "RCEMLearning sepsis module",
            "learning_activity_type": "RCEMlearning Module (Exam & CPD)",
            "resource_details": resource_details,
            "reflection": reflection,
        },
    )
    sim.user_data.clear()
    sim.user_data["last_filing_status"] = "success"
    sim.user_data["last_filing_form_name"] = "Self-directed Learning Reflection"
    sim.user_data["last_amend_draft"] = bot._serialise_draft(draft)
    sim.user_data["last_amend_case_text"] = "self directed learning sepsis module"
    sim.user_data["last_amend_chosen_form"] = "SDL"
    sim.user_data["last_filed_case_text"] = "self directed learning sepsis module"
    sim.user_data["last_filed_form_type"] = "SDL"


# ---------------------------------------------------------------------------
# Issue 4 — missing-field recovery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "you didn't fill the rest of the details for this ticket",
        "you did not complete the other fields",
        "the rest of the details are missing",
        "the rest of the ticket details were not filled",
        "half the form is blank",
        "this draft is incomplete",
        "you left the reflection blank",
    ],
)
def test_incomplete_draft_complaint_detected(text):
    assert bot._is_incomplete_draft_complaint(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "thanks, that's perfect",
        "file another case for me",
        "what forms do you support?",
        "67 year old with chest pain and a raised troponin",
        "patient didn't do well overnight and was escalated to ITU",
    ],
)
def test_non_complaint_text_not_flagged(text):
    assert bot._is_incomplete_draft_complaint(text) is False


@pytest.mark.asyncio
async def test_incomplete_complaint_after_success_recovers_not_resets():
    sim = BotSimulator()
    context = sim._make_context()
    _filed_sdl_context(sim, reflection="", resource_details="")
    update = sim._make_text_update("you didn't fill the rest of the details for this ticket")

    classify = AsyncMock(return_value="chitchat")
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 10, "free"))), \
         patch("bot.classify_intent", new=classify), \
         patch("bot.answer_question", new=AsyncMock(return_value="should not be used")):
        result = await bot.handle_case_input(update, context)

    text = _last_text(sim)
    assert "Ready when you are" not in text
    assert "case notes ready" not in text  # not the idle nudge either
    # Preserves filing context: re-enters amend mode on the saved draft.
    assert result == AWAIT_APPROVAL
    assert context.user_data.get("amend_mode") is True
    assert bot._load_draft(context) is not None


@pytest.mark.asyncio
async def test_incomplete_complaint_lists_missing_reflective_fields():
    sim = BotSimulator()
    context = sim._make_context()
    # resource_details (required) and reflection (optional) are both blank.
    _filed_sdl_context(sim, reflection="", resource_details="")
    update = sim._make_text_update("you didn't fill the rest of the details for this ticket")

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 10, "free"))), \
         patch("bot.classify_intent", new=AsyncMock(return_value="chitchat")), \
         patch("bot.answer_question", new=AsyncMock(return_value="x")):
        await bot.handle_case_input(update, context)

    text = _last_text(sim)
    assert "Reflection" in text
    # Acknowledges the gap rather than inventing content.
    assert "Self-directed Learning Reflection" in text
    # Channel-safe: no raw markdown emphasis characters leak into the copy.
    assert "*" not in text


@pytest.mark.asyncio
async def test_plain_chitchat_after_success_does_not_trigger_recovery():
    """Guard against over-triggering: ordinary thanks should not amend."""
    sim = BotSimulator()
    context = sim._make_context()
    _filed_sdl_context(sim, reflection="Solid reflection here.", resource_details="RCEMLearning")
    update = sim._make_text_update("thanks!")

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 10, "free"))), \
         patch("bot.classify_intent", new=AsyncMock(return_value="chitchat")), \
         patch("bot.answer_question", new=AsyncMock(return_value="x")):
        result = await bot.handle_case_input(update, context)

    assert context.user_data.get("amend_mode") is not True
    assert result != AWAIT_APPROVAL or context.user_data.get("amend_mode") is not True


@pytest.mark.asyncio
async def test_incomplete_complaint_with_active_draft_recovers():
    """Mid-conversation (AWAIT_APPROVAL) complaint reuses the active draft."""
    sim = BotSimulator()
    context = sim._make_context()
    draft = FormDraft(
        form_type="SDL",
        uuid="uuid-sdl",
        fields={
            "reflection_title": "Sepsis module",
            "learning_activity_type": "RCEMlearning Module (Exam & CPD)",
            "resource_details": "",
            "reflection": "",
        },
    )
    sim.user_data["draft_data"] = bot._serialise_draft(draft)
    sim.user_data["chosen_form"] = "SDL"
    sim.user_data["case_text"] = "self directed learning sepsis module"
    update = sim._make_text_update("you didn't fill the rest of the details")

    classify = AsyncMock(return_value="chitchat")
    with patch("bot.classify_intent", new=classify):
        result = await bot.handle_mid_conversation_text(update, context)

    text = _last_text(sim)
    assert "Ready when you are" not in text
    assert "Reflection" in text
    assert result == AWAIT_APPROVAL
    assert context.user_data.get("amend_mode") is True


# ---------------------------------------------------------------------------
# Issue 2 — naming consistency (SDL is not "Reflective Practice Log")
# ---------------------------------------------------------------------------


def test_sdl_public_name_is_self_directed_learning_not_reflective_practice_log():
    from form_display import public_form_name

    sdl_name = public_form_name("SDL")
    assert "Self-directed Learning" in sdl_name
    assert "Reflective Practice Log" not in sdl_name


def test_reflect_log_and_sdl_are_distinct_names():
    from form_display import public_form_name

    assert public_form_name("SDL") != public_form_name("REFLECT_LOG")
    assert public_form_name("REFLECT_LOG") == "Reflective Practice Log"


# ---------------------------------------------------------------------------
# Issue 3 — date handling maps into the Kaizen draft
# ---------------------------------------------------------------------------


def test_reflect_log_date_of_event_maps_into_kaizen():
    from kaizen_form_filer import FORM_FIELD_MAP

    field_map = FORM_FIELD_MAP["REFLECT_LOG"]
    # A "Date of Event" shown in the preview must have a Kaizen target.
    assert "date_of_event" in field_map
    assert field_map["date_of_event"]
    # The encounter/created date is a separate target, so the two never alias.
    assert field_map["date_of_event"] != field_map.get("date_of_encounter")


# ---------------------------------------------------------------------------
# Issue 1 — SDL initial form-intent copy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sdl_first_turn_file_request_asks_for_learning_details_not_generic_answer():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("Can you file a self-directed learning?")

    answer = AsyncMock(return_value="LLM answer should not be used")
    classify = AsyncMock(return_value="question")
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 10, "free"))), \
         patch("bot.answer_question", new=answer), \
         patch("bot.classify_intent", new=classify):
        result = await bot.handle_case_input(update, context)

    text = _last_text(sim)
    assert result == AWAIT_CASE_INPUT
    assert context.user_data["chosen_form"] == "SDL"
    assert context.user_data["awaiting_detail"] is True
    assert "self-directed learning reflection" in text.lower()
    assert "45 forms" not in text
    assert "would you like to start" not in text.lower()
    assert "**" not in text
    answer.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_form", "expected_label"),
    [
        ("Can you file a CBD?", "CBD", "Case-Based Discussion"),
        ("Create a DOPS for me", "DOPS", "Direct Observation of Procedural Skills"),
        ("Log this as an ultrasound case", "US_CASE", "Ultrasound Case Reflection"),
    ],
)
async def test_explicit_form_start_locks_requested_form_generically(
    text,
    expected_form,
    expected_label,
):
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update(text)

    answer = AsyncMock(return_value="LLM answer should not be used")
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 10, "free"))), \
         patch("bot.answer_question", new=answer), \
         patch("bot.classify_intent", new=AsyncMock(return_value="question")):
        result = await bot.handle_case_input(update, context)

    response = _last_text(sim)
    assert result == AWAIT_CASE_INPUT
    assert context.user_data["chosen_form"] == expected_form
    assert context.user_data["awaiting_detail"] is True
    assert expected_label in response
    assert "whatever you have" in response
    assert "appropriate" not in response.lower()
    answer.assert_not_called()


@pytest.mark.parametrize(
    "text",
    [
        "What is a CBD?",
        "Do you support DOPS?",
    ],
)
def test_form_questions_do_not_lock_form_cycle(text):
    assert bot._explicit_form_start_request(text) is None


@pytest.mark.asyncio
async def test_sdl_intent_copy_is_short_action_forward_no_start_question():
    from bot import AWAIT_FORM_CHOICE, _process_case_text

    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("file a self directed learning reflection")

    result = await _process_case_text(
        update.message,
        context,
        update.effective_user.id,
        update.message.text,
        "text",
    )

    assert result == AWAIT_FORM_CHOICE
    text = _last_text(sim)
    assert "Self-directed Learning" in text
    # Action-forward, not a "would you like to start?" re-prompt.
    assert "would you like to start" not in text.lower()
    assert "Reflective Practice Log" not in text
