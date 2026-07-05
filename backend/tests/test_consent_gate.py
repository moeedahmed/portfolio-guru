"""Art 9(2)(a) explicit-consent gate tests (launch checklist 1.2).

Invariants pinned here:

1. An unconsented user's case is blocked BEFORE any LLM/processor call, for
   every input path (they all route through handle_case_input's gate).
2. Accepting records a versioned, hashed, timestamped grant; the gate opens.
3. Declining keeps the gate closed and processes nothing.
4. A consent-version bump re-gates previously consented users.
5. The record store is append-only: withdrawal adds a record, the original
   grant survives as evidence.
6. The shipped consent text matches its immutable archived copy byte-for-byte
   (docs/legal/consent-versions/) — wording can't drift without a version bump.
7. A group member can't consent on the prompted user's behalf.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

from tests.bot_simulator import BotSimulator


def _all_visible_text(sim: BotSimulator) -> str:
    return "\n".join(text for _, text, _ in sim.messages_sent if isinstance(text, str))


@pytest.fixture
def tmp_consent_db(tmp_path, monkeypatch):
    import usage

    monkeypatch.setattr(usage, "DB_PATH", str(tmp_path / "usage.db"))
    import consent

    return consent


# ─── The gate in handle_case_input ────────────────────────────────────────


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_unconsented_case_is_blocked_before_any_processing(tmp_consent_db):
    import bot
    from bot import handle_case_input

    sim = BotSimulator()
    update = sim._make_text_update(
        "45M with chest pain, troponin positive, managed as ACS and reflected on escalation."
    )
    context = sim._make_context()

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 5, "free"))) as can_file, \
         patch("bot.classify_intent", new_callable=AsyncMock) as classify, \
         patch("bot.recommend_form_types", new_callable=AsyncMock) as recommend:
        result = await handle_case_input(update, context)

    assert result == ConversationHandler.END
    text = sim.get_last_text() or ""
    assert "consent" in text.lower()
    assert "has not been processed" in text
    assert tmp_consent_db.CONSENT_VERSION not in text
    # Nothing clinical reached a processor and no usage was counted.
    classify.assert_not_awaited()
    recommend.assert_not_awaited()
    can_file.assert_not_awaited()
    button_data = [data for _, data in sim.get_last_buttons()]
    assert any(d.startswith("CONSENT|accept|") for d in button_data)
    assert any(d.startswith("CONSENT|decline|") for d in button_data)


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_case_that_triggered_consent_resumes_after_accept(tmp_consent_db):
    import bot
    from bot import AWAIT_FORM_CHOICE, handle_case_input, handle_consent_callback

    sim = BotSimulator()
    context = sim._make_context()
    case_text = "45M chest pain, ECG reviewed, troponins negative, discharged with safety netting."
    update = sim._make_text_update(case_text)

    async def fake_process(message, ctx, user_id, text, input_source):
        ctx.user_data["resumed_case_text"] = text
        ctx.user_data["resumed_input_source"] = input_source
        return AWAIT_FORM_CHOICE

    with patch("bot.has_credentials", return_value=True), \
         patch("bot._process_case_text", new=AsyncMock(side_effect=fake_process)) as process:
        result = await handle_case_input(update, context)

    assert result == ConversationHandler.END
    assert context.user_data["_consent_prompt_pending"] is True
    assert context.user_data["_consent_pending_input"]["kind"] == "text"
    assert "continue from it automatically" in (sim.get_last_text() or "")
    process.assert_not_awaited()

    accept = sim._make_callback_update(f"CONSENT|accept|{sim.user_id}")
    with patch("bot._process_case_text", new=AsyncMock(side_effect=fake_process)) as process:
        result = await handle_consent_callback(accept, context)

    assert result == AWAIT_FORM_CHOICE
    assert await tmp_consent_db.has_current_consent(sim.user_id) is True
    assert context.user_data["resumed_case_text"] == case_text
    assert context.user_data["resumed_input_source"] == "text"
    assert "_consent_pending_input" not in context.user_data
    process.assert_awaited_once()


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_photo_that_triggered_consent_resumes_to_image_intent(tmp_consent_db):
    import bot
    from bot import AWAIT_DOC_INTENT, handle_case_input, handle_consent_callback

    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("")
    photo = MagicMock()
    photo.file_id = "telegram-photo-file-id"
    update.message.text = None
    update.message.voice = None
    update.message.audio = None
    update.message.document = None
    update.message.caption = "I reviewed this ECG and documented my interpretation."
    update.message.photo = [photo]

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.extract_from_image", new=AsyncMock()) as extract:
        result = await handle_case_input(update, context)

    assert result == ConversationHandler.END
    assert context.user_data["_consent_pending_input"]["kind"] == "photo"
    assert context.user_data["_consent_pending_input"]["caption"] == update.message.caption
    extract.assert_not_awaited()

    async def fake_download(path):
        Path(path).write_bytes(b"image bytes")

    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock(side_effect=fake_download)
    context.bot.get_file = AsyncMock(return_value=file_obj)

    accept = sim._make_callback_update(f"CONSENT|accept|{sim.user_id}")
    result = await handle_consent_callback(accept, context)

    assert result == AWAIT_DOC_INTENT
    context.bot.get_file.assert_awaited_once_with("telegram-photo-file-id")
    pending_doc = context.user_data["_pending_doc"]
    assert pending_doc["kind"] == "image"
    assert pending_doc["name"] == "portfolio-image.jpg"
    assert Path(pending_doc["path"]).exists()
    assert context.user_data["_pending_doc_context"] == update.message.caption
    buttons = sim.get_last_buttons()
    assert ("📝 Use for drafting", "DOCUSE|info") in buttons
    assert ("📎 Attach only", "DOCUSE|attach") in buttons
    assert ("📎 Use + attach", "DOCUSE|both") in buttons
    assert "_consent_pending_input" not in context.user_data

    Path(pending_doc["path"]).unlink(missing_ok=True)


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_video_that_triggered_consent_resumes_to_video_intent(tmp_consent_db):
    from bot import AWAIT_DOC_INTENT, handle_case_input, handle_consent_callback

    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("")
    video = MagicMock()
    video.file_id = "telegram-video-file-id"
    video.mime_type = "video/mp4"
    update.message.text = None
    update.message.voice = None
    update.message.audio = None
    update.message.photo = []
    update.message.video = video
    update.message.document = None
    update.message.caption = "POCUS clip; I documented my interpretation separately."

    with patch("bot.has_credentials", return_value=True):
        result = await handle_case_input(update, context)

    assert result == ConversationHandler.END
    assert context.user_data["_consent_pending_input"]["kind"] == "video"
    assert context.user_data["_consent_pending_input"]["caption"] == update.message.caption

    async def fake_download(path):
        Path(path).write_bytes(b"video bytes")

    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock(side_effect=fake_download)
    context.bot.get_file = AsyncMock(return_value=file_obj)

    accept = sim._make_callback_update(f"CONSENT|accept|{sim.user_id}")
    result = await handle_consent_callback(accept, context)

    assert result == AWAIT_DOC_INTENT
    context.bot.get_file.assert_awaited_once_with("telegram-video-file-id")
    pending_doc = context.user_data["_pending_doc"]
    assert pending_doc["kind"] == "video"
    assert pending_doc["name"] == "portfolio-video.mp4"
    assert Path(pending_doc["path"]).exists()
    assert context.user_data["_pending_doc_context"] == update.message.caption
    buttons = sim.get_last_buttons()
    assert ("📎 Attach video", "DOCUSE|attach") in buttons
    assert ("❌ Remove video", "DOCUSE|ignore") in buttons
    assert "_consent_pending_input" not in context.user_data

    Path(pending_doc["path"]).unlink(missing_ok=True)


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_video_document_that_triggered_consent_resumes_to_video_intent(tmp_consent_db):
    from bot import AWAIT_DOC_INTENT, handle_case_input, handle_consent_callback

    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("")
    document = MagicMock()
    document.file_id = "telegram-video-document-file-id"
    document.file_name = "PXL_20260705_130629103.TS.mp4"
    document.mime_type = "video/mp4"
    update.message.text = None
    update.message.voice = None
    update.message.audio = None
    update.message.photo = []
    update.message.video = None
    update.message.document = document
    update.message.caption = "POCUS clip; I documented my interpretation separately."

    with patch("bot.has_credentials", return_value=True):
        result = await handle_case_input(update, context)

    assert result == ConversationHandler.END
    assert context.user_data["_consent_pending_input"]["kind"] == "video"
    assert context.user_data["_consent_pending_input"]["caption"] == update.message.caption

    async def fake_download(path):
        Path(path).write_bytes(b"video bytes")

    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock(side_effect=fake_download)
    context.bot.get_file = AsyncMock(return_value=file_obj)

    accept = sim._make_callback_update(f"CONSENT|accept|{sim.user_id}")
    result = await handle_consent_callback(accept, context)

    assert result == AWAIT_DOC_INTENT
    context.bot.get_file.assert_awaited_once_with("telegram-video-document-file-id")
    pending_doc = context.user_data["_pending_doc"]
    assert pending_doc["kind"] == "video"
    assert pending_doc["name"] == "portfolio-video.mp4"
    assert Path(pending_doc["path"]).exists()
    assert context.user_data["_pending_doc_context"] == update.message.caption
    buttons = sim.get_last_buttons()
    assert ("📎 Attach video", "DOCUSE|attach") in buttons
    assert ("❌ Remove video", "DOCUSE|ignore") in buttons
    assert "Couldn't transcribe voice note" not in _all_visible_text(sim)
    assert "_consent_pending_input" not in context.user_data

    Path(pending_doc["path"]).unlink(missing_ok=True)


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_oversized_video_document_after_consent_explains_limit_without_download(tmp_consent_db):
    from bot import AWAIT_CASE_INPUT, handle_case_input, handle_consent_callback

    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("")
    document = MagicMock()
    document.file_id = "telegram-large-video-document-file-id"
    document.file_name = "PXL_20260705_130629103.TS.mp4"
    document.mime_type = "video/mp4"
    document.file_size = 24_900_000
    update.message.text = None
    update.message.voice = None
    update.message.audio = None
    update.message.photo = []
    update.message.video = None
    update.message.document = document
    update.message.caption = "POCUS clip; I documented my interpretation separately."

    with patch("bot.has_credentials", return_value=True):
        result = await handle_case_input(update, context)

    assert result == ConversationHandler.END
    pending = context.user_data["_consent_pending_input"]
    assert pending["kind"] == "video"
    assert pending["file_size"] == 24_900_000

    context.bot.get_file = AsyncMock()
    accept = sim._make_callback_update(f"CONSENT|accept|{sim.user_id}")
    result = await handle_consent_callback(accept, context)

    assert result == AWAIT_CASE_INPUT
    context.bot.get_file.assert_not_awaited()
    assert "_pending_doc" not in context.user_data
    text = _all_visible_text(sim)
    assert "Consent recorded" in text
    assert "over Telegram's 20 MB bot download limit" in text
    assert "under 20 MB" in text
    assert "couldn't recover" not in text.lower()
    assert "PXL_20260705_130629103.TS.mp4" not in text
    assert "_consent_pending_input" not in context.user_data


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_document_that_triggered_consent_resumes_without_showing_filename(tmp_consent_db):
    from bot import AWAIT_DOC_INTENT, handle_case_input, handle_consent_callback

    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("")
    document = MagicMock()
    document.file_id = "telegram-document-file-id"
    document.file_name = "patient-name-ecg-result.pdf"
    document.mime_type = "application/pdf"
    update.message.text = None
    update.message.voice = None
    update.message.audio = None
    update.message.photo = []
    update.message.video = None
    update.message.document = document
    update.message.caption = None

    with patch("bot.has_credentials", return_value=True):
        result = await handle_case_input(update, context)

    assert result == ConversationHandler.END
    assert context.user_data["_consent_pending_input"]["kind"] == "document"
    assert "patient-name-ecg-result.pdf" not in _all_visible_text(sim)

    async def fake_download(path):
        Path(path).write_bytes(b"document bytes")

    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock(side_effect=fake_download)
    context.bot.get_file = AsyncMock(return_value=file_obj)

    accept = sim._make_callback_update(f"CONSENT|accept|{sim.user_id}")
    result = await handle_consent_callback(accept, context)

    assert result == AWAIT_DOC_INTENT
    context.bot.get_file.assert_awaited_once_with("telegram-document-file-id")
    pending_doc = context.user_data["_pending_doc"]
    assert pending_doc["name"] == "patient-name-ecg-result.pdf"
    assert Path(pending_doc["path"]).exists()
    assert "patient-name-ecg-result.pdf" not in _all_visible_text(sim)
    assert "How would you like to use this document?" in sim.get_last_text()

    Path(pending_doc["path"]).unlink(missing_ok=True)


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_accept_records_grant_and_opens_the_gate(tmp_consent_db):
    consent = tmp_consent_db
    from bot import handle_consent_callback

    sim = BotSimulator()
    user_id = sim.user_id
    update = sim._make_callback_update(f"CONSENT|accept|{user_id}")
    context = sim._make_context()

    await handle_consent_callback(update, context)

    assert await consent.has_current_consent(user_id) is True
    status = await consent.get_consent_status(user_id)
    assert status["version"] == consent.CONSENT_VERSION
    assert status["action"] == "granted"
    assert status["at"]  # timestamped
    edited = update.callback_query.edit_message_text.call_args.args[0]
    assert edited.startswith("✅ Consent recorded.")
    assert consent.CONSENT_VERSION not in edited


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_privacy_reports_grant_after_accepting(tmp_consent_db):
    """Regression: after a user accepts consent, /privacy must report the
    recorded grant — never 'haven't been asked' / 'no consent recorded'. This
    is the exact reported bug: bot said 'Consent recorded' then /privacy
    claimed the user had never been asked."""
    consent = tmp_consent_db
    from bot import handle_consent_callback, privacy_command

    sim = BotSimulator()
    user_id = sim.user_id
    context = sim._make_context()

    # The setup flow showed the consent notice (step 3), then the user taps
    # "I consent" — the exact flow from the reported bug.
    context.user_data["_consent_prompt_pending"] = True
    context.user_data["_consent_prompt_source"] = "setup"
    accept = sim._make_callback_update(f"CONSENT|accept|{user_id}")
    await handle_consent_callback(accept, context)
    sim.clear_messages()

    # Same user, same context/DB — /privacy must reflect the recorded grant.
    await privacy_command(sim._make_text_update("/privacy"), context)

    text = sim.get_last_text() or ""
    assert "Consent recorded on" in text
    assert consent.CONSENT_VERSION not in text
    assert "haven't been asked" not in text
    assert "No consent has been recorded" not in text
    assert "waiting for your choice" not in text  # pending flag was cleared
    assert "may use your case notes" not in text
    assert "EU (London)" not in text
    assert "Vertex AI in the UK (London region)" in text
    assert "Portfolio Guru's stored data" in text


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_decline_keeps_the_gate_closed(tmp_consent_db):
    consent = tmp_consent_db
    from bot import handle_consent_callback

    sim = BotSimulator()
    user_id = sim.user_id
    update = sim._make_callback_update(f"CONSENT|decline|{user_id}")
    context = sim._make_context()

    await handle_consent_callback(update, context)

    assert await consent.has_current_consent(user_id) is False
    edited = update.callback_query.edit_message_text.call_args.args[0]
    assert "didn't process or store" in edited.lower()
    assert "whenever you want to review the consent notice" in edited


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_setup_consent_decline_is_calm_and_reversible(tmp_consent_db):
    consent = tmp_consent_db
    from bot import handle_consent_callback

    sim = BotSimulator()
    user_id = sim.user_id
    update = sim._make_callback_update(f"CONSENT|decline|{user_id}")
    context = sim._make_context()
    context.user_data["_consent_prompt_pending"] = True
    context.user_data["_consent_prompt_source"] = "setup"

    await handle_consent_callback(update, context)

    assert await consent.has_current_consent(user_id) is False
    edited = update.callback_query.edit_message_text.call_args.args[0]
    assert "Kaizen is connected" in edited
    assert "I won't draft from cases unless you choose to consent" in edited
    assert "send your first anonymised case" in edited
    assert "cannot draft" not in edited


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_privacy_reports_pending_consent_prompt(tmp_consent_db):
    from bot import _prompt_consent, privacy_command

    sim = BotSimulator()
    context = sim._make_context()

    await _prompt_consent(sim._make_text_update("Chest pain case"), context)
    assert context.user_data["_consent_prompt_pending"] is True
    sim.clear_messages()

    await privacy_command(sim._make_text_update("/privacy"), context)

    text = sim.get_last_text() or ""
    assert "Consent notice shown" in text
    assert "waiting for your choice" in text
    assert "haven't been asked" not in text


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_legacy_review_button_opens_full_consent_notice(tmp_consent_db):
    """Old already-sent setup cards may still carry CONSENT|review buttons.
    Keep them safe, but do not show that button in new prompts."""
    from bot import handle_consent_callback

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["_consent_prompt_pending"] = True
    context.user_data["_consent_prompt_source"] = "setup"
    update = sim._make_callback_update(f"CONSENT|review|{sim.user_id}")

    await handle_consent_callback(update, context)

    edited = update.callback_query.edit_message_text.call_args.args[0]
    assert "Consent before your first case" in edited
    assert "Step 3 of 3" not in edited
    assert context.user_data["_consent_prompt_pending"] is True
    assert context.user_data["_consent_prompt_source"] == "setup"
    assert ("✅ I consent", f"CONSENT|accept|{sim.user_id}") in sim.get_last_buttons()
    assert ("🔐 Review consent", f"CONSENT|review|{sim.user_id}") not in sim.get_last_buttons()


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_another_users_tap_cannot_grant_consent(tmp_consent_db):
    consent = tmp_consent_db
    from bot import handle_consent_callback

    sim = BotSimulator()
    prompted_uid = sim.user_id + 1  # prompt belongs to someone else
    update = sim._make_callback_update(f"CONSENT|accept|{prompted_uid}")
    context = sim._make_context()

    await handle_consent_callback(update, context)

    assert await consent.has_current_consent(sim.user_id) is False
    assert await consent.has_current_consent(prompted_uid) is False
    update.callback_query.edit_message_text.assert_not_called()


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_successful_setup_prompts_consent_before_ready_state(tmp_consent_db):
    import bot

    sim = BotSimulator()
    update = sim._make_text_update("safe-password")
    update.message.delete = AsyncMock()
    context = sim._make_context()
    context.user_data["setup_username"] = "doctor@example.com"

    with patch("bot._test_kaizen_login", new=AsyncMock(return_value="hst")), \
         patch("bot.get_credentials", return_value=None), \
         patch("bot.store_credentials"), \
         patch("bot.store_training_level"), \
         patch("bot.store_curriculum"), \
         patch("bot._autoset_health_pathway_from_role", return_value=None), \
         patch("supervisor_workflow.set_role_if_better"):
        result = await bot.setup_password(update, context)

    assert result == ConversationHandler.END
    texts = [text for _, text, _ in sim.messages_sent if text]
    assert any("Kaizen connected" in text and "Step 3 of 3" in text for text in texts)
    text = sim.get_last_text() or ""
    assert "Step 3 of 3" in text
    assert "Case notes are health data" in text
    assert "By tapping I consent" in text
    assert "Full details: /privacy" in text
    assert bot.consent.CONSENT_VERSION not in text
    assert "has not been processed" not in text
    assert context.user_data["_consent_prompt_pending"] is True
    assert context.user_data["_consent_prompt_source"] == "setup"
    assert ("✅ I consent", f"CONSENT|accept|{sim.user_id}") in sim.get_last_buttons()
    assert ("❌ Not now", f"CONSENT|decline|{sim.user_id}") in sim.get_last_buttons()
    assert ("🔐 Review consent", f"CONSENT|review|{sim.user_id}") not in sim.get_last_buttons()


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_setup_consent_accept_lands_on_ready_state(tmp_consent_db):
    consent = tmp_consent_db
    from bot import WELCOME_MSG_CONNECTED, handle_consent_callback

    sim = BotSimulator()
    update = sim._make_callback_update(f"CONSENT|accept|{sim.user_id}")
    context = sim._make_context()
    context.user_data["_consent_prompt_pending"] = True
    context.user_data["_consent_prompt_source"] = "setup"

    await handle_consent_callback(update, context)

    assert await consent.has_current_consent(sim.user_id) is True
    edited = update.callback_query.edit_message_text.call_args.args[0]
    assert WELCOME_MSG_CONNECTED in edited
    assert "send it again" not in edited.lower()


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_start_fresh_without_consent_restarts_at_step_1(tmp_consent_db):
    """Regression: a genuinely fresh/reset /start must land on Step 1, never be
    dropped into 'Step 3 of 3' consent it never navigated to this session.

    Reproduces the reported bug: after a bot-level reset (credentials may
    linger while consent was wiped/version-bumped), the user typed /start and
    saw 'Step 3 of 3' instead of Step 1. With no in-flight setup-to-consent
    continuation in user_data, /start is a top-of-funnel entry to Step 1.
    """
    from telegram.ext import ConversationHandler as _CH

    import bot

    sim = BotSimulator()
    update = sim._make_text_update("/start")
    context = sim._make_context()
    # No _consent_prompt_pending flag: not a mid-setup continuation.

    with patch("bot.has_credentials", return_value=True):
        result = await bot.start(update, context)

    assert result == bot.AWAIT_USERNAME
    assert result != _CH.END
    text = sim.get_last_text() or ""
    assert "Step 1 of 3" in text
    assert "Step 3 of 3" not in text
    assert "consent before your first case" not in text
    assert context.user_data.get("_setup_state_hint") == "username"


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_email_after_step_1_routes_to_password_not_consent(tmp_consent_db):
    """Regression: after a consent-version bump, a user may still have Kaizen
    credentials but no current consent. /start deliberately restarts at Step 1
    so the flow is coherent. The email typed after that Step 1 prompt must be
    treated as setup input, not as a clinical case that triggers consent."""
    import bot

    sim = BotSimulator()
    context = sim._make_context()

    with patch("bot.has_credentials", return_value=True):
        result = await bot.start(sim._make_text_update("/start"), context)

    assert result == bot.AWAIT_USERNAME
    assert context.user_data.get("_setup_state_hint") == "username"
    assert "Step 1 of 3" in (sim.get_last_text() or "")

    sim.clear_messages()
    with patch("bot.has_credentials", return_value=True):
        result = await bot.handle_case_input(
            sim._make_text_update("doctor@example.com"),
            context,
        )

    assert result == bot.AWAIT_PASSWORD
    assert context.user_data["setup_username"] == "doctor@example.com"
    assert context.user_data["_setup_state_hint"] == "password"
    text = sim.get_last_text() or ""
    assert "Step 2 of 3" in text
    assert "Kaizen password" in text
    assert "Consent before your first case" not in text
    assert "has not been processed" not in text


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_start_continues_step_3_when_setup_consent_pending(tmp_consent_db):
    """Intended continuation: a user who just cleared Steps 1-2 is sitting on
    the pending Step 3 consent prompt. If they re-send /start, resume Step 3
    rather than bouncing them back to Step 1."""
    import bot

    sim = BotSimulator()
    update = sim._make_text_update("/start")
    context = sim._make_context()
    # The setup flow set these when it reached Step 3 (setup_password /
    # setup_training_level to _prompt_consent(source="setup")).
    context.user_data["_consent_prompt_pending"] = True
    context.user_data["_consent_prompt_source"] = "setup"

    with patch("bot.has_credentials", return_value=True):
        result = await bot.start(update, context)

    assert result == ConversationHandler.END
    texts = [text for _, text, _ in sim.messages_sent if text]
    assert any("Kaizen is already connected" in text and "Step 3 of 3" in text for text in texts)
    text = sim.get_last_text() or ""
    assert "Step 3 of 3" in text
    assert "Case notes are health data" in text
    assert "By tapping I consent" in text
    assert "Full details: /privacy" in text
    assert "Portfolio Guru is ready" not in text
    assert bot.consent.CONSENT_VERSION not in text
    assert ("✅ I consent", f"CONSENT|accept|{sim.user_id}") in sim.get_last_buttons()
    assert ("❌ Not now", f"CONSENT|decline|{sim.user_id}") in sim.get_last_buttons()
    assert ("🔐 Review consent", f"CONSENT|review|{sim.user_id}") not in sim.get_last_buttons()


# ─── Record semantics ─────────────────────────────────────────────────────


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_version_bump_regates_consented_users(tmp_consent_db, monkeypatch):
    consent = tmp_consent_db
    user_id = 201
    await consent.record_consent(user_id)
    assert await consent.has_current_consent(user_id) is True

    monkeypatch.setattr(consent, "CONSENT_VERSION", "2099-01-01.v2")
    assert await consent.has_current_consent(user_id) is False


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_withdrawal_appends_and_preserves_the_grant(tmp_consent_db):
    consent = tmp_consent_db
    import aiosqlite
    import usage

    user_id = 202
    await consent.record_consent(user_id)
    await consent.record_withdrawal(user_id)

    assert await consent.has_current_consent(user_id) is False
    async with aiosqlite.connect(usage.DB_PATH) as db:
        async with db.execute(
            "SELECT action FROM consent_records WHERE telegram_user_id = ? ORDER BY id",
            (user_id,),
        ) as cursor:
            actions = [r[0] for r in await cursor.fetchall()]
    assert actions == ["granted", "withdrawn"]  # append-only, grant preserved

    # Re-granting after withdrawal is recorded as such.
    await consent.record_consent(user_id)
    assert await consent.has_current_consent(user_id) is True
    status = await consent.get_consent_status(user_id)
    assert status["action"] == "re-granted"


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_withdrawal_without_grant_is_a_noop(tmp_consent_db):
    consent = tmp_consent_db
    import aiosqlite
    import usage

    await consent.record_withdrawal(303)
    async with aiosqlite.connect(usage.DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM consent_records") as cursor:
            assert (await cursor.fetchone())[0] == 0


# ─── Immutable wording archive ────────────────────────────────────────────


def test_shipped_consent_text_matches_immutable_archive():
    import hashlib

    import consent

    archive = (
        Path(__file__).resolve().parent.parent.parent
        / "docs" / "legal" / "consent-versions" / f"{consent.CONSENT_VERSION}.md"
    )
    assert archive.is_file(), (
        f"missing archived copy for consent version {consent.CONSENT_VERSION} — "
        "every shipped version must be archived in docs/legal/consent-versions/"
    )
    archived = archive.read_text(encoding="utf-8")
    assert archived == consent.CONSENT_TEXT, (
        "consent wording differs from its archived copy — changing the text "
        "requires a NEW CONSENT_VERSION (and a new archive file), never an edit"
    )
    assert hashlib.sha256(archived.encode("utf-8")).hexdigest() == consent.consent_text_hash()


def test_current_consent_copy_uses_precise_ai_processing_wording():
    import consent

    assert "may use your case notes" not in consent.CONSENT_TEXT
    assert "EU (London)" not in consent.CONSENT_TEXT
    assert "When drafting" in consent.CONSENT_TEXT
    assert "anonymised case details you provide" in consent.CONSENT_TEXT
    assert "Vertex AI in the UK (London region)" in consent.CONSENT_TEXT
    assert "Portfolio Guru's stored data" in consent.CONSENT_TEXT
