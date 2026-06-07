"""Focused tests for the /reset command consolidation.

Product decision: a single public command — /reset — clears all local Portfolio
Guru state and prompts a Kaizen reconnect. /delete survives only as a hidden,
backwards-compatible alias and must never be advertised to users. Cases already
saved in Kaizen are never touched.
"""

import inspect

import pytest


def test_reset_is_the_public_command_and_delete_is_hidden():
    import bot

    commands = {command for command, _ in bot.BOT_COMMANDS}
    assert "reset" in commands
    assert "delete" not in commands

    reset_description = next(desc for command, desc in bot.BOT_COMMANDS if command == "reset")
    assert "Kaizen" in reset_description


def test_help_copy_lists_reset_not_delete():
    import bot

    assert "/reset" in bot.HELP_MSG
    assert "/delete" not in bot.HELP_MSG


def test_clear_card_copy_is_reset_framed_and_protects_kaizen_cases():
    import bot

    text = bot._DATA_CLEAR_TEXT
    assert "Cases already saved in Kaizen are unaffected." in text
    assert "reconnect Kaizen" in text
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
