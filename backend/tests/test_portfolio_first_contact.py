"""First-contact classifier for the channel-neutral inbound boundary.

These tests pin the deterministic behaviour the WhatsApp bridge relies on so a
first message like ``/start``, ``hi``, or ``what can you do?`` is answered with
onboarding copy instead of a "describe the clinical case" demand, while a real
case still routes to the engine. No network, no LLM, no Telegram.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from message_policy import render_message
from portfolio_first_contact import (
    FirstContactKind,
    classify_first_contact,
    first_contact_reply,
)


@pytest.mark.parametrize(
    "text",
    [
        "/start",
        "start",
        "Start",
        "  /Start  ",
        "restart",
        "begin",
        "hi",
        "Hi!",
        "hello",
        "hello there",
        "hey",
        "hey there",
        "yo",
        "good morning",
        "Good Evening",
    ],
)
def test_start_and_greetings_are_onboarding(text: str):
    assert classify_first_contact(text) is FirstContactKind.START_OR_GREETING


@pytest.mark.parametrize(
    "text",
    [
        "help",
        "features",
        "what can you do?",
        "how does this work?",
        "what do you do?",
    ],
)
def test_capability_questions_are_capability(text: str):
    assert classify_first_contact(text) is FirstContactKind.CAPABILITY


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "58M chest pain, CBD reflection",
        "I completed an ED sepsis QI project with baseline audit and re-audit.",
        # A real case that merely opens with a greeting word must NOT be diverted.
        "hi, 62F presented with sudden onset chest pain, I led the resus and intubated",
        "thanks",
    ],
)
def test_case_and_blank_turns_route_to_engine(text):
    assert classify_first_contact(text) is FirstContactKind.CASE


def test_start_reply_is_the_fixed_welcome_copy():
    reply = first_contact_reply(FirstContactKind.START_OR_GREETING)
    assert reply is not None
    assert reply.body == render_message("welcome_disconnected")
    # Onboarding copy must orient the user, not demand a clinical case first.
    assert "Portfolio Guru" in reply.body
    assert "describe the clinical case" not in reply.body.lower()


def test_capability_reply_is_the_fixed_overview_copy():
    reply = first_contact_reply(FirstContactKind.CAPABILITY)
    assert reply is not None
    assert reply.body == render_message("capability_overview")


def test_case_kind_has_no_onboarding_reply():
    assert first_contact_reply(FirstContactKind.CASE) is None


def test_onboarding_copy_is_plain_text_safe_for_numbered_channels():
    # No Telegram markdown emphasis — these render verbatim on WhatsApp.
    for kind in (FirstContactKind.START_OR_GREETING, FirstContactKind.CAPABILITY):
        reply = first_contact_reply(kind)
        assert reply is not None
        assert "*" not in reply.body
