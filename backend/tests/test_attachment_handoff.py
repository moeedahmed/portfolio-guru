import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from bot import (
    AWAIT_APPROVAL,
    AWAIT_CASE_INPUT,
    AWAIT_DOC_INTENT,
    AWAIT_FORM_CHOICE,
    AWAIT_GATHERING,
    _attachment_path_with_original_name,
    handle_approval_approve,
    handle_case_input,
    handle_document_intent,
    gather_done_callback,
    handle_gathering_input,
    handle_mid_conversation_text,
)
from tests.bot_simulator import BotSimulator
from extractor import FormDraft
from channel_actions import ChannelReply
from conversation_supervisor import (
    GatheringDecision,
    GatheringTurnKind,
)
from conversational_router import ConversationalIntent


def _all_visible_text(sim: BotSimulator) -> str:
    return "\n".join(text for _, text, _ in sim.messages_sent if isinstance(text, str))


@pytest.mark.asyncio
async def test_document_case_stores_attachment_path():
    """Document uploads first ask how the file should be used."""
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update('')
    
    # Mock document attachment
    document = MagicMock()
    document.file_name = "clinical-notes.pdf"
    document.mime_type = "application/pdf"
    
    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock()
    document.get_file = AsyncMock(return_value=file_obj)
    
    update.message.text = None
    update.message.voice = None
    update.message.audio = None
    update.message.photo = []
    update.message.document = document

    with patch('bot.has_credentials', return_value=True), \
         patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
         patch('bot.extract_from_document', new=AsyncMock(return_value="Patient presented with chest pain...")) as extract_mock, \
         patch('bot.get_training_level', return_value='ST5'), \
         patch('bot.get_curriculum', return_value='2025'), \
         patch('bot.recommend_form_types', new=AsyncMock(return_value=[])):
        
        result = await handle_case_input(update, context)

    assert result == AWAIT_DOC_INTENT
    assert "_pending_doc" in context.user_data
    assert context.user_data["_pending_doc"]["name"] == "clinical-notes.pdf"
    assert os.path.exists(context.user_data["_pending_doc"]["path"])
    extract_mock.assert_not_called()
    buttons = sim.get_last_buttons()
    assert ("Use as case", "DOCUSE|info") in buttons
    assert ("Attach only", "DOCUSE|attach") in buttons
    assert ("Read + attach", "DOCUSE|both") in buttons
    assert "clinical-notes.pdf" not in _all_visible_text(sim)
    
    # Clean up the cached file
    path = context.user_data["_pending_doc"]["path"]
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.asyncio
async def test_photo_case_stores_pending_image_and_asks_intent():
    """Photo uploads should ask how the image should be used before OCR/drafting."""
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update('')

    photo = MagicMock()
    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock()
    photo.get_file = AsyncMock(return_value=file_obj)

    update.message.text = None
    update.message.voice = None
    update.message.audio = None
    update.message.document = None
    update.message.caption = None
    update.message.photo = [photo]

    with patch('bot.has_credentials', return_value=True), \
         patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
         patch('bot.extract_from_image', new=AsyncMock(return_value="visible clinical text")) as extract_mock:
        result = await handle_case_input(update, context)

    assert result == AWAIT_DOC_INTENT
    assert context.user_data["_pending_doc"]["kind"] == "image"
    assert context.user_data["_pending_doc"]["name"] == "portfolio-image.jpg"
    assert context.user_data["_pending_doc"]["source_chat_id"] == update.message.chat_id
    assert context.user_data["_pending_doc"]["source_message_id"] == update.message.message_id
    assert context.user_data["_pending_doc"]["source_chat_type"] == "private"
    assert os.path.exists(context.user_data["_pending_doc"]["path"])
    # The image is now read on arrival so the bot can tell whether there is
    # any text to offer. Text was found here, so the choice is real and shown.
    extract_mock.assert_called_once()
    buttons = sim.get_last_buttons()
    assert ("Read text", "DOCUSE|info") in buttons
    assert ("Attach only", "DOCUSE|attach") in buttons
    assert ("Read + attach", "DOCUSE|both") in buttons
    assert ("Remove", "DOCUSE|ignore") in buttons

    path = context.user_data["_pending_doc"]["path"]
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.asyncio
async def test_video_case_stores_pending_video_and_asks_attach_intent():
    """Video uploads should ask whether to attach, without attempting interpretation."""
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update('')

    video = MagicMock()
    video.mime_type = "video/mp4"
    file_obj = MagicMock()

    async def fake_download(path):
        with open(path, "wb") as handle:
            handle.write(b"dummy video content")

    file_obj.download_to_drive = AsyncMock(side_effect=fake_download)
    video.get_file = AsyncMock(return_value=file_obj)

    update.message.text = None
    update.message.voice = None
    update.message.audio = None
    update.message.photo = []
    update.message.video = video
    update.message.document = None
    update.message.caption = "POCUS clip with my findings in text."

    with patch('bot.has_credentials', return_value=True), \
         patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))):
        result = await handle_case_input(update, context)

    # A video has no text, so there is nothing to decide: it attaches and the
    # bot asks for the one thing it actually needs, the doctor's account.
    assert result == AWAIT_CASE_INPUT
    assert context.user_data["_pending_doc"]["kind"] == "video"
    assert context.user_data["_pending_doc"]["name"] == "portfolio-video.mp4"
    assert context.user_data["_pending_doc_context"] == update.message.caption
    assert os.path.exists(context.user_data["_pending_doc"]["path"])
    assert sim.get_last_buttons() == []
    assert any("attached to this case" in t for _, t, _ in sim.messages_sent if t)

    path = context.user_data["_pending_doc"]["path"]
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.asyncio
async def test_video_sent_as_document_uses_video_intent_not_voice_transcription():
    """Android/file-picker MP4 uploads can arrive as Telegram documents."""
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update('')

    document = MagicMock()
    document.file_name = "PXL_20260705_130629103.TS.mp4"
    document.mime_type = "video/mp4"
    file_obj = MagicMock()

    async def fake_download(path):
        with open(path, "wb") as handle:
            handle.write(b"dummy video content")

    file_obj.download_to_drive = AsyncMock(side_effect=fake_download)
    document.get_file = AsyncMock(return_value=file_obj)

    update.message.text = None
    update.message.voice = None
    update.message.audio = None
    update.message.photo = []
    update.message.video = None
    update.message.document = document
    update.message.caption = "POCUS clip with my findings in text."

    with patch('bot.has_credentials', return_value=True), \
         patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
         patch('bot.transcribe_voice', new=AsyncMock()) as transcribe_mock, \
         patch('bot.extract_from_document', new=AsyncMock()) as document_extract:
        result = await handle_case_input(update, context)

    assert result == AWAIT_CASE_INPUT
    transcribe_mock.assert_not_called()
    document_extract.assert_not_called()
    pending_doc = context.user_data["_pending_doc"]
    assert pending_doc["kind"] == "video"
    assert pending_doc["name"] == "portfolio-video.mp4"
    assert context.user_data["_pending_doc_context"] == update.message.caption
    assert sim.get_last_buttons() == []
    assert "Couldn't transcribe voice note" not in _all_visible_text(sim)
    assert "PXL_20260705_130629103.TS.mp4" not in _all_visible_text(sim)

    path = pending_doc["path"]
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.asyncio
async def test_oversized_video_document_explains_telegram_limit_without_download():
    """Files over Telegram's hosted Bot API download limit need clear guidance."""
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update('')

    document = MagicMock()
    document.file_id = "telegram-large-video-document-file-id"
    document.file_name = "PXL_20260705_130629103.TS.mp4"
    document.mime_type = "video/mp4"
    document.file_size = 24_900_000
    document.get_file = AsyncMock()

    update.message.text = None
    update.message.voice = None
    update.message.audio = None
    update.message.photo = []
    update.message.video = None
    update.message.document = document
    update.message.caption = "POCUS clip with my findings in text."

    with patch('bot.has_credentials', return_value=True), \
         patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
         patch('bot.transcribe_voice', new=AsyncMock()) as transcribe_mock, \
         patch('bot.extract_from_document', new=AsyncMock()) as document_extract:
        result = await handle_case_input(update, context)

    assert result == AWAIT_CASE_INPUT
    document.get_file.assert_not_awaited()
    transcribe_mock.assert_not_called()
    document_extract.assert_not_called()
    assert "_pending_doc" not in context.user_data
    text = _all_visible_text(sim)
    assert "over Telegram's 20 MB bot download limit" in text
    assert "under 20 MB" in text
    assert "Couldn't transcribe voice note" not in text
    assert "Try again" not in text
    assert "PXL_20260705_130629103.TS.mp4" not in text


