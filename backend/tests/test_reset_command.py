"""Focused tests for the /reset command consolidation.

Product decision: a single public command — /reset — clears all local Portfolio
Guru state and prompts a Kaizen reconnect. /delete survives only as a hidden,
backwards-compatible alias and must never be advertised to users. Cases already
saved in Kaizen are never touched.
"""

import inspect
from unittest.mock import AsyncMock

import pytest
from tests.test_e2e_offline import offline_app, _prepare_update


def test_reset_is_the_public_command_and_setup_is_hidden():
    import bot

    commands = {command for command, _ in bot.BOT_COMMANDS}
    assert "reset" in commands
    assert "delete" not in commands
    # /setup is no longer advertised — Kaizen connection is owned by /settings.
    assert "setup" not in commands

    reset_description = next(desc for command, desc in bot.BOT_COMMANDS if command == "reset")
    assert "Kaizen" in reset_description


def test_public_command_menu_is_core_only():
    import bot

    commands = [command for command, _ in bot.BOT_COMMANDS]

    assert commands == ["start", "settings", "health", "cancel", "reset", "help"]


def test_help_copy_lists_reset_not_delete_and_not_setup():
    import bot

    command_lines = [
        line.strip()
        for line in bot.HELP_MSG.splitlines()
        if line.strip().startswith("/")
    ]
    assert command_lines == [
        f"/{command} — {description}"
        for command, description in bot.BOT_COMMANDS
    ]
    assert "/reset" in bot.HELP_MSG
    assert "/delete" not in bot.HELP_MSG
    # /setup is no longer a user-facing command — Kaizen connection
    # is now owned by /settings.
    assert "/setup" not in bot.HELP_MSG
    assert "/link" not in bot.HELP_MSG
    assert "/voice" not in bot.HELP_MSG
    assert "/health" in bot.HELP_MSG
    assert "/upgrade" not in bot.HELP_MSG
    assert "How it works" not in bot.HELP_MSG
    assert "Saved as Kaizen draft" not in bot.HELP_MSG
    assert "ask before saving anything to Kaizen" in bot.HELP_MSG


@pytest.mark.asyncio
async def test_help_command_is_text_only_with_no_dead_inline_buttons():
    import bot
    from tests.bot_simulator import BotSimulator

    sim = BotSimulator(user_id=4242)
    update = sim._make_text_update("/help")
    context = sim._make_context()

    result = await bot.help_command(update, context)

    assert result == bot.ConversationHandler.END
    assert "Portfolio Guru help" in sim.get_last_text()
    assert sim.messages_sent[-1][2] is None


@pytest.mark.asyncio
async def test_setup_command_redirects_connected_user_to_settings(monkeypatch):
    import bot
    from tests.bot_simulator import BotSimulator

    sim = BotSimulator(user_id=4242)
    update = sim._make_text_update("/setup")
    update.effective_chat.type = "private"
    context = sim._make_context()

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="free"))
    monkeypatch.setattr(bot, "get_cases_this_month", AsyncMock(return_value=0))
    monkeypatch.setattr(bot, "is_beta_tester", AsyncMock(return_value=False))
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))

    result = await bot.setup_start(update, context)

    assert result == bot.ConversationHandler.END
    assert "Settings" in sim.get_last_text()
    assert "What's your Kaizen username" not in sim.get_last_text()


def test_clear_card_copy_is_reset_framed_and_protects_kaizen_cases():
    import bot

    text = bot._DATA_CLEAR_TEXT
    assert "Cases already saved in Kaizen are unaffected." in text
    # The all-clear card should reassure, never threaten deletion of real cases.
    assert "delete" not in text.lower()


def test_reset_handlers_exist_and_are_coroutines():
    import bot

    for name in ("reset_data", "_perform_reset", "handle_reset_confirm"):
        fn = getattr(bot, name)
        assert inspect.iscoroutinefunction(fn), f"{name} should be async"


