import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from bot import handle_case_input, handle_approval_approve, AWAIT_FORM_CHOICE, AWAIT_APPROVAL
from tests.bot_simulator import BotSimulator
from extractor import FormDraft

@pytest.mark.asyncio
async def test_document_case_stores_attachment_path():
    """Verify that document cases preserve the attachment in a cache directory and save metadata."""
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
         patch('bot.extract_from_document', new=AsyncMock(return_value="Patient presented with chest pain...")), \
         patch('bot.get_training_level', return_value='ST5'), \
         patch('bot.get_curriculum', return_value='2025'), \
         patch('bot.recommend_form_types', new=AsyncMock(return_value=[])):
        
        result = await handle_case_input(update, context)

    assert "attachment_path" in context.user_data
    assert context.user_data["attachment_name"] == "clinical-notes.pdf"
    assert os.path.exists(context.user_data["attachment_path"])
    
    # Clean up the cached file
    if os.path.exists(context.user_data["attachment_path"]):
        os.unlink(context.user_data["attachment_path"])


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

    # Verify route_filing was called with the correct attachment_path
    route_mock.assert_called_once()
    assert route_mock.call_args[1].get("attachment_path") == temp_path

    # Clean up
    if os.path.exists(temp_path):
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
    
    # Verify that the user was notified about the skipped attachment in the final message
    any_missing_msg = any("Attachment skipped: file missing" in str(msg) for msg in sim.messages_sent)
    assert any_missing_msg

