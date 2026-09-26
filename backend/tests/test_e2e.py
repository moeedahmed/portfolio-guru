import re
import sys
import pytest
import pytest_asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from tests.helpers import unstamp

from tests.telegram_live_harness import (
    TelegramExchange,
    assert_live_telegram_guardrails,
    button_texts,
    classify_post_click_draft_state,
    has_telethon_env,
    message_fingerprint,
    parse_visible_kc_selections,
    telethon_env,
    wait_for_matching_message,
    write_transcript_artifact,
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
async def test_e2e_cbd_ready_draft_to_cancel_journey(telethon_client):
    """One synthetic draft-first case, one bounded supervision reply if needed,
    then exact review controls, three distinct KCs and confirmed cancellation.

    Never saves to Kaizen. A failed assertion also triggers /cancel cleanup,
    and every captured exchange is retained on success or failure.
    """
    transcript: list[TelegramExchange] = []
    cancel_confirmed = False
    try:
        async with telethon_client.conversation(BOT_USERNAME, timeout=60) as reset:
            await reset.send_message("/cancel")
            reset_reply = await reset.get_response()
        transcript.append(
            TelegramExchange(
                step="reset",
                action="send:/cancel",
                received=reset_reply.raw_text or "",
                buttons=button_texts(reset_reply),
            )
        )
        # cancel_command's _cancelled_next_step_text always includes "Cancelled",
        # connected or not — the one recognised clean-state response this
        # journey may proceed from.
        assert "cancelled" in (reset_reply.raw_text or "").lower(), (
            f"expected a recognised clean-state response to /cancel; got {reset_reply.raw_text!r}"
        )
        before_case_send = message_fingerprint(reset_reply)

        # Use the full primary form name. A bare "CBD" is a secondary code, while
        # "resus case" can contain the phrase "us case"; the full name makes this
        # proof deterministic without relying on either ambiguous short match.
        case_text = (
            "Please file this as a case-based discussion. I reviewed a 68-year-old man with acute chest pain "
            "and known COPD in resus. I took a focused history and examination, recognised "
            "early signs of decompensation, and escalated promptly to my senior registrar "
            "rather than managing alone given the diagnostic uncertainty. I led the immediate "
            "resuscitation, delegating tasks to the nursing team and briefing them clearly on "
            "the working diagnosis and plan. Serial troponins and a repeat ECG were arranged, "
            "and I discussed the differential openly with the patient and his wife, explaining "
            "the uncertainty and safety-netting advice in plain language before a stable "
            "discharge. I reflected afterwards on how earlier escalation and clearer team "
            "briefing improved the outcome, and I plan to apply the same approach to future "
            "complex resus cases."
        )

        async with telethon_client.conversation(BOT_USERNAME, timeout=60) as conv:
            await conv.send_message(case_text)

        # Live extraction is a real Vertex call, not the mock the offline
        # transcript uses. Reject the pre-send fingerprint rather than relying
        # on id ordering alone: the bot may edit the reset reply in place
        # (same id) once it processes the case.
        form_choice = await wait_for_matching_message(
            telethon_client,
            BOT_USERNAME,
            180,
            expect_buttons=True,
            expect_button_any=("CBD",),
            min_id=getattr(reset_reply, "id", None),
            reject_fingerprint=before_case_send,
        )
        transcript.append(
            TelegramExchange(
                step="case",
                action=f"send:{case_text}",
                received=form_choice.raw_text or "",
                buttons=button_texts(form_choice),
            )
        )
        before_form_click = message_fingerprint(form_choice)

        cbd_button = next(
            (
                button
                for row in (form_choice.buttons or [])
                for button in row
                if "cbd" in (getattr(button, "text", "") or "").lower()
            ),
            None,
        )
        assert cbd_button is not None, f"no CBD button offered: {button_texts(form_choice)!r}"
        clicked_form_button_text = cbd_button.text
        form_payload = cbd_button.data.decode() if isinstance(cbd_button.data, bytes) else cbd_button.data
        assert form_payload in {"FORM|CBD", "FORM|CBD_2021", "FORM|best"}, "Unreviewed clinical form control"
        await cbd_button.click()

        # Require a genuinely changed draft-first preview, including its gap
        # list when the doctor has not supplied a required detail.
        post_click = await wait_for_matching_message(
            telethon_client,
            BOT_USERNAME,
            180,
            expect_buttons=True,
            min_id=getattr(form_choice, "id", None),
            reject_fingerprint=before_form_click,
        )
        transcript.append(
            TelegramExchange(
                step="case",
                action="click_button",
                received=post_click.raw_text or "",
                buttons=button_texts(post_click),
                clicked_button=clicked_form_button_text,
            )
        )

        post_click_state = classify_post_click_draft_state(post_click)

        assert post_click_state != "missing_essentials", "expected a draft-first preview, not the retired ask-first prompt"
        if post_click_state == "draft_with_gaps":
            assert re.search(r"still needed:\s*level of supervision\.\s*reply", post_click.raw_text or "", re.IGNORECASE), (
                "this bounded journey supplies only a missing Level of Supervision; "
                f"got {post_click.raw_text!r}"
            )
            before_detail_reply = message_fingerprint(post_click)
            detail_text = (
                "Level of supervision: indirect. The case was discussed with my senior registrar."
            )
            async with telethon_client.conversation(BOT_USERNAME, timeout=60) as conv:
                await conv.send_message(detail_text)

            ready_draft = await wait_for_matching_message(
                telethon_client,
                BOT_USERNAME,
                180,
                expect_buttons=True,
                expect_button_any=("Save to Kaizen",),
                min_id=getattr(post_click, "id", None),
                reject_fingerprint=before_detail_reply,
            )
            transcript.append(
                TelegramExchange(
                    step="draft-gaps",
                    action=f"send:{detail_text}",
                    received=ready_draft.raw_text or "",
                    buttons=button_texts(ready_draft),
                )
            )
            assert classify_post_click_draft_state(ready_draft) == "ready", (
                "expected the ready draft after supplying Level of Supervision; got "
                f"text={ready_draft.raw_text!r} buttons={button_texts(ready_draft)!r}"
            )
        else:
            ready_draft = post_click

        assert any(unstamp(b.data.decode() if isinstance(b.data, bytes) else b.data) == "APPROVE|draft"
                   for row in ready_draft.buttons for b in row), "Save boundary payload not observed"
        draft_buttons = button_texts(ready_draft)
        assert any("save to kaizen" in text.lower() for text in draft_buttons), draft_buttons
        assert any("cancel" in text.lower() for text in draft_buttons), draft_buttons
        assert len(draft_buttons) == 2, (
            f"the ready draft must offer exactly Save to Kaizen and Cancel; got {draft_buttons!r}"
        )

        kc_selections = parse_visible_kc_selections(ready_draft.raw_text or "")
        distinct_kc_selections = set(kc_selections)
        assert len(kc_selections) == 3 and len(distinct_kc_selections) == 3, (
            "expected exactly three distinct, visible KC child selections under their "
            f"visible parent SLOs; got {kc_selections!r} in draft text {ready_draft.raw_text!r}"
        )

        before_cancel_click = message_fingerprint(ready_draft)
        cancel_button = next(
            (
                button
                for row in (ready_draft.buttons or [])
                for button in row
                if "cancel" in (getattr(button, "text", "") or "").lower()
            ),
            None,
        )
        assert cancel_button is not None, f"no Cancel button on ready draft: {draft_buttons!r}"
        clicked_cancel_button_text = cancel_button.text
        cancel_payload = cancel_button.data.decode() if isinstance(cancel_button.data, bytes) else cancel_button.data
        assert unstamp(cancel_payload) in {"ACTION|cancel", "CANCEL|draft"}, "Protected or unknown control labelled Cancel"
        await cancel_button.click()

        cancelled = await wait_for_matching_message(
            telethon_client,
            BOT_USERNAME,
            60,
            expect_text_any=("cancelled",),
            min_id=getattr(ready_draft, "id", None),
            reject_fingerprint=before_cancel_click,
        )
        transcript.append(
            TelegramExchange(
                step="cancel",
                action="click_button",
                received=cancelled.raw_text or "",
                buttons=button_texts(cancelled),
                clicked_button=clicked_cancel_button_text,
            )
        )
        assert "cancelled" in (cancelled.raw_text or "").lower()
        cancel_confirmed = True
    finally:
        original_failure = sys.exception()
        try:
            if not cancel_confirmed:
                cleanup = TelegramExchange(
                    step="cleanup", action="send:/cancel",
                    received="Cancellation unconfirmed; cleanup attempted.",
                )
                transcript.append(cleanup)
                try:
                    sent = await telethon_client.send_message(BOT_USERNAME, "/cancel")
                    cleaned = await wait_for_matching_message(
                        telethon_client, BOT_USERNAME, 60,
                        expect_text_any=("cancelled",), min_id=sent.id,
                    )
                    assert "cancelled" in (cleaned.raw_text or "").lower(), "synthetic journey cleanup was not confirmed"
                    cleanup.received = cleaned.raw_text or ""
                    cleanup.buttons = button_texts(cleaned)
                except Exception as cleanup_error:
                    cleanup.received = f"Cancellation unconfirmed: {type(cleanup_error).__name__} during cleanup."
                    if original_failure is None:
                        raise
                    original_failure.add_note(cleanup.received)
        finally:
            write_transcript_artifact(transcript)


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
