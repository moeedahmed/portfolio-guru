import re
import shutil
import subprocess
import sys
import textwrap
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

# Invented case only. All modalities contain the same complete CBD evidence;
# the caption contains no case detail, so it cannot mask a failed media read.
SYNTHETIC_CASE = (
    "Synthetic training case. "
    "On 17 March 2026 in ED resus I reviewed a 68-year-old adult with chest pain. "
    "I took a focused history and examination, arranged ECG and serial troponins, "
    "and discussed diagnostic uncertainty with my senior registrar. "
    "Level of supervision: indirect. I delegated tasks to the nursing team "
    "and explained the plan and safety-netting to the patient. "
    "Reflection: earlier escalation and clearer team briefing improved care; "
    "I will apply this approach to future cases."
)


def _synthetic_photo(path):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1400, 1000), "#fffdf3")
    draw = ImageDraw.Draw(image)
    # Comic Sans is installed on macOS; an oblique font is a portable fallback.
    for font_name in ("/System/Library/Fonts/Supplemental/Comic Sans MS.ttf", "DejaVuSans-Oblique.ttf"):
        try:
            font = ImageFont.truetype(font_name, 30)
            break
        except OSError:
            continue
    else:
        font = ImageFont.load_default(size=30)
    for index, line in enumerate(textwrap.wrap(SYNTHETIC_CASE, 72)):
        y = 60 + index * 58
        draw.line((40, y + 40, 1360, y + 40), fill="#d6e0ee", width=2)
        draw.text((60 + (index % 3) * 4, y), line, fill="#163c70", font=font)
    image.save(path, "JPEG", quality=95)
    return path


def _synthetic_pdf(path):
    # One deterministic text PDF, using only the standard library. Offsets in
    # the xref table are byte offsets; content is ASCII with PDF escaping.
    lines = textwrap.wrap(SYNTHETIC_CASE, 80)
    content = b"BT /F1 12 Tf 50 780 Td 18 TL\n"
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content += f"({escaped}) Tj T*\n".encode("ascii")
    content += b"ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n"
    pdf += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    pdf += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(pdf)
    return path


