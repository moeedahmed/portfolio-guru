"""Tests for the product-owned Hermes public command surface manifest.

The Hermes test bot (``@portfolio_guru_test_bot``) runs on the shared
Hermes gateway runtime, whose Telegram slash-command menu is built from a
global, hardcoded ``COMMAND_REGISTRY`` in
``hermes-agent/hermes_cli/commands.py``. That runtime exposes operator /
admin commands (``/model``, ``/debug``, ``/restart``, ``/agents``,
``/rollback``, ``/usage``, ``/approve``, ``/deny`` …) in the public menu,
and offers **no per-profile config hook** to suppress them.

``backend/hermes_command_surface.py`` is the repo-owned source of truth
for what the test bot's *public* menu should contain. These tests pin the
contract that:

* forbidden admin/runtime commands are never part of the public surface;
* the product-appropriate commands a doctor needs stay visible;
* :func:`filter_menu` deterministically reduces an arbitrary runtime menu
  to the product surface (the function a future runtime hook — or an
  interim installer — would call); and
* the manifest does not drift from the real Hermes runtime registry
  (cross-checked only when ``hermes_cli`` is importable).
"""

from __future__ import annotations

import importlib.util

import pytest

import hermes_command_surface as surface

# The admin/runtime commands the brief explicitly requires hidden from the
# public test-bot menu.
NAMED_FORBIDDEN = (
    "model",
    "debug",
    "restart",
    "agents",
    "rollback",
    "usage",
    "approve",
    "deny",
)

# The public command menu must match the current beta bot.
EXPECTED_PUBLIC = (
    "start",
    "settings",
    "cancel",
    "reset",
    "help",
)


def test_named_admin_commands_are_all_forbidden():
    for name in NAMED_FORBIDDEN:
        assert surface.is_forbidden(name), f"{name!r} must be forbidden"


def test_expected_product_commands_are_public():
    public = set(surface.public_command_names())
    for name in EXPECTED_PUBLIC:
        assert name in public, f"{name!r} must be in the public surface"


def test_no_forbidden_command_is_also_public():
    overlap = set(surface.public_command_names()) & surface.FORBIDDEN_COMMANDS
    assert not overlap, f"commands both public and forbidden: {sorted(overlap)}"


def test_no_named_admin_command_is_public():
    public = set(surface.public_command_names())
    leaked = [name for name in NAMED_FORBIDDEN if name in public]
    assert not leaked, f"admin commands leaked into public surface: {leaked}"


def test_public_commands_carry_product_descriptions():
    for name, description in surface.PUBLIC_COMMANDS:
        assert isinstance(name, str) and name, "command name must be non-empty"
        assert (
            isinstance(description, str) and description.strip()
        ), f"{name!r} needs a product-facing description"


def test_filter_menu_strips_forbidden_commands():
    runtime_menu = [
        ("help", "Show available commands"),
        ("model", "Switch model for this session"),
        ("debug", "Toggle debug output"),
        ("status", "Show session, model, token, and context info"),
        ("approve", "Approve a pending dangerous command"),
    ]
    kept = {name for name, _ in surface.filter_menu(runtime_menu)}
    assert "model" not in kept
    assert "debug" not in kept
    assert "approve" not in kept
    assert "help" in kept
    assert "status" not in kept


def test_filter_menu_drops_unknown_non_public_commands():
    # A command that is neither forbidden nor public must not appear: the
    # public menu is an allowlist, not merely a denylist.
    runtime_menu = [("help", "Show available commands"), ("kanban", "boards")]
    kept = {name for name, _ in surface.filter_menu(runtime_menu)}
    assert kept == {"help"}


def test_filter_menu_uses_manifest_descriptions_not_runtime_ones():
    # Product wording must win over the runtime's generic description.
    runtime_menu = [("cancel", "Kill all running background processes")]
    filtered = dict(surface.filter_menu(runtime_menu))
    assert filtered["cancel"] == dict(surface.PUBLIC_COMMANDS)["cancel"]


def test_filter_menu_is_order_stable_to_manifest():
    # Output follows manifest order, independent of runtime menu order.
    runtime_menu = [("reset", "x"), ("help", "y"), ("start", "z")]
    ordered = [name for name, _ in surface.filter_menu(runtime_menu)]
    manifest_order = [
        n for n in surface.public_command_names() if n in {"reset", "help", "start"}
    ]
    assert ordered == manifest_order


def test_validate_manifest_reports_no_problems():
    assert surface.validate_manifest() == []


def _hermes_cli_available() -> bool:
    # find_spec raises ModuleNotFoundError when the parent package is absent,
    # which is the common case in CI (the Hermes runtime is not installed).
    try:
        return importlib.util.find_spec("hermes_cli.commands") is not None
    except ModuleNotFoundError:
        return False


@pytest.mark.skipif(
    not _hermes_cli_available(),
    reason="hermes_cli not importable in this environment",
)
def test_manifest_names_exist_in_live_runtime_registry():
    # Guards against drift for forbidden runtime commands. Public product
    # commands intentionally match the beta bot even where Hermes does not
    # yet have first-class handlers, because Telegram command menu UX is
    # product-owned.
    from hermes_cli.commands import COMMAND_REGISTRY

    registry = {cmd.name for cmd in COMMAND_REGISTRY}
    registry |= {alias for cmd in COMMAND_REGISTRY for alias in cmd.aliases}

    unknown_forbidden = [n for n in NAMED_FORBIDDEN if n not in registry]
    assert not unknown_forbidden, f"forbidden names absent from runtime: {unknown_forbidden}"


@pytest.mark.skipif(
    not _hermes_cli_available(),
    reason="hermes_cli not importable in this environment",
)
def test_filter_menu_cleans_the_live_runtime_menu():
    # Documents that, applied to the actual runtime menu, the manifest
    # removes the forbidden admin commands. This is the gap the blocked
    # runtime hook must close at the gateway level.
    from hermes_cli.commands import telegram_bot_commands

    runtime_menu = list(telegram_bot_commands())
    cleaned = {name for name, _ in surface.filter_menu(runtime_menu)}
    for name in NAMED_FORBIDDEN:
        assert name not in cleaned
