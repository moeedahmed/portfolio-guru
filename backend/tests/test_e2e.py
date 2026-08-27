import pytest
import pytest_asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

from tests.telegram_live_harness import (
    TelegramStep,
    assert_live_telegram_guardrails,
    assert_transcript_is_sensible,
    has_telethon_env,
    run_telegram_workflow,
    telethon_env,
    wait_for_matching_message,
)


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not has_telethon_env(),
        reason="Telethon credentials not configured",
    ),
]


BOT_USERNAME = telethon_env()["bot_username"]


@pytest_asyncio.fixture
async def telethon_client():
    assert_live_telegram_guardrails()
    env = telethon_env()
    if not env["session"] or not env["api_id"] or not env["api_hash"]:
        pytest.skip("Telethon session or API hash not configured")

    client = TelegramClient(
        StringSession(env["session"]),
        int(env["api_id"]),
        env["api_hash"],
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
async def test_e2e_case_text_enters_draft_flow(telethon_client):
    # Reset the conversation first. This is the release gate's only live proof
    # and it runs on its own, so it inherits whatever state the chat was left
    # in — a half-finished /health, an open settings menu, a pending prompt. On
    # 2026-08-27 it failed for exactly that: the case text arrived, the bot
    # replied once, and the draft offer never came because the chat was not at
    # the start of a case journey. A user opening a fresh case is the journey
    # worth proving, so the test has to start there rather than wherever the
    # last human left off.
    async with telethon_client.conversation(BOT_USERNAME, timeout=60) as reset:
        await reset.send_message("/cancel")
        await reset.get_response()

    async with telethon_client.conversation(BOT_USERNAME, timeout=180) as conv:
        sent = await conv.send_message(
            "I reviewed an emergency department patient with acute chest pain. "
            "I took a focused history and examination, reviewed the ECG, arranged serial troponins, "
            "discussed the case with a senior, documented safety-netting, and made an appropriate referral. "
            "The patient remained stable, and I learned to document risk stratification more clearly."
        )
        reply = await wait_for_matching_message(
            telethon_client,
            BOT_USERNAME,
            # Live extraction is a real Vertex call, not the mock the offline
            # transcript uses.
            180,
            expect_buttons=True,
            expect_button_any=("Draft now",),
            min_id=getattr(sent, "id", None),
        )

    buttons = [button.text for row in (reply.buttons or []) for button in row]
    assert any("Draft now" in text for text in buttons)


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
        reply = await conv.get_response()

    assert "Kaizen username" in reply.raw_text or "username" in reply.raw_text.lower()


@pytest.mark.asyncio
async def test_e2e_realistic_case_workflow_is_sensible(telethon_client):
    transcript = await run_telegram_workflow(
        telethon_client,
        BOT_USERNAME,
        [
            TelegramStep(
                name="reset",
                message="/cancel",
                expect_text_any=("cancel", "ready", "connect", "file"),
                timeout_seconds=60,
            ),
            TelegramStep(
                name="clinical-case",
                message=(
                    "Adult ED case: 42M with pleuritic chest pain, normal ECG, "
                    "negative troponin, discharged with safety-netting after senior discussion."
                ),
                expect_text_any=("draft", "form", "case", "CBD", "DOPS", "Mini-CEX"),
                expect_button_any=("CBD", "Use best fit", "See all forms"),
                click_button_any=("Use best fit", "CBD", "Case-Based"),
                expect_after_click_text_any=("draft", "case", "CBD", "reflection", "portfolio"),
                expect_after_click_button_any=("Regenerate", "Save", "Edit", "Copy", "Back"),
                timeout_seconds=120,
            ),
        ],
    )
    assert_transcript_is_sensible(transcript)
