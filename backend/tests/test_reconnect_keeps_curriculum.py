"""Reconnecting Kaizen must not change the doctor's curriculum.

Every reconnect, including a passwordless re-sign-in, used to store the 2025
curriculum, silently moving 2021-curriculum doctors onto 2025 forms.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot
from tests.helpers import isolate_bot_storage, make_text_update


@pytest.mark.asyncio
@pytest.mark.parametrize("stored, expected", [("2021", "2021"), (None, "2025")])
async def test_reconnect_keeps_a_chosen_curriculum(monkeypatch, tmp_path, stored, expected):
    isolate_bot_storage(monkeypatch, tmp_path)
    update = make_text_update("done")
    user_id = update.effective_user.id
    if stored:
        bot.store_curriculum(user_id, stored)
    context = MagicMock(user_data={})
    with patch.object(bot, "_flow_msg", new=AsyncMock()), \
         patch("telegram.Message.reply_text", new=AsyncMock()), \
         patch.object(bot, "_prompt_consent", new=AsyncMock(return_value=bot.ConversationHandler.END)):
        await bot._finish_setup_after_connect(update, context, "hst")
    assert bot.get_curriculum(user_id) == expected