@pytest.mark.asyncio
async def test_remove_image_deletes_private_chat_photo_message_and_cache():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|ignore")

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy image content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-image.jpg",
        "kind": "image",
        "source_chat_id": sim.user_id,
        "source_message_id": 123,
        "source_chat_type": "private",
    }

    result = await handle_document_intent(update, context)

    assert result == AWAIT_CASE_INPUT
    assert not os.path.exists(temp_path)
    context.bot.delete_message.assert_awaited_once_with(chat_id=sim.user_id, message_id=123)
    assert "↩️ Removed that image. Send the anonymised case details" in sim.get_last_text()


@pytest.mark.asyncio
async def test_remove_image_clears_draft_without_delete_attempt_outside_private_chat():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|ignore")

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy image content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-image.jpg",
        "kind": "image",
        "source_chat_id": -100123,
        "source_message_id": 123,
        "source_chat_type": "supergroup",
    }

    result = await handle_document_intent(update, context)

    assert result == AWAIT_CASE_INPUT
    assert not os.path.exists(temp_path)
    context.bot.delete_message.assert_not_awaited()
    assert "↩️ Removed that image from the draft" in sim.get_last_text()


@pytest.mark.asyncio
async def test_document_attach_only_does_not_extract_and_waits_for_case_details():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|attach")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy pdf content")
    context.user_data["_pending_doc"] = {"path": temp_path, "name": "evidence.pdf"}

    with patch('bot.extract_from_document', new=AsyncMock()) as extract_mock:
        result = await handle_document_intent(update, context)

    assert result == AWAIT_GATHERING
    extract_mock.assert_not_called()
    assert context.user_data["attachment_path"] == temp_path
    assert context.user_data["attachment_name"] == "evidence.pdf"
    assert "case_text" not in context.user_data
    assert sim.get_last_text().startswith("📎 Document attached.")
    assert "Add anonymised case details before choosing a form." in sim.get_last_text()
    assert sim.get_last_buttons() == [
        ("📋 Choose form", "GATHER|done"),
        ("❌ Discard case", "ACTION|cancel"),
    ]
    assert context.user_data["gathering_msg_id"] == update.callback_query.message.message_id
    assert "evidence.pdf" not in _all_visible_text(sim)

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_image_attach_only_does_not_extract_and_waits_for_case_details():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|attach")

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy image content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-image.jpg",
        "kind": "image",
    }

    with patch('bot.extract_from_image', new=AsyncMock()) as extract_mock:
        result = await handle_document_intent(update, context)

    assert result == AWAIT_GATHERING
    extract_mock.assert_not_called()
    assert context.user_data["attachment_path"] == temp_path
    assert context.user_data["attachment_name"] == "portfolio-image.jpg"
    assert "case_text" not in context.user_data
    assert sim.get_last_text().startswith("📎 Image attached.")
    assert "send your own interpretation/context" in sim.get_last_text()
    assert sim.get_last_buttons() == [
        ("📋 Choose form", "GATHER|done"),
        ("❌ Discard case", "ACTION|cancel"),
    ]
    assert context.user_data["gathering_msg_id"] == update.callback_query.message.message_id
    assert "portfolio-image.jpg" not in _all_visible_text(sim)

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_image_attach_only_prompt_rejoins_gathering_loop_on_next_text(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    attach_update = sim._make_callback_update("DOCUSE|attach")

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy image content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-image.jpg",
        "kind": "image",
    }

    attach_result = await handle_document_intent(attach_update, context)
    assert attach_result == AWAIT_GATHERING
    attached_prompt_id = context.user_data["gathering_msg_id"]

    text_update = sim._make_text_update("I also performed a 12-lead ECG and arranged urgent cardiology review.")

    decision = GatheringDecision(
        kind=GatheringTurnKind.CONTINUE_GATHERING,
        intent=ConversationalIntent.NEW_CASE,
        add_to_case=True,
        reply=ChannelReply(body="📥 Case captured.\n\nSend another anonymised message to add details.\n\nWhen you're ready, tap Choose form."),
    )
    with patch("bot.decide_gathering_turn", new=AsyncMock(return_value=decision)):
        result = await handle_gathering_input(text_update, context)

    assert result == AWAIT_GATHERING
    context.bot.edit_message_text.assert_awaited()
    assert context.bot.edit_message_text.await_args.kwargs["message_id"] == attached_prompt_id
    assert context.bot.edit_message_text.await_args.kwargs["reply_markup"] is None
    assert context.user_data["gathering_msg_id"] != attached_prompt_id
    assert context.user_data["attachment_path"] == temp_path
    assert sim.get_last_text() == "📥 Case captured.\n\nSend another anonymised message to add details.\n\nWhen you're ready, tap Choose form."
    assert sim.get_last_buttons() == [
        ("📋 Choose form", "GATHER|done"),
        ("❌ Discard case", "ACTION|cancel"),
    ]

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_attach_only_draft_now_before_case_context_keeps_attachment_and_asks_for_details():
    sim = BotSimulator()
    context = sim._make_context()
    attach_update = sim._make_callback_update("DOCUSE|attach")

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy image content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-image.jpg",
        "kind": "image",
    }

    attach_result = await handle_document_intent(attach_update, context)
    assert attach_result == AWAIT_GATHERING

    draft_update = sim._make_callback_update("GATHER|done")
    draft_update.callback_query.message.message_id = context.user_data["gathering_msg_id"]
    draft_update.callback_query.message.chat_id = context.user_data["gathering_chat_id"]

    result = await gather_done_callback(draft_update, context)

    assert result == AWAIT_CASE_INPUT
    assert context.user_data["attachment_path"] == temp_path
    assert "Case details needed" in sim.get_last_text()
    assert "attachment is saved" in sim.get_last_text()

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_video_attach_only_waits_for_user_context_without_extracting():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|attach")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy video content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-video.mp4",
        "kind": "video",
    }

    with patch('bot.extract_from_document', new=AsyncMock()) as document_extract, \
         patch('bot.extract_from_image', new=AsyncMock()) as image_extract:
        result = await handle_document_intent(update, context)

    assert result == AWAIT_GATHERING
    document_extract.assert_not_called()
    image_extract.assert_not_called()
    assert context.user_data["attachment_path"] == temp_path
    assert context.user_data["attachment_name"] == "portfolio-video.mp4"
    assert context.user_data["attachment_kind"] == "video"
    assert "case_text" not in context.user_data
    assert sim.get_last_text().startswith("📎 Video attached.")
    assert "won't interpret clinical videos" in sim.get_last_text()
    assert sim.get_last_buttons() == [
        ("📋 Choose form", "GATHER|done"),
        ("❌ Discard case", "ACTION|cancel"),
    ]
    assert context.user_data["gathering_msg_id"] == update.callback_query.message.message_id
    assert "portfolio-video.mp4" not in _all_visible_text(sim)

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_video_attach_blocks_symptom_fragments_before_drafting():
    sim = BotSimulator()
    context = sim._make_context()
    attach_update = sim._make_callback_update("DOCUSE|attach")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy video content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-video.mp4",
        "kind": "video",
    }

    attach_result = await handle_document_intent(attach_update, context)
    assert attach_result == AWAIT_GATHERING

    text_update = sim._make_text_update(
        "Okay, so I saw a case that I was surprised with: chest pain, "
        "shortness of breath, fever, fall. Turn that into a case"
    )

    with patch('bot.has_credentials', return_value=True), \
         patch('bot.consent.has_current_consent', new=AsyncMock(return_value=True)), \
         patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
         patch('bot.classify_intent', new=AsyncMock(return_value="case")), \
         patch('bot.recommend_form_types', new=AsyncMock()) as recommend_mock:
        result = await handle_case_input(text_update, context)

    assert result == AWAIT_CASE_INPUT
    recommend_mock.assert_not_awaited()
    assert context.user_data["attachment_path"] == temp_path
    assert context.user_data["attachment_name"] == "portfolio-video.mp4"
    assert context.user_data["attachment_kind"] == "video"
    text = _all_visible_text(sim)
    assert "what the video shows" in text
    assert "what you did or decided" in text
    assert "Drafted" not in text

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_document_read_and_attach_extracts_case_and_preserves_attachment():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|both")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy pdf content")
    context.user_data["_pending_doc"] = {"path": temp_path, "name": "notes.pdf"}

    with patch('bot.extract_from_document', new=AsyncMock(return_value="45F chest pain with normal ECG and negative troponins.")), \
         patch('bot.get_training_level', return_value='ST5'), \
         patch('bot.get_curriculum', return_value='2025'), \
         patch('bot.recommend_form_types', new=AsyncMock(return_value=[])):
        result = await handle_document_intent(update, context)

    assert result == AWAIT_FORM_CHOICE
    assert context.user_data["attachment_path"] == temp_path
    assert context.user_data["attachment_name"] == "notes.pdf"
    assert "45F chest pain" in context.user_data["case_text"]

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_image_read_and_attach_extracts_case_and_preserves_attachment():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|both")

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy image content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-image.jpg",
        "kind": "image",
    }
    context.user_data["_pending_doc_context"] = "I performed and documented the ECG review."

    async def fake_process(message, ctx, user_id, case_text, input_source):
        ctx.user_data["processed_case_text"] = case_text
        ctx.user_data["processed_input_source"] = input_source
        return AWAIT_FORM_CHOICE

    with patch('bot.extract_from_image', new=AsyncMock(return_value="Visible ECG text: sinus rhythm.")), \
         patch('bot._process_case_text', new=AsyncMock(side_effect=fake_process)):
        result = await handle_document_intent(update, context)

    assert result == AWAIT_FORM_CHOICE
    assert context.user_data["attachment_path"] == temp_path
    assert context.user_data["attachment_name"] == "portfolio-image.jpg"
    assert "I performed and documented" in context.user_data["processed_case_text"]
    assert "Visible ECG text" in context.user_data["processed_case_text"]
    assert context.user_data["processed_input_source"] == "photo"

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_image_use_for_drafting_blocks_nonclinical_without_context():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|info")

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy image content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-image.jpg",
        "kind": "image",
    }

    with patch('bot.extract_from_image', new=AsyncMock(return_value="NOT_CLINICAL")):
        result = await handle_document_intent(update, context)

    assert result == AWAIT_CASE_INPUT
    assert "case_text" not in context.user_data
    assert "send your own interpretation/context" in sim.get_last_text()
    assert not os.path.exists(temp_path)


