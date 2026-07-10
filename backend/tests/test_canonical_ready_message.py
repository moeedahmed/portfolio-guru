"""Consistency test for the canonical "ready to file" intake message.

The same intake copy (``WELCOME_MSG_CONNECTED`` — rendered from the
``welcome_connected`` message-policy template) must appear in full-entry flows
so the user sees a stable greeting when intentionally starting a filing route:

1. ``/start`` after credentials are connected.
2. ``ACTION|file`` — the "File another case" post-filing button.

``/cancel`` is deliberately different: it is an escape hatch, not another
welcome screen, so it stays concise and avoids repeating the same full ready
message shown by ``/start``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from telegram.ext import ConversationHandler

from tests.bot_simulator import BotSimulator


pytestmark = pytest.mark.asyncio


async def _run_start_connected() -> str:
    from bot import start

    sim = BotSimulator()
    update = sim._make_text_update("/start")
    context = sim._make_context()
    with patch("bot.has_credentials", return_value=True):
        await start(update, context)
    return sim.get_last_text() or ""


async def _run_cancel_connected() -> str:
    from bot import handle_callback

    sim = BotSimulator()
    update = sim._make_callback_update("CANCEL|draft")
    context = sim._make_context()
    context.user_data["case_text"] = "some in-progress case text"
    with patch("bot.has_credentials", return_value=True):
        result = await handle_callback(update, context)
    assert result == ConversationHandler.END
    return sim.get_last_text() or ""


async def _run_file_another() -> str:
    from bot import AWAIT_CASE_INPUT, handle_action_button

    sim = BotSimulator()
    update = sim._make_callback_update("ACTION|file")
    context = sim._make_context()
    with patch("bot.has_credentials", return_value=True):
        result = await handle_action_button(update, context)
    assert result == AWAIT_CASE_INPUT
    return sim.get_last_text() or ""


async def test_entry_flows_share_the_same_canonical_ready_body():
    from bot import WELCOME_MSG_CONNECTED

    start_text = await _run_start_connected()
    cancel_text = await _run_cancel_connected()
    file_another_text = await _run_file_another()

    # Full-entry flows must show the canonical body verbatim.
    assert WELCOME_MSG_CONNECTED in start_text, start_text
    assert WELCOME_MSG_CONNECTED in file_another_text, file_another_text

    # /start and ACTION|file have no prefix — the canonical text is the
    # whole message.
    assert start_text == WELCOME_MSG_CONNECTED
    assert file_another_text == WELCOME_MSG_CONNECTED

    # /cancel is a concise state-clear acknowledgement, not a duplicate /start.
    assert "Cancelled" in cancel_text
    assert WELCOME_MSG_CONNECTED not in cancel_text
    assert "Send an anonymised case" in cancel_text


async def test_canonical_message_matches_brief():
    """Pin the wording requirements from the product brief.

    The canonical message must:
    - stay short because the Telegram profile already explains the product,
    - invite case evidence in any supported modality,
    - stay lean enough to work as the central idempotent ready state,
    - not tell the user to say "draft it".
    """
    from bot import WELCOME_MSG_CONNECTED

    text = WELCOME_MSG_CONNECTED
    assert "Ready" in text
    for modality in ("text", "voice", "photo", "document"):
        assert modality in text.lower(), f"missing modality in canonical copy: {modality}"
    assert "Kaizen" not in text
    assert "buttons" not in text.lower()
    assert len(text.splitlines()) <= 3
    assert "draft it" not in text.lower()
