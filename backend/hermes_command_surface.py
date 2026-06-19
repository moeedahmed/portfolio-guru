"""Product-owned public command surface for the Hermes test bot.

The Hermes test bot (``@portfolio_guru_test_bot``) runs on the shared
Hermes gateway runtime. That runtime builds its Telegram slash-command
menu from a global, hardcoded ``COMMAND_REGISTRY`` in
``hermes-agent/hermes_cli/commands.py`` (consumed via
``telegram_menu_commands()`` and pushed with ``bot.set_my_commands`` in
``gateway/platforms/telegram.py``). The registry exposes operator and
maintenance commands — ``/model``, ``/debug``, ``/restart``, ``/agents``,
``/rollback``, ``/usage``, ``/approve``, ``/deny`` and friends — in the
public menu, and offers **no per-profile config hook** to suppress them
(the only ``gateway_config_gate`` mechanism works the other way: it
*reveals* CLI-only commands when a config value is truthy).

This module is the repo-owned source of truth for what the test bot's
public menu *should* contain. It is intentionally pure data plus
validation helpers so it can be consumed by:

* tests (proving forbidden admin commands are excluded and the product
  commands a doctor needs are present);
* a future Hermes runtime hook that filters the menu per profile (see
  "Blocked runtime hook" below); and
* an interim installer that calls ``set_my_commands`` with the filtered
  list, should one ever be added on the profile side.

Design invariants (mirroring ``hermes_bridge_contract``):

- **No network, LLM, Kaizen, or Stripe calls.** Pure data + helpers.
- **No python-telegram-bot import.** Importable inside any process.
- **No BWS / secrets access.** Bot tokens are never touched here.
- **No side effects.** Callers decide what to do with the filtered menu.

Blocked runtime hook
--------------------
Making the *live* public menu match this manifest requires a change in
protected global runtime that this repo must not edit and that would
require a gateway restart (forbidden):

    hermes-agent/hermes_cli/commands.py :: telegram_menu_commands()

It should honour a per-profile config denylist/allowlist (e.g.
``telegram.menu_command_denylist`` / ``telegram.menu_commands``) read
from ``config.yaml`` and apply it before returning the menu. Until that
hook exists, this manifest is the declared contract and the verification
point; the live menu is changed only by the Hermes maintainer.
"""

from __future__ import annotations

from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Public surface — the allowlist (ordered; menu order follows this tuple)
# ---------------------------------------------------------------------------
#: Product-appropriate commands the test bot should show, paired with
#: product-facing descriptions that override the runtime's generic wording.
#: All names are real Hermes built-ins (cross-checked against the live
#: registry in the test suite) so the filter never invents commands.
PUBLIC_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Open Portfolio Guru and get started"),
    ("settings", "Manage Kaizen, writing style, and portfolio defaults"),
    ("cancel", "Cancel the current action"),
    ("reset", "Reset Portfolio Guru and reconnect Kaizen"),
    ("help", "How to use Portfolio Guru"),
)

# ---------------------------------------------------------------------------
# Forbidden surface — the admin/runtime denylist
# ---------------------------------------------------------------------------
#: Operator, maintenance, and diagnostic commands that must never appear
#: in the public test-bot menu. The first block is the set the brief names
#: explicitly; the rest are closely-related runtime commands that are
#: equally inappropriate for a clinician-facing menu.
FORBIDDEN_COMMANDS: frozenset[str] = frozenset(
    {
        # Brief-named admin/runtime commands.
        "model",
        "debug",
        "restart",
        "agents",
        "rollback",
        "usage",
        "approve",
        "deny",
        # Other operator / maintenance / billing runtime commands that are
        # not product-appropriate for a clinician-facing menu.
        "update",
        "reload",
        "reload-mcp",
        "reload-skills",
        "yolo",
        "verbose",
        "billing",
        "credits",
        "insights",
        "platform",
        "platforms",
        "commands",
        "whoami",
        "profile",
        "sethome",
        "background",
        "queue",
        "steer",
        "goal",
        "subgoal",
        "snapshot",
        "branch",
        "compress",
        "codex-runtime",
        "footer",
    }
)

#: The ideal product command list, expressed in product terms. Some of
#: these (settings/setup, health) have no Hermes built-in equivalent yet
#: and would need a plugin-registered command to become visible; they are
#: documented here so the product intent is explicit even where the
#: runtime cannot satisfy it today.
DESIRED_PRODUCT_COMMANDS: tuple[str, ...] = tuple(
    name for name, _ in PUBLIC_COMMANDS
)


def public_command_names() -> tuple[str, ...]:
    """Return the public command names in manifest order."""
    return tuple(name for name, _ in PUBLIC_COMMANDS)


def is_forbidden(name: str) -> bool:
    """Return True if *name* must be hidden from the public menu."""
    return name in FORBIDDEN_COMMANDS


def filter_menu(candidates: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Reduce a runtime ``(name, description)`` menu to the public surface.

    The public menu is an **allowlist**: only commands present in
    :data:`PUBLIC_COMMANDS` survive, regardless of what the runtime
    offered. Forbidden commands are therefore dropped implicitly (they are
    never in the allowlist), and unknown commands are dropped too.

    Output order follows :data:`PUBLIC_COMMANDS`, and descriptions come
    from the manifest (product wording), not from the runtime's generic
    text. A command absent from ``candidates`` is omitted — the function
    never surfaces a command the runtime did not actually offer.
    """
    offered = {name for name, _ in candidates}
    descriptions = dict(PUBLIC_COMMANDS)
    return [
        (name, descriptions[name])
        for name in public_command_names()
        if name in offered
    ]


def validate_manifest() -> list[str]:
    """Return a list of manifest integrity problems (empty == valid).

    Checks the internal invariants that the rest of the contract relies
    on, so a bad edit fails the test suite rather than silently shipping a
    menu that leaks admin commands.
    """
    problems: list[str] = []

    names = public_command_names()
    if len(names) != len(set(names)):
        problems.append("PUBLIC_COMMANDS contains duplicate names")

    overlap = set(names) & FORBIDDEN_COMMANDS
    if overlap:
        problems.append(
            f"commands both public and forbidden: {sorted(overlap)}"
        )

    for name, description in PUBLIC_COMMANDS:
        if not (isinstance(name, str) and name):
            problems.append(f"empty command name in PUBLIC_COMMANDS: {name!r}")
        if not (isinstance(description, str) and description.strip()):
            problems.append(f"empty description for {name!r}")

    return problems
