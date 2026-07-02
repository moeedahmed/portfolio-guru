"""Setup-flow manual profile fallback (SETLEVEL|*) discipline.

The bug this slice protects against: ``setup_password``'s "couldn't
auto-detect" path shows manual-profile buttons whose ``callback_data``
is ``SETLEVEL|ACCS|INTERMEDIATE|HIGHER|SAS`` — the same prefix the
/settings → change portfolio path uses. If the global ``SETLEVEL``
handler is checked before ``setup_conv``'s ``AWAIT_TRAINING_LEVEL``
state, PTB silently exits setup with the "Back to settings" copy and
the user loses the in-progress connection flow.

Three contracts pinned here:

* ``setup_training_level`` (the in-setup handler) produces a Kaizen-
  connected completion message — not a settings-style "Back to
  settings" button — and ends the conversation without asking for a
  curriculum (already defaulted to 2025 by ``setup_password`` before
  manual buttons render);
* the in-setup ``AWAIT_TRAINING_LEVEL`` callback pattern matches the
  actual ``SETLEVEL|*`` callback_data emitted by ``setup_password`` —
  the original bug was an ``^LEVEL\\|`` pattern that quietly missed
  every click and let the global handler steal the update;
* the production handler registration order keeps ``setup_conv`` ahead
  of the global ``handle_set_level`` so the conv handler intercepts
  ``SETLEVEL`` clicks while the user is in setup, while still letting
  /settings → change portfolio reach ``handle_set_level`` when no
  conv is active.

Boundary: offline pure-function + app-construction tests. No live
Telegram, no live Kaizen, no credentials, no network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import CallbackQueryHandler, ConversationHandler


# ─── setup_training_level behaviour (the in-setup handler) ──────────────────


def _arm_callback_message(mock_callback_update):
    """The conftest fixture gives a MagicMock message; the setup handler
    drives it through ``_safe_edit_text`` which awaits ``edit_text``.
    Swap in a fresh ``AsyncMock`` so we can assert on the call."""
    mock_callback_update.callback_query.message.edit_text = AsyncMock()
    mock_callback_update.effective_chat = MagicMock()


def _captured_edit_text(mock_callback_update) -> str:
    edit_text = mock_callback_update.callback_query.message.edit_text
    edit_text.assert_called_once()
    call = edit_text.call_args
    if call.args:
        return call.args[0]
    return call.kwargs.get("text", "")


@pytest.mark.asyncio
async def test_setup_training_level_handles_setlevel_sas_with_completion_copy(
    mock_callback_update, mock_context, monkeypatch
):
    """``SETLEVEL|SAS`` clicked in setup must yield the Kaizen-connected
    completion message — the same end state the auto-detect success
    path produces — not the settings-style "Back to settings" button.
    Pins the user-visible copy so a future refactor can't silently
    re-route the fallback to ``handle_set_level``.
    """
    import bot

    captured: dict = {}

    def _fake_store_training_level(user_id, level):
        captured["user_id"] = user_id
        captured["level"] = level

    monkeypatch.setattr(bot, "store_training_level", _fake_store_training_level)
    _arm_callback_message(mock_callback_update)
    mock_callback_update.callback_query.data = "SETLEVEL|SAS"

    result = await bot.setup_training_level(mock_callback_update, mock_context)

    assert captured == {"user_id": 99999999, "level": "SAS"}
    assert result == ConversationHandler.END

    sent_text = _captured_edit_text(mock_callback_update)
    assert "Kaizen connected" in sent_text
    assert "Non-Training Profile" in sent_text  # SAS bucket label
    # Hard pin: the settings-handler "Back to settings" copy MUST NOT
    # surface inside the setup flow.
    assert "Back to settings" not in sent_text
    # And the curriculum follow-up question must not be re-introduced;
    # setup_password already defaulted the curriculum to 2025.
    assert "curriculum" not in sent_text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["ACCS", "INTERMEDIATE", "HIGHER", "SAS"])
async def test_setup_training_level_ends_setup_conv_for_every_manual_button(
    mock_callback_update, mock_context, monkeypatch, level
):
    """Every manual-fallback button must end ``setup_conv``. Returning
    ``AWAIT_CURRICULUM`` (the old behaviour) would re-prompt for
    curriculum, but ``setup_password`` already stores the 2025 default
    before the manual buttons render — so the second question is dead
    UI in this branch.
    """
    import bot

    monkeypatch.setattr(bot, "store_training_level", lambda *a, **kw: None)
    _arm_callback_message(mock_callback_update)
    mock_callback_update.callback_query.data = f"SETLEVEL|{level}"

    result = await bot.setup_training_level(mock_callback_update, mock_context)

    assert result == ConversationHandler.END


# ─── handle_set_level (the /settings global handler) ────────────────────────


@pytest.mark.asyncio
async def test_handle_set_level_still_emits_back_to_settings_outside_setup(
    mock_callback_update, mock_context, monkeypatch
):
    """The /settings → change portfolio path must keep its "Back to
    settings" pop-back button. Pin this so a future "just unify them"
    refactor doesn't silently strip the settings round-trip — the only
    behavioural signal that this handler is the settings one (not the
    setup one) is the button copy on its response.
    """
    import bot

    monkeypatch.setattr(bot, "store_training_level", lambda *a, **kw: None)
    mock_callback_update.callback_query.data = "SETLEVEL|SAS"
    mock_callback_update.callback_query.edit_message_text = AsyncMock()

    await bot.handle_set_level(mock_callback_update, mock_context)

    mock_callback_update.callback_query.edit_message_text.assert_called_once()
    call = mock_callback_update.callback_query.edit_message_text.call_args
    markup = call.kwargs.get("reply_markup")
    assert markup is not None, (
        "handle_set_level dropped its reply_markup — without it the user "
        "has no path back to /settings."
    )
    buttons = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("Back to settings" in b for b in buttons), (
        f"handle_set_level lost its 'Back to settings' button: {buttons!r}"
    )


# ─── production handler wiring ──────────────────────────────────────────────


@pytest.fixture
def production_app(monkeypatch, tmp_path):
    """Build the real Telegram ``Application`` with handlers registered.
    Token + persistence path are stubbed so this stays fully offline —
    no Telegram network, no writes outside the test's tmp_path.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("HOME", str(tmp_path))
    import bot
    return bot.build_application()