def _synthetic_voice(path):
    say, ffmpeg = shutil.which("say"), shutil.which("ffmpeg")
    if not say or not ffmpeg:
        pytest.skip("Synthetic voice requires local macOS say and ffmpeg with libopus; no voice was sent")
    source = path.with_suffix(".aiff")
    try:
        subprocess.run([say, "-v", "Samantha", "-r", "175", "-o", str(source), SYNTHETIC_CASE],
                       check=True, capture_output=True, timeout=60)
        subprocess.run([ffmpeg, "-nostdin", "-y", "-i", str(source), "-map_metadata", "-1",
                        "-fflags", "+bitexact", "-flags:a", "+bitexact",
                        "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "32k", str(path)],
                       check=True, capture_output=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        tool = str(exc.cmd[0]).rsplit("/", 1)[-1]
        pytest.skip(f"Local {tool} synthetic voice generation failed ({type(exc).__name__}); no voice was sent")
    assert path.stat().st_size > 0, "Synthetic voice clip is empty"
    return path


def _payload(button):
    data = button.data.decode() if isinstance(button.data, bytes) else button.data
    return unstamp(data)


async def _wider_wait(client, transcript, action, *, before=None, min_id=None, **expect):
    exchange = TelegramExchange(step="wider-journey", action=action, received="Response unconfirmed")
    transcript.append(exchange)
    reply = await wait_for_matching_message(
        client, BOT_USERNAME, 180, min_id=min_id if before is None else before[0],
        reject_fingerprint=before, **expect,
    )
    exchange.received = reply.raw_text or ""
    exchange.buttons = button_texts(reply)
    return reply


async def _wider_click(client, transcript, message, payload, **expect):
    # Explicit safe navigation/read controls only. Save is observed, never clicked.
    assert payload in {
        "DOCUSE|info", "GATHER|done", "FORM|CBD", "FORM|CBD_2021", "FORM|best",
        "ACTION|cancel", "CANCEL|draft", "ACTION|portfolio_defaults",
        "REMIND|menu", "ACTION|voice", "ACTION|settings", "VOICE|back_to_settings",
        "ACTION|change_level", "ACTION|change_pathway", "ACTION|change_curriculum",
        "VOICE|path_manual", "VOICE|back_to_choice",
    }, "Protected or unreviewed control"
    button = next((b for row in (message.buttons or []) for b in row if _payload(b) == payload), None)
    assert button is not None, f"Expected safe control {payload} in {button_texts(message)!r}"
    transcript.append(TelegramExchange(step="click", action=f"click:{payload}", received="Click attempted",
                                       clicked_button=button.text))
    fingerprint = message_fingerprint(message)
    await button.click()
    return await _wider_wait(client, transcript, f"after:{payload}", before=fingerprint, **expect)


async def _wider_before_send(client):
    # Observe the current bubble without changing bot state. Its fingerprint
    # also permits a response that edits that same bubble in place.
    messages = await client.get_messages(BOT_USERNAME, limit=1)
    return message_fingerprint(messages[0]) if messages else None


async def _wider_cleanup(client, transcript, name):
    original_failure = sys.exception()
    try:
        assert_live_telegram_guardrails(BOT_USERNAME)
        sent = await client.send_message(BOT_USERNAME, "/cancel")
        await _wider_wait(client, transcript, "cleanup:/cancel", min_id=sent.id, expect_text_any=("cancelled",))
    except Exception as exc:
        transcript.append(TelegramExchange(step="cleanup", action="send:/cancel",
                                           received=f"Cancellation unconfirmed: {type(exc).__name__}"))
        if original_failure is None:
            raise
        original_failure.add_note(f"Cleanup unconfirmed: {type(exc).__name__}")
    finally:
        write_transcript_artifact(transcript, filename=f"portfolio-guru-{name}-transcript.json")


def _assert_ready_review(reply):
    assert classify_post_click_draft_state(reply) == "ready", "Expected ready draft review"
    buttons = [b for row in (reply.buttons or []) for b in row]
    assert len(buttons) == 2 and any(_payload(b) == "APPROVE|draft" for b in buttons), "Save boundary not observed"
    cancel = next((b for b in buttons if _payload(b) in {"ACTION|cancel", "CANCEL|draft"}), None)
    assert cancel and "cancel" in cancel.text.lower(), "Safe Cancel control missing"


def _preview_date(reply):
    match = re.search(r"^.*?Date:\s*([^\n]+)", (reply.raw_text or "").replace("*", ""), re.M)
    assert match, "Encounter date missing from preview"
    return match.group(1).strip()


async def _media_ready_draft_to_cancel(client, path, kind):
    assert_live_telegram_guardrails(BOT_USERNAME)
    transcript = []
    try:
        before = await _wider_before_send(client)
        transcript.append(TelegramExchange(step="capture", action=f"send:{kind}", received="Capture attempted"))
        if kind == "text":
            sent = await client.send_message(BOT_USERNAME, SYNTHETIC_CASE)
        else:
            sent = await client.send_file(
                BOT_USERNAME, str(path), voice_note=kind == "voice", force_document=kind == "document",
                caption=None if kind == "voice" else "Synthetic training note only.",
            )
        capture_controls = dict(expect_buttons=True, expect_button_any=("Read text", "Use as case", "Choose form", "CBD", "Case-based discussion"))
        reply = await _wider_wait(client, transcript, f"after:{kind}", before=before, min_id=sent.id, **capture_controls)
        if any(_payload(b) == "DOCUSE|info" for row in (reply.buttons or []) for b in row):
            reply = await _wider_click(client, transcript, reply, "DOCUSE|info", **capture_controls)
        # Choose form must be observed. Gathering-off or a direct-form shortcut
        # is incomplete proof, rather than a silently shortened journey.
        reply = await _wider_click(client, transcript, reply, "GATHER|done",
                                   expect_buttons=True, expect_button_any=("CBD", "Case-based discussion", "Forms", "Restart"))
        form = next((_payload(b) for row in (reply.buttons or []) for b in row
                     if _payload(b) in {"FORM|CBD", "FORM|CBD_2021", "FORM|best"}), None)
        assert form, "CBD choice missing"
        reply = await _wider_click(client, transcript, reply, form,
                                   expect_buttons=True, expect_button_any=("Save to Kaizen", "Save draft to Kaizen"))
        if classify_post_click_draft_state(reply) == "draft_with_gaps":
            assert re.search(r"still needed:\s*level of supervision\.\s*reply", reply.raw_text or "", re.I), "Unreviewed draft gap"
            detail = "Level of supervision: indirect. I discussed the case with my senior registrar."
            fingerprint = message_fingerprint(reply)
            await client.send_message(BOT_USERNAME, detail)
            reply = await _wider_wait(client, transcript, f"send:{detail}", before=fingerprint,
                                      expect_buttons=True, expect_button_any=("Save to Kaizen",))
        _assert_ready_review(reply)
        original_date = _preview_date(reply)
        assert original_date != "18 Mar 2026", "Edit must change the encounter date"
        # Current bot has no Edit button: its own review hint directs the
        # doctor to reply with a correction, handled by handle_edit_value.
        assert "reply" in (reply.raw_text or "").lower(), "Bot's reply-to-edit control missing"
        correction = "Change only the encounter date to 18 March 2026. Keep all other fields unchanged."
        sent = await client.send_message(BOT_USERNAME, correction, reply_to=reply.id)
        refreshed = await _wider_wait(client, transcript, f"edit:{correction}", before=message_fingerprint(reply),
                                      expect_buttons=True, expect_button_any=("Save to Kaizen",))
        _assert_ready_review(refreshed)
        assert _preview_date(refreshed) == "18 Mar 2026", "Encounter date change missing from refreshed preview"
        if kind == "text":
            selections = parse_visible_kc_selections(refreshed.raw_text or "")
            assert len(selections) == len(set(selections)) == 3, "Expected three distinct visible KCs"
        # Stop at the refreshed preview. No Save or additional Cancel click.
    finally:
        await _wider_cleanup(client, transcript, kind)


@pytest.mark.asyncio
async def test_e2e_photo_ready_draft_to_cancel_journey(telethon_client, tmp_path):
    await _media_ready_draft_to_cancel(telethon_client, _synthetic_photo(tmp_path / "synthetic-note.jpg"), "photo")


@pytest.mark.asyncio
async def test_e2e_voice_ready_draft_to_cancel_journey(telethon_client, tmp_path):
    await _media_ready_draft_to_cancel(telethon_client, _synthetic_voice(tmp_path / "synthetic-note.ogg"), "voice")


@pytest.mark.asyncio
async def test_e2e_document_ready_draft_to_cancel_journey(telethon_client, tmp_path):
    await _media_ready_draft_to_cancel(telethon_client, _synthetic_pdf(tmp_path / "synthetic-summary.pdf"), "document")


@pytest.mark.asyncio
async def test_e2e_settings_read_only_journey(telethon_client):
    assert_live_telegram_guardrails(BOT_USERNAME)
    transcript = []
    try:
        sent = await telethon_client.send_message(BOT_USERNAME, "/settings")
        settings = await _wider_wait(telethon_client, transcript, "send:/settings", min_id=sent.id,
                                     expect_text_any=("Settings",), expect_buttons=True)
        defaults = await _wider_click(telethon_client, transcript, settings, "ACTION|portfolio_defaults",
                                       expect_text_any=("Portfolio defaults",), expect_button_any=("Back",))
        for payload, title in (
            ("ACTION|change_level", "portfolio"),
            ("ACTION|change_pathway", "pathway"),
            ("ACTION|change_curriculum", "curriculum"),
        ):
            picker = await _wider_click(telethon_client, transcript, defaults, payload,
                                       expect_text_any=(title,), expect_button_any=("Back",))
            assert title in picker.raw_text.lower()
            defaults = await _wider_click(telethon_client, transcript, picker, "ACTION|portfolio_defaults",
                                         expect_text_any=("Portfolio defaults",), expect_button_any=("Back",))
        settings = await _wider_click(telethon_client, transcript, defaults, "ACTION|settings",
                                     expect_text_any=("Settings",), expect_buttons=True)
        reminders = await _wider_click(telethon_client, transcript, settings, "REMIND|menu",
                                      expect_text_any=("Reminders",), expect_button_any=("Back",))
        settings = await _wider_click(telethon_client, transcript, reminders, "ACTION|settings",
                                     expect_text_any=("Settings",), expect_buttons=True)
        sources = await _wider_click(telethon_client, transcript, settings, "ACTION|voice",
                                    expect_text_any=("Writing style",), expect_button_any=("Back",))
        # Manual examples is an empty input view. Kaizen entries is NOT merely
        # a view: it reads entries and builds/activates a profile on success.
        # Observe that source, but never click it in a read-only journey.
        assert {"VOICE|path_manual", "VOICE|path_kaizen"} <= {
            _payload(b) for row in (sources.buttons or []) for b in row
        }, "Writing style sources missing"
        manual = await _wider_click(telethon_client, transcript, sources, "VOICE|path_manual",
                                   expect_text_any=("Add examples manually",), expect_button_any=("Back",))
        sources = await _wider_click(telethon_client, transcript, manual, "VOICE|back_to_choice",
                                    expect_text_any=("Writing style",), expect_button_any=("Back",))
        settings = await _wider_click(telethon_client, transcript, sources, "VOICE|back_to_settings",
                                     expect_text_any=("Settings",), expect_buttons=True)
        assert "settings" in settings.raw_text.lower()

    finally:
        await _wider_cleanup(telethon_client, transcript, "settings")


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
async def test_e2e_text_ready_draft_to_cancel_journey(telethon_client):
    """Capture -> Choose form -> CBD -> review -> reply-to-edit -> review.

    Stop before Save and issue exactly one cleanup /cancel, including failures.
    """
    await _media_ready_draft_to_cancel(telethon_client, None, "text")


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
