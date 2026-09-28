"""The /privacy notice: short, layered, and never missing a UK GDPR point.

/privacy is a summary that fits one phone screen plus a details card behind a
button. Shortening either must not drop anything a privacy notice needs, so
this file pins each point to the layer that carries it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import bot

SUMMARY_FACTS = (
    "EM Gurus",  # who is responsible
    "health data",  # what data
    "Kaizen login",
    "explicit consent",  # why
    "Google Gemini on Vertex AI, UK (London)",  # recipients
    "Kaizen, when you approve a save",
    "encrypted, never sent to the AI",
    "Drafts only, never submitted to a supervisor",
    "/reset withdraws consent and erases your data",
)

DETAILS_FACTS = (
    "NHS, hospital and record numbers",  # what is stripped
    "titled names (Mr, Mrs, Dr)",
    "Other names can slip through",
    "Files you attach go to Kaizen unchanged",
    "24 hours",  # retention
    "180 days",
    "Art. 9(2)(a)",  # lawful basis
    "Art. 6(1)(b)",
    "correct, restrict, move or erase",  # rights
    "/reset",
    "ico.org.uk/make-a-complaint",  # complaints
    "portfolio@emgurus.com",  # privacy contact
)


@pytest.mark.parametrize("fact", SUMMARY_FACTS)
def test_summary_keeps_fact(fact):
    assert fact in bot._PRIVACY_SUMMARY_TEXT


@pytest.mark.parametrize("fact", DETAILS_FACTS)
def test_details_keep_fact(fact):
    assert fact in bot._PRIVACY_DETAILS_TEXT


def test_both_layers_stay_short():
    # Fixed text only; the consent status line is added at send time.
    summary = bot._PRIVACY_SUMMARY_TEXT
    assert len(summary) <= 560 and summary.count("\n") + 1 <= 10
    details = bot._PRIVACY_DETAILS_TEXT
    assert len(details) <= 900 and details.count("\n") + 1 <= 12
    for text in (summary, details):
        assert "—" not in text  # no em-dashes in the notice


def _query(data):
    message = SimpleNamespace(edit_text=AsyncMock())
    return SimpleNamespace(data=data, message=message)


@pytest.mark.asyncio
async def test_more_detail_then_back_swaps_layers():
    context = SimpleNamespace(user_data={})
    with patch.object(bot.consent, "get_consent_status", AsyncMock(return_value=None)):
        q = _query("INFO|privacy_details")
        await bot._show_privacy_layer(q, 1, context)
        text = q.message.edit_text.call_args.args[0]
        assert text.startswith("🔐 Privacy details")
        markup = q.message.edit_text.call_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == "INFO|privacy_summary"

        q = _query("INFO|privacy_summary")
        await bot._show_privacy_layer(q, 1, context)
        text = q.message.edit_text.call_args.args[0]
        assert text.startswith("🔐 Privacy & consent")
        assert "No consent recorded yet" in text
        markup = q.message.edit_text.call_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == "INFO|privacy_details"


def test_passwordless_line_only_when_offered():
    with patch.object(bot.kaizen_connection, "passwordless_offered_to", return_value=False):
        assert "Don't save my login: " not in bot._privacy_details_text(1)
    with patch.object(bot.kaizen_connection, "passwordless_offered_to", return_value=True):
        text = bot._privacy_details_text(1)
    assert "Don't save my login: " in text
    assert text.index("Don't save my login: ") < text.index("Lawful basis")