def test_build_application_registers_reset_and_delete_alias(monkeypatch, tmp_path):
    import bot
    from telegram.ext import CommandHandler, CallbackQueryHandler

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("HOME", str(tmp_path))  # redirect PicklePersistence path

    application = bot.build_application()

    command_targets = {}
    confirm_patterns = []
    for handlers in application.handlers.values():
        for handler in handlers:
            if isinstance(handler, CommandHandler):
                for command in handler.commands:
                    command_targets.setdefault(command, handler.callback)
            elif isinstance(handler, CallbackQueryHandler) and handler.pattern is not None:
                confirm_patterns.append(handler.pattern.pattern)

    # Both the public command and the hidden alias route to the same purge.
    assert command_targets.get("reset") is bot.reset_data
    assert command_targets.get("delete") is bot.reset_data

    # The inline reset confirmation is wired (accepting the legacy payload too).
    assert any(
        "CONFIRM" in pattern and "reset" in pattern
        for pattern in confirm_patterns
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['mistyped_secret', 'beta', 'link', 'bulk', 'chase'])
async def test_unknown_and_retired_commands_reply_without_echo(offline_app, command):
    import bot
    from tests.helpers import make_command_update

    app, collector = offline_app
    update = make_command_update(command, args=['private-content'])
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert collector.texts == [bot.UNKNOWN_COMMAND_MSG]
    assert 'private-content' not in collector.texts[0]
    assert command not in collector.texts[0]
    assert all('/' + name in collector.texts[0] for name, _ in bot.BOT_COMMANDS)


def test_command_fallback_and_tracker_order():
    import bot
    from telegram.ext import CommandHandler, MessageHandler
    from tests.helpers import build_offline_application
    from tests.whole_bot_coverage import inventory

    app = build_offline_application()
    assert app.handlers[0][-1].callback is bot.unknown_command
    tracker = app.handlers[2][0]
    assert isinstance(tracker, MessageHandler) and tracker.callback is bot._track_command_use
    assert tracker.block is False
    registered = {name for slot in inventory(app) for name in slot.commands}
    assert registered == bot._KNOWN_COMMAND_NAMES
    assert not registered & {'beta', 'link', 'bulk', 'chase'}


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['help', 'start', 'unknown_private', 'chase'])
async def test_command_usage_independent_dispatch(offline_app, monkeypatch, command):
    import asyncio
    import bot
    import funnel_metrics
    from tests.helpers import TEST_USER, make_command_update

    app, collector = offline_app
    records = []
    def log(**kwargs):
        records.append(kwargs)
    monkeypatch.setattr(funnel_metrics, 'log_event', log)
    update = make_command_update(command)
    _prepare_update(update, app.bot)
    await app.process_update(update)
    await asyncio.sleep(0)  # Allow the nonblocking telemetry handler to run.
    events = [r for r in records if r['event'] == 'command_used']
    assert len(events) == 1
    assert events[0]['user_id'] == TEST_USER.id
    assert events[0]['metadata'] == {'command': command if command in bot._KNOWN_COMMAND_NAMES else 'unknown'}
    assert collector.texts
    if command == 'help':
        assert collector.texts == [bot.HELP_MSG]
    elif command in {'unknown_private', 'chase'}:
        assert collector.texts == [bot.UNKNOWN_COMMAND_MSG]


@pytest.mark.asyncio
async def test_unknown_command_does_not_end_active_case_conversation(offline_app):
    import bot
    from tests.helpers import TEST_USER, make_command_update
    from telegram.ext import ConversationHandler

    app, collector = offline_app
    conv = next(h for h in app.handlers[0] if isinstance(h, ConversationHandler) and h.name == 'case_conv')
    key = (TEST_USER.id, TEST_USER.id)
    conv._conversations[key] = bot.AWAIT_APPROVAL
    update = make_command_update('mistyped_private')
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert collector.texts == [bot.UNKNOWN_COMMAND_MSG]
    assert conv._conversations[key] == bot.AWAIT_APPROVAL


def test_unknown_reply_only_handles_private_commands():
    import bot
    from telegram import Chat
    from tests.helpers import build_offline_application, make_command_update, make_text_update

    handler = build_offline_application().handlers[0][-1]
    assert handler.callback is bot.unknown_command
    update = make_command_update('mistyped')
    assert handler.check_update(update)
    with update.message._unfrozen():
        update.message.chat = Chat(id=-123, type=Chat.GROUP)
    assert not handler.check_update(update)
    assert not handler.check_update(make_text_update('ordinary case text'))
