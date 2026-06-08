from unittest.mock import AsyncMock, patch

import pytest

import bot
from tests.bot_simulator import BotSimulator


@pytest.mark.asyncio
async def test_disconnected_email_text_starts_kaizen_password_step():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("doctor@example.com")

    with patch("bot.has_credentials", return_value=False):
        result = await bot.handle_case_input(update, context)

    assert result == bot.AWAIT_PASSWORD
    assert context.user_data["setup_username"] == "doctor@example.com"
    assert context.user_data["_setup_state_hint"] == "password"
    assert "Kaizen password" in sim.get_last_text()


@pytest.mark.asyncio
async def test_disconnected_reconnect_sentence_extracts_email():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("Please reconnect my Kaizen email doctor@example.com")

    with patch("bot.has_credentials", return_value=False):
        result = await bot.handle_case_input(update, context)

    assert result == bot.AWAIT_PASSWORD
    assert context.user_data["setup_username"] == "doctor@example.com"


@pytest.mark.asyncio
async def test_disconnected_clinical_case_goes_straight_to_username_step():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update(
        "Adult ED resus case: assessed chest pain, escalated to cardiology, "
        "documented ECG findings and reflected on earlier senior review."
    )

    process_case = AsyncMock()
    with patch("bot.has_credentials", return_value=False), \
         patch("bot._process_case_text", new=process_case):
        result = await bot.handle_case_input(update, context)

    assert result == bot.AWAIT_USERNAME
    process_case.assert_not_awaited()
    assert "Before I can save drafts to Kaizen" in sim.get_last_text()
    assert "Send your Kaizen username or email" in sim.get_last_text()
    # The forced connection prompt offers no Cancel: entering a username/email
    # is the only usable path for a disconnected user, so a Cancel button would
    # just loop back to the same state.
    assert sim.get_last_buttons() == []


@pytest.mark.asyncio
async def test_connected_case_text_still_uses_case_flow(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["gathering_mode"] = False
    update = sim._make_text_update(
        "Adult ED resus case: assessed chest pain, escalated to cardiology, "
        "documented ECG findings and reflected on earlier senior review."
    )

    process_case = AsyncMock(return_value=bot.AWAIT_FORM_CHOICE)
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot._process_case_text", new=process_case):
        result = await bot.handle_case_input(update, context)

    assert result == bot.AWAIT_FORM_CHOICE
    process_case.assert_awaited_once()