@pytest.mark.asyncio
async def test_attach_only_attachment_survives_next_text_case():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("45F chest pain, ECG normal, troponins negative, discharged with safety netting.")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy pdf content")
    context.user_data["attachment_path"] = temp_path
    context.user_data["attachment_name"] = "evidence.pdf"

    with patch('bot.has_credentials', return_value=True), \
         patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
         patch('bot.get_training_level', return_value='ST5'), \
         patch('bot.get_curriculum', return_value='2025'), \
         patch('bot.recommend_form_types', new=AsyncMock(return_value=[])):
        result = await handle_case_input(update, context)

    assert result == AWAIT_FORM_CHOICE
    assert context.user_data["attachment_path"] == temp_path
    assert context.user_data["attachment_name"] == "evidence.pdf"

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_mid_flow_submit_question_answers_draft_only_and_preserves_state():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("Will this submit to my supervisor?")
    context.user_data["case_text"] = "45F chest pain, ECG normal, troponins negative."
    context.user_data["form_recommendations"] = []

    with patch('bot.classify_intent', new=AsyncMock()) as classify_mock:
        result = await handle_mid_conversation_text(update, context)

    assert result == AWAIT_FORM_CHOICE
    classify_mock.assert_not_called()
    text = sim.get_last_text()
    assert "drafts only" in text
    assert "No supervisor request" in text
    assert context.user_data["case_text"] == "45F chest pain, ECG normal, troponins negative."


@pytest.mark.asyncio
async def test_mid_flow_sdl_reflection_with_supervisor_action_plan_is_processed_as_case():
    from bot import AWAIT_FORM_CHOICE

    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update(
        "Self-directed learning reflection. I completed the RCEMLearning module on adult "
        "sepsis recognition and initial ED management on 6 June 2026. I reviewed the NICE "
        "sepsis guidance and local ED sepsis pathway afterwards. Key learning was earlier "
        "recognition of high-risk features, prompt senior escalation, timely antibiotics, "
        "lactate measurement, cultures, and fluid reassessment. I realised I need to be "
        "more systematic with documenting sepsis screening and safety-netting when patients "
        "are discharged after infection assessment. I will use the ED sepsis checklist during "
        "my next shifts and discuss one relevant case with my supervisor to evidence change "
        "in practice."
    )
    context.user_data["case_text"] = "previous case still in form-choice state"
    context.user_data["form_recommendations"] = []

    with patch('bot.classify_intent', new=AsyncMock(return_value='new_case')), \
         patch('bot._process_case_text', new=AsyncMock(return_value=AWAIT_FORM_CHOICE)) as process_case:
        result = await handle_mid_conversation_text(update, context)

    assert result == AWAIT_FORM_CHOICE
    process_case.assert_awaited_once()
    assert "Self-directed learning reflection" in process_case.await_args.args[3]


