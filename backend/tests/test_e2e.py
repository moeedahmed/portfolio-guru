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
    allowed_bot_usernames,
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

# Categories and labels are from bot.FORM_CATEGORIES/_CAT_SLUGS and
# FORM_BUTTON_LABELS. These invented cases contain no identifying details.
# Five cases try a matching recommendation; four always use Forms.
FORM_VARIETY_CASES = {
    "LAT": ("MANAGEMENT", "LAT", True,
            "I led an ED evening shift, allocated staff, chaired safety huddles and escalated crowding. "
            "Leadership context: coordinating the multidisciplinary team under indirect supervision. "
            "I prioritised resus cover to reduce delays. Reflection: early task allocation helped; I will repeat the huddles."),
    "TEACH": ("TEACHING", "Teaching Session", False,
              "I delivered a twenty-minute teaching session to junior doctors in ED. Title: Safe handover. "
              "Learning outcomes: structure an SBAR handover and identify escalation triggers. "
              "Learners practised scenarios and gave positive feedback. Reflection: rehearsal helped; I will allow more discussion time."),
    "QIAT": ("QUALITY", "QIAT", True,
             "Stage of Training: ST4 (Higher). In my Emergency Medicine placement I led a QI project on equipment checks. "
             "My QI PDP goal was reliable daily checks; I attended local QI education. "
             "I was involved in the project: baseline measurement, process mapping, PDSA and remeasurement. "
             "Checks improved from 60 to 90 percent. Reflection: staff feedback simplified the checklist. "
             "Next year's PDP: sustain monthly measurement and share learning."),
    "MGMT_ROTA": ("MANAGEMENT", "Rota", False,
                  "I coordinated an ED rota, reviewed staffing gaps with the duty consultant and arranged fair cover. "
                  "I checked rest periods and communicated the revised schedule. "
                  "Reflection: earlier consultation reduced last-minute changes; I will review gaps weekly."),
    "SERIOUS_INC": ("REFLECTIVE", "Serious Incident", True,
                    "I reflected on a simulated serious incident in ED: delayed recognition of a deteriorating adult. "
                    "An escalation call was missed; root cause was unclear escalation ownership. "
                    "Contributing factors were workload and an incomplete handover. "
                    "Learning: assign escalation explicitly. Further action: practise closed-loop communication at the next simulation."),
    "PROC_LOG": ("PROCEDURAL", "Procedural Log", False,
                 "In ED resus I inserted a chest drain in a simulated adult with pneumothorax, under direct supervision. "
                 "I checked consent, equipment and sterile technique, inserted the drain and confirmed its position. "
                 "Reflective comments: preparing equipment improved flow; I will rehearse the safety checklist."),
    "US_CASE": ("PROCEDURAL", "Ultrasound Case", True,
                "I performed point-of-care ultrasound in an ED simulation for an adult with shock. "
                "I obtained cardiac views under direct supervision, found a pericardial effusion and escalated to the senior clinician. "
                "Reflection: integrating images with the examination improved decisions; I will practise subcostal views."),
    "FORMAL_COURSE": ("TEACHING", "Formal Course", False,
                      "I attended a simulation instructor course. Project description: designing and delivering an ED simulation. "
                      "Resources used: the course workbook, faculty demonstrations and debrief checklist. "
                      "Reflective notes: structured debrief improved participation. Lessons learned: use open questions; "
                      "I will apply them in my next teaching session."),
    "REFLECT_LOG": ("REFLECTIVE", "Reflective log", True,
                    "I reflected on a simulated ED handover where task ownership was unclear. "
                    "I clarified roles with the team and repeated the plan. "
                    "Reflection: closed-loop communication reduced confusion; I will confirm ownership at future handovers."),
}


def _form_variety_case(form_code):
    return "Synthetic training evidence only. On 17 March 2026. " + FORM_VARIETY_CASES[form_code][3]


FORM_VARIETY_PDF_CASE = (
    "Synthetic training evidence only. On 17 March 2026 I delivered ED teaching to junior doctors. "
    "Title: Team briefing. Learning outcomes: assign clear roles and use closed-loop communication. "
    "Learners practised a simulation and gave feedback. Reflection: practice clarified roles; "
    "I will allow more rehearsal time."
)

