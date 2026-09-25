"""One doctor's slow save must not freeze the bot for everyone else.

Before PerUserUpdateProcessor the bot handled one Telegram update at a time, so
a Kaizen save or model call for one user queued every other user's messages
and let their button taps expire.
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from telegram import Chat, Message, Update, User

from update_processor import PerUserUpdateProcessor, update_owner


def _update(update_id: int, user_id: int) -> Update:
    user = User(id=user_id, first_name="Test", is_bot=False)
    chat = Chat(id=user_id, type="private")
    message = Message(message_id=update_id, date=datetime.now(timezone.utc), chat=chat, from_user=user, text="x")
    return Update(update_id=update_id, message=message)


@pytest.mark.asyncio
async def test_another_user_is_served_while_one_user_is_blocked():
    processor = PerUserUpdateProcessor()
    release_a = asyncio.Event()
    done = []

    async def slow_save():
        await release_a.wait()
        done.append("a")

    async def quick_reply():
        done.append("b")

    a = asyncio.create_task(processor.process_update(_update(1, 111), slow_save()))
    b = asyncio.create_task(processor.process_update(_update(2, 222), quick_reply()))
    await asyncio.wait_for(b, timeout=1)
    assert done == ["b"]
    release_a.set()
    await a
    assert done == ["b", "a"]


@pytest.mark.asyncio
async def test_one_users_updates_run_in_arrival_order_never_overlapping():
    processor = PerUserUpdateProcessor()
    events = []

    def step(name, delay):
        async def run():
            events.append(f"{name} start")
            await asyncio.sleep(delay)
            events.append(f"{name} end")
        return run()

    await asyncio.gather(
        processor.process_update(_update(1, 111), step("first", 0.05)),
        processor.process_update(_update(2, 111), step("second", 0)),
        processor.process_update(_update(3, 111), step("third", 0)),
    )
    assert events == ["first start", "first end", "second start", "second end", "third start", "third end"]


@pytest.mark.asyncio
async def test_per_user_locks_are_released_after_use():
    processor = PerUserUpdateProcessor()

    async def noop():
        return None

    await processor.process_update(_update(1, 111), noop())
    assert processor._locks == {} and processor._holders == {}


def test_updates_without_a_user_or_chat_are_not_serialised():
    assert update_owner(object()) is None
    assert update_owner(_update(1, 555)) == 555


def test_the_bot_is_built_with_the_per_user_processor():
    from telegram.ext import ApplicationBuilder
    import bot

    class _Built(Exception):
        pass

    seen = []

    def record(builder):
        seen.append(builder._update_processor)
        raise _Built

    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "0:FAKE"}), \
         patch("clinical_persistence.purge_existing_file", return_value={"status": "offline"}), \
         patch.object(ApplicationBuilder, "build", autospec=True, side_effect=record), \
         pytest.raises(_Built):
        bot.build_application()
    assert isinstance(seen[0], PerUserUpdateProcessor)
