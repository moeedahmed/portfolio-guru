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
  navigate to Kaizen. ``review``, ``recapture``, and ``cancel-draft``
  manage the local assessor draft only.
* :func:`connect_cdp_page` — minimal helper that attaches to the
  persistent Chrome session at ``localhost:18800``. It never falls back
  to a fresh headless launch — the scheduler relies on the connection
  failing fast when CDP isn't available.
* :func:`handle_assessor_intent_capture` — message handler for text and
  voice notes that runs *after* the supervisor has tapped Open. It
  feeds the utterance into :mod:`assessor_drafter` and replies with a
  local-only draft preview. No Kaizen write action is ever invoked from
  this path — the safety contract keeps Fill in / Save / Submit / Sign
  out of scope for the entire module.

The module is kept free of Telegram-bot import-time work so it can be
loaded from tests without the full ``backend/bot.py`` graph.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

import assessor_drafter
import assessor_session_store as session_store
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
CALLBACK_PATTERN = (
    r"^SUP\|(?:open|skip|later|review|recapture|cancel-draft)\|[0-9a-fA-F\-]+$"
)

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
_INTENT_PROMPT = (
    "🎙 *Send your assessment as text or a voice note.*\n"
    "I'll draft the assessor section locally so you can review it. "
    "Nothing is saved, submitted, or signed in Kaizen — review only."
)
_INTENT_BLANK_TEXT = (
    "⚠️ Couldn't read your assessment. Try again as text or a clearer voice note."
)
_TRANSCRIBE_FAILED_TEXT = (
    "⚠️ Couldn't transcribe that voice note. Try again or send your assessment as text."
)
_DRAFT_CANCELLED_TEXT = (
    "❌ Draft discarded. Nothing was saved to Kaizen.\n"
    "The ticket is still on your queue — tap *Open* on a future notification to start again."
)
_DRAFT_MISSING_TEXT = (
    "That earlier draft has expired. Tap *Open* on a fresh notification to start again."
)
_RECAPTURE_TEXT = (
    "🔄 Cleared the previous draft.\n\n" + _INTENT_PROMPT
)
_FORM_TYPE_UNKNOWN_TEXT = (
    "⚠️ I couldn't identify the assessor form type for this ticket — "
    "draft generation is disabled. Tap *Cancel* and try a different ticket."
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


def _intent_prompt_keyboard(ticket_uuid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=f"{CALLBACK_NAMESPACE}|cancel-draft|{ticket_uuid}",
                ),
            ],
        ]
    )