def _find_setup_conv(app):
    import bot
    for handler in app.handlers.get(0, []):
        if (
            isinstance(handler, ConversationHandler)
            and bot.AWAIT_TRAINING_LEVEL in handler.states
        ):
            return handler
    return None


def test_setup_conv_await_training_level_pattern_matches_setlevel(production_app):
    """``AWAIT_TRAINING_LEVEL`` must match the ``SETLEVEL|*`` buttons
    emitted by ``setup_password``'s manual fallback. The original bug
    was a stale ``^LEVEL\\|`` pattern (from a long-removed button set)
    that quietly missed every click and let the global handler steal
    the update.
    """
    import bot

    setup_conv = _find_setup_conv(production_app)
    assert setup_conv is not None, "setup_conv not registered on production app"

    handlers = setup_conv.states[bot.AWAIT_TRAINING_LEVEL]
    matching = [
        h
        for h in handlers
        if isinstance(h, CallbackQueryHandler) and h.pattern.match("SETLEVEL|SAS")
    ]
    assert matching, (
        "No AWAIT_TRAINING_LEVEL handler matches 'SETLEVEL|SAS'. Patterns: "
        f"{[getattr(h, 'pattern', None) for h in handlers]}"
    )
    assert matching[0].callback is bot.setup_training_level, (
        "AWAIT_TRAINING_LEVEL must route SETLEVEL|* to setup_training_level "
        "(the setup-flow handler), not handle_set_level (the settings one)."
    )


