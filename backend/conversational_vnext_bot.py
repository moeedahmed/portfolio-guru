"""Private vNext test-bot scaffold — currently a no-op.

The approved direction for Portfolio Guru vNext is to dogfood the
conversational case engine inside a *separate private test bot* before
ever pointing the public Portfolio Guru bot at the new engine. This
module is the entrypoint that future slice will use.

It is intentionally inert today:

* It does not import ``python-telegram-bot`` or any handler from
  ``backend/bot.py``.
* It does not start polling, register handlers, touch credentials,
  Kaizen, billing, BWS secrets, or launchd.
* It refuses to start unless a dedicated env var holds a *different*
  token from the public bot.

A future slice can extend :func:`main` to attach the conversational
case engine (``conversational_case_engine.apply_event``) to a real
polling loop. Until then, importing this module is safe and running it
does nothing beyond printing a status line.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable

from conversational_case_engine import CaseWorkspace, EngineSnapshot, apply_event
from conversational_case_engine import IngestEvent, IngestKind, SourceType
from telegram_vnext_adapter import event_from_telegram_message
from vnext_dialogue_policy import is_completion_request

VNEXT_TOKEN_ENV = "PG_VNEXT_BOT_TOKEN"

PRODUCTION_TOKEN_ENVS: tuple[str, ...] = (
    "BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_TOKEN",
    "PORTFOLIO_GURU_BOT_TOKEN",
)

log = logging.getLogger("portfolio_guru.vnext")


def is_enabled(env: "os._Environ[str] | dict[str, str] | None" = None) -> bool:
    source = os.environ if env is None else env
    return bool((source.get(VNEXT_TOKEN_ENV) or "").strip())


def guard_token_separation(
    env: "os._Environ[str] | dict[str, str] | None" = None,
) -> str | None:
    """Return an error message if the vNext token collides with production.

    Returns ``None`` when the configuration is safe (vNext disabled, or
    vNext token differs from every known production token env var).
    """

    source = os.environ if env is None else env
    token = (source.get(VNEXT_TOKEN_ENV) or "").strip()
    if not token:
        return None
    for prod_env in PRODUCTION_TOKEN_ENVS:
        prod_token = (source.get(prod_env) or "").strip()
        if prod_token and prod_token == token:
            return (
                f"{VNEXT_TOKEN_ENV} matches {prod_env}; the vNext private "
                "test bot must use a separate Telegram token."
            )
    return None


VNextHandler = Callable[[CaseWorkspace, Any], EngineSnapshot]


def build_handler(
    env: "os._Environ[str] | dict[str, str] | None" = None,
) -> VNextHandler | None:
    """Return the conversational case handler if vNext is safely enabled.

    The handler is a stateless ``(workspace, telegram_message) -> EngineSnapshot``
    callable that runs the message through :func:`event_from_telegram_message`
    and applies the resulting event to the case engine. It never imports
    ``python-telegram-bot``, opens credentials, or performs I/O.

    Returns ``None`` when vNext is disabled (no token), when the token
    collides with any known production env var, or when the token is
    blank — mirroring :func:`main`'s refusal contract so the private
    bot can never silently degrade into the public bot's identity.
    """

    source = os.environ if env is None else env
    if not is_enabled(source):
        return None
    if guard_token_separation(source) is not None:
        return None

    def _handle(workspace: CaseWorkspace, message: Any) -> EngineSnapshot:
        text = _message_text(message)
        if is_completion_request(text):
            event = IngestEvent(
                turn_id=f"{event_from_telegram_message(message).turn_id}:completion",
                text=text,
                source_type=SourceType.TEXT,
                kind=IngestKind.REQUEST_DRAFT,
            )
            return apply_event(workspace, event)
        event = event_from_telegram_message(message)
        return apply_event(workspace, event)

    return _handle


def _message_text(message: Any) -> str:
    text = getattr(message, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    caption = getattr(message, "caption", None)
    if isinstance(caption, str) and caption.strip():
        return caption.strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    """Run the vNext scaffold (currently a no-op).

    Returns ``0`` when the scaffold completed safely (either disabled or
    enabled-but-no-op), and a non-zero exit code when the safety guard
    refused to start.
    """

    del argv  # Reserved for a future CLI surface.

    if not is_enabled():
        print(
            f"[vNext] disabled — set {VNEXT_TOKEN_ENV} to a SEPARATE Telegram "
            "bot token (not the public Portfolio Guru token) to enable in a "
            "future slice.",
            file=sys.stderr,
        )
        return 0

    conflict = guard_token_separation()
    if conflict:
        print(f"[vNext] refused to start: {conflict}", file=sys.stderr)
        return 2

    print(
        "[vNext] scaffold enabled — this entry point is a no-op. "
        "Run backend/vnext_runner.py to start the private polling loop. "
        "No Telegram, Kaizen, launchd, or credential action runs from this module.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main(sys.argv[1:]))
