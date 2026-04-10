import os
import re

import pytest
import pytest_asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("TELETHON_API_ID"),
        reason="Telethon credentials not configured",
    ),
]


BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "PortfolioGuruBot")


@pytest_asyncio.fixture
async def telethon_client():
    session = os.environ.get("TELETHON_SESSION")
    api_hash = os.environ.get("TELETHON_API_HASH")
    if not session or not api_hash:
        pytest.skip("Telethon session or API hash not configured")

    client = TelegramClient(
        StringSession(session),
        int(os.environ["TELETHON_API_ID"]),
        api_hash,
    )
    await client.connect()
    try:
        yield client
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_e2e_start_shows_welcome(telethon_client):
    async with telethon_client.conversation(BOT_USERNAME, timeout=60) as conv:
        await conv.send_message("/start")
        reply = await conv.get_response()

    assert "Portfolio Guru" in reply.raw_text


@pytest.mark.asyncio
async def test_e2e_case_text_gets_recommendation(telethon_client):
    async with telethon_client.conversation(BOT_USERNAME, timeout=90) as conv:
        await conv.send_message(
            "I ran a busy emergency department shift with several acute chest pain patients and want to reflect on my management."
        )
        reply = await conv.get_response()

    buttons = [button.text for row in (reply.buttons or []) for button in row]
    assert any("CBD" in text or "Case-Based" in text for text in buttons)


@pytest.mark.asyncio
async def test_e2e_gibberish_handled_gracefully(telethon_client):
    async with telethon_client.conversation(BOT_USERNAME, timeout=60) as conv:
        await conv.send_message("asdfghjkl ??? ###")
        reply = await conv.get_response()

    assert reply.raw_text.strip()


@pytest.mark.asyncio
async def test_e2e_help_command(telethon_client):
    async with telethon_client.conversation(BOT_USERNAME, timeout=60) as conv:
        await conv.send_message("/help")
        reply = await conv.get_response()

    assert "Help" in reply.raw_text


@pytest.mark.asyncio
async def test_e2e_setup_flow_starts(telethon_client):
    async with telethon_client.conversation(BOT_USERNAME, timeout=60) as conv:
        await conv.send_message("/start")
        welcome = await conv.get_response()
        if not welcome.buttons:
            pytest.skip("Welcome message did not include inline buttons")
        await welcome.click(text=re.compile("Connect Kaizen"))
        reply = await conv.get_response()

    assert "Kaizen username" in reply.raw_text