# Reviewed curriculum variants present in extractor.FORM_UUIDS. MGMT_ROTA
# has no 2021 variant; do not allow arbitrary FORM payloads or suffixes.
FORM_VARIETY_PAYLOADS = frozenset(
    {f"FORM|{code}" for code in FORM_VARIETY_CASES}
    | {f"FORM|{code}_2021" for code in FORM_VARIETY_CASES if code != "MGMT_ROTA"}
    | {f"FORM|cat_{spec[0]}" for spec in FORM_VARIETY_CASES.values()}
    | {"FORM|show_all"}
)

# Reviewed in handle_form_choice/handle_callback: menu edits, draft Cancel,
# recommendation Restart. No saving, setup, reset or preference changes.
FORM_SWITCHING_PAYLOADS = frozenset({
    "FORM|show_all", "FORM|back", "FORM|cat_CLINICAL", "FORM|cat_REFLECTIVE",
    "FORM|cat_TEACHING", "FORM|cat_PROCEDURAL", "FORM|cat_QUALITY", "FORM|cat_MANAGEMENT",
    "FORM|CBD", "FORM|CBD_2021", "FORM|MINI_CEX", "FORM|MINI_CEX_2021",
    "CANCEL|draft", "CANCEL|form",
})


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


def _synthetic_pdf(path, case_text=SYNTHETIC_CASE):
    # One deterministic text PDF, using only the standard library. Offsets in
    # the xref table are byte offsets; content is ASCII with PDF escaping.
    lines = textwrap.wrap(case_text, 80)
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
    assert payload in FORM_VARIETY_PAYLOADS | FORM_SWITCHING_PAYLOADS | {
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


async def _form_variety_ready_draft_to_cancel(client, form_code, case_text, *, prefer_recommendation, document=None):
    assert_live_telegram_guardrails(BOT_USERNAME)
    assert BOT_USERNAME == "portfolio_guru_test_bot" and allowed_bot_usernames() == {BOT_USERNAME}, "Form variety is test-bot-only"
    transcript = []
    name = f"form-variety-{form_code}" + ("-pdf" if document is not None else "")
    try:
        category, label, _, _ = FORM_VARIETY_CASES[form_code]
        targets = {payload for payload in FORM_VARIETY_PAYLOADS
                   if payload in {f"FORM|{form_code}", f"FORM|{form_code}_2021"}}
        before = await _wider_before_send(client)
        transcript.append(TelegramExchange(step="capture", action=f"send:{name}", received="Capture attempted"))
        if document is None:
            sent = await client.send_message(BOT_USERNAME, case_text)
        else:
            sent = await client.send_file(BOT_USERNAME, str(document), force_document=True,
                                          caption="Synthetic training note only.")
        reply = await _wider_wait(client, transcript, f"after:{name}", before=before, min_id=sent.id,
                                  expect_buttons=True, expect_button_any=("Use text as case", "Choose form"))
        if any(_payload(b) == "DOCUSE|info" for row in (reply.buttons or []) for b in row):
            reply = await _wider_click(client, transcript, reply, "DOCUSE|info",
                                       expect_buttons=True, expect_button_any=("Choose form",))
        reply = await _wider_click(client, transcript, reply, "GATHER|done",
                                   expect_buttons=True, expect_button_any=(label, "Forms"))
        form = None
        if prefer_recommendation:
            for row in (reply.buttons or []):
                for button in row:
                    payload = _payload(button)
                    # best does not name a form in its payload: require the
                    # exact target label, with only its leading emoji removed.
                    visible_label = re.sub(r"^[^\w]+", "", button.text).strip()
                    if payload in targets or (payload == "FORM|best" and visible_label == label):
                        form = payload
                        break
                if form:
                    break
        if form is None:
            reply = await _wider_click(client, transcript, reply, "FORM|show_all", expect_buttons=True)
            reply = await _wider_click(client, transcript, reply, f"FORM|cat_{category}",
                                       expect_buttons=True, expect_button_any=(label,))
            form = next((_payload(b) for row in (reply.buttons or []) for b in row if _payload(b) in targets), None)
            assert form, f"Target form {form_code} missing from Forms list"
        reply = await _wider_click(client, transcript, reply, form,
                                   expect_buttons=True, expect_button_any=("Save to Kaizen",))
        if classify_post_click_draft_state(reply) == "draft_with_gaps":
            # Only the two existing gap labels are reviewed. Match the entire
            # list, not a known prefix that could hide an unknown extra gap.
            known = r"(?:level of supervision|your reflection \(what you learned or would do differently\))"
            assert re.search(rf"still needed:\s*{known}(?:(?:, | and ){known})*\.\s*reply\b",
                             reply.raw_text or "", re.I), "Unreviewed draft gap"
            detail = ("Level of supervision: indirect. I discussed this activity with my senior. "
                      "What I learned: clear roles helped the team; I will confirm responsibilities next time.")
            fingerprint = message_fingerprint(reply)
            await client.send_message(BOT_USERNAME, detail)
            reply = await _wider_wait(client, transcript, f"send:{detail}", before=fingerprint,
                                      expect_buttons=True, expect_button_any=("Save to Kaizen",))
        _assert_ready_review(reply)
        # Ready preview is the end of this journey. Save is never clicked.
    finally:
        await _wider_cleanup(client, transcript, name)


@pytest.mark.asyncio
@pytest.mark.parametrize("form_code", FORM_VARIETY_CASES, ids=list(FORM_VARIETY_CASES))
async def test_e2e_form_variety_ready_draft_to_cancel_journey(telethon_client, form_code):
    await _form_variety_ready_draft_to_cancel(
        telethon_client, form_code, _form_variety_case(form_code),
        prefer_recommendation=FORM_VARIETY_CASES[form_code][2])


@pytest.mark.asyncio
async def test_e2e_form_variety_pdf_ready_draft_to_cancel_journey(telethon_client, tmp_path):
    document = _synthetic_pdf(tmp_path / "synthetic-teaching.pdf", FORM_VARIETY_PDF_CASE)
    await _form_variety_ready_draft_to_cancel(
        telethon_client, "TEACH", FORM_VARIETY_PDF_CASE, prefer_recommendation=False, document=document)


def _screen_controls(message):
    return [(b.text, _payload(b)) for row in (message.buttons or []) for b in row]


def _assert_form_switching_screen(reply, history, previous, state, *, snapshot=None, form=None):
    """Screen contract from bot.py, checking payloads as well as visible labels.

    Shared navigation (e.g. Forms) may legitimately recur; the complete new
    keyboard must match its screen, and every older keyboard must be retired.
    """
    import bot

    text = (reply.raw_text or "").strip()
    controls = _screen_controls(reply)
    payloads = [p for _, p in controls]
    assert text, "Empty form-switching screen"
    assert len(payloads) == len(set(payloads)), "Duplicate screen controls"
    if state == "idle":
        assert not controls, "Stale buttons after cancellation"
        assert "cancelled" in text.lower() and "send an anonymised case" in text.lower(), "Dead end after cancellation"
    else:
        assert controls, f"Dead end on {state}"
        if state == "capture":
            expected = [("📋 Choose form", "GATHER|done"), ("❌ Cancel", "ACTION|cancel")]
            assert "captured" in text.lower(), "Wrong capture screen"
        elif state == "recommendation":
            assert re.search(r"best fit|which form|form.*(?:entry|draft|create)|pick a form", text, re.I), "Wrong recommendation text"
            assert {"FORM|show_all", "CANCEL|form"} <= set(payloads), "Dead end on recommendation"
            assert all(p in {"FORM|show_all", "CANCEL|form", "FORM|best", "FORM|disabled"}
                       or (p.startswith("FORM|") and p[5:] in bot.FORM_UUIDS) for p in payloads), "Stale recommendation button"
            assert any(p == "FORM|best" or p[5:] in bot.FORM_UUIDS for p in payloads), "No suggested form"
            navigation = {"FORM|show_all": "📋 Forms", "CANCEL|form": "❌ Cancel"}
            for label, payload in controls:
                if payload in navigation:
                    assert label == navigation[payload], "Stale recommendation navigation label"
                    continue
                codes = bot.FORM_UUIDS if payload in {"FORM|best", "FORM|disabled"} else [payload[5:]]
                valid_labels = set()
                for code in codes:
                    base = code.removesuffix("_2021")
                    name = bot.FORM_BUTTON_LABELS.get(code) or bot.FORM_BUTTON_LABELS.get(base) or bot._recommendation_form_display_name(base)[:24]
                    emoji = bot.FORM_EMOJIS.get(code) or bot.FORM_EMOJIS.get(base, "📋")
                    if payload != "FORM|best" or bot._form_display_name(code).lower() in text.splitlines()[0].lower():
                        valid_labels.add(f"{emoji} {name}" + (" (soon)" if payload == "FORM|disabled" else ""))
                assert label in valid_labels, "Recommendation label does not match form"
            expected = controls if snapshot is None else snapshot[1]
            if snapshot is not None:
                assert text == snapshot[0], "Recommendation text was not restored"
        elif state == "categories":
            assert re.fullmatch(r"Browse supported forms \(202[15] curriculum\):", text), "Wrong category picker text"
            assert "FORM|back" in payloads, "Dead end: category Back missing"
            labels = {f"FORM|cat_{slug}": label for label, slug in bot._CAT_SLUGS.items()}
            assert all(p in labels or p == "FORM|back" for p in payloads), "Stale category button"
            assert any(p in labels for p in payloads), "Dead end: no offered categories"
            expected = [(labels.get(p, "📋 Suggested forms"), p) for p in payloads] if snapshot is None else snapshot[1]
            if snapshot is not None:
                assert text == snapshot[0], "Category picker text changed"
        elif state.startswith("category:"):
            slug = state.split(":", 1)[1]
            label = bot._SLUG_TO_CAT[slug]
            assert text == f"{label} — pick a form:", "Wrong category text"
            assert "FORM|show_all" in payloads, "Dead end: category Back missing"
            expected = []
            for _, payload in controls:
                if payload == "FORM|show_all":
                    expected.append(("📋 Forms", payload))
                    continue
                code = payload.removeprefix("FORM|")
                base = code.removesuffix("_2021")
                assert payload.startswith("FORM|") and base in bot.FORM_CATEGORIES[label] and code in bot.FORM_UUIDS, "Stale form button in category"
                name = bot.FORM_BUTTON_LABELS.get(code) or bot.FORM_BUTTON_LABELS.get(base) or bot._form_display_name(base)[:24]
                emoji = bot.FORM_EMOJIS.get(code) or bot.FORM_EMOJIS.get(base, "📋")
                expected.append((f"{emoji} {name}", payload))
            assert len(expected) > 1, "Dead end: empty offered category"
        elif state == "draft":
            assert form and bot._form_display_name(form).lower() in text.lower() and "draft" in text.lower(), "Wrong form draft"
            classification = classify_post_click_draft_state(reply)
            save_label = "💾 Save to Kaizen"
            expected = [(save_label, "APPROVE|draft"), ("❌ Cancel", "CANCEL|draft")]
            assert "CANCEL|draft" in payloads, "Dead end: draft Cancel missing"
        else:
            raise AssertionError(f"Unknown screen {state}")
        assert controls == expected, f"Stale or incorrect buttons on {state}: {controls!r}"

    inbound = [m for m in history if not getattr(m, "out", False)]
    observed = next((m for m in inbound if m.id == reply.id), None)
    assert observed is not None and (observed.raw_text, _screen_controls(observed)) == (reply.raw_text, controls), "Screen changed during observation"
    # Identical passive cancellation receipts from earlier recaptures are
    # legitimate. Menus/drafts must have only one copy, even if disarmed.
    if state != "idle":
        copies = [m for m in inbound if (m.raw_text or "").strip() == text]
        assert len(copies) == 1, f"Duplicate screen on {state}"
    assert all(not _screen_controls(m) for m in inbound if m.id != reply.id), "Stale buttons on previous screen"
    if previous is not None:
        same_id = reply.id == previous.id
        if state == "categories" or state.startswith("category:") or (state == "recommendation" and snapshot is not None):
            assert same_id, f"Expected menu edited in place on {state}"
        elif state == "idle":
            assert not same_id and reply.id > previous.id, "Expected replacement cancellation receipt"
        else:
            assert same_id or reply.id > previous.id, "Replacement went backwards"
        return "edited-in-place" if same_id else "replaced"
    return "captured"


async def _form_switching_to_cancel(client):
    assert_live_telegram_guardrails(BOT_USERNAME)
    assert BOT_USERNAME == "portfolio_guru_test_bot" and allowed_bot_usernames() == {BOT_USERNAME}, "Form switching is test-bot-only"
    transcript, floor = [], 0
    # Short, invented evidence reusable for CBD and Mini-CEX, including the
    # directly observed encounter needed by Mini-CEX. No real patient details.
    case = ("Synthetic training case. On 17 March 2026 in ED my senior directly observed my "
            "history, examination and management of a simulated adult with chest pain. "
            "I discussed ECG, troponins and diagnostic uncertainty. Supervision: direct. "
            "Reflection: early escalation helped; I will brief the team earlier next time.")

    async def check(reply, previous, state, **spec):
        history = await client.get_messages(BOT_USERNAME, limit=50)
        assert len(history) < 50 or history[-1].id <= floor, "Screen history ceiling: stale-button proof incomplete"
        transition = "captured" if previous is None else (
            "edited-in-place" if reply.id == previous.id else "replaced")
        transcript.append(TelegramExchange(step=f"screen:{transition}", action=state,
                                           received=reply.raw_text, buttons=button_texts(reply),
                                           message_id=reply.id,
                                           controls=[{"text": label, "payload": payload} for label, payload in _screen_controls(reply)]))
        _assert_form_switching_screen(reply, [m for m in history if m.id >= floor], previous, state, **spec)
        return reply

    async def tap(reply, payload, state, **spec):
        import bot
        assert payload in FORM_SWITCHING_PAYLOADS | {"GATHER|done"}, "Unreviewed switching control"
        text_tokens = {
            "recommendation": ("Best fit", "Which form", "for this entry"),
            "categories": ("Browse supported forms",),
            "draft": ("draft",), "idle": ("cancelled",),
        }
        if state.startswith("category:"):
            text_tokens[state] = (f'{bot._SLUG_TO_CAT[state.split(":", 1)[1]]} — pick a form:',)
        response = await _wider_click(client, transcript, reply, payload,
                                      expect_text_any=text_tokens[state])
        return await check(response, reply, state, **spec)

    async def capture():
        nonlocal floor
        before = await _wider_before_send(client)
        sent = await client.send_message(BOT_USERNAME, case)
        reply = await _wider_wait(client, transcript, "send:synthetic-case", before=before,
                                  min_id=sent.id, expect_button_any=("Choose form",))
        # Include an older bubble edited during capture, as well as every
        # subsequent recapture. Messages from before this journey are excluded.
        floor = min(floor or sent.id, reply.id)
        await check(reply, None, "capture")
        return await tap(reply, "GATHER|done", "recommendation")

    async def select(reply, payload):
        picker = await tap(reply, "FORM|show_all", "categories")
        forms = await tap(picker, "FORM|cat_CLINICAL", "category:CLINICAL")
        return await tap(forms, payload, "draft", form=payload[5:])

    try:
        recommendation = await capture()
        saved = (recommendation.raw_text.strip(), _screen_controls(recommendation))
        suggested = next(label for label, payload in saved[1] if payload == "FORM|best")
        picker = await tap(recommendation, "FORM|show_all", "categories")
        picker_snapshot = (picker.raw_text.strip(), _screen_controls(picker))
        offered = [p for _, p in picker_snapshot[1] if p.startswith("FORM|cat_")]
        candidates = []
        for payload in offered:
            forms = await tap(picker, payload, "category:" + payload.split("cat_", 1)[1])
            if payload == "FORM|cat_CLINICAL":
                candidates = [(label, p) for label, p in _screen_controls(forms)
                              if p in {"FORM|CBD", "FORM|CBD_2021", "FORM|MINI_CEX", "FORM|MINI_CEX_2021"}]
            picker = await tap(forms, "FORM|show_all", "categories", snapshot=picker_snapshot)
        recommendation = await tap(picker, "FORM|back", "recommendation", snapshot=saved)
        first = next((p for label, p in candidates if label != suggested), None)
        second = next((p for _, p in candidates if p != first), None)
        assert first and second, "Two distinct supported clinical forms required"
        draft = await select(recommendation, first)
        # _build_approval_keyboard has no switch-form/Restart control. The
        # supported route cancels this draft, then recaptures the same case.
        await tap(draft, "CANCEL|draft", "idle")
        recommendation = await capture()
        draft = await select(recommendation, second)
        await tap(draft, "CANCEL|draft", "idle")
        recommendation = await capture()
        await tap(recommendation, "CANCEL|form", "idle")
    finally:
        await _wider_cleanup(client, transcript, "form-switching")


@pytest.mark.asyncio
async def test_e2e_form_switching_to_cancel_journey(telethon_client):
    await _form_switching_to_cancel(telethon_client)


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
        capture_controls = dict(expect_buttons=True, expect_button_any=("Use text as case", "Choose form", "CBD", "Case-based discussion"))
        reply = await _wider_wait(client, transcript, f"after:{kind}", before=before, min_id=sent.id, **capture_controls)
        if any(_payload(b) == "DOCUSE|info" for row in (reply.buttons or []) for b in row):
            reply = await _wider_click(client, transcript, reply, "DOCUSE|info", **capture_controls)
        # Choose form must be observed. Gathering-off or a direct-form shortcut
        # is incomplete proof, rather than a silently shortened journey.
        reply = await _wider_click(client, transcript, reply, "GATHER|done",
                                   expect_buttons=True, expect_button_any=("CBD", "Case-based discussion", "Forms", "Cancel"))
        form = next((_payload(b) for row in (reply.buttons or []) for b in row
                     if _payload(b) in {"FORM|CBD", "FORM|CBD_2021", "FORM|best"}), None)
        assert form, "CBD choice missing"
        reply = await _wider_click(client, transcript, reply, form,
                                   expect_buttons=True, expect_button_any=("Save to Kaizen",))
        if classify_post_click_draft_state(reply) == "draft_with_gaps":
            # The recommended form decides the gap (supervision for CBD, the
            # learning point for a reflection); the bot must name it and ask.
            assert re.search(r"still needed:\s*(level of supervision|your reflection)\b.*?reply", reply.raw_text or "", re.I | re.S), "Unreviewed draft gap"
            detail = ("Level of supervision: indirect. I discussed the case with my senior registrar. "
                      "What I learned: I will repeat the ECG at 15 minutes for ongoing chest pain.")
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
            # The bot keeps fewer than three when the case supports fewer;
            # it must never pad or invent curriculum links.
            assert 1 <= len(selections) == len(set(selections)) <= 3, "Expected 1-3 distinct visible KCs"
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
                                       expect_text_any=("Portfolio defaults",), expect_button_any=("Settings",))
        for payload, title in (
            ("ACTION|change_level", "portfolio"),
            ("ACTION|change_pathway", "pathway"),
            ("ACTION|change_curriculum", "curriculum"),
        ):
            picker = await _wider_click(telethon_client, transcript, defaults, payload,
                                       expect_text_any=(title,), expect_button_any=("Portfolio defaults",))
            assert title in picker.raw_text.lower()
            defaults = await _wider_click(telethon_client, transcript, picker, "ACTION|portfolio_defaults",
                                         expect_text_any=("Portfolio defaults",), expect_button_any=("Settings",))
        settings = await _wider_click(telethon_client, transcript, defaults, "ACTION|settings",
                                     expect_text_any=("Settings",), expect_buttons=True)
        reminders = await _wider_click(telethon_client, transcript, settings, "REMIND|menu",
                                      expect_text_any=("Reminders",), expect_button_any=("Settings",))
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