def test_setup_conv_registered_before_global_set_level_handler(production_app):
    """Registration-order discipline: ``setup_conv`` must come before the
    global ``SETLEVEL`` handler so PTB picks the conv handler first while
    the user is in ``AWAIT_TRAINING_LEVEL``. Reverse the order and the
    bug is back — the global handler silently wins.
    """
    import bot

    handlers = production_app.handlers.get(0, [])
    setup_conv_idx = next(
        (
            i
            for i, h in enumerate(handlers)
            if isinstance(h, ConversationHandler)
            and bot.AWAIT_TRAINING_LEVEL in h.states
        ),
        None,
    )
    set_level_idx = next(
        (
            i
            for i, h in enumerate(handlers)
            if isinstance(h, CallbackQueryHandler)
            and h.callback is bot.handle_set_level
        ),
        None,
    )
    assert setup_conv_idx is not None, "setup_conv not found in group 0"
    assert set_level_idx is not None, "handle_set_level not found in group 0"
    assert setup_conv_idx < set_level_idx, (
        f"setup_conv (idx {setup_conv_idx}) must register BEFORE "
        f"handle_set_level (idx {set_level_idx}) so the conv handler "
        f"intercepts SETLEVEL clicks during setup."
    )


# ─── reset→setup path lives in case_conv ────────────────────────────────────
#
# The live bug: after /reset the user reconnects Kaizen by typing their
# username, which enters *case_conv* (``handle_case_input`` is a case_conv
# entry point). ``setup_password`` then runs as a case_conv state and, when
# auto-detect fails, returns ``AWAIT_TRAINING_LEVEL`` — so the manual-profile
# buttons are answered by case_conv, NOT setup_conv. The original wiring
# registered the global ``handle_set_level`` between setup_conv and case_conv,
# so the SETLEVEL|* tap was stolen by the settings handler ("Portfolio set to
# HST Profile" / "Back to settings") and consent step 3 was skipped.


def _find_case_conv(app):
    """case_conv is the OTHER ConversationHandler carrying AWAIT_TRAINING_LEVEL
    — distinguished from setup_conv by also owning AWAIT_APPROVAL (a case-only
    state)."""
    import bot

    for handler in app.handlers.get(0, []):
        if (
            isinstance(handler, ConversationHandler)
            and bot.AWAIT_TRAINING_LEVEL in handler.states
            and bot.AWAIT_APPROVAL in handler.states
        ):
            return handler
    return None


def test_case_conv_routes_setlevel_to_setup_training_level(production_app):
    """The reset→setup flow runs inside case_conv, so case_conv's
    ``AWAIT_TRAINING_LEVEL`` state must route ``SETLEVEL|*`` to
    ``setup_training_level`` (the consent-continuing handler), never to the
    settings ``handle_set_level``.
    """
    import bot

    case_conv = _find_case_conv(production_app)
    assert case_conv is not None, "case_conv (with AWAIT_TRAINING_LEVEL) not found"

    handlers = case_conv.states[bot.AWAIT_TRAINING_LEVEL]
    matching = [
        h
        for h in handlers
        if isinstance(h, CallbackQueryHandler) and h.pattern.match("SETLEVEL|HIGHER")
    ]
    assert matching, (
        "case_conv AWAIT_TRAINING_LEVEL has no handler matching 'SETLEVEL|HIGHER'. "
        f"Patterns: {[getattr(h, 'pattern', None) for h in handlers]}"
    )
    assert matching[0].callback is bot.setup_training_level, (
        "case_conv AWAIT_TRAINING_LEVEL must route SETLEVEL|* to "
        "setup_training_level (consent-continuing), not handle_set_level."
    )


