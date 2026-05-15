"""Bot simulator for flow-walker tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from telegram import Chat, CallbackQuery, Message, Update, User


class BotSimulator:
    """Simulates Telegram updates while capturing outbound bot messages."""

    def __init__(self, user_id: int = 99999999):
        self.user_id = user_id
        self.messages_sent = []
        self.user_data = {}
        self.message_id_counter = 1

    def _make_context(self):
        context = MagicMock()
        context.user_data = self.user_data
        context.args = []
        context.bot = AsyncMock()
        context.bot.send_message = AsyncMock(side_effect=self._capture_send)
        context.bot.edit_message_text = AsyncMock(side_effect=self._capture_bot_edit)
        return context

    def _make_chat(self):
        chat = MagicMock(spec=Chat)
        chat.id = self.user_id
        chat.send_action = AsyncMock()
        chat.send_message = AsyncMock(side_effect=self._capture_send)
        return chat

    def _make_text_update(self, text: str):
        update = MagicMock(spec=Update)
        update.effective_user = MagicMock(spec=User)
        update.effective_user.id = self.user_id
        update.effective_user.first_name = "TestDoctor"

        chat = self._make_chat()
        message = MagicMock(spec=Message)
        message.text = text
        message.voice = None
        message.photo = []
        message.document = None
        message.caption = None
        message.chat = chat
        message.chat_id = chat.id
        message.message_id = self.message_id_counter
        message.reply_text = AsyncMock(side_effect=self._capture_reply)
        message.edit_text = AsyncMock(side_effect=self._capture_edit)
        self.message_id_counter += 1

        update.message = message
        update.callback_query = None
        update.effective_chat = chat
        update.effective_message = message
        return update

    def _make_callback_update(self, callback_data: str, message_text: str = "previous message"):
        update = MagicMock(spec=Update)
        update.effective_user = MagicMock(spec=User)
        update.effective_user.id = self.user_id
        update.effective_user.first_name = "TestDoctor"

        chat = self._make_chat()
        message = MagicMock(spec=Message)
        message.text = message_text
        message.chat = chat
        message.chat_id = chat.id
        message.message_id = self.message_id_counter
        message.reply_text = AsyncMock(side_effect=self._capture_reply)
        message.edit_text = AsyncMock(side_effect=self._capture_edit)
        self.message_id_counter += 1

        query = MagicMock(spec=CallbackQuery)
        query.data = callback_data
        query.answer = AsyncMock()
        query.message = message
        query.edit_message_text = AsyncMock(side_effect=self._capture_edit)
        query.edit_message_reply_markup = AsyncMock(side_effect=self._capture_edit_markup)

        update.message = None
        update.callback_query = query
        update.effective_chat = chat
        update.effective_message = message
        return update

    async def _capture_send(self, *args, **kwargs):
        text = kwargs.get("text", args[1] if len(args) > 1 else "")
        markup = kwargs.get("reply_markup")
        self.messages_sent.append(("send", text, markup))
        return self._mock_message()

    async def _capture_reply(self, text, **kwargs):
        markup = kwargs.get("reply_markup")
        self.messages_sent.append(("reply", text, markup))
        return self._mock_message()

    async def _capture_edit(self, text=None, *args, **kwargs):
        markup = kwargs.get("reply_markup")
        self.messages_sent.append(("edit", text, markup))
        return self._mock_message()

    async def _capture_bot_edit(self, *args, **kwargs):
        text = kwargs.get("text", "")
        markup = kwargs.get("reply_markup")
        self.messages_sent.append(("bot_edit", text, markup))
        return self._mock_message()

    async def _capture_edit_markup(self, *args, **kwargs):
        markup = kwargs.get("reply_markup")
        self.messages_sent.append(("markup", None, markup))
        return self._mock_message()

    def _mock_message(self):
        message = MagicMock()
        message.message_id = self.message_id_counter
        message.chat_id = self.user_id
        message.chat = self._make_chat()
        message.edit_text = AsyncMock(side_effect=self._capture_edit)
        self.message_id_counter += 1
        return message

    def get_last_buttons(self):
        for _, _, markup in reversed(self.messages_sent):
            if markup and hasattr(markup, "inline_keyboard"):
                return [
                    (button.text, button.callback_data)
                    for row in markup.inline_keyboard
                    for button in row
                    if button.callback_data
                ]
        return []

    def get_last_text(self):
        return self.messages_sent[-1][1] if self.messages_sent else None

    def clear_messages(self):
        self.messages_sent = []
