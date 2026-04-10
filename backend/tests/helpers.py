"""PTB-native test helpers — OfflineRequest, real Update/Message factories."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock

from telegram import Bot, Chat, Message, Update, User, CallbackQuery
from telegram.request import BaseRequest


_UPDATE_COUNTER = 0
_MSG_COUNTER = 1000


def _next_update_id() -> int:
    global _UPDATE_COUNTER
    _UPDATE_COUNTER += 1
    return _UPDATE_COUNTER


def _next_msg_id() -> int:
    global _MSG_COUNTER
    _MSG_COUNTER += 1
    return _MSG_COUNTER


class OfflineRequest(BaseRequest):
    """Blocks any network call — test fails immediately if bot tries to reach Telegram."""

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    @property
    def read_timeout(self) -> float | None:
        return None

    async def do_request(
        self,
        url,
        method,
        request_data=None,
        read_timeout=None,
        write_timeout=None,
        connect_timeout=None,
        pool_timeout=None,
    ):
        import pytest

        pytest.fail(f"OfflineRequest: bot tried to make a network call to {url}")


TEST_USER = User(id=99999, is_bot=False, first_name="TestDoctor")
TEST_CHAT = Chat(id=99999, type=Chat.PRIVATE)
BOT_USER = User(id=12345, is_bot=True, first_name="PortfolioGuru", username="PortfolioGuruBot")


def make_message(text: str, user: User | None = None, chat: Chat | None = None) -> Message:
    """Build a real PTB Message object."""
    user = user or TEST_USER
    chat = chat or TEST_CHAT
    msg = Message(
        message_id=_next_msg_id(),
        date=datetime.datetime.now(tz=datetime.timezone.utc),
        chat=chat,
        from_user=user,
        text=text,
    )
    return msg


def make_text_update(text: str, user: User | None = None) -> Update:
    """Build a real Update containing a text message."""
    msg = make_message(text, user=user)
    return Update(update_id=_next_update_id(), message=msg)


def make_callback_update(
    data: str,
    user: User | None = None,
    message_text: str = "prev",
) -> Update:
    """Build a real Update containing a CallbackQuery."""
    user = user or TEST_USER
    chat = TEST_CHAT
    # The message that the button was attached to
    msg = Message(
        message_id=_next_msg_id(),
        date=datetime.datetime.now(tz=datetime.timezone.utc),
        chat=chat,
        from_user=BOT_USER,
        text=message_text,
    )
    cq = CallbackQuery(
        id=str(_next_update_id()),
        from_user=user,
        chat_instance=str(chat.id),
        data=data,
        message=msg,
    )
    return Update(update_id=_next_update_id(), callback_query=cq)


def make_command_update(command: str, user: User | None = None, args: list[str] | None = None) -> Update:
    """Build a real Update for a /command. Includes proper entity so PTB recognises it."""
    from telegram import MessageEntity

    full_text = f"/{command}" + (" " + " ".join(args) if args else "")
    user = user or TEST_USER
    msg = Message(
        message_id=_next_msg_id(),
        date=datetime.datetime.now(tz=datetime.timezone.utc),
        chat=TEST_CHAT,
        from_user=user,
        text=full_text,
        entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(f"/{command}"))],
    )
    return Update(update_id=_next_update_id(), message=msg)
