"""Live Telegram tests — real messages to the real bot via Telethon.

Marked with @pytest.mark.live. Skipped unless TELETHON_SESSION env var is set.
NEVER run in CI — manual trigger only: pytest -m live

Requires: TELETHON_SESSION, TELEGRAM_API_ID, TELEGRAM_API_HASH env vars.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from tests.telegram_live_harness import (
    assert_live_telegram_guardrails,
    button_texts,
    has_telethon_env,
    telethon_env,
    wait_for_matching_message,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not has_telethon_env(),
        reason="Telethon credentials not set — skipping live Telegram tests",
    ),
]

RESPONSE_WAIT = 10  # seconds to wait for bot response


@pytest_asyncio.fixture
async def client():
    """Create and start a Telethon client from session string."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    assert_live_telegram_guardrails()
    env = telethon_env()
    session_str = env["session"]
    api_id = int(env["api_id"])
    api_hash = env["api_hash"]

    tc = TelegramClient(StringSession(session_str), api_id, api_hash)
    await tc.start()
    yield tc
    await tc.disconnect()


async def _send_and_wait(
    client,
    text: str,
    *,
    wait: int = RESPONSE_WAIT,
    expect_text_any: tuple[str, ...] = (),
    expect_buttons: bool = False,
    expect_button_any: tuple[str, ...] = (),
):
    """Send a message to the bot and wait for a response."""
    bot_username = telethon_env()["bot_username"]
    async with client.conversation(bot_username, timeout=wait * 3) as conv:
        sent = await conv.send_message(text)
        try:
            return await wait_for_matching_message(
                client,
                bot_username,
                wait * 3,
                expect_text_any=expect_text_any,
                expect_buttons=expect_buttons,
                expect_button_any=expect_button_any,
                min_id=getattr(sent, "id", None),
            )
        except TimeoutError:
            return await conv.get_response(timeout=1)


@pytest.mark.asyncio
async def test_live_start(client):
    """Send /start → verify response contains 'Portfolio Guru'."""
    msg = await _send_and_wait(client, "/start", expect_text_any=("Portfolio Guru",))
    assert msg is not None
    assert "Portfolio Guru" in msg.text


@pytest.mark.asyncio
async def test_live_case_text(client):
    """Send a clinical case → verify buttons appear in response."""
    await asyncio.sleep(RESPONSE_WAIT)
    msg = await _send_and_wait(
        client,
        "35F with ankle injury, examined and X-rayed, no fracture, discharged with advice",
        expect_buttons=True,
    )
    assert msg is not None
    assert button_texts(msg) or msg.reply_markup is not None


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
    msg = await _send_and_wait(client, "/help", expect_text_any=("help", "portfolio", "file"))
    assert msg is not None
    assert "help" in msg.text.lower() or "portfolio" in msg.text.lower() or "file" in msg.text.lower()


@pytest.mark.asyncio
async def test_live_cancel(client):
    """Send /cancel → verify clean reset."""
    await asyncio.sleep(RESPONSE_WAIT)
    msg = await _send_and_wait(client, "/cancel", expect_text_any=("cancel", "❌"))
    assert msg is not None
    assert "cancel" in msg.text.lower() or "❌" in msg.text