def test_global_set_level_registered_after_case_conv(production_app):
    """The regression guard for the live bug. ``handle_set_level`` matches
    ``^SETLEVEL\\|`` unconditionally, so it must be registered AFTER *every*
    conversation handler that owns an AWAIT_TRAINING_LEVEL state — including
    case_conv, which drives the reset→setup path. If it sits before case_conv
    (the original wiring), PTB dispatches the manual-profile tap to the
    settings handler and consent step 3 is skipped.
    """
    import bot

    handlers = production_app.handlers.get(0, [])
    conv_indices = [
        i
        for i, h in enumerate(handlers)
        if isinstance(h, ConversationHandler)
        and bot.AWAIT_TRAINING_LEVEL in h.states
    ]
    set_level_idx = next(
        (
            i
            for i, h in enumerate(handlers)
            if isinstance(h, CallbackQueryHandler)
            and h.callback is bot.handle_set_level
        ),
        None,
    )
    assert conv_indices, "no AWAIT_TRAINING_LEVEL conversation handlers found"
    assert set_level_idx is not None, "handle_set_level not found in group 0"
    assert set_level_idx > max(conv_indices), (
        f"handle_set_level (idx {set_level_idx}) must register AFTER every "
        f"AWAIT_TRAINING_LEVEL conversation handler (indices {conv_indices}). "
        "Otherwise the global settings handler steals the manual-profile tap "
        "during the reset→setup flow and consent step 3 is skipped."
    )


# ─── consent continuation from the setup manual pick ────────────────────────


@pytest.mark.consent_gate
@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["ACCS", "INTERMEDIATE", "HIGHER", "SAS"])
async def test_setup_training_level_prompts_consent_step3_when_no_consent(
    mock_callback_update, mock_context, monkeypatch, level
):
    """When the user has NOT yet consented (the state right after /reset wipes
    consent), a manual profile pick in setup must continue to consent step 3 —
    the exact break the live bug caused. ``@pytest.mark.consent_gate`` opts out
    of the autouse consent bypass so the real gate is exercised.
    """
    import bot
    import consent

    monkeypatch.setattr(bot, "store_training_level", lambda *a, **kw: None)
    monkeypatch.setattr(
        consent, "has_current_consent", AsyncMock(return_value=False)
    )
    _arm_callback_message(mock_callback_update)
    mock_callback_update.callback_query.data = f"SETLEVEL|{level}"
    # The consent step-3 prompt edits the setup flow anchor in place via
    # context.bot.edit_message_text — seed an anchor so we can read that call.
    mock_context.user_data["_flow_anchor_setup"] = (123, 456)

    result = await bot.setup_training_level(mock_callback_update, mock_context)

    assert result == ConversationHandler.END
    mock_context.bot.edit_message_text.assert_called_once()
    sent_text = mock_context.bot.edit_message_text.call_args.kwargs["text"]
    assert "Step 3 of 3" in sent_text, (
        f"manual profile pick for {level} did not continue to consent step 3; "
        f"got: {sent_text!r}"
    )
    assert "Back to settings" not in sent_text


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_setup_training_level_lands_ready_when_consent_already_current(
    mock_callback_update, mock_context, monkeypatch
):
    """If consent is already current, the setup manual pick must land ready
    (Kaizen-connected welcome) and NOT re-prompt consent — the third
    acceptance contract. Uses the real gate (consent_gate) with a stubbed
    has_current_consent → True.
    """
    import bot
    import consent

    monkeypatch.setattr(bot, "store_training_level", lambda *a, **kw: None)
    monkeypatch.setattr(
        consent, "has_current_consent", AsyncMock(return_value=True)
    )
    _arm_callback_message(mock_callback_update)
    mock_callback_update.callback_query.data = "SETLEVEL|HIGHER"

    result = await bot.setup_training_level(mock_callback_update, mock_context)

    assert result == ConversationHandler.END
    sent_text = _captured_edit_text(mock_callback_update)
    assert "Kaizen connected" in sent_text
    assert "Step 3 of 3" not in sent_text, (
        "consent was re-prompted even though consent is already current: "
        f"{sent_text!r}"
    )
    assert "Back to settings" not in sent_text
