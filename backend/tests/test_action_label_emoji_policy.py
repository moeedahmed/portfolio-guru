"""Bot-wide guardrail for functional action-label emoji.

The source scan covers every production ``InlineKeyboardButton`` and
``ChannelAction`` constructor, plus raw Bot API dictionaries that pair
``text`` with ``callback_data``. Literal labels are checked directly. The few
dynamic InlineKeyboardButton sources are pinned here and exercised through
their real builders so this test never silently treats an unknown expression
as covered.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot
from channel_actions import ChannelAction, ChannelReply, to_telegram_button_rows


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (REPO_ROOT / "backend", REPO_ROOT / "scripts")
BANNED_DECORATIVE_EMOJI = frozenset({"⭐", "✨", "🤖", "🎉"})

_EMOJI_BASE = (
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002100-\U000021FF"
    "\U00002300-\U000025FF"
    "\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF"
)
_EMOJI_CLUSTER = re.compile(
    rf"^[{_EMOJI_BASE}]"
    rf"[\ufe0e\ufe0f]?"
    rf"[\U0001F3FB-\U0001F3FF]?"
    rf"(?:\u200d[{_EMOJI_BASE}][\ufe0e\ufe0f]?[\U0001F3FB-\U0001F3FF]?)*"
)


# These are the only InlineKeyboardButton labels whose leading emoji cannot
# be proven from a literal prefix by the AST scan. Each production route is
# executed below, and any new dynamic source fails until it gains real proof.
EXPECTED_DYNAMIC_INLINE_SITES = Counter(
    {
        ("backend/bot.py", "_build_form_choice_keyboard", "f'{emoji} {label}'"): 2,
        (
            "backend/bot.py",
            "_build_form_choice_keyboard",
            "f'{emoji} {label} (soon)'",
        ): 1,
        ("backend/bot.py", "_build_category_picker_keyboard", "cat_name"): 1,
        ("backend/bot.py", "_build_category_forms_keyboard", "f'{emoji} {label}'"): 1,
        ("backend/bot.py", "_build_explicit_form_keyboard", "f'{emoji} {label}'"): 1,
        ("backend/bot.py", "handle_form_search_text", "f'{emoji} {label}'"): 1,
        (
            "backend/channel_actions.py",
            "to_telegram_keyboard",
            "_render_action_label(action.label)",
        ): 1,
    }
)

# These constructors receive labels through the ChannelAction model. Its
# runtime invariant is tested below, so serialised/Hermes inputs cannot bypass
# the same policy.
EXPECTED_DYNAMIC_CHANNEL_ACTION_SITES = Counter(
    {
        ("backend/hermes_bridge_contract.py", "deserialise_reply", "str(a['label'])"): 1,
        (
            "backend/hermes_pg_cli.py",
            "_resolve_stored_action",
            "str(action.get('label') or '')",
        ): 1,
    }
)

EXPECTED_MODEL_BACKED_RAW_BUTTON_SITES = Counter(
    {
        (
            "backend/channel_actions.py",
            "to_telegram_button_rows",
            "_render_action_label(action.label)",
        ): 1,
    }
)


def _assert_functional_action_label(label: str) -> None:
    assert not (set(label) & BANNED_DECORATIVE_EMOJI), label
    match = _EMOJI_CLUSTER.match(label)
    assert match is not None, f"missing leading functional emoji: {label!r}"
    remainder = label[match.end():]
    assert remainder.startswith(" "), f"emoji must be followed by one space: {label!r}"
    wording = remainder[1:]
    assert wording and not wording.startswith(" "), f"invalid emoji spacing: {label!r}"
    assert _EMOJI_CLUSTER.match(wording) is None, f"multiple leading emoji: {label!r}"


def _scope_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def _literal_or_prefixed_text(
    expression: ast.AST,
    module_constants: dict[str, str],
) -> str | None:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value
    if isinstance(expression, ast.Name):
        return module_constants.get(expression.id)
    if isinstance(expression, ast.JoinedStr):
        first = expression.values[0] if expression.values else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value + "dynamic wording"
        return None
    if isinstance(expression, ast.Subscript):
        return _literal_or_prefixed_text(expression.value, module_constants)
    return None


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        value = statement.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def _production_python_files() -> list[Path]:
    return sorted(
        path
        for root in SOURCE_ROOTS
        for path in root.rglob("*.py")
        if "tests" not in path.parts and "_archived" not in path.parts
    )


def test_all_production_action_constructor_sources_follow_the_emoji_policy():
    dynamic_inline: Counter[tuple[str, str, str]] = Counter()
    dynamic_channel_actions: Counter[tuple[str, str, str]] = Counter()
    dynamic_raw_buttons: Counter[tuple[str, str, str]] = Counter()
    constructor_count = 0

    for path in _production_python_files():
        relative_path = str(path.relative_to(REPO_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        constants = _module_string_constants(tree)

        for node in ast.walk(tree):
            expression: ast.AST | None = None
            constructor_kind = ""
            if isinstance(node, ast.Call):
                name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else ""
                )
                if name == "InlineKeyboardButton":
                    constructor_kind = "inline"
                    expression = node.args[0] if node.args else next(
                        (kw.value for kw in node.keywords if kw.arg == "text"), None
                    )
                elif name == "ChannelAction":
                    constructor_kind = "channel"
                    expression = next(
                        (kw.value for kw in node.keywords if kw.arg == "label"),
                        node.args[1] if len(node.args) > 1 else None,
                    )
            elif isinstance(node, ast.Dict):
                keyed = {
                    key.value: value
                    for key, value in zip(node.keys, node.values)
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                if "text" in keyed and "callback_data" in keyed:
                    constructor_kind = "raw"
                    expression = keyed["text"]

            if expression is None:
                continue
            constructor_count += 1
            static_text = _literal_or_prefixed_text(expression, constants)
            if static_text is not None:
                _assert_functional_action_label(static_text)
                continue

            site = (relative_path, _scope_name(node, parents), ast.unparse(expression))
            if constructor_kind == "inline":
                dynamic_inline[site] += 1
            elif constructor_kind == "channel":
                dynamic_channel_actions[site] += 1
            else:
                dynamic_raw_buttons[site] += 1

    assert constructor_count, "no production action constructors were found"
    assert dynamic_inline == EXPECTED_DYNAMIC_INLINE_SITES
    assert dynamic_channel_actions == EXPECTED_DYNAMIC_CHANNEL_ACTION_SITES
    assert dynamic_raw_buttons == EXPECTED_MODEL_BACKED_RAW_BUTTON_SITES


@pytest.mark.parametrize(
    ("label", "rendered"),
    [
        ("Choose form", "➡️ Choose form"),
        ("⭐ Upgrade", "➡️ Upgrade"),
        ("📋 ❌ Discard case", "➡️ Discard case"),
        ("📋📋 Choose form", "➡️ Choose form"),
    ],
)
def test_channel_action_renderer_normalises_legacy_labels(label, rendered):
    action = ChannelAction(action_id="ACTION|test", label=label)
    reply = ChannelReply(actions=(action,), body="Test")
    assert to_telegram_button_rows(reply)[0][0]["text"] == rendered


def test_channel_action_accepts_one_functional_leading_emoji():
    action = ChannelAction(action_id="ACTION|test", label="📋 Choose form")
    assert action.label == "📋 Choose form"


def _button_labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def test_dynamic_form_and_category_builders_render_compliant_labels(monkeypatch):
    recommendations = [
        SimpleNamespace(form_type="CBD", uuid="cbd-uuid"),
        SimpleNamespace(form_type="REFLECT_LOG", uuid=None),
    ]
    monkeypatch.setattr(bot, "_get_allowed_forms", lambda _user_id: ["CBD"])
    monkeypatch.setattr(bot, "_effective_curriculum", lambda _user_id: "2025")

    markups = (
        bot._build_form_choice_keyboard(recommendations),
        bot._build_category_picker_keyboard(123),
        bot._build_category_forms_keyboard(123, "CLINICAL"),
        bot._build_explicit_form_keyboard("CBD"),
    )
    for markup in markups:
        for label in _button_labels(markup):
            _assert_functional_action_label(label)


@pytest.mark.asyncio
async def test_dynamic_form_search_route_renders_compliant_labels(monkeypatch):
    captured: dict[str, object] = {}

    class Message:
        text = "cbd"

        async def reply_text(self, text, **kwargs):
            captured["text"] = text
            captured.update(kwargs)

    update = SimpleNamespace(
        message=Message(),
        effective_user=SimpleNamespace(id=123),
    )
    monkeypatch.setattr(bot, "_get_allowed_forms", lambda _user_id: ["CBD"])

    await bot.handle_form_search_text(update, SimpleNamespace(user_data={}))

    markup = captured["reply_markup"]
    for label in _button_labels(markup):
        _assert_functional_action_label(label)


def test_every_form_and_category_mapping_icon_is_policy_compliant():
    for emoji in bot.FORM_EMOJIS.values():
        _assert_functional_action_label(f"{emoji} Form")
    for category_label in bot.FORM_CATEGORIES:
        _assert_functional_action_label(category_label)
