"""Real polling loop for the private vNext test bot.

This module is the entry point for the private vNext dogfood bot.
It imports python-telegram-bot, registers handlers, and runs polling.

The conversational case engine (``conversational_case_engine``) handles
all state transitions; this module manages the per-chat workspace dict
and translates the engine's ``NextAction`` tuples into short reply text.

Usage
-----
    # Token already in environment:
    PG_VNEXT_BOT_TOKEN=<token> python backend/vnext_runner.py

    # Or via the helper script (fetches token from BWS if needed):
    scripts/run_vnext_local.sh

Safety
------
This module never imports ``backend/bot.py``, never touches Kaizen,
credentials, billing, launchd, or the public bot token. Workspaces are
in-memory and scoped to a single process lifetime — suitable for dogfood
only, not for production state.
"""

from __future__ import annotations

import logging
import os
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from conversational_case_engine import (
    ActionKind,
    CaseWorkspace,
    EngineSnapshot,
    new_workspace,
)
from conversational_vnext_bot import (
    VNEXT_TOKEN_ENV,
    build_handler,
    guard_token_separation,
    is_enabled,
)

log = logging.getLogger("portfolio_guru.vnext.runner")

_TOKEN_NOISY_LOGGERS: tuple[str, ...] = (
    "httpx",
    "telegram.request",
    "telegram.ext.Application",
    "apscheduler.scheduler",
)

# In-memory workspace store keyed by Telegram chat_id.
# Dogfood-only: cleared on restart, never persisted to DB, Kaizen, or disk.
_workspaces: dict[int, CaseWorkspace] = {}

_INTRO = (
    "Private vNext test bot\n\n"
    "Send a clinical case description and I'll run it through the conversational "
    "case engine. Kaizen filing is not wired in this slice — dogfood only.\n\n"
    "/start — reset workspace\n"
    "/reset — clear workspace"
)


def _get_workspace(chat_id: int) -> CaseWorkspace:
    if chat_id not in _workspaces:
        _workspaces[chat_id] = new_workspace()
    return _workspaces[chat_id]


def _reset_workspace(chat_id: int) -> CaseWorkspace:
    _workspaces[chat_id] = new_workspace()
    return _workspaces[chat_id]


async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    assert update.effective_chat is not None
    assert update.message is not None
    _reset_workspace(update.effective_chat.id)
    await update.message.reply_text(_INTRO)


async def reset_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    assert update.effective_chat is not None
    assert update.message is not None
    _reset_workspace(update.effective_chat.id)
    await update.message.reply_text("Workspace cleared. Send a new case description.")


async def handle_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    assert update.effective_chat is not None
    assert update.message is not None

    handler = context.bot_data.get("vnext_handler")
    if handler is None:
        await update.message.reply_text("[vNext] handler not initialised.")
        return

    chat_id = update.effective_chat.id
    workspace = _get_workspace(chat_id)
    snapshot: EngineSnapshot = handler(workspace, update.message)
    _workspaces[chat_id] = snapshot.workspace
    await update.message.reply_text(_build_reply(snapshot))


def _build_reply(snapshot: EngineSnapshot) -> str:
    """Translate engine NextAction tuples into short dogfood-safe reply text."""
    parts: list[str] = []
    state = snapshot.workspace.state.value
    eligible = snapshot.workspace.draft_eligible_facts()

    for action in snapshot.actions:
        if action.kind is ActionKind.SAVE_DRAFT:
            parts.append(
                "Private vNext test bot: Kaizen filing is not wired in this slice. "
                "Use /reset to start a new case."
            )
        elif action.kind is ActionKind.REQUEST_CASE_CONFIRMATION:
            keys = ", ".join(f.key for f in snapshot.workspace.facts) or "none"
            parts.append(
                f"Case signal detected (state: {state}). "
                f"Facts so far: {keys}. Keep adding detail or /reset."
            )
        elif action.kind is ActionKind.ACK_CASE_DETAILS:
            keys = ", ".join(f.key for f in eligible) or "none"
            parts.append(f"Case updated (state: {state}). Eligible facts: {keys}.")
        elif action.kind is ActionKind.OFFER_DRAFT:
            n = action.payload.get("eligible_facts", "?")
            parts.append(
                f"Draft ready (state: {state}, eligible facts: {n}). "
                "Kaizen filing not wired — dogfood only."
            )
        elif action.kind is ActionKind.DRAFT_NOT_READY:
            reason = action.payload.get("reason", "unknown")
            parts.append(
                f"Not ready to draft ({reason}, state: {state}). "
                "Keep adding case details."
            )
        elif action.kind is ActionKind.ANSWER_CHAT:
            parts.append(
                f"[Side conversation, state: {state}] "
                "Send a clinical case description to test the engine."
            )
        elif action.kind is ActionKind.START_NEW_CASE:
            parts.append(f"New case started (state: {state}).")
        elif action.kind is ActionKind.ABANDON_CASE:
            parts.append("Case abandoned. Use /reset to start fresh.")
        elif action.kind is ActionKind.REQUEST_FACT_CONFIRMATION:
            parts.append(
                f"Strict-source facts present (state: {state}) — "
                "image/document facts need user confirmation before draft."
            )
        elif action.kind is ActionKind.REQUEST_CLARIFICATION:
            target = action.payload.get("target", "")
            suffix = f": {target}" if target else ""
            parts.append(f"Clarification needed (state: {state}){suffix}.")
        elif action.kind is ActionKind.NOOP:
            pass

    return "\n".join(parts) if parts else f"[vNext] engine state: {state}"


def run(token: str, handler: object) -> None:
    """Build the Application, wire handlers, and start polling (blocking)."""
    app = Application.builder().token(token).build()
    app.bot_data["vnext_handler"] = handler

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_message)
    )

    log.info("[vNext] Private test bot polling started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def main() -> int:
    _suppress_token_noisy_logs()

    if not is_enabled():
        print(
            f"[vNext runner] disabled — set {VNEXT_TOKEN_ENV} to a separate "
            "private bot token to enable.",
            file=sys.stderr,
        )
        return 1

    conflict = guard_token_separation()
    if conflict:
        print(f"[vNext runner] refused to start: {conflict}", file=sys.stderr)
        return 2

    handler = build_handler()
    if handler is None:
        print(
            "[vNext runner] build_handler() returned None; check guards.",
            file=sys.stderr,
        )
        return 2

    token = os.environ[VNEXT_TOKEN_ENV].strip()
    run(token, handler)
    return 0


def _suppress_token_noisy_logs() -> None:
    """Keep third-party startup logs from printing Telegram token URLs."""
    for logger_name in _TOKEN_NOISY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
