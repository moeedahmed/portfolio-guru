from unittest.mock import AsyncMock, patch

import pytest

import bot
from bot import (
    AWAIT_CASE_INPUT,
    AWAIT_FORM_CHOICE,
    AWAIT_GATHERING,
    AWAIT_TEMPLATE_REVIEW,
    gather_done_callback,
    handle_callback,
    handle_case_input,
    handle_gathering_input,
)
from tests.bot_simulator import BotSimulator


_FIRST_CASE = (
    "A 62M patient presented to ED resus with chest pain and was diagnosed with STEMI. "
    "I assessed him with the consultant, arranged cath lab referral, and learned about "
    "early escalation in high-risk ACS cases."
)


def _make_voice_update(sim: BotSimulator):
    update = sim._make_text_update("")
    message = update.message
    message.text = None
    voice = AsyncMock()
    voice.file_name = "voice.ogg"
    voice.mime_type = "audio/ogg"
    voice_file = AsyncMock()
    voice.get_file = AsyncMock(return_value=voice_file)
    voice_file.download_to_drive = AsyncMock()
    message.voice = voice
    message.audio = None
    message.document = None
    return update


@pytest.mark.asyncio
async def test_gathering_mode_starts_collection_instead_of_recommending(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update(_FIRST_CASE)

    process_case = AsyncMock(return_value=AWAIT_FORM_CHOICE)
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot._process_case_text", new=process_case):
        result = await handle_case_input(update, context)

    assert result == AWAIT_GATHERING
    assert process_case.await_count == 0
    assert context.user_data["gathering_case"]["parts"][0]["text"] == _FIRST_CASE
    assert any("Captured" in message for _, message, _ in sim.messages_sent)


@pytest.mark.asyncio
async def test_gathering_mode_combines_parts_when_user_says_done(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    bot._append_gathering_case(context, "Reflection: I need to escalate ACS cases earlier.", "text")
    update = sim._make_text_update("done")

    process_case = AsyncMock(return_value=AWAIT_FORM_CHOICE)
    with patch("bot._process_case_text", new=process_case):
        result = await handle_gathering_input(update, context)

    assert result == AWAIT_FORM_CHOICE
    process_case.assert_awaited_once()
    combined_text = process_case.await_args.args[3]
    assert "62M patient presented" in combined_text
    assert "escalate ACS cases earlier" in combined_text
    assert "gathering_case" not in context.user_data


@pytest.mark.asyncio
async def test_gathering_mode_answers_side_question_without_adding_case_detail(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    original_parts = list(context.user_data["gathering_case"]["parts"])
    update = sim._make_text_update("How does this work?")

    result = await handle_gathering_input(update, context)

    assert result == AWAIT_GATHERING
    assert context.user_data["gathering_case"]["parts"] == original_parts
    answered = sim.messages_sent[-1][1]
    assert "collect a case across several messages" in answered
    assert "Nothing goes to Kaizen until you approve it" in answered
    assert "Back to your case" in answered  # continuation keeps user in the filling flow
    assert sim.get_last_buttons() == []
    lowered = answered.lower()
    assert "dogfood" not in lowered
    assert "vnext" not in lowered
    assert "test bot" not in lowered


@pytest.mark.asyncio
async def test_gathering_mode_portfolio_question_uses_grounded_answer(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    original_parts = list(context.user_data["gathering_case"]["parts"])
    update = sim._make_text_update("Which form would this map to?")

    grounded = AsyncMock(return_value="This fits a CBD based on the clinical reasoning you described.")
    with patch("bot.answer_question", new=grounded):
        result = await handle_gathering_input(update, context)

    assert result == AWAIT_GATHERING
    grounded.assert_awaited_once()
    assert context.user_data["gathering_case"]["parts"] == original_parts
    answered = sim.messages_sent[-1][1]
    assert "This fits a CBD" in answered
    assert "Back to your case" in answered  # continuation returns the user to filling
    assert sim.get_last_buttons() == []


@pytest.mark.asyncio
async def test_gathering_mode_is_default_without_user_toggle(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    assert bot._gathering_enabled(context) is True


@pytest.mark.asyncio
async def test_user_can_opt_out_of_gathering_mode(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["gathering_mode"] = False
    assert bot._gathering_enabled(context) is False


@pytest.mark.asyncio
async def test_env_var_can_disable_gathering_mode_globally(monkeypatch):
    monkeypatch.setenv("PG_GATHERING_MODE", "off")
    sim = BotSimulator()
    context = sim._make_context()
    assert bot._gathering_enabled(context) is False


@pytest.mark.asyncio
async def test_gathering_reply_offers_done_button(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update(_FIRST_CASE)

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot._process_case_text", new=AsyncMock(return_value=AWAIT_FORM_CHOICE)):
        await handle_case_input(update, context)

    assert sim.messages_sent[-1][1] == "📥 Captured. Add anything else before I draft this?"
    assert sim.get_last_buttons() == [
        ("✅ Draft now", "GATHER|done"),
        ("❌ Cancel", "ACTION|cancel"),
    ]


@pytest.mark.asyncio
async def test_capability_question_does_not_start_gathering_or_show_draft_button(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("what can you do?")

    grounded = AsyncMock(return_value="I can draft RCEM portfolio evidence from anonymised notes.")
    process_case = AsyncMock(return_value=AWAIT_FORM_CHOICE)
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot.answer_question", new=grounded), \
         patch("bot._process_case_text", new=process_case):
        result = await handle_case_input(update, context)

    assert result == bot.ConversationHandler.END
    grounded.assert_awaited_once()
    process_case.assert_not_awaited()
    assert "gathering_case" not in context.user_data
    assert sim.get_last_buttons() == []
    assert "I can draft RCEM portfolio evidence" in sim.get_last_text()


@pytest.mark.asyncio
async def test_form_choice_question_does_not_start_gathering_or_show_draft_button(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("What form is best for doing procedural sedation?")

    process_case = AsyncMock(return_value=AWAIT_FORM_CHOICE)
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot._process_case_text", new=process_case):
        result = await handle_case_input(update, context)

    assert result == bot.ConversationHandler.END
    process_case.assert_not_awaited()
    assert "gathering_case" not in context.user_data
    assert sim.get_last_buttons() == []
    answer = sim.get_last_text()
    assert "Direct Observation of Procedural Skills" in answer
    assert "Procedural Log" in answer
    assert "I support 45 RCEM forms" not in answer


@pytest.mark.asyncio
async def test_gathering_cancel_button_clears_case(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    context.user_data["gathering_msg_id"] = 123
    context.user_data["gathering_chat_id"] = 456
    update = sim._make_callback_update("ACTION|cancel")

    with patch("bot.has_credentials", return_value=True):
        result = await handle_callback(update, context)

    assert result == bot.ConversationHandler.END
    assert "gathering_case" not in context.user_data
    assert "gathering_msg_id" not in context.user_data
    assert "gathering_chat_id" not in context.user_data
    update.callback_query.answer.assert_awaited()
    assert sim.get_last_text().startswith("↩️ Cancelled.")


@pytest.mark.asyncio
async def test_gather_done_callback_finishes_case(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    update = sim._make_callback_update("GATHER|done")

    process_case = AsyncMock(return_value=AWAIT_FORM_CHOICE)
    with patch("bot._process_case_text", new=process_case):
        result = await gather_done_callback(update, context)

    assert result == AWAIT_FORM_CHOICE
    process_case.assert_awaited_once()
    assert "gathering_case" not in context.user_data
    update.callback_query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_voice_transcript_fragment_asks_for_grounding_before_recommendation(monkeypatch):
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("done")

    with patch("bot.recommend_form_types", new=AsyncMock()) as recommend:
        result = await bot._process_case_text(
            update.message,
            context,
            update.effective_user.id,
            "test case adult sedation chest pain maybe procedural log",
            "voice",
        )

    assert result == AWAIT_CASE_INPUT
    recommend.assert_not_awaited()
    assert context.user_data["awaiting_source_detail"] is True
    assert "voice transcript" in sim.get_last_text()
    assert "isn't enough grounded detail to draft safely" in sim.get_last_text()


@pytest.mark.asyncio
async def test_voice_transcript_explicit_procedural_log_fragment_does_not_open_draft_button(monkeypatch):
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("done")

    with patch("bot.recommend_form_types", new=AsyncMock()) as recommend:
        result = await bot._process_case_text(
            update.message,
            context,
            update.effective_user.id,
            "please make this a procedural log adult sedation chest pain",
            "voice",
        )

    assert result == AWAIT_CASE_INPUT
    recommend.assert_not_awaited()
    assert context.user_data.get("chosen_form") is None
    assert ("✅ Draft Procedural Log", "FORM|PROC_LOG") not in sim.get_last_buttons()
    assert "isn't enough grounded detail to draft safely" in sim.get_last_text()


@pytest.mark.asyncio
async def test_grounded_voice_transcript_can_reach_recommendations(monkeypatch):
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("done")
    case_text = (
        "A 70-year-old patient attended ED with a shoulder dislocation. "
        "I assessed analgesia and neurovascular status, performed reduction under procedural sedation "
        "with senior support, monitored observations, and reflected that next time I should use a "
        "clearer pre-sedation checklist."
    )
    from extractor import FORM_UUIDS
    from models import FormTypeRecommendation

    recommendations = [
        FormTypeRecommendation(
            form_type="PROC_LOG",
            rationale="Grounded procedural sedation case.",
            uuid=FORM_UUIDS.get("PROC_LOG"),
        )
    ]
    with patch("bot.recommend_form_types", new=AsyncMock(return_value=recommendations)), \
         patch("bot.get_training_level", return_value="ST5"), \
         patch("bot.get_curriculum", return_value="2025"):
        result = await bot._process_case_text(
            update.message,
            context,
            update.effective_user.id,
            case_text,
            "voice",
        )

    assert result == AWAIT_FORM_CHOICE
    assert context.user_data.get("awaiting_source_detail") is None
    assert ("✅ Use best fit: Procedural Log", "FORM|best") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_stale_gather_done_callback_keeps_current_case(monkeypatch):
    """Old Draft now buttons must not finish the latest gathering case."""
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    context.user_data["gathering_msg_id"] = 999
    context.user_data["gathering_chat_id"] = sim.user_id
    update = sim._make_callback_update("GATHER|done")

    process_case = AsyncMock(return_value=AWAIT_FORM_CHOICE)
    with patch("bot._process_case_text", new=process_case):
        result = await gather_done_callback(update, context)

    assert result == AWAIT_GATHERING
    process_case.assert_not_awaited()
    assert "gathering_case" in context.user_data
    assert context.user_data["gathering_msg_id"] == 999
    update.callback_query.answer.assert_awaited()
    assert "earlier Draft now button" in sim.get_last_text()


@pytest.mark.asyncio
async def test_stale_gather_done_callback_does_not_draft(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["gathering_msg_id"] = 123
    context.user_data["gathering_chat_id"] = 456
    update = sim._make_callback_update("GATHER|done")

    process_case = AsyncMock(return_value=AWAIT_FORM_CHOICE)
    with patch("bot._process_case_text", new=process_case):
        result = await gather_done_callback(update, context)

    assert result == bot.ConversationHandler.END
    process_case.assert_not_awaited()
    assert "gathering_case" not in context.user_data
    assert "gathering_msg_id" not in context.user_data
    assert "gathering_chat_id" not in context.user_data
    update.callback_query.answer.assert_awaited()
    assert "do not have a case captured" in sim.get_last_text()


@pytest.mark.asyncio
async def test_second_text_addition_keeps_both_buttons(monkeypatch):
    """Regression: adding text during AWAIT_GATHERING must keep Draft Now + Cancel."""
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")

    update = sim._make_text_update("I also performed a 12-lead ECG and arranged urgent cardiology review.")

    result = await handle_gathering_input(update, context)

    assert result == AWAIT_GATHERING
    assert "Captured" in sim.get_last_text()
    assert sim.get_last_buttons() == [
        ("✅ Draft now", "GATHER|done"),
        ("❌ Cancel", "ACTION|cancel"),
    ]


@pytest.mark.asyncio
async def test_second_text_addition_disarms_previous_gathering_prompt(monkeypatch):
    """A new capture prompt must retire the previous prompt's inline keyboard."""
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    context.user_data["gathering_msg_id"] = 123
    context.user_data["gathering_chat_id"] = sim.user_id

    update = sim._make_text_update("I also arranged urgent cardiology review.")
    result = await handle_gathering_input(update, context)

    assert result == AWAIT_GATHERING
    context.bot.edit_message_text.assert_awaited()
    assert context.bot.edit_message_text.await_args.kwargs["message_id"] == 123
    assert context.bot.edit_message_text.await_args.kwargs["reply_markup"] is None
    assert context.user_data["gathering_msg_id"] != 123
    assert sim.get_last_buttons() == [
        ("✅ Draft now", "GATHER|done"),
        ("❌ Cancel", "ACTION|cancel"),
    ]


@pytest.mark.asyncio
async def test_voice_addition_disarms_previous_gathering_prompt(monkeypatch):
    """A second voice note must not leave the previous Draft now button visible."""
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "voice")
    bot._track_gathering_prompt(context, 123, sim.user_id)

    update = _make_voice_update(sim)

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.consent.has_current_consent", new=AsyncMock(return_value=True)), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot.transcribe_voice", new=AsyncMock(return_value="Reflection: I should escalate earlier.")):
        result = await handle_case_input(update, context)

    assert result == AWAIT_GATHERING
    context.bot.edit_message_text.assert_awaited()
    assert context.bot.edit_message_text.await_args.kwargs["message_id"] == 123
    assert context.bot.edit_message_text.await_args.kwargs["reply_markup"] is None
    assert context.user_data["gathering_msg_id"] != 123
    assert context.user_data["gathering_prompt_refs"] == [{
        "message_id": context.user_data["gathering_msg_id"],
        "chat_id": sim.user_id,
    }]
    assert sim.get_last_buttons() == [
        ("✅ Draft now", "GATHER|done"),
        ("❌ Cancel", "ACTION|cancel"),
    ]


@pytest.mark.asyncio
async def test_gather_done_disarms_all_tracked_prompts(monkeypatch):
    """Finishing a gathered case must strip any duplicate prompt buttons."""
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "voice")
    bot._track_gathering_prompt(context, 111, sim.user_id)
    bot._track_gathering_prompt(context, 222, sim.user_id)
    update = sim._make_callback_update("GATHER|done")
    update.callback_query.message.message_id = 222
    update.callback_query.message.chat_id = sim.user_id

    process_case = AsyncMock(return_value=AWAIT_FORM_CHOICE)
    with patch("bot._process_case_text", new=process_case):
        result = await gather_done_callback(update, context)

    assert result == AWAIT_FORM_CHOICE
    process_case.assert_awaited_once()
    context.bot.edit_message_text.assert_awaited()
    assert context.bot.edit_message_text.await_args.kwargs["message_id"] == 222
    context.bot.edit_message_reply_markup.assert_awaited()
    assert context.bot.edit_message_reply_markup.await_args.kwargs["message_id"] == 111
    assert "gathering_prompt_refs" not in context.user_data


@pytest.mark.asyncio
async def test_gathering_prompt_idempotent_across_repeated_additions(monkeypatch):
    """Every new text addition must produce the same two-button surface — idempotency."""
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")

    additions = [
        "I performed a 12-lead ECG and arranged urgent cardiology review.",
        "The patient was haemodynamically stable throughout.",
        "Reflection: I should escalate high-risk ACS cases earlier in future.",
    ]
    expected_buttons = [
        ("✅ Draft now", "GATHER|done"),
        ("❌ Cancel", "ACTION|cancel"),
    ]
    for text in additions:
        sim.clear_messages()
        update = sim._make_text_update(text)
        result = await handle_gathering_input(update, context)
        assert result == AWAIT_GATHERING, f"Expected AWAIT_GATHERING after: {text!r}"
        assert sim.get_last_buttons() == expected_buttons, (
            f"Both buttons missing after addition: {text!r}"
        )


@pytest.mark.asyncio
async def test_explicit_new_case_during_thin_detail_state_prompts_choice(monkeypatch):
    """An unresolved thin-detail draft must not silently absorb a new case."""
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["chosen_form"] = "REFLECT_LOG"
    context.user_data["awaiting_detail"] = True
    context.user_data["case_text"] = "Initial reflective practice log notes."

    update = sim._make_text_update(
        "This is a new case: 70M patient presented to ED resus with sepsis and hypotension."
    )

    analyse = AsyncMock()
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.consent.has_current_consent", new=AsyncMock(return_value=True)), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_case_input(update, context)

    assert result == AWAIT_TEMPLATE_REVIEW
    analyse.assert_not_awaited()
    assert context.user_data["pending_new_case_text"].startswith("This is a new case")
    assert "current draft is still open" in sim.get_last_text()
    assert sim.get_last_buttons() == [
        ("📋 Start new case", "CASE|new"),
        ("✏️ Add to current draft", "CASE|improve"),
        ("❌ Cancel current draft", "ACTION|cancel"),
    ]
