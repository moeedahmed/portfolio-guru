"""Length budgets from the Portfolio Guru message standard.

See docs/message-standard.md. Every reusable message must be classified by
kind and stay within that kind's budget, so no message grows into a wall of
text. A new template or message constant fails here until it is given a kind.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

import bot
from message_policy import MESSAGE_TEMPLATES, MODALITY_CLAUSE

# kind -> (max characters, max lines)
BUDGETS = {
    "confirmation": (120, 3),
    "prompt": (200, 6),
    "error": (220, 4),
    "menu": (320, 10),
    "explainer": (450, 12),
}

TEMPLATE_KINDS = {
    "welcome_disconnected": "prompt",
    "welcome_connected": "prompt",
    "bot_profile_description": "explainer",
    "bot_profile_short_description": "confirmation",
    "what_is_this": "explainer",
    "file_case_prompt": "prompt",
    "captured_ack": "confirmation",
    "thin_case_detail_request": "prompt",
    "essentials_check_retry": "error",
    "essential_unavailable_change_form": "error",
    "thin_sdl_detail_request": "prompt",
    "ai_temporarily_unavailable": "error",
    "form_recommendation": "menu",
    "photo_privacy_nudge": "confirmation",
    "draft_reply_hint": "confirmation",
    "draft_gap_hint": "prompt",
    "capability_overview": "explainer",
    "kaizen_setup_guide": "explainer",
    "greeting_reply": "confirmation",
    "gathering_captured": "prompt",
    "attachment_captured": "prompt",
    "gathering_continuation": "confirmation",
    "prompt_injection_refusal": "error",
    "medical_advice_refusal": "error",
    "scope_redirect": "prompt",
}

# Named message constants in bot.py. Consent is legal text, reviewed separately.
CONSTANT_KINDS = {
    "_OFFLINE_SAVED_TEXT": "confirmation",
    "CONSENT_TEXT": "exempt",
    "FILE_CASE_PROMPT": "prompt",
    "UNKNOWN_COMMAND_MSG": "error",
    "HELP_MSG": "exempt",  # generated command list; intro checked below
    "WELCOME_MSG": "prompt",
    "WELCOME_MSG_CONNECTED": "prompt",
    "WHAT_IS_THIS_MSG": "explainer",
    "_ALREADY_UNLIMITED_TEXT": "confirmation",
    "_BETA_PLAN_TEXT": "confirmation",
    "_PAYMENT_ACTIVE_TEXT": "confirmation",
    "_PAYMENT_PENDING_TEXT": "confirmation",
    "_PAYMENT_CANCELLED_TEXT": "confirmation",
    "_RESET_CONFIRM_TEXT": "prompt",
    "_RESET_KEPT_TEXT": "confirmation",
    "REMINDERS_OFF_TEXT": "confirmation",
    "_FILING_UNCERTAIN_TEXT": "error",
    "_OPEN_CASE_CHOICE_TEXT": "prompt",
    "_CONNECT_CHOICE_TEXT": "menu",
    "_LOGIN_REJECTED_TEXT": "error",
    "_SAME_CASE_GONE_TEXT": "error",
    "_DATA_CLEAR_TEXT": "confirmation",
    "_IMAGE_STILL_READING_TEXT": "confirmation",
    "_KAIZEN_PASSWORD_ROUTE_PROMPT": "prompt",
    "_KAIZEN_USERNAME_PRIVACY_NOTE": "confirmation",
    # Privacy notice (legal text): its own checks live in test_privacy_notice.py.
    "_PRIVACY_SUMMARY_TEXT": "exempt",
    "_PRIVACY_DETAILS_TEXT": "exempt",
    "_KAIZEN_USERNAME_PROMPT": "prompt",
    "_PASSWORDLESS_CHECK_FAILED_TEXT": "error",
    "_PASSWORDLESS_CONNECTED_AGAIN_TEXT": "confirmation",
    "_PASSWORDLESS_FEATURE_UNAVAILABLE_TEXT": "error",
    "_PASSWORDLESS_LINK_ENDED_TEXT": "error",
    "_PASSWORDLESS_LINK_TEXT": "prompt",
    "_PASSWORDLESS_NOT_SIGNED_IN_TEXT": "error",
    "_PASSWORDLESS_SIGNED_IN_TEXT": "confirmation",
    "_PASSWORDLESS_UNAVAILABLE_TEXT": "error",
}

_CONSTANT_NAME = re.compile(r"_?[A-Z0-9_]*(_TEXT|_MSG|_PROMPT|_NOTE)")
_DECORATIVE = re.compile("[✨🤖🎉⭐]")


def _check(name: str, text: str, kind: str) -> None:
    max_chars, max_lines = BUDGETS[kind]
    # The shared list of accepted inputs is a fixed phrase, not extra prose.
    body = text.replace(MODALITY_CLAUSE, "…").strip()
    assert len(body) <= max_chars, (
        f"{name} is {len(body)} characters; a {kind} may be {max_chars}. "
        "Shorten it (docs/message-standard.md)."
    )
    lines = body.count("\n") + 1
    assert lines <= max_lines, f"{name} has {lines} lines; a {kind} may have {max_lines}."
    assert not _DECORATIVE.search(body), f"{name} uses decorative emoji."


def test_every_template_has_a_kind():
    missing = sorted(set(MESSAGE_TEMPLATES) - set(TEMPLATE_KINDS))
    assert not missing, f"Give these templates a kind in {__file__}: {missing}"


@pytest.mark.parametrize("key", sorted(MESSAGE_TEMPLATES))
def test_template_within_budget(key):
    _check(key, MESSAGE_TEMPLATES[key].text, TEMPLATE_KINDS[key])


def _bot_message_constants() -> dict[str, str]:
    return {
        name: value
        for name, value in vars(bot).items()
        if isinstance(value, str) and _CONSTANT_NAME.fullmatch(name)
    }


def test_every_bot_message_constant_has_a_kind():
    missing = sorted(set(_bot_message_constants()) - set(CONSTANT_KINDS))
    assert not missing, f"Give these messages a kind in {__file__}: {missing}"


@pytest.mark.parametrize("name", sorted(CONSTANT_KINDS))
def test_bot_message_constant_within_budget(name):
    kind = CONSTANT_KINDS[name]
    if kind == "exempt":
        pytest.skip("reviewed separately")
    _check(name, getattr(bot, name), kind)


def test_help_intro_within_budget():
    # The command list is generated; the fixed intro must stay short.
    intro = bot.HELP_MSG.split("*Commands:*")[0]
    _check("HELP_MSG intro", intro, "prompt")


def test_settings_screen_within_menu_budget():
    with patch("bot.get_curriculum", return_value="2025"), \
         patch("bot.get_training_level", return_value="HIGHER"), \
         patch("bot.get_voice_profile", return_value={"tone": "x"}):
        text, _ = bot._settings_view_components(
            123, tier="pro_plus", used=12, connected=True
        )
    _check("Settings", text, "menu")
