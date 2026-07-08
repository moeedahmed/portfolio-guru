"""Shared reply policy tests for Telegram/WhatsApp parity."""

from __future__ import annotations

import pytest

from channel_actions import render_numbered, to_telegram_button_rows
from channel_reply_policy import copy_mode_for_intent, select_deterministic_reply
from conversational_router import ConversationalIntent
from message_policy import render_message


@pytest.mark.parametrize(
    ("text", "expected_terms"),
    [
        (
            "What can you help me with?",
            ("Portfolio Guru turns your case notes", "Kaizen"),
        ),
        (
            "How do I set up Kaizen?",
            ("Connect Kaizen", "send /start"),
        ),
        (
            "Can you help me with my ARCP portfolio?",
            ("RCEM", "WPBA", "drafts"),
        ),
        (
            "I need help with a CBD",
            ("CBD", "best fit", "case details"),
        ),
        (
            "Can you give me medical advice about chest pain?",
            ("advise", "prescribing", "portfolio draft"),
        ),
        (
            "How much does this cost?",
            ("account", "billing", "payment details"),
        ),
        (
            "asdf random nonsense",
            ("portfolio evidence", "portfolio questions", "Kaizen"),
        ),
    ],
)
def test_short_side_questions_use_shared_deterministic_replies(text, expected_terms):
    reply = select_deterministic_reply(text, include_first_contact=True)

    assert reply is not None
    rendered = reply.full_text()
    assert "Please describe the clinical case you want to document" not in rendered
    for term in expected_terms:
        assert term in rendered


def test_kaizen_setup_reply_has_same_core_copy_across_channel_renderers():
    reply = select_deterministic_reply("How do I set up Kaizen?")

    assert reply is not None
    assert reply.body == render_message("kaizen_setup_guide")
    assert reply.full_text() in render_numbered(reply)
    assert "1. 🔗 Connect Kaizen" in render_numbered(reply)
    assert "2. ⚙️ Settings" in render_numbered(reply)
    assert to_telegram_button_rows(reply) == [
        [{"text": "🔗 Connect Kaizen", "callback_data": "ACTION|setup"}],
        [{"text": "⚙️ Settings", "callback_data": "ACTION|settings"}],
    ]


@pytest.mark.parametrize(
    "intent",
    [
        ConversationalIntent.SETUP_OR_CREDENTIALS,
        ConversationalIntent.SAFETY_OR_MEDICAL_ADVICE,
        ConversationalIntent.ACCOUNT_OR_BILLING,
        ConversationalIntent.OUT_OF_SCOPE,
    ],
)
def test_high_risk_intents_use_static_copy(intent):
    assert copy_mode_for_intent(intent) == "static"


@pytest.mark.parametrize(
    "intent",
    [
        ConversationalIntent.PORTFOLIO_QUESTION,
        ConversationalIntent.HELP_OR_CAPABILITY,
        ConversationalIntent.EDIT_DRAFT,
        ConversationalIntent.FILE_TO_KAIZEN,
        ConversationalIntent.UNKNOWN,
    ],
)
def test_low_risk_guidance_intents_allow_controlled_flexible_copy(intent):
    assert copy_mode_for_intent(intent) == "controlled-flexible"