@pytest.mark.asyncio
async def test_text_while_document_choice_pending_is_captured_and_keeps_buttons_valid():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("I completed ATLS and have a certificate.")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy pdf content")
    context.user_data["_pending_doc"] = {"path": temp_path, "name": "atls.pdf"}

    with patch('bot.classify_intent', new=AsyncMock()) as classify_mock:
        result = await handle_mid_conversation_text(update, context)

    assert result == AWAIT_DOC_INTENT
    classify_mock.assert_not_called()
    assert context.user_data["_pending_doc"]["name"] == "atls.pdf"
    assert context.user_data["_pending_doc_context"] == "I completed ATLS and have a certificate."
    assert "document choice is still pending" in sim.get_last_text()

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_text_while_image_choice_pending_is_captured_and_keeps_buttons_valid():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("This was an ECG I reviewed during a chest pain case.")

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy image content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-image.jpg",
        "kind": "image",
    }

    with patch('bot.classify_intent', new=AsyncMock()) as classify_mock:
        result = await handle_mid_conversation_text(update, context)

    assert result == AWAIT_DOC_INTENT
    classify_mock.assert_not_called()
    assert context.user_data["_pending_doc"]["kind"] == "image"
    assert context.user_data["_pending_doc_context"] == "This was an ECG I reviewed during a chest pain case."
    assert "image choice is still pending" in sim.get_last_text()

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_rich_ultrasound_log_text_advances_while_video_choice_stays_pending():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update(
        "Ultrasound log: I assessed a hypotensive patient in resus, performed a focused "
        "cardiac ultrasound, identified poor LV function, escalated to my consultant, "
        "and reflected that I should document my findings contemporaneously."
    )

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy video content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-video.mp4",
        "kind": "video",
    }
    context.user_data["_pending_doc_context"] = "POCUS clip supplied as supporting evidence."

    with patch("bot.extract_from_document", new=AsyncMock()) as document_extract, \
         patch("bot.extract_from_image", new=AsyncMock()) as image_extract, \
         patch("bot.transcribe_voice", new=AsyncMock()) as transcribe:
        result = await handle_mid_conversation_text(update, context)

    assert result == AWAIT_FORM_CHOICE
    assert context.user_data["chosen_form"] == "US_CASE"
    assert "focused cardiac ultrasound" in context.user_data["case_text"]
    assert "POCUS clip supplied" in context.user_data["case_text"]
    assert context.user_data["_pending_doc"]["path"] == temp_path
    assert os.path.exists(temp_path)
    assert "_pending_doc_context" not in context.user_data
    document_extract.assert_not_awaited()
    image_extract.assert_not_awaited()
    transcribe.assert_not_awaited()

    os.unlink(temp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["attach", "ignore"])
async def test_video_retain_or_remove_preserves_active_content_state(mode):
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update(f"DOCUSE|{mode}")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy video content")
    context.user_data.update(
        {
            "_pending_doc": {
                "path": temp_path,
                "name": "portfolio-video.mp4",
                "kind": "video",
            },
            "case_text": "Source-backed ultrasound case narrative.",
            "chosen_form": "US_CASE",
            "explicit_form_choice": "US_CASE",
            "form_recommendations": ["US_CASE recommendation"],
            "draft_data": {
                "_type": "FORM",
                "form_type": "US_CASE",
                "fields": {"reflection": "I will document findings contemporaneously."},
            },
        }
    )
    content_before = {
        key: context.user_data[key]
        for key in (
            "case_text", "chosen_form", "explicit_form_choice", "form_recommendations", "draft_data"
        )
    }

    result = await handle_document_intent(update, context)

    assert result == AWAIT_APPROVAL
    assert {
        key: context.user_data[key]
        for key in (
            "case_text", "chosen_form", "explicit_form_choice", "form_recommendations", "draft_data"
        )
    } == content_before
    assert "_pending_doc" not in context.user_data
    if mode == "attach":
        assert context.user_data["attachment_path"] == temp_path
        assert os.path.exists(temp_path)
        assert "Video kept" in (sim.get_last_text() or "")
    else:
        assert "attachment_path" not in context.user_data
        assert not os.path.exists(temp_path)
        assert "Removed that video" in _all_visible_text(sim)

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_side_question_while_video_choice_pending_names_real_video_choices():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["_pending_doc"] = {"kind": "video"}
    before = dict(context.user_data)

    with patch(
        "bot.answer_question",
        new=AsyncMock(return_value="A CBD explores reasoning; DOPS observes a procedure."),
    ):
        result = await handle_mid_conversation_text(
            sim._make_text_update("What is the difference between CBD and DOPS?"),
            context,
        )

    assert result == AWAIT_DOC_INTENT
    assert context.user_data == before
    reply = (sim.get_last_text() or "").lower()
    assert "cbd explores reasoning" in reply
    assert "video choice is still waiting" in reply
    assert "attach or remove" in reply
    assert "both" not in reply


def test_video_choice_callbacks_remain_routed_after_content_advances():
    import inspect
    import bot

    source = inspect.getsource(bot.build_application)
    pending_block = source.split("AWAIT_DOC_INTENT:", 1)[1].split("],", 1)[0]
    assert "MessageHandler(filters.VOICE, handle_pending_media_context)" in pending_block
    for state_name in (
        "AWAIT_CASE_INPUT",
        "AWAIT_GATHERING",
        "AWAIT_FORM_CHOICE",
        "AWAIT_FORM_SEARCH",
        "AWAIT_TEMPLATE_REVIEW",
        "AWAIT_APPROVAL",
        "AWAIT_EDIT_FIELD",
        "AWAIT_EDIT_VALUE",
    ):
        state_block = source.split(f"{state_name}:", 1)[1].split("],", 1)[0]
        assert "handle_document_intent" in state_block
        assert r'^DOCUSE\|' in state_block


@pytest.mark.asyncio
async def test_rich_voice_context_advances_while_video_choice_stays_pending():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("")
    voice = MagicMock()
    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock()
    voice.get_file = AsyncMock(return_value=file_obj)
    update.message.text = None
    update.message.voice = voice
    update.message.audio = None
    update.message.photo = []
    update.message.video = None
    update.message.document = None

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy video content")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "portfolio-video.mp4",
        "kind": "video",
    }
    context.user_data["_pending_doc_context"] = "POCUS clip supplied as supporting evidence."

    spoken_case = (
        "Ultrasound log: I assessed a shocked patient in resus, performed focused cardiac "
        "ultrasound, documented poor LV function, escalated to my consultant, and reflected "
        "on documenting my findings sooner."
    )
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot.transcribe_voice", new=AsyncMock(return_value=spoken_case)):
        result = await handle_case_input(update, context)

    assert result == AWAIT_FORM_CHOICE
    assert context.user_data["chosen_form"] == "US_CASE"
    assert "POCUS clip supplied" in context.user_data["case_text"]
    assert "shocked patient" in context.user_data["case_text"]
    assert context.user_data["_pending_doc"]["path"] == temp_path
    assert os.path.exists(temp_path)
    assert "_pending_doc_context" not in context.user_data

    os.unlink(temp_path)


