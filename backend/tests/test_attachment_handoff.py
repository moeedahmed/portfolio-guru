import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from bot import (
    AWAIT_CASE_INPUT,
    AWAIT_DOC_INTENT,
    AWAIT_FORM_CHOICE,
    _attachment_path_with_original_name,
    handle_approval_approve,
    handle_case_input,
    handle_document_intent,
    handle_mid_conversation_text,
)
from tests.bot_simulator import BotSimulator
from extractor import FormDraft


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
    assert ("📝 Read as case info", "DOCUSE|info") in buttons
    assert ("📎 Attach only", "DOCUSE|attach") in buttons
    assert ("📎 Read + attach", "DOCUSE|both") in buttons
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
    extract_mock.assert_not_called()
    buttons = sim.get_last_buttons()
    assert ("📝 Use for drafting", "DOCUSE|info") in buttons
    assert ("📎 Attach only", "DOCUSE|attach") in buttons
    assert ("📎 Use + attach", "DOCUSE|both") in buttons
    assert ("❌ Remove image", "DOCUSE|ignore") in buttons

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

    assert result == AWAIT_DOC_INTENT
    assert context.user_data["_pending_doc"]["kind"] == "video"
    assert context.user_data["_pending_doc"]["name"] == "portfolio-video.mp4"
    assert context.user_data["_pending_doc_context"] == update.message.caption
    assert os.path.exists(context.user_data["_pending_doc"]["path"])
    buttons = sim.get_last_buttons()
    assert ("📎 Attach video", "DOCUSE|attach") in buttons
    assert ("❌ Remove video", "DOCUSE|ignore") in buttons

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

    assert result == AWAIT_DOC_INTENT
    transcribe_mock.assert_not_called()
    document_extract.assert_not_called()
    pending_doc = context.user_data["_pending_doc"]
    assert pending_doc["kind"] == "video"
    assert pending_doc["name"] == "portfolio-video.mp4"
    assert context.user_data["_pending_doc_context"] == update.message.caption
    buttons = sim.get_last_buttons()
    assert ("📎 Attach video", "DOCUSE|attach") in buttons
    assert ("❌ Remove video", "DOCUSE|ignore") in buttons
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

    assert result == AWAIT_CASE_INPUT
    extract_mock.assert_not_called()
    assert context.user_data["attachment_path"] == temp_path
    assert context.user_data["attachment_name"] == "evidence.pdf"
    assert "case_text" not in context.user_data
    assert "attached to the Kaizen draft" in sim.get_last_text()
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

    assert result == AWAIT_CASE_INPUT
    extract_mock.assert_not_called()
    assert context.user_data["attachment_path"] == temp_path
    assert context.user_data["attachment_name"] == "portfolio-image.jpg"
    assert "case_text" not in context.user_data
    assert "will be attached to the Kaizen draft" in sim.get_last_text()
    assert "send your own interpretation/context" in sim.get_last_text()
    assert "portfolio-image.jpg" not in _all_visible_text(sim)

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

    assert result == AWAIT_CASE_INPUT
    document_extract.assert_not_called()
    image_extract.assert_not_called()
    assert context.user_data["attachment_path"] == temp_path
    assert context.user_data["attachment_name"] == "portfolio-video.mp4"
    assert context.user_data["attachment_kind"] == "video"
    assert "case_text" not in context.user_data
    assert "will be attached to the Kaizen draft" in sim.get_last_text()
    assert "won't interpret clinical videos" in sim.get_last_text()
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
    assert attach_result == AWAIT_CASE_INPUT

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

    with patch('bot._process_case_text', new=AsyncMock(return_value=AWAIT_FORM_CHOICE)) as process_case:
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
    assert os.path.basename(filed_path) == "portfolio-video.mp4"
    assert "Attachment skipped" not in _all_visible_text(sim)

    if os.path.exists(temp_path):
        os.unlink(temp_path)
    if filed_path and os.path.exists(filed_path):
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
    assert "📎 Attachment not added: file was no longer available. Draft saved without it." in final_text
    assert "Attachment skipped" not in final_text
