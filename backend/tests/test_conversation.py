import pytest
import asyncio
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import AsyncMock, MagicMock, patch

class TestBotImport:
    """Smoke test — verify bot imports cleanly."""

    def test_bot_imports(self):
        """Bot must import without errors."""
        import bot
        assert bot is not None

    def test_conversation_states_defined(self):
        """All conversation states must be importable."""
        from bot import (AWAIT_CASE_INPUT, AWAIT_FORM_CHOICE, AWAIT_APPROVAL,
                         AWAIT_EDIT_FIELD, AWAIT_EDIT_VALUE, AWAIT_TRAINING_LEVEL)
        for state in [AWAIT_CASE_INPUT, AWAIT_FORM_CHOICE, AWAIT_APPROVAL,
                      AWAIT_EDIT_FIELD, AWAIT_EDIT_VALUE, AWAIT_TRAINING_LEVEL]:
            assert isinstance(state, int)

    def test_form_emojis_defined(self):
        """FORM_EMOJIS must exist and be a dict."""
        from bot import FORM_EMOJIS
        assert isinstance(FORM_EMOJIS, dict)
        assert len(FORM_EMOJIS) > 0

class TestKeyboardBuilding:
    """Verify keyboards are built with correct layout."""

    def test_form_choice_keyboard_two_per_row(self):
        """Recommendation keyboard must use 2-per-row layout."""
        from bot import _build_form_choice_keyboard
        from models import FormTypeRecommendation
        from extractor import FORM_UUIDS

        recs = [
            FormTypeRecommendation(form_type="CBD", rationale="test", uuid=FORM_UUIDS.get("CBD")),
            FormTypeRecommendation(form_type="DOPS", rationale="test", uuid=FORM_UUIDS.get("DOPS")),
            FormTypeRecommendation(form_type="LAT", rationale="test", uuid=FORM_UUIDS.get("LAT")),
            FormTypeRecommendation(form_type="ACAT", rationale="test", uuid=FORM_UUIDS.get("ACAT")),
        ]
        keyboard = _build_form_choice_keyboard(recs, curriculum="2025")
        rows = keyboard.inline_keyboard

        form_rows = [r for r in rows if any("FORM|" in (b.callback_data or "") for b in r)
                     and not any("show_all" in (b.callback_data or "") for b in r)]
        for row in form_rows:
            assert len(row) <= 2, f"Row has {len(row)} buttons — expected max 2"