@pytest.mark.asyncio
async def test_pending_document_context_is_merged_after_read_choice():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|info")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy pdf content")
    context.user_data["_pending_doc"] = {"path": temp_path, "name": "atls.pdf"}
    context.user_data["_pending_doc_context"] = "I completed ATLS and have a certificate."

    async def fake_process(message, ctx, user_id, case_text, input_source):
        ctx.user_data["processed_case_text"] = case_text
        ctx.user_data["processed_input_source"] = input_source
        return AWAIT_FORM_CHOICE

    with patch('bot.extract_from_document', new=AsyncMock(return_value="Advanced Trauma Life Support certificate")), \
         patch('bot._process_case_text', new=AsyncMock(side_effect=fake_process)):
        result = await handle_document_intent(update, context)

    assert result == AWAIT_FORM_CHOICE
    assert "I completed ATLS" in context.user_data["processed_case_text"]
    assert "Advanced Trauma Life Support certificate" in context.user_data["processed_case_text"]
    assert context.user_data["processed_input_source"] == "document"
    assert "_pending_doc_context" not in context.user_data

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_filing_call_receives_attachment_path():
    """Verify that when a user saves a draft, the preserved attachment path is passed to route_filing."""
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("ACTION|approve")

    # Set up user data simulating a prepared draft and a cached attachment path
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy pdf content")

    context.user_data["attachment_path"] = temp_path
    context.user_data["attachment_name"] = "clinical-notes.pdf"
    # Upload consent is recorded: these tests cover path plumbing into
    # route_filing, which happens after the doctor taps "Attach it anyway".
    # The gate itself is covered in test_attachment_upload_consent.py.
    context.user_data["attachment_upload_confirmed"] = True
    context.user_data["chosen_form"] = "CBD"
    
    # Mock draft loading
    draft = FormDraft(form_type="CBD", fields={
        "date_of_encounter": "2026-05-27",
        "reflection": "test reflection"
    })
    
    with patch('bot.get_credentials', return_value=("testuser", "testpass")), \
         patch('bot._load_draft', return_value=draft), \
         patch('bot.route_filing', new=AsyncMock(return_value={"status": "success", "filled": ["reflection", "attachment"], "skipped": []})) as route_mock, \
         patch('bot.record_case_filed', new=AsyncMock()), \
         patch('bot.check_can_file', new=AsyncMock(return_value=(True, 1, 10, 'free'))):
         
        await handle_approval_approve(update, context)

    # Verify route_filing was called with a path renamed to the original
    # filename the user sent, not the random tempfile basename it was
    # downloaded under.
    route_mock.assert_called_once()
    filed_path = route_mock.call_args[1].get("attachment_path")
    assert filed_path is not None
    assert os.path.basename(filed_path) == "clinical-notes.pdf"
    assert filed_path != temp_path
    with open(filed_path, "rb") as f:
        assert f.read() == b"dummy pdf content"

    # Clean up
    if os.path.exists(temp_path):
        os.unlink(temp_path)
    if os.path.exists(filed_path):
        os.unlink(filed_path)
        os.rmdir(os.path.dirname(filed_path))


@pytest.mark.asyncio
async def test_filing_call_accepts_video_attachment_path():
    """MP4 video attachments should reach the Kaizen filer instead of being skipped."""
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("ACTION|approve")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy video content")

    context.user_data["attachment_path"] = temp_path
    context.user_data["attachment_name"] = "portfolio-video.mp4"
    # Upload consent is recorded: these tests cover path plumbing into
    # route_filing, which happens after the doctor taps "Attach it anyway".
    # The gate itself is covered in test_attachment_upload_consent.py.
    context.user_data["attachment_upload_confirmed"] = True
    context.user_data["attachment_kind"] = "video"
    context.user_data["chosen_form"] = "CBD"

    draft = FormDraft(form_type="CBD", fields={
        "date_of_encounter": "2026-05-27",
        "reflection": "I learned to document my own interpretation before attaching clinical videos.",
    })

    with patch('bot.get_credentials', return_value=("testuser", "testpass")), \
         patch('bot._load_draft', return_value=draft), \
         patch('bot.route_filing', new=AsyncMock(return_value={"status": "success", "filled": ["reflection", "attachment"], "skipped": []})) as route_mock, \
         patch('bot.record_case_filed', new=AsyncMock()), \
         patch('bot.check_can_file', new=AsyncMock(return_value=(True, 1, 10, 'free'))):
        await handle_approval_approve(update, context)

    route_mock.assert_called_once()
    filed_path = route_mock.call_args[1].get("attachment_path")
    assert filed_path is not None
    # Bot-named files are renamed to the suggested Kaizen convention on the
    # way to the filer; a name the doctor chose is left alone (covered above).
    basename = os.path.basename(filed_path)
    assert basename.endswith(".mp4")
    assert basename.count("-") >= 3, f"expected Category-Grade-Description-Date, got {basename}"
    assert not basename.startswith("portfolio-video")
    assert "Attachment skipped" not in _all_visible_text(sim)

    if os.path.exists(temp_path):
        os.unlink(temp_path)
    if filed_path and os.path.exists(filed_path):
        os.unlink(filed_path)
        os.rmdir(os.path.dirname(filed_path))


@pytest.mark.asyncio
async def test_video_reaches_filer_without_a_second_consent_prompt():
    """Consent for a video is taken at upload, not again after the draft.

    This previously asserted a save-time prompt for every video. In use that
    landed as the bot asking the same question twice — once when the video was
    received ("would you like to attach it?") and again after the draft was
    approved. The upload prompt now carries the retention warning, so the
    save-time gate fires only on identifiers discovered after upload.
    """
    import bot

    sim = BotSimulator()
    context = sim._make_context()
    approve_update = sim._make_callback_update("APPROVE|draft")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy video content")
    context.user_data.update(
        {
            "attachment_path": temp_path,
            "attachment_name": "portfolio-video.mp4",
            "attachment_kind": "video",
            "chosen_form": "US_CASE",
                "case_text": (
                    "I performed a focused ultrasound and documented my own findings. "
                    "I learned to record the findings contemporaneously and will do that next time."
                ),
        }
    )
    draft = FormDraft(
        form_type="US_CASE",
        fields={"reflection": "I will document my ultrasound findings contemporaneously."},
    )

    with patch("bot.get_credentials", return_value=("testuser", "testpass")), \
         patch("bot._load_draft", return_value=draft), \
         patch("bot._needs_filing_curriculum_choice", return_value=False), \
         patch("bot.route_filing", new=AsyncMock(return_value={
             "status": "success", "filled": ["reflection", "attachment"], "skipped": []
         })) as route_mock, \
         patch("bot.record_case_filed", new=AsyncMock()), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 10, "free"))):
        await handle_approval_approve(approve_update, context)

    route_mock.assert_awaited_once()
    assert ("📎 Attach it anyway", "ATTACH|yes") not in sim.get_last_buttons(), (
        "a video the doctor already chose to attach must not be re-queried"
    )

    filed_path = route_mock.call_args[1].get("attachment_path")
    if os.path.exists(temp_path):
        os.unlink(temp_path)
    if filed_path and filed_path != temp_path and os.path.exists(filed_path):
        os.unlink(filed_path)
        os.rmdir(os.path.dirname(filed_path))


