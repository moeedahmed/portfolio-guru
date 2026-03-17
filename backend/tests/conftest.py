import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

@pytest.fixture
def mock_update():
    """Fake Telegram update — simulates a user sending a message."""
    update = MagicMock()
    update.effective_user.id = 99999999
    update.effective_user.first_name = "TestDoctor"
    update.message = MagicMock()
    update.message.text = None
    update.message.reply_text = AsyncMock()
    update.message.edit_text = AsyncMock()
    update.callback_query = None
    return update

@pytest.fixture
def mock_context():
    """Fake bot context with user_data store."""
    context = MagicMock()
    context.bot = AsyncMock()
    context.user_data = {}
    return context

@pytest.fixture
def mock_callback_update():
    """Fake Telegram update for button taps."""
    update = MagicMock()
    update.effective_user.id = 99999999
    update.callback_query = MagicMock()
    update.callback_query.data = None
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.text = "test"
    return update