def _draft_review_keyboard(ticket_uuid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📝 Re-show draft",
                    callback_data=f"{CALLBACK_NAMESPACE}|review|{ticket_uuid}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔄 Re-record",
                    callback_data=f"{CALLBACK_NAMESPACE}|recapture|{ticket_uuid}",
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=f"{CALLBACK_NAMESPACE}|cancel-draft|{ticket_uuid}",
                ),
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
    session_store.start(
        NOTIFICATION_CACHE_DIR,
        telegram_user_id=user_id,
        ticket_uuid=ticket_uuid,
        form_type=data.form_type,
        ticket_url=data.ticket_url,
        trainee_section=data.trainee_section,
        pending_assessor_fields=data.pending_assessor_fields,
    )
    await update.callback_query.message.reply_text(
        text=_INTENT_PROMPT,
        parse_mode="Markdown",
        reply_markup=_intent_prompt_keyboard(ticket_uuid),
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


async def _handle_review(update: Update, ticket_uuid: str) -> None:
    user_id = update.effective_user.id
    session = session_store.get(NOTIFICATION_CACHE_DIR, telegram_user_id=user_id)
    if session is None or session.ticket_uuid != ticket_uuid or not session.draft:
        await update.callback_query.edit_message_text(text=_DRAFT_MISSING_TEXT)
        return
    draft = assessor_drafter.AssessorDraft(**session.draft)
    preview = assessor_drafter.render_preview(draft)
    await update.callback_query.message.reply_text(
        text=preview,
        parse_mode="Markdown",
        reply_markup=_draft_review_keyboard(ticket_uuid),
    )


async def _handle_recapture(update: Update, ticket_uuid: str) -> None:
    user_id = update.effective_user.id
    session = session_store.get(NOTIFICATION_CACHE_DIR, telegram_user_id=user_id)
    if session is None or session.ticket_uuid != ticket_uuid:
        await update.callback_query.edit_message_text(text=_DRAFT_MISSING_TEXT)
        return
    # Overwrite session with the same metadata but blank intent/draft.
    session_store.start(
        NOTIFICATION_CACHE_DIR,
        telegram_user_id=user_id,
        ticket_uuid=session.ticket_uuid,
        form_type=session.form_type,
        ticket_url=session.ticket_url,
        trainee_section=session.trainee_section,
        pending_assessor_fields=session.pending_assessor_fields,
    )
    await update.callback_query.message.reply_text(
        text=_RECAPTURE_TEXT,
        parse_mode="Markdown",
        reply_markup=_intent_prompt_keyboard(ticket_uuid),
    )


async def _handle_cancel_draft(update: Update, ticket_uuid: str) -> None:
    user_id = update.effective_user.id
    session_store.end(NOTIFICATION_CACHE_DIR, telegram_user_id=user_id)
    await update.callback_query.edit_message_text(
        text=_DRAFT_CANCELLED_TEXT, parse_mode="Markdown"
    )


async def handle_supervisor_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch SUP|open / SUP|skip / SUP|later / SUP|review / SUP|recapture /
    SUP|cancel-draft button taps."""
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
    elif action == "review":
        await _handle_review(update, ticket_uuid)
    elif action == "recapture":
        await _handle_recapture(update, ticket_uuid)
    elif action == "cancel-draft":
        await _handle_cancel_draft(update, ticket_uuid)
    # Unknown actions are silently ignored — the callback regex should
    # already filter them out, but defence in depth is cheap here.


async def _extract_intent_text(update: Update) -> str | None:
    """Return the supervisor's intent as plain text, transcribing voice if needed."""
    message = update.effective_message
    if message is None:
        return None
    if message.text:
        stripped = message.text.strip()
        return stripped or None
    voice_obj = message.voice or message.audio
    if voice_obj is None:
        return None
    try:
        file = await voice_obj.get_file()
    except Exception as exc:
        logger.warning("Supervisor intent: voice file fetch failed (%s)", exc)
        return None
    suffix = ".ogg"
    mime = getattr(voice_obj, "mime_type", "") or ""
    if mime == "audio/mp4":
        suffix = ".m4a"
    elif mime == "audio/mpeg":
        suffix = ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
    try:
        await file.download_to_drive(tmp_path)
        from whisper import transcribe_voice
        text = await transcribe_voice(tmp_path)
    except Exception as exc:
        logger.warning("Supervisor intent: voice transcription failed (%s)", exc)
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return (text or "").strip() or None


async def handle_assessor_intent_capture(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Capture an assessor intent if a session is active for this Telegram user.

    Runs in a high-priority handler group so it can claim text/voice
    updates BEFORE the trainee filing flow when an assessor session is
    open. When no session is active for the user, the handler returns
    quietly so the trainee handlers in the default group keep working
    untouched.
    """
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    session = session_store.get(NOTIFICATION_CACHE_DIR, telegram_user_id=user.id)
    if session is None:
        return
    intent = await _extract_intent_text(update)
    if intent is None:
        is_voice_like = bool(message.voice or message.audio)
        await message.reply_text(
            _TRANSCRIBE_FAILED_TEXT if is_voice_like else _INTENT_BLANK_TEXT
        )
        raise ApplicationHandlerStop
    if not session.form_type:
        await message.reply_text(
            _FORM_TYPE_UNKNOWN_TEXT,
            parse_mode="Markdown",
            reply_markup=_intent_prompt_keyboard(session.ticket_uuid),
        )
        raise ApplicationHandlerStop
    draft = assessor_drafter.draft_from_intent(
        intent,
        form_type=session.form_type,
        ticket_uuid=session.ticket_uuid,
    )
    session_store.update_intent(
        NOTIFICATION_CACHE_DIR,
        telegram_user_id=user.id,
        intent=intent,
    )
    session_store.update_draft(
        NOTIFICATION_CACHE_DIR,
        telegram_user_id=user.id,
        draft=draft,
    )
    preview = assessor_drafter.render_preview(draft)
    await message.reply_text(
        preview,
        parse_mode="Markdown",
        reply_markup=_draft_review_keyboard(session.ticket_uuid),
    )
    raise ApplicationHandlerStop
