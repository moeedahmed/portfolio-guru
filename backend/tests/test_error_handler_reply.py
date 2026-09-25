"""An unexpected error gets an honest reply at the bottom of the chat.

The handler used to edit an old message, say "went wrong while filing" for any
error, and offer a Retry that saved to Kaizen.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot
from tests.helpers import make_text_update


def _buttons(markup):
    return {b.callback_data for row in markup.inline_keyboard for b in row} if markup else set()


@pytest.mark.asyncio
@pytest.mark.parametrize("saving, expected", [
    (True, "check your Kaizen drafts"),
    (False, "Nothing was saved"),
])
async def test_error_reply_is_new_honest_and_never_saves(saving, expected):
    update = make_text_update("hello")
    user_data = {"filing_in_progress": True} if saving else {}
    context = MagicMock(user_data=user_data, error=RuntimeError("boom"))
    with patch("telegram.Chat.send_message", new=AsyncMock()) as send, \
         patch.object(bot, "_edit_last_bot_msg", new=AsyncMock()) as edit, \
         patch("ops_alert.notify_operator", new=AsyncMock()):
        await bot.error_handler(update, context)
    edit.assert_not_awaited()
    text = send.call_args.args[0]
    assert expected in text
    assert "ACTION|retry_filing" not in _buttons(send.call_args.kwargs.get("reply_markup"))
    assert "filing_in_progress" not in context.user_data