def test_attachment_path_with_original_name_renames_to_original():
    """Random tempfile basenames get replaced with the user's real filename."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"cert bytes")

    try:
        result = _attachment_path_with_original_name(temp_path, "Moeed KH A Kind Life.pdf")
        assert result != temp_path
        assert os.path.basename(result) == "Moeed KH A Kind Life.pdf"
        with open(result, "rb") as f:
            assert f.read() == b"cert bytes"
    finally:
        os.unlink(temp_path)
        if result != temp_path and os.path.exists(result):
            os.unlink(result)
            os.rmdir(os.path.dirname(result))


def test_attachment_path_with_original_name_noop_when_already_matching():
    """No copy/rename happens if the path's basename already matches."""
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "clinical-notes.pdf")
    with open(path, "wb") as f:
        f.write(b"cert bytes")

    try:
        result = _attachment_path_with_original_name(path, "clinical-notes.pdf")
        assert result == path
    finally:
        os.unlink(path)
        os.rmdir(tmpdir)


def test_attachment_path_with_original_name_blank_original_returns_input():
    """A missing/blank original filename is a no-op, not an error."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name

    try:
        assert _attachment_path_with_original_name(temp_path, "") == temp_path
        assert _attachment_path_with_original_name(temp_path, None) == temp_path
    finally:
        os.unlink(temp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("input_type", ["text", "photo", "voice"])
async def test_attachment_path_not_added_for_other_types(input_type):
    """Verify that attachment metadata is not added for text, photo, or voice-only cases."""
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update('')
    
    if input_type == "text":
        update.message.text = "Patient presented with appendicitis."
    elif input_type == "photo":
        photo = MagicMock()
        file_obj = MagicMock()
        file_obj.download_to_drive = AsyncMock()
        photo.get_file = AsyncMock(return_value=file_obj)
        update.message.text = None
        update.message.photo = [photo]
    elif input_type == "voice":
        voice = MagicMock()
        file_obj = MagicMock()
        file_obj.download_to_drive = AsyncMock()
        voice.get_file = AsyncMock(return_value=file_obj)
        update.message.text = None
        update.message.voice = voice

    with patch('bot.has_credentials', return_value=True), \
         patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
         patch('bot.extract_from_image', new=AsyncMock(return_value="clinical text")), \
         patch('bot.transcribe_voice', new=AsyncMock(return_value="clinical text")), \
         patch('bot.get_training_level', return_value='ST5'), \
         patch('bot.get_curriculum', return_value='2025'), \
         patch('bot.recommend_form_types', new=AsyncMock(return_value=[])):
        
        await handle_case_input(update, context)

    assert "attachment_path" not in context.user_data


@pytest.mark.asyncio
async def test_filing_handles_missing_attachment_gracefully():
    """Verify that filing handles missing attachment gracefully (reports it as skipped, no crash)."""
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("ACTION|approve")

    # Set non-existent path
    context.user_data["attachment_path"] = "/nonexistent/file.pdf"
    context.user_data["attachment_name"] = "clinical-notes.pdf"
    # Upload consent is recorded: these tests cover path plumbing into
    # route_filing, which happens after the doctor taps "Attach it anyway".
    # The gate itself is covered in test_attachment_upload_consent.py.
    context.user_data["attachment_upload_confirmed"] = True
    context.user_data["chosen_form"] = "CBD"
    
    # Mock draft loading
    draft = FormDraft(form_type="CBD", fields={
        "date_of_encounter": "2026-05-27",
        "reflection": "test reflection"
    })
    
    with patch('bot.get_credentials', return_value=("testuser", "testpass")), \
         patch('bot._load_draft', return_value=draft), \
         patch('bot.route_filing', new=AsyncMock(return_value={"status": "success", "filled": ["reflection"], "skipped": []})) as route_mock, \
         patch('bot.record_case_filed', new=AsyncMock()), \
         patch('bot.check_can_file', new=AsyncMock(return_value=(True, 1, 10, 'free'))):
         
        await handle_approval_approve(update, context)

    # Verify route_filing was called with attachment_path=None because the file was missing
    route_mock.assert_called_once()
    assert route_mock.call_args[1].get("attachment_path") is None
    
    # Verify that the user was notified without making the saved draft feel failed.
    final_text = _all_visible_text(sim)
    assert "📎 Attachment not added\nFile was no longer available. Draft saved without the attachment." in final_text
    assert "Attachment skipped" not in final_text


# ── Multiple attachments on one case ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_attached_file_reaches_the_filer():
    """Reported: sending several files attached only the last one, silently.

    Each upload overwrote `attachment_path`, and because every upload had been
    acknowledged in chat there was nothing to tell the doctor the rest had been
    dropped.
    """
    import bot

    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("APPROVE|draft")

    paths = []
    for suffix in (".mp4", ".png", ".pdf"):
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(b"bytes")
            paths.append(f.name)

    for path, kind in zip(paths, ("video", "image", "document")):
        bot._add_case_attachment(context, path, os.path.basename(path), kind)
    context.user_data.update({
        "chosen_form": "US_CASE",
        "case_text": (
            "I performed the scan, escalated to cardiology, and documented my findings. "
            "I learned to document sooner and will do that next time."
        ),
        "attachment_upload_confirmed": True,
    })

    draft = FormDraft(form_type="US_CASE", fields={"reflection": "I will document sooner."})

    with patch("bot.get_credentials", return_value=("u", "p")), \
         patch("bot._load_draft", return_value=draft), \
         patch("bot._needs_filing_curriculum_choice", return_value=False), \
         patch("bot.route_filing", new=AsyncMock(return_value={
             "status": "success", "filled": ["reflection", "attachment"], "skipped": []
         })) as route_mock, \
         patch("bot.record_case_filed", new=AsyncMock()), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 10, "free"))):
        await handle_approval_approve(update, context)

    route_mock.assert_awaited_once()
    filed = route_mock.call_args[1].get("attachment_path")
    assert isinstance(filed, list), f"all three files must be filed, got {filed!r}"
    assert len(filed) == 3

    for path in paths:
        if os.path.exists(path):
            os.unlink(path)


@pytest.mark.asyncio
async def test_a_single_attachment_still_files_as_a_plain_string():
    """Keeps the existing contract for the common one-file case."""
    import bot

    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("APPROVE|draft")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"bytes")
        path = f.name
    bot._add_case_attachment(context, path, "notes.pdf", "document")
    context.user_data.update({
        "chosen_form": "CBD",
        "case_text": (
            "I led the assessment and escalated appropriately. "
            "I learned to document the escalation decision more clearly next time."
        ),
        "attachment_upload_confirmed": True,
    })

    draft = FormDraft(
        form_type="CBD",
        fields={"reflection": "I learned to document the escalation decision more clearly."},
    )

    with patch("bot.get_credentials", return_value=("u", "p")), \
         patch("bot._load_draft", return_value=draft), \
         patch("bot._needs_filing_curriculum_choice", return_value=False), \
         patch("bot.route_filing", new=AsyncMock(return_value={
             "status": "success", "filled": ["reflection", "attachment"], "skipped": []
         })) as route_mock, \
         patch("bot.record_case_filed", new=AsyncMock()), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 10, "free"))):
        await handle_approval_approve(update, context)

    filed = route_mock.call_args[1].get("attachment_path")
    assert isinstance(filed, str)

    if os.path.exists(path):
        os.unlink(path)


def test_queueing_the_same_file_twice_does_not_duplicate_it():
    import bot

    sim = BotSimulator()
    context = sim._make_context()

    assert bot._add_case_attachment(context, "/tmp/a.png", "a.png", "image") is True
    assert bot._add_case_attachment(context, "/tmp/a.png", "a.png", "image") is False
    assert len(bot._case_attachments(context)) == 1


def test_a_pre_list_draft_still_reports_its_single_attachment():
    """Drafts restored from persistence carry only the old singular keys."""
    import bot

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update({
        "attachment_path": "/tmp/legacy.pdf",
        "attachment_name": "legacy.pdf",
        "attachment_kind": "document",
    })

    items = bot._case_attachments(context)
    assert [i["path"] for i in items] == ["/tmp/legacy.pdf"]


# ── Albums (several files sent at once) ──────────────────────────────────────
# Telegram delivers an album as separate updates sharing a media_group_id.
# With one `_pending_doc` slot and no media handler in AWAIT_DOC_INTENT, the
# second and third files matched nothing and were dropped without a word.


def test_album_files_all_buffer_instead_of_overwriting():
    import bot

    sim = BotSimulator()
    context = sim._make_context()

    bot._queue_pending_media(context, {"path": "/tmp/a.mp4", "name": "a.mp4", "kind": "video"})
    bot._queue_pending_media(context, {"path": "/tmp/b.mp4", "name": "b.mp4", "kind": "video"})
    bot._queue_pending_media(context, {"path": "/tmp/c.jpg", "name": "c.jpg", "kind": "image"})

    items = bot._pending_media_items(context)
    assert [i["path"] for i in items] == ["/tmp/a.mp4", "/tmp/b.mp4", "/tmp/c.jpg"]
    # The single-file body still reads the first item.
    assert context.user_data["_pending_doc"]["path"] == "/tmp/a.mp4"


def test_prompt_names_everything_that_arrived():
    import bot

    sim = BotSimulator()
    context = sim._make_context()
    for path, kind in (("/tmp/a.mp4", "video"), ("/tmp/b.mp4", "video"), ("/tmp/c.jpg", "image")):
        bot._queue_pending_media(context, {"path": path, "name": path, "kind": kind})

    assert bot._describe_pending_media(context) == "2 videos and 1 image"
    text = bot._pending_media_prompt_text(context, single="ignored")
    assert "2 videos and 1 image" in text
    assert "applies to all of them" in text


def test_single_file_prompt_is_unchanged():
    import bot

    sim = BotSimulator()
    context = sim._make_context()
    bot._queue_pending_media(context, {"path": "/tmp/a.mp4", "name": "a.mp4", "kind": "video"})

    text = bot._pending_media_prompt_text(context, single="🎞️ Video received — attach it?")
    assert text.startswith("🎞️ Video received — attach it?")


def test_video_only_upload_never_offers_to_read_it():
    """The bot refuses to interpret video, so it must not offer to."""
    import bot

    sim = BotSimulator()
    context = sim._make_context()
    bot._queue_pending_media(context, {"path": "/tmp/a.mp4", "name": "a.mp4", "kind": "video"})
    bot._queue_pending_media(context, {"path": "/tmp/b.mp4", "name": "b.mp4", "kind": "video"})

    data = [b.callback_data for row in bot._build_pending_media_keyboard(context).inline_keyboard for b in row]
    assert "DOCUSE|info" not in data
    assert "DOCUSE|attach" in data


def test_mixed_upload_offers_the_read_options():
    import bot

    sim = BotSimulator()
    context = sim._make_context()
    bot._queue_pending_media(context, {"path": "/tmp/a.mp4", "name": "a.mp4", "kind": "video"})
    bot._queue_pending_media(
        context,
        {"path": "/tmp/c.jpg", "name": "c.jpg", "kind": "image", "text": "Discharge summary: ..."},
    )

    data = [b.callback_data for row in bot._build_pending_media_keyboard(context).inline_keyboard for b in row]
    assert "DOCUSE|info" in data


def test_media_handlers_are_registered_in_the_intent_state():
    """The actual cause of the drop: nothing in AWAIT_DOC_INTENT matched a
    photo, video or document, so album siblings hit no handler at all."""
    import inspect

    import bot

    source = inspect.getsource(bot.build_application)
    intent_block = source.split("AWAIT_DOC_INTENT: [", 1)[1].split("]", 1)[0]
    for expected in ("filters.PHOTO", "filters.VIDEO", "filters.Document.ALL"):
        assert expected in intent_block, f"{expected} missing from AWAIT_DOC_INTENT"


def test_later_files_get_distinct_names():
    import bot

    sim = BotSimulator()
    context = sim._make_context()

    first = bot._numbered_media_name(context, "portfolio-image", ".jpg")
    bot._queue_pending_media(context, {"path": "/tmp/1.jpg", "name": first, "kind": "image"})
    second = bot._numbered_media_name(context, "portfolio-image", ".jpg")

    assert first == "portfolio-image.jpg"
    assert second == "portfolio-image-2.jpg"


def test_image_buttons_do_not_promise_interpretation():
    """"Use for drafting" implied the bot would read an ECG or ultrasound.

    It refuses to — it extracts text and asks the doctor for the clinical
    account. The label has to match that, or the choice looks bigger than it is.
    """
    import bot

    labels = [b.text for row in bot._build_image_intent_keyboard().inline_keyboard for b in row]
    assert any("Read text" in label for label in labels)
    assert not any("drafting" in label.lower() for label in labels)


@pytest.mark.asyncio
async def test_album_shows_one_prompt_that_updates_not_three():
    """Three files produced three stacked prompts, each with live buttons —
    "Video received", then "2 videos received", then "2 videos and 1 image".
    Buffering the files was right; leaving three questions on screen was not.
    """
    from unittest.mock import AsyncMock, MagicMock

    import bot

    sim = BotSimulator()
    context = sim._make_context()
    context.bot = MagicMock()
    context.bot.edit_message_text = AsyncMock()

    def _ack(message_id):
        ack = MagicMock()
        ack.chat_id = 99
        ack.message_id = message_id
        ack.edit_text = AsyncMock()
        ack.delete = AsyncMock()
        return ack

    first, second, third = _ack(1), _ack(2), _ack(3)

    bot._queue_pending_media(context, {"path": "/tmp/a.mp4", "name": "a.mp4", "kind": "video"})
    await bot._show_pending_media_prompt(context, first, single="🎞️ Video received")

    bot._queue_pending_media(context, {"path": "/tmp/b.mp4", "name": "b.mp4", "kind": "video"})
    await bot._show_pending_media_prompt(context, second, single="🎞️ Video received")

    bot._queue_pending_media(
        context,
        {"path": "/tmp/c.jpg", "name": "c.jpg", "kind": "image", "text": "Discharge summary"},
    )
    await bot._show_pending_media_prompt(context, third, single="📷 Image received")

    # Only the first ack ever becomes a prompt.
    first.edit_text.assert_awaited_once()
    second.edit_text.assert_not_awaited()
    third.edit_text.assert_not_awaited()

    # The later acks are removed rather than left as stray messages.
    second.delete.assert_awaited_once()
    third.delete.assert_awaited_once()

    # And the one surviving prompt was rewritten to describe everything.
    assert context.bot.edit_message_text.await_count == 2
    final = context.bot.edit_message_text.await_args.kwargs
    assert final["message_id"] == 1
    assert "2 videos and 1 image" in final["text"]


@pytest.mark.asyncio
async def test_prompt_reanchors_if_the_original_was_deleted():
    """A deleted prompt must not swallow the file silently."""
    from unittest.mock import AsyncMock, MagicMock

    import bot

    sim = BotSimulator()
    context = sim._make_context()
    context.bot = MagicMock()
    context.bot.edit_message_text = AsyncMock(side_effect=Exception("message to edit not found"))
    context.user_data["_pending_media_prompt"] = {"chat_id": 99, "message_id": 1}

    ack = MagicMock()
    ack.chat_id = 99
    ack.message_id = 7
    ack.edit_text = AsyncMock()
    ack.delete = AsyncMock()

    bot._queue_pending_media(context, {"path": "/tmp/a.mp4", "name": "a.mp4", "kind": "video"})
    await bot._show_pending_media_prompt(context, ack, single="🎞️ Video received")

    ack.edit_text.assert_awaited_once()
    assert context.user_data["_pending_media_prompt"]["message_id"] == 7


# ── Kaizen document naming (London EM / RCEM suggested convention) ────────────


def test_bot_named_files_follow_the_suggested_convention():
    """Category-Grade-Description-Date, per the RCEM/London guidance."""
    import bot

    name = bot._kaizen_document_name("portfolio-image.jpg", "US_CASE", "HIGHER")
    assert name.startswith("POCUS-Higher-")
    assert name.endswith(".jpg")


def test_a_file_the_doctor_named_is_never_renamed():
    """The convention is explicitly advisory. Overwriting a name the doctor
    chose would be the tool overruling them on optional guidance."""
    import bot

    for original in ("ALS certificate.pdf", "Moeed KH A Kind Life.pdf", "scan.png"):
        assert bot._kaizen_document_name(original, "US_CASE", "HIGHER") == original


def test_several_files_on_one_case_keep_distinct_names():
    """Identical names are indistinguishable in Kaizen and would defeat the
    filer's per-filename upload confirmation."""
    import bot

    first = bot._kaizen_document_name("portfolio-image.jpg", "US_CASE", "HIGHER")
    second = bot._kaizen_document_name("portfolio-image-2.jpg", "US_CASE", "HIGHER")
    third = bot._kaizen_document_name("portfolio-video-3.mp4", "US_CASE", "HIGHER")
    assert len({first, second, third}) == 3


def test_unknown_form_falls_back_to_other_not_a_wrong_category():
    import bot

    name = bot._kaizen_document_name("portfolio-image.jpg", "REFLECT_LOG", None)
    assert name.startswith("Other-")


def test_draft_preview_does_not_repeat_one_marker_on_every_line():
    """An ultrasound reflection rendered eight identical 📌 pins, because every
    unmapped field fell back to the same default. A marker on everything marks
    nothing."""
    import inspect

    import bot

    source = inspect.getsource(bot._format_generic_draft)
    assert 'FIELD_EMOJIS.get(key, "📌")' not in source


# ── Files sent mid-conversation ──────────────────────────────────────────────
# Only handle_case_input could attach anything. A clip sent after the form was
# chosen, or once the draft was on screen, went to a handler that read it for
# text and deleted it — so remembering a file late meant losing it silently.


@pytest.mark.asyncio
async def test_photo_sent_with_a_draft_on_screen_is_attached():
    import bot
    from models import CBDData

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update({
        "case_text": "58M chest pain, I led the assessment and escalated.",
        "case_input_source": "text",
    })
    bot._store_draft(context, CBDData(patient_presentation="Chest pain"))

    update = sim._make_text_update('')
    photo = MagicMock()
    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock(
        side_effect=lambda path: open(path, "wb").write(b"jpeg bytes")
    )
    photo.get_file = AsyncMock(return_value=file_obj)
    update.message.photo = [photo]
    update.message.text = None
    update.message.caption = "the scan I mentioned"

    with patch("bot.extract_from_image", new=AsyncMock(return_value="Findings")), \
         patch("bot.extract_cbd_data", new=AsyncMock(return_value=CBDData(patient_presentation="Chest pain"))), \
         patch("bot.get_voice_profile", return_value=None):
        await bot.handle_approval_media_feedback(update, context)

    queued = bot._case_attachments(context)
    assert queued, "a photo sent with a draft on screen must still be attached"
    assert queued[0]["kind"] == "image"

    for item in queued:
        if os.path.exists(item["path"]):
            os.unlink(item["path"])


