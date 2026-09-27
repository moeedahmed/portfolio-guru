"""The passwordless Kaizen connection inside the bot: setup, saving, signing in again.

Offline only: the sign-in service, Kaizen and the filer are all stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

import bot
from tests.bot_simulator import BotSimulator


@pytest.fixture(autouse=True)
def _isolated_flow_storage(monkeypatch, tmp_path):
    from tests.helpers import isolate_bot_storage

    isolate_bot_storage(monkeypatch, tmp_path)


@pytest.fixture
def offered(monkeypatch):
    monkeypatch.setenv("PG_ENABLE_PASSWORDLESS_CONNECT", "1")
    monkeypatch.setenv("PG_PASSWORDLESS_ALLOWLIST", "*")


@pytest.fixture
def passwordless_user(monkeypatch):
    """A user connected passwordless: no stored login, profile says so."""
    import profile_store

    monkeypatch.setattr(bot, "has_credentials", lambda uid: False)
    monkeypatch.setattr(bot, "get_credentials", lambda uid: None)
    monkeypatch.setattr(profile_store, "get_kaizen_connection", lambda uid: "passwordless")


def _cbd_draft():
    return {
        "_type": "FORM",
        "form_type": "CBD",
        "uuid": "uuid-cbd",
        "fields": {
            "date_of_encounter": "2026-03-17",
            "clinical_setting": "ED",
            "patient_presentation": "Chest pain",
            "stage_of_training": "Higher/ST4-ST6",
            "trainee_role": "Led the assessment",
            "clinical_reasoning": "Escalated early",
            "reflection": "I would escalate sooner",
            "level_of_supervision": "Indirect",
        },
    }


# --- Setup ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_setup_offers_passwordless_only_when_switched_on(monkeypatch):
    sim = BotSimulator()
    monkeypatch.setattr(bot, "has_credentials", lambda uid: False)

    await bot.setup_start(sim._make_text_update("/setup"), sim._make_context())
    assert ("🔒 Sign in without password", "ACTION|connect_passwordless") not in sim.get_last_buttons()

    monkeypatch.setenv("PG_ENABLE_PASSWORDLESS_CONNECT", "1")
    monkeypatch.setenv("PG_PASSWORDLESS_ALLOWLIST", str(sim.user_id))
    sim.clear_messages()
    await bot.setup_start(sim._make_text_update("/setup"), sim._make_context())
    assert ("🔒 Sign in without password", "ACTION|connect_passwordless") in sim.get_last_buttons()
    assert "about a day" in sim.get_last_text() and "encrypted" in sim.get_last_text()


@pytest.mark.asyncio
async def test_choosing_passwordless_sends_a_one_time_sign_in_link(offered, monkeypatch):
    sim = BotSimulator()
    link = SimpleNamespace(url="https://connect.emgurus.com/handoff#token", expires_in_seconds=600)
    monkeypatch.setattr(bot, "_create_passwordless_link", AsyncMock(return_value=link))

    state = await bot.passwordless_setup_start(
        sim._make_callback_update("ACTION|connect_passwordless"), sim._make_context()
    )

    assert state == bot.AWAIT_PASSWORDLESS
    markup = sim.messages_sent[-1][2]
    urls = [b.url for row in markup.inline_keyboard for b in row if b.url]
    assert urls == ["https://connect.emgurus.com/handoff#token"]
    assert ("✅ I've signed in", "ACTION|passwordless_done") in sim.get_last_buttons()
    assert "never stored" in sim.get_last_text()


@pytest.mark.asyncio
async def test_sign_in_page_down_falls_back_to_the_password_route(offered, monkeypatch):
    sim = BotSimulator()
    monkeypatch.setattr(bot, "_create_passwordless_link", AsyncMock(return_value=None))

    state = await bot.passwordless_setup_start(
        sim._make_callback_update("ACTION|connect_passwordless"), sim._make_context()
    )

    assert state == bot.AWAIT_USERNAME
    assert "isn't available right now" in sim.get_last_text()


@pytest.mark.asyncio
async def test_signed_in_is_only_believed_after_kaizen_opens(offered, monkeypatch):
    sim = BotSimulator()
    mark = MagicMock()
    finish = AsyncMock(return_value=ConversationHandler.END)
    monkeypatch.setattr(bot.kaizen_connection, "mark_passwordless", mark)
    monkeypatch.setattr(bot, "_finish_setup_after_connect", finish)
    monkeypatch.setattr(bot, "has_credentials", lambda uid: False)

    monkeypatch.setattr(bot, "_probe_kept_kaizen_session", AsyncMock(return_value=False))
    state = await bot.passwordless_setup_done(
        sim._make_callback_update("ACTION|passwordless_done"), sim._make_context()
    )
    assert state == bot.AWAIT_PASSWORDLESS
    assert "can't see a Kaizen sign-in yet" in sim.get_last_text()
    mark.assert_not_called()
    finish.assert_not_awaited()

    monkeypatch.setattr(bot, "_probe_kept_kaizen_session", AsyncMock(return_value="hst"))
    context = sim._make_context()
    state = await bot.passwordless_setup_done(
        sim._make_callback_update("ACTION|passwordless_done"), context
    )
    assert state == ConversationHandler.END
    mark.assert_called_once_with(sim.user_id)
    finish.assert_awaited_once()
    assert finish.await_args.args[2] == "hst"


@pytest.mark.asyncio
async def test_switching_from_a_password_clears_the_old_accounts_local_data(offered, monkeypatch):
    sim = BotSimulator()
    cleared = AsyncMock(return_value={})
    monkeypatch.setattr(bot, "_clear_local_portfolio_account_data", cleared)
    monkeypatch.setattr(bot.kaizen_connection, "mark_passwordless", MagicMock())
    monkeypatch.setattr(bot, "_finish_setup_after_connect", AsyncMock(return_value=ConversationHandler.END))
    monkeypatch.setattr(bot, "has_credentials", lambda uid: True)
    monkeypatch.setattr(bot, "_probe_kept_kaizen_session", AsyncMock(return_value="hst"))

    await bot.passwordless_setup_done(sim._make_callback_update("ACTION|passwordless_done"), sim._make_context())

    cleared.assert_awaited_once_with(sim.user_id, reason="kaizen_account_switch")


# --- Saving --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_passwordless_user_saves_with_the_kept_session_not_a_password(passwordless_user):
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["draft_data"] = _cbd_draft()
    route = AsyncMock(return_value={"status": "success", "filled": ["reflection"], "skipped": [], "method": "deterministic"})

    with patch("bot.route_filing", new=route):
        await bot.handle_approval_approve(sim._make_callback_update("APPROVE|draft"), context)

    route.assert_awaited()
    assert route.await_args.kwargs["credentials"] == {"username": "", "password": ""}


@pytest.mark.asyncio
async def test_signed_out_passwordless_user_is_asked_to_sign_in_again_and_keeps_the_draft(passwordless_user):
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["draft_data"] = _cbd_draft()
    alert = AsyncMock()

    with patch("bot.route_filing", new_callable=AsyncMock, return_value={
        "status": "failed", "filled": [], "skipped": [], "method": "deterministic",
        "error": "Could not log in to Kaizen with your saved credentials. Use /settings to reconnect.",
    }), patch("bot._alert_filing_failure", new=alert), \
            patch("bot.compose_filing_recovery_copy", new=AsyncMock(return_value="ignored")):
        state = await bot.handle_approval_approve(sim._make_callback_update("APPROVE|draft"), context)

    assert state == bot.AWAIT_APPROVAL
    assert "Kaizen has signed you out" in sim.get_last_text()
    assert ("🔒 Sign in again", "ACTION|pwl_reconnect") in sim.get_last_buttons()
    assert ("🔗 Reconnect Kaizen", "ACTION|setup") not in sim.get_last_buttons()
    assert context.user_data.get("draft_data") is not None
    # Expected daily for passwordless users: never page the operator.
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_signing_in_again_saves_the_waiting_draft(passwordless_user, monkeypatch):
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["draft_data"] = _cbd_draft()
    save = AsyncMock(return_value=bot.AWAIT_APPROVAL)
    monkeypatch.setattr(bot, "handle_approval_approve", save)
    monkeypatch.setattr(bot, "_probe_kept_kaizen_session", AsyncMock(return_value="hst"))

    await bot.passwordless_reconnected(sim._make_callback_update("ACTION|pwl_reconnected"), context)

    save.assert_awaited_once()
    assert context.user_data["retry_filing_requested"] is True


@pytest.mark.asyncio
async def test_signing_in_again_without_a_session_does_not_try_to_save(passwordless_user, monkeypatch):
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["draft_data"] = _cbd_draft()
    save = AsyncMock()
    monkeypatch.setattr(bot, "handle_approval_approve", save)
    monkeypatch.setattr(bot, "_probe_kept_kaizen_session", AsyncMock(return_value=False))

    await bot.passwordless_reconnected(sim._make_callback_update("ACTION|pwl_reconnected"), context)

    save.assert_not_awaited()
    assert "can't see a Kaizen sign-in yet" in sim.get_last_text()


# --- Connected everywhere, password-only features explained --------------------

def test_passwordless_user_counts_as_connected(passwordless_user):
    assert bot._kaizen_connected(12345) is True
    assert bot._is_passwordless_user(12345) is True


@pytest.mark.asyncio
async def test_unsigned_explains_it_needs_a_password_connection(passwordless_user):
    sim = BotSimulator()

    await bot.unsigned_command(sim._make_text_update("/unsigned"), sim._make_context())

    assert "needs a username-and-password connection" in sim.get_last_text()


# --- One shared first step -------------------------------------------------------

_CHOICE = ("🔒 Sign in without password", "ACTION|connect_passwordless")


@pytest.mark.asyncio
async def test_reset_asks_first_and_wipes_nothing_yet(offered, monkeypatch):
    """/reset only asks; the wipe and the connect step come from the Delete button."""
    sim = BotSimulator()
    wipe = AsyncMock()
    monkeypatch.setattr(bot, "_perform_reset", wipe)

    await bot.reset_data(sim._make_text_update("/reset"), sim._make_context())

    wipe.assert_not_awaited()
    assert sim.get_last_text() == bot._RESET_CONFIRM_TEXT
    assert ("🗑️ Delete data", "CONFIRM|reset") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_reset_confirm_button_offers_the_same_connect_choice(offered, monkeypatch):
    sim = BotSimulator()
    monkeypatch.setattr(bot, "_perform_reset", AsyncMock())
    monkeypatch.setattr(bot, "_safe_edit_text", AsyncMock(), raising=False)

    state = await bot.handle_reset_confirm(
        sim._make_callback_update("CONFIRM|reset"), sim._make_context()
    )

    assert state == bot.AWAIT_USERNAME
    assert _CHOICE in sim.get_last_buttons()
    assert sim.get_last_text().endswith(bot._CONNECT_CHOICE_TEXT)


@pytest.mark.asyncio
async def test_reset_ends_on_exactly_what_settings_connect_shows(offered, monkeypatch):
    """After Delete, the doctor sees the same message and buttons as Settings > Connect Kaizen."""
    monkeypatch.setattr(bot, "_perform_reset", AsyncMock())
    monkeypatch.setattr(bot, "_safe_edit_text", AsyncMock(), raising=False)
    reset_sim = BotSimulator()
    await bot.handle_reset_confirm(
        reset_sim._make_callback_update("CONFIRM|reset"), reset_sim._make_context()
    )
    settings_sim = BotSimulator()
    await bot.setup_start(
        settings_sim._make_callback_update("ACTION|setup"), settings_sim._make_context()
    )

    assert reset_sim.get_last_text() == bot._DATA_CLEAR_TEXT + "\n\n" + settings_sim.get_last_text()
    assert reset_sim.get_last_buttons() == settings_sim.get_last_buttons()


@pytest.mark.asyncio
async def test_reset_without_passwordless_still_asks_for_the_username(monkeypatch):
    sim = BotSimulator()
    monkeypatch.setattr(bot, "_perform_reset", AsyncMock())
    monkeypatch.setattr(bot, "_safe_edit_text", AsyncMock(), raising=False)

    await bot.handle_reset_confirm(
        sim._make_callback_update("CONFIRM|reset"), sim._make_context()
    )

    assert _CHOICE not in sim.get_last_buttons()
    assert sim.get_last_text() == bot._DATA_CLEAR_TEXT + "\n\n" + bot._KAIZEN_USERNAME_PROMPT


def test_connect_prompt_is_only_built_by_the_shared_step():
    """Guard: a new route must call _connect_kaizen_prompt, not send the bare
    username prompt, or passwordless users lose the choice again. The only bare
    uses are the fallbacks for when passwordless is off or its page is down."""
    import inspect
    import re

    source = inspect.getsource(bot)
    uses = [m.start() for m in re.finditer(r"\b_KAIZEN_USERNAME_PROMPT\b", source)]
    allowed_owners = {"_connect_kaizen_prompt", "passwordless_setup_start"}
    owners = set()
    for pos in uses:
        before = source[:pos]
        if source[before.rfind("\n") + 1:].startswith("_KAIZEN_USERNAME_PROMPT = "):
            continue  # the definition itself
        owners.add(re.findall(r"^(?:async )?def (\w+)", before, re.M)[-1])
    assert owners <= allowed_owners, f"bare username prompt used in {owners - allowed_owners}"


# --- Sign-in is one message that updates in place ----------------------------

def _typed(sim, text):
    update = sim._make_text_update(text)
    update.message.delete = AsyncMock()
    return update


@pytest.mark.asyncio
async def test_reset_to_password_step_stays_in_one_message(offered, monkeypatch):
    """Delete data, then the email: every step edits the same message, and the
    typed email is deleted like the password because both are sensitive."""
    sim = BotSimulator()
    context = sim._make_context()
    monkeypatch.setattr(bot, "_perform_reset", AsyncMock())

    await bot.handle_reset_confirm(sim._make_callback_update("CONFIRM|reset"), context)
    email = _typed(sim, "doctor@nhs.net")
    state = await bot.setup_username(email, context)

    assert state == bot.AWAIT_PASSWORD
    email.message.delete.assert_awaited_once()
    assert not [kind for kind, _, _ in sim.messages_sent if kind in ("send", "reply")]
    assert "Step 2 of 3" in sim.get_last_text()


@pytest.mark.asyncio
async def test_not_an_email_is_deleted_and_asked_again_in_place(offered, monkeypatch):
    sim = BotSimulator()
    context = sim._make_context()
    monkeypatch.setattr(bot, "_perform_reset", AsyncMock())
    await bot.handle_reset_confirm(sim._make_callback_update("CONFIRM|reset"), context)

    typo = _typed(sim, "doctor at nhs")
    state = await bot.setup_username(typo, context)

    assert state == bot.AWAIT_USERNAME
    typo.message.delete.assert_awaited_once()
    assert sim.messages_sent[-1][0] == "bot_edit"
    assert "doesn't look like an email" in sim.get_last_text()


@pytest.mark.asyncio
async def test_email_that_cannot_be_deleted_gets_a_fresh_prompt(offered, monkeypatch):
    """If Telegram refuses the delete, don't edit a message above a visible email."""
    sim = BotSimulator()
    context = sim._make_context()
    monkeypatch.setattr(bot, "_perform_reset", AsyncMock())
    await bot.handle_reset_confirm(sim._make_callback_update("CONFIRM|reset"), context)

    email = sim._make_text_update("doctor@nhs.net")
    email.message.delete = AsyncMock(side_effect=RuntimeError("no rights"))
    await bot.setup_username(email, context)

    assert sim.messages_sent[-1][0] == "send"


@pytest.mark.asyncio
async def test_settings_connect_turns_the_settings_message_into_the_connect_step(offered):
    sim = BotSimulator()
    context = sim._make_context()
    tap = sim._make_callback_update("ACTION|setup", message_text=bot._SETTINGS_TITLE + "\n\nKaizen: not connected")

    await bot.setup_start(tap, context)

    assert sim.messages_sent[-1][0] == "bot_edit"
    assert not [kind for kind, _, _ in sim.messages_sent if kind in ("send", "reply")]
    assert _CHOICE in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_reconnect_on_another_message_keeps_that_message(offered):
    """A Reconnect button on a draft must not overwrite the draft."""
    sim = BotSimulator()
    tap = sim._make_callback_update("ACTION|setup", message_text="📝 Your CBD draft")

    await bot.setup_start(tap, sim._make_context())

    assert sim.messages_sent[-1][0] == "send"
