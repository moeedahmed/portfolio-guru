"""Process different users' Telegram updates concurrently, each user's in order.

python-telegram-bot's default processes every update one after another, so a
Kaizen save (minutes) or a model call (tens of seconds) for one doctor froze the
bot for everyone else; their queued button taps then expired. Turning on plain
concurrency would break ConversationHandler, which needs a user's own updates
handled one by one. This processor gives both: a per-user queue, run in
parallel across users.
"""
import asyncio
from collections.abc import Awaitable
from typing import Any

from telegram import Update
from telegram.ext import BaseUpdateProcessor

DEFAULT_MAX_CONCURRENT_UPDATES = 256


def update_owner(update: object) -> int | None:
    """The key a user's updates are serialised on: their user id, else the chat id."""
    if not isinstance(update, Update):
        return None
    if update.effective_user is not None:
        return update.effective_user.id
    if update.effective_chat is not None:
        return update.effective_chat.id
    return None


class PerUserUpdateProcessor(BaseUpdateProcessor):
    __slots__ = ("_locks", "_holders")

    def __init__(self, max_concurrent_updates: int = DEFAULT_MAX_CONCURRENT_UPDATES):
        super().__init__(max_concurrent_updates)
        self._locks: dict[int, asyncio.Lock] = {}
        self._holders: dict[int, int] = {}

    async def do_process_update(self, update: object, coroutine: Awaitable[Any]) -> None:
        owner = update_owner(update)
        if owner is None:
            await coroutine
            return
        lock = self._locks.setdefault(owner, asyncio.Lock())
        self._holders[owner] = self._holders.get(owner, 0) + 1
        try:
            # asyncio.Lock wakes waiters first-in, first-out, so a user's
            # updates run in the order Telegram delivered them.
            async with lock:
                await coroutine
        finally:
            self._holders[owner] -= 1
            if self._holders[owner] == 0:
                del self._holders[owner]
                del self._locks[owner]

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass
