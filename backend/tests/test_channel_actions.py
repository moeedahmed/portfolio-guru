"""Channel-agnostic action/option rendering.

A reply is defined once and must render losslessly as Telegram buttons
(callback_data == action_id) and as a WhatsApp-friendly numbered block,
with a resolver that maps a numbered/plain reply back to the action_id.
"""

from __future__ import annotations

from channel_actions import (
    ChannelAction,
    ChannelReply,
    render_numbered,
    resolve_numbered_choice,
    to_telegram_button_rows,
    to_telegram_keyboard,
)


def _reply() -> ChannelReply:
    return ChannelReply(
        body="Here is what I can do.",
        continuation="💬 Back to your case — add detail or choose Draft now.",
        actions=(
            ChannelAction(action_id="GATHER|done", label="✅ Draft now"),
            ChannelAction(action_id="ACTION|cancel", label="❌ Cancel"),
        ),
    )


def test_full_text_joins_body_and_continuation_without_action_text():
    reply = _reply()
    text = reply.full_text()
    assert text.startswith("Here is what I can do.")
    assert "Back to your case" in text
    assert "Draft now" in text  # only via continuation copy, not an option list
    assert "1." not in text


def test_telegram_keyboard_uses_action_id_as_callback_data():
    reply = _reply()
    markup = to_telegram_keyboard(reply)
    buttons = [b for row in markup.inline_keyboard for b in row]

    assert [(b.text, b.callback_data) for b in buttons] == [
        ("✅ Draft now", "GATHER|done"),
        ("❌ Cancel", "ACTION|cancel"),
    ]


def test_plain_telegram_button_rows_use_action_id_as_callback_data():
    assert to_telegram_button_rows(_reply()) == [
        [{"text": "✅ Draft now", "callback_data": "GATHER|done"}],
        [{"text": "❌ Cancel", "callback_data": "ACTION|cancel"}],
    ]


def test_telegram_keyboard_is_none_without_actions():
    assert to_telegram_keyboard(ChannelReply(body="hello")) is None


def test_numbered_render_preserves_every_label_and_context():
    reply = _reply()
    rendered = render_numbered(reply)

    assert "Here is what I can do." in rendered
    assert "Back to your case" in rendered
    # Same labels as the Telegram buttons, just numbered.
    assert "1. ✅ Draft now" in rendered
    assert "2. ❌ Cancel" in rendered
    assert "Reply with the number" in rendered


def test_telegram_and_numbered_share_the_same_labels():
    reply = _reply()
    tg_labels = [
        b.text for row in to_telegram_keyboard(reply).inline_keyboard for b in row
    ]
    numbered = render_numbered(reply)
    for label in tg_labels:
        assert label in numbered


def test_channel_renderers_do_not_require_identical_body_copy_for_stable_actions():
    original = _reply()
    variant = ChannelReply(
        body="Different wording, same workflow.",
        continuation="Use the same stable actions.",
        actions=original.actions,
    )

    assert original.full_text() != variant.full_text()
    assert to_telegram_button_rows(original) == to_telegram_button_rows(variant)
    assert resolve_numbered_choice(variant, "1") == "GATHER|done"
    assert resolve_numbered_choice(variant, "cancel") == "ACTION|cancel"


def test_resolve_numbered_choice_by_number():
    reply = _reply()
    assert resolve_numbered_choice(reply, "1") == "GATHER|done"
    assert resolve_numbered_choice(reply, "2") == "ACTION|cancel"


def test_resolve_numbered_choice_by_label_ignoring_emoji_and_case():
    reply = _reply()
    assert resolve_numbered_choice(reply, "Draft now") == "GATHER|done"
    assert resolve_numbered_choice(reply, "  cancel  ") == "ACTION|cancel"


def test_resolve_numbered_choice_returns_none_for_no_match():
    reply = _reply()
    assert resolve_numbered_choice(reply, "9") is None
    assert resolve_numbered_choice(reply, "tell me a joke") is None
    assert resolve_numbered_choice(reply, "") is None