@pytest.mark.asyncio
async def test_document_sent_during_template_review_is_attached():
    import bot

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update({"chosen_form": "CBD", "case_text": "Original case."})

    update = sim._make_text_update('')
    doc = MagicMock()
    doc.file_name = "ecg-report.pdf"
    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock(
        side_effect=lambda path: open(path, "wb").write(b"%PDF-1.4")
    )
    doc.get_file = AsyncMock(return_value=file_obj)
    update.message.document = doc
    update.message.text = None

    with patch("bot.extract_from_document", new=AsyncMock(return_value="Report text")), \
         patch("bot._accumulate_and_refresh", new=AsyncMock(return_value=0)):
        await bot.handle_template_review_media(update, context)

    queued = bot._case_attachments(context)
    assert [i["name"] for i in queued] == ["ecg-report.pdf"]

    for item in queued:
        if os.path.exists(item["path"]):
            os.unlink(item["path"])


def test_the_mid_conversation_helper_refuses_unsupported_types():
    """Kaizen rejects them, so queuing one would report a phantom attachment."""
    import bot

    sim = BotSimulator()
    context = sim._make_context()

    with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
        f.write(b"bytes")
        path = f.name

    assert bot._cache_and_queue_attachment(context, path, "notes.xyz", "document") is False
    assert bot._case_attachments(context) == []
    os.unlink(path)


def test_a_vanished_temp_file_is_not_queued():
    import bot

    sim = BotSimulator()
    context = sim._make_context()
    assert bot._cache_and_queue_attachment(context, "/tmp/gone.jpg", "gone.jpg", "image") is False
    assert bot._case_attachments(context) == []
