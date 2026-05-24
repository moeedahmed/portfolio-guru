"""Telegram surface for the Clinical Supervisor read-only workflow.

This module is the boundary between the python-telegram-bot world and
the supervisor stack:

* :func:`send_supervisor_notification` — turns a PHI-free
  :class:`supervisor_workflow.SupervisorNotificationPayload` into a
  Telegram message with Open / Skip / Later buttons and stashes the
  payload in :mod:`supervisor_notification_cache` so the callbacks can
  recover the ``ticket_url`` later.
* :func:`handle_supervisor_callback` — fires when a supervisor taps a
  button. ``open`` is the only action that touches Kaizen, and it does
  so read-only via :func:`assessor_reader.open_ticket_readonly`.
  ``skip`` and ``later`` are pure UI acknowledgements; they never
  navigate to Kaizen.
* :func:`connect_cdp_page` — minimal helper that attaches to the
  persistent Chrome session at ``localhost:18800``. It never falls back
  to a fresh headless launch — the scheduler relies on the connection
  failing fast when CDP isn't available.

The module is kept free of Telegram-bot import-time work so it can be
loaded from tests without the full ``backend/bot.py`` graph.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from playwright.async_api import async_playwright
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import supervisor_notification_cache as cache_mod
import supervisor_workflow
from assessor_mapper import AssessorTicketSummary
from assessor_reader import open_ticket_readonly
from supervisor_workflow import SupervisorNotificationPayload

logger = logging.getLogger(__name__)

CDP_URL = os.environ.get("KAIZEN_CDP_URL", "http://localhost:18800")
NOTIFICATION_CACHE_DIR = Path(
    os.environ.get(
        "PORTFOLIO_GURU_SUPERVISOR_CACHE_DIR",
        os.path.expanduser("~/.openclaw/data/portfolio-guru/supervisor"),
    )
)

CALLBACK_NAMESPACE = "SUP"
CALLBACK_PATTERN = r"^SUP\|(?:open|skip|later)\|[0-9a-fA-F\-]+$"

_SKIP_TEXT = "✅ Skipped — I won't notify you about this ticket again."
_LATER_TEXT = (
    "👌 Keeping it on your queue. Tap *Open* whenever you're ready to read it."
)
_STALE_TEXT = (
    "This notification is no longer active — the bot was restarted or the "
    "queue has moved on. The next supervisor poll will refresh your tickets."
)
_CDP_DOWN_TEXT = (
    "⚠️ Couldn't reach Kaizen right now. Try Open again in a moment."
)


def _supervisor_keyboard(payload: SupervisorNotificationPayload) -> InlineKeyboardMarkup:
    uuid = payload.ticket_uuid
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📄 Open", callback_data=f"{CALLBACK_NAMESPACE}|open|{uuid}"),
            ],
            [
                InlineKeyboardButton("⏭ Skip", callback_data=f"{CALLBACK_NAMESPACE}|skip|{uuid}"),
                InlineKeyboardButton("🕒 Later", callback_data=f"{CALLBACK_NAMESPACE}|later|{uuid}"),
            ],
        ]
    )


async def send_supervisor_notification(
    *,
    bot,
    telegram_user_id: int,
    payload: SupervisorNotificationPayload,
) -> None:
    """Dispatch one supervisor notification + cache the payload for callbacks."""
    text = supervisor_workflow.render_supervisor_notification_text(payload)
    keyboard = _supervisor_keyboard(payload)
    cache_mod.remember(
        NOTIFICATION_CACHE_DIR,
        telegram_user_id=telegram_user_id,
        payload=payload,
    )
    await bot.send_message(
        chat_id=telegram_user_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def connect_cdp_page():
    """Attach to the persistent Chrome session at ``localhost:18800``.

    Raises when CDP is unavailable — the supervisor stack treats that
    failure as inert, so callers must handle the exception locally
    rather than catch and continue.
    """
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
    except Exception:
        await pw.stop()
        raise
    contexts = browser.contexts
    context = contexts[0] if contexts else await browser.new_context()
    pages = context.pages
    page = pages[0] if pages else await context.new_page()
    return page


def _parse_callback_data(raw: str) -> tuple[str, str] | None:
    parts = (raw or "").split("|")
    if len(parts) != 3 or parts[0] != CALLBACK_NAMESPACE:
        return None
    return parts[1], parts[2]


async def _handle_open(update: Update, ticket_uuid: str) -> None:
    user_id = update.effective_user.id
    payload = cache_mod.lookup(
        NOTIFICATION_CACHE_DIR,
        telegram_user_id=user_id,
        ticket_uuid=ticket_uuid,
    )
    if payload is None:
        await update.callback_query.edit_message_text(text=_STALE_TEXT)
        return
    try:
        page = await connect_cdp_page()
    except Exception as exc:
        logger.warning("Supervisor open: CDP unavailable (%s)", exc)
        await update.callback_query.edit_message_text(text=_CDP_DOWN_TEXT)
        return
    summary = AssessorTicketSummary(
        title=payload.redacted_title,
        href=payload.ticket_url,
        uuid=ticket_uuid,
        state=payload.status,
        section_view="view-section" in (payload.ticket_url or ""),
    )
    try:
        data = await open_ticket_readonly(page, summary)
    except Exception as exc:
        logger.warning("Supervisor open: ticket read failed (%s)", exc)
        await update.callback_query.edit_message_text(text=_CDP_DOWN_TEXT)
        return
    detail_text = supervisor_workflow.render_supervisor_ticket_detail_text(data)
    await update.callback_query.edit_message_text(
        text=detail_text, parse_mode="Markdown"
    )
    cache_mod.forget(
        NOTIFICATION_CACHE_DIR,
        telegram_user_id=user_id,
        ticket_uuid=ticket_uuid,
    )


async def _handle_skip(update: Update, ticket_uuid: str) -> None:
    user_id = update.effective_user.id
    cache_mod.forget(
        NOTIFICATION_CACHE_DIR,
        telegram_user_id=user_id,
        ticket_uuid=ticket_uuid,
    )
    await update.callback_query.edit_message_text(text=_SKIP_TEXT)


async def _handle_later(update: Update, ticket_uuid: str) -> None:
    # No state mutation — the cache row stays so Open remains valid. The
    # next periodic poll will not re-fire the row because the state
    # tracker already marked it seen at notification time.
    await update.callback_query.edit_message_text(
        text=_LATER_TEXT, parse_mode="Markdown"
    )


async def handle_supervisor_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch SUP|open / SUP|skip / SUP|later button taps."""
    query = update.callback_query
    await query.answer()
    parsed = _parse_callback_data(query.data or "")
    if parsed is None:
        return
    action, ticket_uuid = parsed
    if action == "open":
        await _handle_open(update, ticket_uuid)
    elif action == "skip":
        await _handle_skip(update, ticket_uuid)
    elif action == "later":
        await _handle_later(update, ticket_uuid)
    # Unknown actions are silently ignored — the callback regex should
    # already filter them out, but defence in depth is cheap here.
