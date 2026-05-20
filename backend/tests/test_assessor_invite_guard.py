import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kaizen_form_filer import _fill_assessor_invite, _verify_sent_to_assessor
import kaizen_form_filer


class _LocatorList:
    def __init__(self, items):
        self._items = items

    @property
    def first(self):
        return self

    async def count(self):
        return len(self._items) or 1

    async def all(self):
        return self._items

    async def click(self):
        return None

    async def type(self, *args, **kwargs):
        return None


class _Suggestion:
    def __init__(self, text):
        self.text = text
        self.clicked = False

    async def inner_text(self):
        return self.text

    async def click(self):
        self.clicked = True


@pytest.mark.asyncio
async def test_assessor_invite_requires_expected_name_match(monkeypatch):
    async def noop_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr("kaizen_form_filer.asyncio.sleep", noop_sleep)

    wrong = _Suggestion("Different Person")
    right = _Suggestion("Ahmed Mahdi")
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=True)
    page.keyboard.press = AsyncMock()
    page.locator.side_effect = lambda selector: _LocatorList([wrong, right]) if "tt-suggestion" in selector else _LocatorList([object()])

    assert await _fill_assessor_invite(page, "Ahmed Mahdi", "Ahmed Mahdi") is True
    assert not wrong.clicked
    assert not right.clicked
    assert page.keyboard.press.await_count == 4  # Meta+A, ArrowDown twice, Enter


@pytest.mark.asyncio
async def test_assessor_invite_refuses_non_matching_suggestions(monkeypatch):
    async def noop_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr("kaizen_form_filer.asyncio.sleep", noop_sleep)

    suggestion = _Suggestion("Different Person")
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=True)
    page.keyboard.press = AsyncMock()
    page.locator.side_effect = lambda selector: _LocatorList([suggestion]) if "tt-suggestion" in selector else _LocatorList([object()])

    assert await _fill_assessor_invite(page, "Ahmed Mahdi", "Ahmed Mahdi") is False
    assert not suggestion.clicked


@pytest.mark.asyncio
async def test_verify_sent_to_assessor_rejects_trainee_draft_controls():
    page = MagicMock()
    page.inner_text = AsyncMock(return_value="Actions Fill in Delete Preview")

    assert await _verify_sent_to_assessor(page, "Ahmed Mahdi") is False


@pytest.mark.asyncio
async def test_verify_sent_to_assessor_accepts_awaiting_response_to_named_assessor():
    page = MagicMock()
    page.inner_text = AsyncMock(return_value="Awaiting response from Ahmed Mahdi")

    assert await _verify_sent_to_assessor(page, "Ahmed Mahdi") is True


def test_kc_ticking_supports_short_kc_codes():
    assert "Key Capability" in kaizen_form_filer.TICK_KC_JS
    assert "KC" in kaizen_form_filer.TICK_KC_JS
