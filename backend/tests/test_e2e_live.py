"""Live Telegram tests — real messages to the real bot via Telethon.

Marked with @pytest.mark.live. Skipped unless TELETHON_SESSION env var is set.
NEVER run in CI — manual trigger only: pytest -m live

Requires: TELETHON_SESSION, TELEGRAM_API_ID, TELEGRAM_API_HASH env vars.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("TELETHON_SESSION"),
        reason="TELETHON_SESSION not set — skipping live Telegram tests",
    ),
]

BOT_USERNAME = "@PortfolioGuruBot"
RESPONSE_WAIT = 10  # seconds to wait for bot response


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def client():
    """Create and start a Telethon client from session string."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    session_str = os.environ.get("TELETHON_SESSION", "")
    api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")

    tc = TelegramClient(StringSession(session_str), api_id, api_hash)
    await tc.start()
    yield tc
    await tc.disconnect()


async def _send_and_wait(client, text: str, wait: int = RESPONSE_WAIT):
    """Send a message to the bot and wait for a response."""
    from telethon.tl.custom.message import Message

    await client.send_message(BOT_USERNAME, text)
    await asyncio.sleep(wait)
    messages = await client.get_messages(BOT_USERNAME, limit=3)
    # Return the most recent bot message (not our own)
    for msg in messages:
        if msg.sender_id != (await client.get_me()).id:
            return msg
    return messages[0] if messages else None


@pytest.mark.asyncio
async def test_live_start(client):
    """Send /start → verify response contains 'Portfolio Guru'."""
    msg = await _send_and_wait(client, "/start")
    assert msg is not None
    assert "Portfolio Guru" in msg.text


@pytest.mark.asyncio
async def test_live_case_text(client):
    """Send a clinical case → verify buttons appear in response."""
    await asyncio.sleep(RESPONSE_WAIT)
    msg = await _send_and_wait(client, "35F with ankle injury, examined and X-rayed, no fracture, discharged with advice")
    assert msg is not None
    assert msg.buttons is not None or msg.reply_markup is not None


@pytest.mark.asyncio
async def test_live_gibberish(client):
    """Send random text → verify bot responds (doesn't crash)."""
    await asyncio.sleep(RESPONSE_WAIT)
    msg = await _send_and_wait(client, "zxcvbnm random word salad 12345")
    assert msg is not None
    assert len(msg.text) > 0


@pytest.mark.asyncio
async def test_live_help(client):
    """Send /help → verify help text."""
    await asyncio.sleep(RESPONSE_WAIT)
    msg = await _send_and_wait(client, "/help")
    assert msg is not None
    assert "help" in msg.text.lower() or "portfolio" in msg.text.lower() or "file" in msg.text.lower()


@pytest.mark.asyncio
async def test_live_cancel(client):
    """Send /cancel → verify clean reset."""
    await asyncio.sleep(RESPONSE_WAIT)
    msg = await _send_and_wait(client, "/cancel")
    assert msg is not None
    assert "cancel" in msg.text.lower() or "❌" in msg.text
