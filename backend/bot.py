"""
Portfolio Guru Telegram Bot — v2
Multimodal input (text/voice/image) with approval flow before filing.
"""
import asyncio
import logging
import os
import re
import tempfile
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler, PicklePersistence,
)
from store import store_credentials, get_credentials, has_credentials, init
from extractor import extract_cbd_data, extract_form_data, recommend_form_types, classify_intent, classify_menu_intent, answer_question, extract_explicit_form_type, is_reuse_request, review_draft, analyse_portfolio_health, summarise_recent_activity, generate_nudge_copy, extract_field_updates, compose_filing_recovery_copy, combine_case_inputs, _has_qi_project_signal, schema_form_type
from usage import record_case_filed, get_cases_this_month, check_can_file, get_user_tier, set_user_tier, get_case_history, TIER_LIMITS, get_all_active_users, get_cases_this_week, is_beta_tester, set_beta_tester, save_kc_coverage
from filer_router import route_filing
from kaizen_form_filer import FORM_UUIDS
from form_schemas import FORM_SCHEMAS
from form_display import public_form_name, sanitize_internal_form_codes
from models import FormDraft, CBDData, FormTypeRecommendation
from whisper import transcribe_voice
from vision import extract_from_image
from documents import extract_from_document, is_supported_document
from profile_store import init_profile_db, store_training_level, get_training_level, get_voice_profile, store_voice_profile, clear_voice_profile, store_curriculum, get_curriculum, get_kaizen_role
from health_models import HealthProfile, HealthDomain, Pathway
from health_engine import case_history_to_evidence_items, compute_snapshot
from health_profile_store import get_health_profile, save_health_profile
from kaizen_index import (
    KaizenSyncStatus,
    evidence_rows_to_health_items,
    get_kaizen_sync_status,
    list_evidence_items,
)
from kaizen_sync import sync_kaizen_portfolio_index_for_user
from bulk_filer import bulk_file
from kaizen_unsigned_scraper import scrape_unsigned_tickets
from conversational_router import ConversationalIntent, route_message
from channel_actions import to_telegram_keyboard
from conversation_supervisor import GatheringTurnKind, decide_gathering_turn
from message_policy import render_message, safety_redirect_text, style_grounded_answer
import chase_guard

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)

# Token redaction for bot/API logging. Telegram Bot API URLs contain the
# token immediately after "/bot", so a plain word-boundary regex misses them.
_token_pattern = re.compile(
    r"(?<=bot)\d{8,10}:[A-Za-z0-9_-]{30,}|"
    r"(?<![A-Za-z0-9_])\d{8,10}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_])"
)
def _redact_token_string(s: str) -> str:
    s = _token_pattern.sub("<REDACTED_TELEGRAM_TOKEN>", s)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token and token in s:
        s = s.replace(token, "<REDACTED_TELEGRAM_TOKEN>")
    return s

_orig_handle = logging.Logger.handle
def _redacted_handle(self, record):
    try:
        if isinstance(record.msg, str):
            record.msg = _redact_token_string(record.msg)
        if record.args:
            new_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    new_args.append(_redact_token_string(arg))
                else:
                    redacted = _redact_token_string(str(arg))
                    new_args.append(redacted if redacted != str(arg) else arg)
            record.args = tuple(new_args)
    except Exception:
        pass
    return _orig_handle(self, record)
logging.Logger.handle = _redacted_handle

logger = logging.getLogger(__name__)

MAX_TELEGRAM_MSG = 4096

BOT_COMMANDS = [
    ("start", "Open Portfolio Guru and get started"),
    ("setup", "Connect your portfolio account"),
    ("voice", "Set up your personal writing voice"),
    ("settings", "View status, usage, and preferences"),
    ("link", "Link to your EM Gurus Hub web account"),
    ("health", "Portfolio health chart and pathway-aware analysis"),
    ("cancel", "Cancel whatever is happening"),
    ("delete", "Delete all your stored data"),
    ("help", "How to use Portfolio Guru"),
]


async def _log_conversational_router_shadow(
    text: str,
    user_id: int | None,
    handler_name: str,
) -> None:
    """Run the Phase 2 conversational router without affecting live flow."""
    try:
        result = route_message(text)
        logger.info(
            "Conversational router shadow route handler=%s user_id=%s intent=%s confidence=%.2f signals=%s chars=%d",
            handler_name,
            user_id,
            result.intent.value,
            result.confidence,
            result.signals,
            len(text),
        )
    except Exception:
        logger.warning(
            "Conversational router shadow route failed handler=%s user_id=%s chars=%d",
            handler_name,
            user_id,
            len(text),
            exc_info=True,
        )


def _start_conversational_router_shadow(update: Update, handler_name: str) -> None:
    """Schedule shadow routing for ordinary text messages only."""
    message = getattr(update, "message", None)
    text = (getattr(message, "text", None) or "").strip()
    if not text:
        return
    user = getattr(update, "effective_user", None)
    user_id = getattr(user, "id", None)
    asyncio.create_task(_log_conversational_router_shadow(text, user_id, handler_name))


async def _safe_edit_text(target, text: str, **kwargs):
    """Edit message text, splitting if it exceeds Telegram's 4096 char limit.
    For the first chunk, passes kwargs (reply_markup, parse_mode) through.
    Subsequent chunks are sent as new messages with no markup."""
    if len(text) <= MAX_TELEGRAM_MSG:
        try:
            return await target.edit_text(text, **kwargs)
        except BadRequest as exc:
            if "can't parse entities" in str(exc).lower() and kwargs.get("parse_mode"):
                plain_kwargs = {k: v for k, v in kwargs.items() if k != "parse_mode"}
                return await target.edit_text(text, **plain_kwargs)
            raise

    # Split at last newline before limit
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= MAX_TELEGRAM_MSG:
            chunks.append(remaining)
            break
        split_at = remaining[:MAX_TELEGRAM_MSG].rfind("\n")
        if split_at < 100:
            split_at = MAX_TELEGRAM_MSG - 1
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    # First chunk gets the kwargs (reply_markup etc.)
    if len(chunks) == 1:
        return await _safe_edit_text(target, chunks[0], **kwargs)

    # First chunk edits the existing message (no buttons — they go on last chunk)
    markup = kwargs.pop("reply_markup", None)
    await _safe_edit_text(target, chunks[0], **kwargs)

    # Middle chunks as new messages
    chat = target.chat if hasattr(target, "chat") else None
    for chunk in chunks[1:-1]:
        if chat:
            await chat.send_message(chunk, parse_mode=kwargs.get("parse_mode"))

    # Last chunk gets the reply_markup
    if chat and chunks[-1:]:
        return await chat.send_message(
            chunks[-1], reply_markup=markup, parse_mode=kwargs.get("parse_mode")
        )


async def _typing_until(chat, stop_event: asyncio.Event, interval: float = 4.0) -> None:
    """Keep Telegram's lightweight typing indicator alive during long work."""
    try:
        while not stop_event.is_set():
            try:
                await chat.send_action(constants.ChatAction.TYPING)
            except Exception:
                logger.debug("Typing indicator failed", exc_info=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        pass


_IMAGE_PROGRESS_DELAY_SECONDS = 8.0
_IMAGE_STILL_READING_TEXT = "📷 Still reading…"


async def _run_image_progress(
    ack,
    *,
    still_text: str = _IMAGE_STILL_READING_TEXT,
    delay_seconds: float = _IMAGE_PROGRESS_DELAY_SECONDS,
    ocr_done: asyncio.Event,
) -> None:
    """Replace the OCR ack with one calm "Still reading…" line if OCR is slow.

    The reassurance edit replaces the original "Reading image…" copy instead
    of appending to it, so the user never sees a stacked two-line bubble.
    Skipped entirely when OCR finishes inside ``delay_seconds`` or when the
    caller cancels the task.
    """
    try:
        try:
            await asyncio.wait_for(ocr_done.wait(), timeout=delay_seconds)
            return
        except asyncio.TimeoutError:
            pass
        if ocr_done.is_set():
            return
        try:
            await ack.edit_text(still_text)
        except Exception:
            logger.debug("Image progress update failed", exc_info=True)
    except asyncio.CancelledError:
        pass


async def _flow_msg(update, context, text, reply_markup=None, parse_mode=None, flow_key="setup"):
    """Send a fresh flow message and make it the new anchor.

    Use after the user typed/uploaded something — the bot's response should be
    a NEW message so the user's reply stays paired with the question they
    answered. (Editing the previous question would leave their typed reply
    orphaned under a different prompt, which is confusing.)
    """
    anchor_key = f"_flow_anchor_{flow_key}"
    chat = update.effective_chat
    msg = await chat.send_message(
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    context.user_data[anchor_key] = (msg.chat_id, msg.message_id)
    return msg


async def _flow_edit(update, context, text, reply_markup=None, parse_mode=None, flow_key="setup"):
    """Edit the flow's anchor message in place.

    Use for button-driven transitions and progress updates where the user is
    waiting and there's no typed reply between bot states. Falls back to
    sending a new message (and updating the anchor) if the anchor is gone or
    too old to edit.
    """
    anchor_key = f"_flow_anchor_{flow_key}"
    anchor = context.user_data.get(anchor_key)
    if anchor:
        chat_id, msg_id = anchor
        try:
            return await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        except Exception as exc:
            logger.debug("Flow anchor edit failed (%s): %s", flow_key, exc)
            context.user_data.pop(anchor_key, None)
    # No anchor (or edit failed) — send fresh and adopt as new anchor.
    return await _flow_msg(update, context, text, reply_markup=reply_markup, parse_mode=parse_mode, flow_key=flow_key)


def _flow_done(context, flow_key="setup"):
    """Clear the flow's anchor — call when the flow ends (success/cancel/error)."""
    context.user_data.pop(f"_flow_anchor_{flow_key}", None)


# ---------------------------------------------------------------------------
# Weekly nudge — FORM_LABELS + helpers ported from weekly_check.py
# ---------------------------------------------------------------------------
FORM_LABELS = {
    "CBD": "CBD", "DOPS": "DOPS", "MINI_CEX": "Mini-CEX", "ACAT": "ACAT",
    "LAT": "LAT", "ACAF": "ACAF", "STAT": "STAT", "MSF": "MSF",
    "QIAT": "QIAT", "JCF": "JCF", "ESLE_ASSESS": "ESLE", "AUDIT": "Audit",
    "REFLECT_LOG": "Reflective Log", "COMPLAINT": "Complaint",
    "SERIOUS_INC": "Serious Incident", "CRIT_INCIDENT": "Critical Incident",
    "PDP": "PDP", "APPRAISAL": "Appraisal", "TEACH": "Teaching",
    "TEACH_OBS": "Teaching Observation", "TEACH_CONFID": "Confidentiality",
    "SDL": "SDL", "EDU_ACT": "Educational Activity", "EDU_MEETING": "ES Meeting",
    "EDU_MEETING_SUPP": "ES Meeting (Supp)", "FORMAL_COURSE": "Formal Course",
    "PROC_LOG": "Procedure Log", "US_CASE": "Ultrasound Case",
    "RESEARCH": "Research", "CLIN_GOV": "Clinical Governance",
    "COST_IMPROVE": "Cost Improvement", "EQUIP_SERVICE": "Equipment/Service",
    "BUSINESS_CASE": "Business Case",
    "MGMT_ROTA": "Rota Management", "MGMT_RISK": "Risk Management",
    "MGMT_RISK_PROC": "Risk Procedure", "MGMT_INFO": "Information Management",
    "MGMT_EXPERIENCE": "Management Experience", "MGMT_REPORT": "Management Report",
    "MGMT_COMPLAINT": "Management Complaint", "MGMT_GUIDELINE": "Guideline Development",
    "MGMT_INDUCTION": "Induction", "MGMT_PROJECT": "Management Project",
    "MGMT_RECRUIT": "Recruitment", "MGMT_TRAINING_EVT": "Training Event",
    "OOP": "Out of Programme", "ABSENCE": "Absence", "CCT": "CCT Application",
    "HIGHER_PROG": "Higher Programme", "FILE_UPLOAD": "File Upload",
}


def _nudge_label(form_type: str) -> str:
    key = form_type.replace("_2021", "")
    return FORM_LABELS.get(key, key)


async def _compute_weekly_stats(user_id: int) -> dict:
    """Compute cases this week + longest form gap for a user.

    Also returns top form this month and how many distinct form types the
    user has filed this month — used by the weekly digest. KC coverage is
    intentionally not computed here: it lives in case payloads, not the
    usage table, so a faithful number would require an LLM pass per user.
    """
    from datetime import datetime, timezone
    import aiosqlite as _aiosqlite
    from usage import DB_PATH, _ensure_db

    await _ensure_db()
    cases = await get_cases_this_week(user_id)
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")

    async with _aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT form_type, MAX(filed_at) as last_filed FROM portfolio_usage "
            "WHERE telegram_user_id = ? GROUP BY form_type",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        async with db.execute(
            "SELECT form_type, COUNT(*) AS n FROM portfolio_usage "
            "WHERE telegram_user_id = ? AND month_key = ? "
            "GROUP BY form_type ORDER BY n DESC, form_type ASC",
            (user_id, month_key),
        ) as cur:
            month_rows = await cur.fetchall()

    gap = None
    if rows:
        now = datetime.now(timezone.utc)
        worst_form, worst_days = None, 0
        for form_type, last_filed_str in rows:
            try:
                last_filed = datetime.fromisoformat(last_filed_str)
                if last_filed.tzinfo is None:
                    last_filed = last_filed.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            gap_days = (now - last_filed).days
            if gap_days > worst_days:
                worst_days = gap_days
                worst_form = form_type
        if worst_days >= 7 and worst_form:
            gap = (_nudge_label(worst_form), worst_days)

    top_form = None
    if month_rows:
        top_form = (_nudge_label(month_rows[0][0]), month_rows[0][1])

    return {
        "cases": cases,
        "gap": gap,
        "top_form": top_form,
        "form_types_this_month": len(month_rows),
    }


def _static_nudge_text(stats: dict) -> str:
    """Fallback static weekly nudge text \u2014 used when the LLM call fails."""
    cases = stats["cases"]
    gap = stats["gap"]
    lines = []
    if cases > 0:
        lines.append("\U0001f4cb Your portfolio this week")
        lines.append("")
        lines.append(f"Cases filed: {cases} this week")
    else:
        lines.append("\U0001f4cb Portfolio check-in")
        lines.append("")
        lines.append("No cases filed this week \u2014 that's fine, but worth a nudge.")
    if gap:
        label, days = gap
        lines.append("")
        lines.append(f"Longest gap: no {label} in {days} days")
    lines.append("")
    if cases > 0:
        lines.append("Keep the momentum going \u2014 just send me what happened.")
    else:
        lines.append("One case takes 2 minutes. Just send me what happened \u2014 text, voice, photo, or document.")
    return "\n".join(lines)


async def _build_nudge_message(stats: dict) -> tuple[str, InlineKeyboardMarkup | None]:
    """Build weekly nudge message text. No CTA button \u2014 the user types or sends
    media directly to start a case (the Menu button at bottom-left gives
    access to /settings, /voice, etc.)."""
    text = await generate_nudge_copy(stats)
    if not text:
        text = _static_nudge_text(stats)
    return text, None


def _build_weekly_digest_text(stats: dict) -> str:
    """Compose the weekly digest body (Markdown).

    Mirrors the fields agreed in the digest brief: cases-this-week, top form
    this month, form-type breadth (used as a faithful proxy for coverage —
    we don't store KC codes in portfolio_usage so we don't claim KC numbers),
    and a single nudge line.
    """
    cases = stats.get("cases", 0)
    top_form = stats.get("top_form")
    form_types = stats.get("form_types_this_month", 0)
    gap = stats.get("gap")

    lines = ["📊 *Your Weekly Portfolio Digest*", ""]
    lines.append(f"This week: {cases} case{'s' if cases != 1 else ''} filed.")
    if top_form:
        label, count = top_form
        lines.append(f"Top form this month: {label} ({count})")
    if form_types:
        lines.append(f"Form types this month: {form_types}")

    if gap:
        label, days = gap
        nudge = f"Longest gap: no {label} in {days} days — worth a quick log."
    elif cases == 0:
        nudge = "No cases this week — one takes 2 minutes. Send text, voice, photo, or document."
    else:
        nudge = "Keep the momentum going — send me what happened next."
    lines.append("")
    lines.append(nudge)
    lines.append("")
    lines.append("Tap /health for full analysis.")
    return "\n".join(lines)


async def weekly_push(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the weekly portfolio digest (chart + summary) to active users.

    Guard: file-based dedup (survives bot restarts + persistence flush failures).
    Skips if run within 6 days of last send — keeps a true weekly cadence even
    if the bot restarts daily.
    """
    import os
    sentinel = os.path.expanduser("~/.openclaw/data/portfolio-guru/weekly_push_last_run")
    os.makedirs(os.path.dirname(sentinel), exist_ok=True)
    now = time.time()
    if os.path.exists(sentinel):
        last_run = float(open(sentinel).read().strip())
        if now - last_run < 518400:
            logger.info("weekly_push skipped — ran %.1f days ago", (now - last_run) / 86400)
            return

    with open(sentinel, "w") as f:
        f.write(str(now))
    logger.info("weekly_push starting")

    users = await get_all_active_users()
    sent = 0
    failed = 0

    for user_id in users:
        try:
            stats = await _compute_weekly_stats(user_id)
            text = _build_weekly_digest_text(stats)

            chart_path = None
            try:
                from portfolio_chart import generate_health_chart_async
                chart_path = await generate_health_chart_async(user_id)
            except ImportError:
                logger.info("matplotlib not installed; weekly digest will be text-only for %s", user_id)
            except Exception as e:
                logger.warning("weekly_push chart generation failed for %s: %s", user_id, e)

            if chart_path:
                try:
                    with open(chart_path, "rb") as fh:
                        await context.bot.send_photo(chat_id=user_id, photo=fh)
                except Exception as e:
                    logger.warning("weekly_push chart send failed for %s: %s", user_id, e)
                finally:
                    try:
                        os.remove(chart_path)
                    except OSError:
                        pass

            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="Markdown",
            )
            sent += 1
        except Exception as e:
            logger.warning("weekly_push failed for %s: %s", user_id, e)
            failed += 1

    logger.info("weekly_push complete: %d sent, %d failed", sent, failed)


async def _edit_last_bot_msg(context, chat_id, text, reply_markup=None, parse_mode=None):
    """Edit the last bot ack message in place. Falls back to new message if not found."""
    msg_id = context.user_data.get("last_bot_msg_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return
        except Exception:
            pass
    # Fallback: send new message and store its id
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    context.user_data["last_bot_msg_id"] = msg.message_id
    context.user_data["last_bot_chat_id"] = chat_id


def _track_latest_message(context, msg):
    """Remember a bot message that later flow steps should edit in place."""
    chat_id = getattr(msg, "chat_id", None) or getattr(getattr(msg, "chat", None), "id", None)
    context.user_data["last_bot_msg_id"] = msg.message_id
    context.user_data["last_bot_chat_id"] = chat_id
    context.user_data["status_msg_id"] = msg.message_id
    context.user_data["status_msg_chat"] = chat_id


def _track_pending_bundle_message(context, msg):
    """Remember only the active media-bundle status message.

    This is intentionally separate from ``last_bot_msg_id`` so a new case
    cannot mutate an old "reading image..." message from a previous bundle.
    """
    context.user_data["pending_bundle_msg_id"] = msg.message_id
    context.user_data["pending_bundle_chat_id"] = msg.chat_id


def _voice_media_from_message(message):
    """Return a voice-like Telegram attachment, including forwarded audio."""
    voice = getattr(message, "voice", None) or getattr(message, "audio", None)
    if voice:
        return voice

    document = getattr(message, "document", None)
    if not document:
        return None

    file_name = (getattr(document, "file_name", None) or "").lower()
    mime_type = (getattr(document, "mime_type", None) or "").lower()
    voice_extensions = (".oga", ".ogg", ".opus", ".mp3", ".m4a", ".mp4", ".wav")
    if mime_type.startswith("audio/") or file_name.endswith(voice_extensions):
        return document
    return None


def _voice_media_suffix(media) -> str:
    file_name = getattr(media, "file_name", None) or ""
    suffix = os.path.splitext(file_name)[1]
    if suffix:
        return suffix
    mime_type = (getattr(media, "mime_type", None) or "").lower()
    if "mpeg" in mime_type or "mp3" in mime_type:
        return ".mp3"
    if "mp4" in mime_type or "m4a" in mime_type:
        return ".m4a"
    return ".ogg"


async def _send_latest_message(message, context, text, reply_markup=None, parse_mode=None):
    """Edit the active bot message when possible, otherwise send and track one."""
    chat_id = getattr(message, "chat_id", None) or getattr(getattr(message, "chat", None), "id", None)
    msg_id = context.user_data.get("last_bot_msg_id")
    if chat_id and msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            context.user_data["last_bot_chat_id"] = chat_id
            class _TrackedMessage:
                def __init__(self, bot, chat_id, message_id):
                    self._bot = bot
                    self.chat_id = chat_id
                    self.message_id = message_id
                    self.chat = getattr(message, "chat", None)

                async def edit_text(self, text, **kwargs):
                    return await self._bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.message_id,
                        text=text,
                        **kwargs,
                    )

            return _TrackedMessage(context.bot, chat_id, msg_id)
        except Exception:
            pass
    msg = await message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    _track_latest_message(context, msg)
    return msg


async def _send_pending_bundle_status(message, context, text, reply_markup=None, parse_mode=None):
    """Edit the current bundle status only, otherwise send a fresh bundle status."""
    chat_id = getattr(message, "chat_id", None) or getattr(getattr(message, "chat", None), "id", None)
    msg_id = context.user_data.get("pending_bundle_msg_id")
    if chat_id and msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            context.user_data["pending_bundle_chat_id"] = chat_id

            class _TrackedMessage:
                def __init__(self, bot, chat_id, message_id):
                    self._bot = bot
                    self.chat_id = chat_id
                    self.message_id = message_id
                    self.chat = getattr(message, "chat", None)

                async def edit_text(self, text, **kwargs):
                    return await self._bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.message_id,
                        text=text,
                        **kwargs,
                    )

            return _TrackedMessage(context.bot, chat_id, msg_id)
        except Exception:
            context.user_data.pop("pending_bundle_msg_id", None)
            context.user_data.pop("pending_bundle_chat_id", None)
    msg = await message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    _track_pending_bundle_message(context, msg)
    return msg


def _serialise_draft(draft):
    """Serialise a draft as a plain dict so PicklePersistence can store it."""
    if isinstance(draft, CBDData):
        return {"_type": "CBD", **draft.model_dump()}
    if isinstance(draft, FormDraft):
        return {"_type": "FORM", "form_type": draft.form_type, "fields": draft.fields, "uuid": draft.uuid}
    return None


def _store_draft(context, draft):
    """Store draft as plain dict so PicklePersistence can serialise it."""
    context.user_data["draft_data"] = _serialise_draft(draft)


def _deserialise_draft(raw):
    """Reconstruct a draft object from its stored dict form."""
    if not raw:
        return None
    if isinstance(raw, (CBDData, FormDraft)):
        return raw
    t = raw.get("_type")
    if t == "CBD":
        d = {k: v for k, v in raw.items() if k != "_type"}
        return CBDData(**d)
    if t == "FORM":
        return FormDraft(form_type=raw["form_type"], fields=raw["fields"], uuid=raw.get("uuid"))
    return None


def _load_draft(context):
    """Reconstruct draft object from stored dict."""
    return _deserialise_draft(context.user_data.get("draft_data"))


def _restore_retryable_draft(context) -> bool:
    """If the active draft is gone but the last filing was partial/failed, restore
    the saved `last_amend_*` snapshot into `draft_data` so a retry approval can
    file it again. Returns True when a draft is restored."""
    status = context.user_data.get("last_filing_status")
    if status not in {"partial", "failed"}:
        return False
    if context.user_data.get("draft_data"):
        return False
    amend_draft = context.user_data.get("last_amend_draft")
    if not amend_draft:
        return False
    context.user_data["draft_data"] = amend_draft
    amend_case = context.user_data.get("last_amend_case_text")
    if amend_case and not context.user_data.get("case_text"):
        context.user_data["case_text"] = amend_case
    amend_form = context.user_data.get("last_amend_chosen_form")
    if amend_form and not context.user_data.get("chosen_form"):
        context.user_data["chosen_form"] = amend_form
    return True


def _store_pending_draft(context, draft) -> None:
    """Store the analysed draft used during template review."""
    context.user_data["pending_draft_data"] = _serialise_draft(draft)


def _load_pending_draft(context):
    """Load the analysed draft used during template review."""
    return _deserialise_draft(context.user_data.get("pending_draft_data"))


def _case_review_state_snapshot(context) -> dict:
    """Debug-safe snapshot for callback routing without logging case content."""
    case_text = context.user_data.get("case_text") or ""
    return {
        "has_case_text": bool(case_text),
        "case_chars": len(case_text),
        "awaiting_detail": bool(context.user_data.get("awaiting_detail")),
        "pending_form": context.user_data.get("chosen_form"),
        "has_pending_draft": bool(context.user_data.get("pending_draft_data")),
        "input_source": context.user_data.get("case_input_source"),
    }


def _clear_case_review_state(context, keep_case: bool = True) -> None:
    """Clear transient case-review flags while optionally preserving the stored case text."""
    for key in (
        "awaiting_detail",
        "case_input_source",
        "chosen_form",
        "paused_flow_rebuild",
        "pending_draft_data",
        "pending_new_case_text",
        "template_prompt_message_id",
        "template_prompt_chat_id",
        "form_recommendations",
        "form_recommendations_text",
        "document_name",
        "accumulating_case",
        "accumulation_additions",
        "pending_case_bundle",
        "pending_bundle_msg_id",
        "pending_bundle_chat_id",
    ):
        context.user_data.pop(key, None)
    if not keep_case:
        for key in (
            "case_text",
            "draft_data",
            "current_draft",
            "pending_new_case_text",
            "excluded_form_type",
            "quick_improve_used",
            "amend_mode",
            "amend_pending_feedback",
            "last_draft_preview",
            "last_amend_draft",
            "last_amend_case_text",
            "last_amend_chosen_form",
            "last_bot_msg_id",
            "last_bot_chat_id",
            "attachment_path",
            "attachment_name",
        ):
            context.user_data.pop(key, None)


_WAIT_FOR_MEDIA_RE = re.compile(
    r"\b("
    r"wait(?:ing)? for (?:the )?(?:image|images|picture|pictures|photo|photos|screenshot|screenshots|attachment|attachments|media)"
    r"|(?:image|images|picture|pictures|photo|photos|screenshot|screenshots|attachment|attachments|media) (?:are |is )?(?:coming|to follow|next)"
    r"|(?:will|going to|gonna) send (?:the )?(?:image|images|picture|pictures|photo|photos|screenshot|screenshots|attachment|attachments|media)"
    r"|don'?t start yet"
    r"|do not start yet"
    r"|hold (?:on|off)"
    r")\b",
    re.IGNORECASE,
)

_BUNDLE_DONE_RE = re.compile(
    r"^\s*(done|all done|finished|that'?s all|that is all|no more|complete|process now)\s*[.!?]*\s*$",
    re.IGNORECASE,
)

_TEXT_FILING_APPROVAL_RE = re.compile(
    r"^\s*(?:"
    # file/save/submit [+ this|it|the draft] [+ as draft|to kaizen|in kaizen] [+ again]
    r"(?:file|save|submit)"
    r"(?:\s+(?:this|it|the\s+draft))?"
    r"(?:\s+(?:as\s+)?(?:draft|to\s+kaizen|in\s+kaizen))?"
    r"(?:\s+(?:again|once\s+more))"
    r"|(?:file|save|submit)\s+(?:this|it|the\s+draft)"
    r"(?:\s+(?:as\s+)?(?:draft|to\s+kaizen|in\s+kaizen))?"
    # try/retry [+ it|this|filing|the draft] [+ again]
    r"|(?:try|retry)\s+(?:it|this|filing|the\s+draft)(?:\s+again)?"
    r"|try\s+again"
    r"|retry"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)

_RECENT_FILING_STATUS_RE = re.compile(
    r"\b(file|filed|filing|save|saved|saving|kaizen|complete|completed|stuck|draft)\b",
    re.IGNORECASE,
)

_NEW_CASE_START_RE = re.compile(
    r"\b("
    r"new case"
    r"|another case"
    r"|case (?:that )?i (?:want|would like) to file"
    r"|this patient (?:came|presented)"
    r"|patient (?:came|presented) with"
    r")\b",
    re.IGNORECASE,
)

PENDING_CASE_BUNDLE_MAX_IDLE_SECONDS = 20 * 60


def _is_waiting_for_media_request(text: str) -> bool:
    return bool(text and _WAIT_FOR_MEDIA_RE.search(text))


def _is_case_bundle_done(text: str) -> bool:
    return bool(text and _BUNDLE_DONE_RE.match(text))


def _is_text_filing_approval(text: str) -> bool:
    return bool(text and _TEXT_FILING_APPROVAL_RE.match(text))


def _is_recent_filing_status_question(text: str) -> bool:
    if not text or not _RECENT_FILING_STATUS_RE.search(text):
        return False
    lowered = text.lower()
    return (
        "?" in text
        or "what happened" in lowered
        or "did it" in lowered
        or "was it" in lowered
        or "were you" in lowered
        or "are you" in lowered
        or "stuck" in lowered
    )


_SUBMIT_INQUIRY_RE = re.compile(
    r"(?:supervisor|assessor)\b"
    r"|save\s+directly\s+(?:in|into|to)\s+kaizen"
    r"|(?:submit|sign|forward)\b.*\b(?:supervisor|assessor|automatic)"
    r"|will\s+(?:this|it)\s+(?:be\s+)?(?:sent|submitted|forwarded|signed|go\s+to)",
    re.IGNORECASE,
)

_DRAFT_ONLY_CLARIFICATION = (
    "Portfolio Guru saves Kaizen entries as *drafts only*. "
    "No supervisor request, sign-off, or submission is ever made automatically.\n\n"
    "You review the draft here, then save it. "
    "Supervisor assignment happens separately in Kaizen when you're ready."
)


def _is_submit_inquiry(text: str) -> bool:
    """True when the user asks whether the bot will submit/sign/send to supervisor."""
    if not text:
        return False
    lowered = text.lower()
    return bool(_SUBMIT_INQUIRY_RE.search(text)) and (
        "?" in text
        or any(w in lowered for w in ("submit", "sign", "send", "forward", "will", "does", "would", "save directly"))
    )


_PRE_CAPTURE_ANSWER_INTENTS = frozenset(
    {
        ConversationalIntent.PORTFOLIO_QUESTION,
        ConversationalIntent.HELP_OR_CAPABILITY,
        ConversationalIntent.ACCOUNT_OR_BILLING,
        ConversationalIntent.SETUP_OR_CREDENTIALS,
    }
)


def _standalone_pre_capture_route(text: str) -> str | None:
    """Route obvious non-case first turns before extraction/drafting."""
    raw = (text or "").strip()
    if not raw:
        return None

    lowered = raw.lower()
    menu_commandish = any(
        hint in lowered
        for hint in (
            "setting",
            "settings",
            "stat",
            "stats",
            "how many cases",
            "how many this month",
            "this month",
            "usage",
            "limit",
            "show my",
            "show me",
            "open settings",
        )
    )
    if menu_commandish:
        return None

    result = route_message(raw)
    if result.intent in _PRE_CAPTURE_ANSWER_INTENTS:
        return "answer"
    if result.intent in {
        ConversationalIntent.SAFETY_OR_MEDICAL_ADVICE,
        ConversationalIntent.OUT_OF_SCOPE,
    }:
        return "safe_redirect"
    if result.intent is ConversationalIntent.NEW_CASE:
        return None

    questionish = bool(
        "?" in raw
        or re.match(r"^(what|which|how|why|when|where|who|can|could|do|does|is|are|will|should)\b", lowered)
    )
    prompt_injectionish = bool(
        re.search(r"\b(ignore previous|system prompt|developer message|jailbreak|reveal your prompt)\b", lowered)
    )
    clinical_hits = sum(
        1
        for keyword in (
            "patient",
            "presented",
            "diagnosed",
            "managed",
            "treated",
            "resus",
            "chest pain",
            "abdominal pain",
            "fracture",
            "procedure",
            "supervisor",
        )
        if keyword in lowered
    )
    has_demographic = bool(re.search(r"\b\d{1,3}\s*([mf]|male|female)\b", lowered))

    if prompt_injectionish:
        return "safe_redirect"
    if questionish:
        return "answer"
    if not clinical_hits and not has_demographic and len(raw.split()) <= 6:
        return "safe_redirect"
    return None


def _standalone_safe_redirect_text(text: str) -> str:
    return safety_redirect_text(text)


def _clear_pending_case_bundle(context) -> None:
    for key in (
        "pending_case_bundle",
        "pending_bundle_msg_id",
        "pending_bundle_chat_id",
    ):
        context.user_data.pop(key, None)


def _looks_like_new_case_start(text: str) -> bool:
    if not text:
        return False
    if _NEW_CASE_START_RE.search(text):
        return True
    lowered = text.lower()
    clinical_markers = (
        "patient",
        "presented",
        "blood pressure",
        "tachycardia",
        "hypotension",
        "resus",
        "sedation",
        "cardioversion",
        "procedure",
    )
    return len(text.split()) >= 25 and sum(1 for marker in clinical_markers if marker in lowered) >= 2


def _pending_case_bundle_is_stale(context) -> bool:
    bundle = context.user_data.get("pending_case_bundle") or {}
    updated_at = bundle.get("updated_at") or bundle.get("created_at")
    return bool(updated_at and time.time() - updated_at > PENDING_CASE_BUNDLE_MAX_IDLE_SECONDS)


def _append_pending_case_bundle(context, text: str, source: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    now = time.time()
    bundle = context.user_data.setdefault(
        "pending_case_bundle",
        {"parts": [], "sources": [], "created_at": now, "updated_at": now},
    )
    bundle.setdefault("created_at", now)
    bundle["updated_at"] = now
    bundle.setdefault("parts", []).append({"source": source, "text": text})
    sources = bundle.setdefault("sources", [])
    if source not in sources:
        sources.append(source)


def _pending_case_bundle_source_count(context, source: str) -> int:
    bundle = context.user_data.get("pending_case_bundle") or {}
    return sum(1 for part in bundle.get("parts", []) if part.get("source") == source)


def _combined_pending_case_bundle(context) -> tuple[str, str]:
    bundle = context.user_data.get("pending_case_bundle") or {}
    parts = [
        part.get("text", "").strip()
        for part in bundle.get("parts", [])
        if part.get("text", "").strip()
    ]
    sources = bundle.get("sources") or ["text"]
    source = "mixed" if len(sources) > 1 else sources[0]
    return combine_case_inputs(parts[0], parts[1:]) if parts else "", source

_GATHERING_MODE_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_GATHERING_CASE_KEY = "gathering_case"


def _gathering_env_enabled() -> bool:
    # Gathering mode is the default. The env var only acts as a deployment-level
    # opt-out: setting PG_GATHERING_MODE to 0/false/no/off disables it for everyone.
    return os.environ.get("PG_GATHERING_MODE", "").strip().lower() not in _GATHERING_MODE_FALSE_VALUES


def _gathering_enabled(context) -> bool:
    if not _gathering_env_enabled():
        return False
    # Default on for every user. Power users opt out via /gather off, which
    # explicitly sets gathering_mode to False.
    return context.user_data.get("gathering_mode") is not False


def _gathering_case_active(context) -> bool:
    return bool((context.user_data.get(_GATHERING_CASE_KEY) or {}).get("parts"))


def _clear_gathering_case(context) -> None:
    context.user_data.pop(_GATHERING_CASE_KEY, None)


def _append_gathering_case(context, text: str, source: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    now = time.time()
    case = context.user_data.setdefault(
        _GATHERING_CASE_KEY,
        {"parts": [], "sources": [], "created_at": now, "updated_at": now},
    )
    case.setdefault("created_at", now)
    case["updated_at"] = now
    case.setdefault("parts", []).append({"source": source, "text": text})
    sources = case.setdefault("sources", [])
    if source not in sources:
        sources.append(source)


def _combined_gathering_case(context) -> tuple[str, str]:
    case = context.user_data.get(_GATHERING_CASE_KEY) or {}
    parts = [
        part.get("text", "").strip()
        for part in case.get("parts", [])
        if part.get("text", "").strip()
    ]
    sources = case.get("sources") or ["text"]
    source = "mixed" if len(sources) > 1 else sources[0]
    return combine_case_inputs(parts[0], parts[1:]) if parts else "", source


def _gathering_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Draft now", callback_data="GATHER|done"),
        InlineKeyboardButton("❌ Cancel", callback_data="ACTION|cancel"),
    ]])


def _gathering_reply(context) -> tuple[str, InlineKeyboardMarkup]:
    return render_message("gathering_captured"), _gathering_done_keyboard()

# ConversationHandler states
(AWAIT_USERNAME, AWAIT_PASSWORD,
 AWAIT_FORM_CHOICE, AWAIT_APPROVAL,
 AWAIT_EDIT_FIELD, AWAIT_EDIT_VALUE,
 AWAIT_CASE_INPUT, AWAIT_TRAINING_LEVEL,
 AWAIT_VOICE_EXAMPLES, AWAIT_TEMPLATE_REVIEW,
 AWAIT_CURRICULUM, AWAIT_FORM_SEARCH,
 AWAIT_GATHERING, AWAIT_PATHWAY,
 AWAIT_DOC_INTENT) = range(15)

# Common button patterns used across the bot
_BTN_SETUP = InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup")
_BTN_CANCEL = InlineKeyboardButton("❌ Cancel", callback_data="ACTION|cancel")
_BTN_HELP = InlineKeyboardButton("ℹ️ Help", callback_data="INFO|what")
_BTN_VOICE = InlineKeyboardButton("✍️ Voice Profile", callback_data="ACTION|voice")
_BTN_CONTINUE_THIN = InlineKeyboardButton("✅ Show me the draft", callback_data="ACTION|continue_thin")
_BTN_BACK_TO_MISSING = InlineKeyboardButton("⬅️ Back to missing details", callback_data="ACTION|back_to_missing")
_DATA_CLEAR_TEXT = (
    "✅ Your Portfolio Guru data is clear.\n\n"
    "I don’t have any Kaizen credentials, portfolio preferences, curriculum choice, voice profile, "
    "or bot draft state stored for you now.\n\n"
    "Cases already saved in Kaizen are unaffected.\n\n"
    "To use Portfolio Guru again, reconnect Kaizen."
)


def _nav_row(
    back_text: str,
    back_callback: str,
    cancel_text: str = "❌ Cancel",
    cancel_callback: str = "ACTION|cancel",
) -> list[InlineKeyboardButton]:
    """Compact Back + Cancel row for navigation-heavy mobile screens."""
    return [
        InlineKeyboardButton(back_text, callback_data=back_callback),
        InlineKeyboardButton(cancel_text, callback_data=cancel_callback),
    ]


# Single-button "❌ Cancel" keyboard used in error / recovery surfaces where
# the user needs an obvious way out. ACTION|cancel clears flow state and
# returns the user to a clean "ready to file" message.
_KB_CANCEL = InlineKeyboardMarkup([[_BTN_CANCEL]])

# Retry + cancel keyboard for transient template-review failures (rate limits,
# upstream LLM outage). Pairs with ACTION|retry_template — the bot re-runs
# `_analyse_selected_form` using context.user_data["chosen_form"].
_KB_RETRY_TEMPLATE = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔄 Try again", callback_data="ACTION|retry_template")],
    [_BTN_CANCEL],
])

_KB_RETRY_SETUP = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔄 Try again", callback_data="ACTION|setup")],
    [_BTN_CANCEL],
])

# Substrings that indicate the LLM call hit a transient upstream issue (rate
# limiting, quota, service overload) rather than a code bug. Matching is
# case-insensitive on str(exception). Keep tight — false positives would hide
# real failures from the user as "try again" rather than surfacing them.
_TRANSIENT_LLM_ERROR_MARKERS = (
    "429",
    "resource_exhausted",
    "rate limit",
    "rate-limit",
    "ratelimit",
    "quota",
    "overloaded",
    "503",
    "502",
    "500",
    "service unavailable",
    "unavailable",
    "temporarily",
)


def _is_transient_llm_error(exc: BaseException) -> bool:
    """Return True when `exc` looks like a transient upstream LLM failure.

    Used to decide whether to show the user a retry-friendly message vs the
    generic "could not review" wall. See `_KB_RETRY_TEMPLATE` for the keyboard
    paired with this branch.
    """
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_LLM_ERROR_MARKERS)


def _setup_needs_finishing(user_id: int) -> bool:
    try:
        return not has_credentials(user_id)
    except Exception:
        logger.warning("Credential setup check failed; treating setup as unfinished", exc_info=True)
        return True


def _build_next_step_keyboard(user_id: int) -> InlineKeyboardMarkup | None:
    """Keyboard shown after a cancellation / recovery. None for connected
    users — the next action is to send a case, so no button is needed.
    Disconnected users see a single Connect button (the one essential CTA)."""
    if _setup_needs_finishing(user_id):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup")],
        ])
    return None


def _build_data_clear_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_BTN_SETUP],
    ])


def _cancelled_next_step_text(user_id: int, scope: str = "Cancelled") -> str:
    if _setup_needs_finishing(user_id):
        return f"❌ {scope}. Connect Kaizen to start filing."
    return f"↩️ {scope}.\n\n{render_message('welcome_connected')}"


def _expired_prompt_text(user_id: int) -> str:
    if _setup_needs_finishing(user_id):
        return "⏳ That button has expired. Finish setup from the latest message and I'll pick it up from there."
    return "⏳ That button has expired. Start a new case from the latest message and I'll pick it up from there."


def _restore_last_filed_case_context(context) -> bool:
    """Restore the previous case for stale same-case/form-list callbacks."""
    case_text = (context.user_data.get("last_filed_case_text") or "").strip()
    if not case_text or context.user_data.get("case_text"):
        return False
    context.user_data["case_text"] = case_text
    context.user_data["case_input_source"] = "same case"
    filed_form = context.user_data.get("last_filed_form_type")
    if filed_form and not context.user_data.get("excluded_form_type"):
        context.user_data["excluded_form_type"] = filed_form
    return True


async def _resume_paused_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str) -> int:
    """Recover a paused case by sending a fresh latest message for the current step."""
    user_id = update.effective_user.id
    message = update.effective_message
    draft = _load_draft(context)
    pending_draft = _load_pending_draft(context)
    _restore_last_filed_case_context(context)
    case_text = (context.user_data.get("case_text") or "").strip()
    chosen_form = context.user_data.get("chosen_form")

    if _setup_needs_finishing(user_id):
        await _send_latest_message(
            message,
            context,
            "That earlier button is no longer active. Finish setup from the latest message below and I'll carry on from there.",
            reply_markup=_build_next_step_keyboard(user_id),
        )
        return ConversationHandler.END

    if draft:
        await _send_latest_message(
            message,
            context,
            f"{reason}\n\nYour draft is still ready below.",
        )
        await _send_latest_message(
            message,
            context,
            _format_draft_preview(draft) + _REPLY_HINT_SUFFIX,
            reply_markup=_build_approval_keyboard(improved_once=context.user_data.get("quick_improve_used", False)),
            parse_mode="Markdown",
        )
        return AWAIT_APPROVAL

    if pending_draft and chosen_form:
        await _send_latest_message(
            message,
            context,
            f"{reason}\n\nYour draft is still ready below.",
        )
        if not _draft_has_useful_content(pending_draft, chosen_form):
            return await _ask_for_more_detail_before_draft(message, context, edit=False)
        return await _show_draft_review(message, context, pending_draft, chosen_form, edit=False)

    if case_text and chosen_form and context.user_data.get("paused_flow_rebuild"):
        context.user_data.pop("paused_flow_rebuild", None)
        await _send_latest_message(
            message,
            context,
            f"{reason}\n\nI rebuilt the latest {_form_display_name(chosen_form)} step below so you can keep going.",
        )
        try:
            refreshed_draft = await _analyse_selected_form(context, user_id, case_text, chosen_form)
        except asyncio.TimeoutError:
            await _send_latest_message(
                message,
                context,
                "That case is still saved, but rebuilding the latest step timed out. Start a new case below and I'll rebuild it with you.",
                reply_markup=_build_next_step_keyboard(user_id),
            )
            return ConversationHandler.END
        except Exception as exc:
            logger.error("Paused flow rebuild failed for %s: %s", chosen_form, exc, exc_info=True)
            await _send_latest_message(
                message,
                context,
                "That case is still saved, but I couldn't rebuild the latest step just now. Start a new case below and I'll rebuild it with you.",
                reply_markup=_build_next_step_keyboard(user_id),
            )
            return ConversationHandler.END

        if not _draft_has_useful_content(refreshed_draft, chosen_form):
            return await _ask_for_more_detail_before_draft(message, context, edit=False)
        return await _show_draft_review(message, context, refreshed_draft, chosen_form, edit=False)

    if case_text:
        recommendations = context.user_data.get("form_recommendations") or []
        if not recommendations:
            try:
                training_level = get_training_level(user_id)
                allowed_forms = _allowed_forms_for_training_level(training_level)
                recommendations = await asyncio.wait_for(
                    recommend_form_types(
                        case_text,
                        input_source=context.user_data.get("case_input_source", "text"),
                    ),
                    timeout=30,
                )
                excluded_form = _normalise_form_type(context.user_data.get("excluded_form_type", ""))
                recommendations = _filter_recommendations_for_allowed_forms(
                    recommendations,
                    allowed_forms,
                    case_text,
                    excluded_form=excluded_form,
                )
                context.user_data["form_recommendations"] = recommendations
            except Exception as exc:
                logger.error("Paused flow recommendation rebuild failed: %s", exc, exc_info=True)
                recommendations = []

        if recommendations:
            prompt_text = context.user_data.get("form_recommendations_text") or (
                "Your case is still saved. Pick one and I'll show that template plus anything still missing."
            )
            await _send_latest_message(
                message,
                context,
                f"{reason}\n\nYour case is still in progress — pick the next step below.",
            )
            await _send_latest_message(
                message,
                context,
                prompt_text,
                reply_markup=_build_form_choice_keyboard(recommendations, curriculum=_effective_curriculum(user_id)),
            )
            return AWAIT_FORM_CHOICE

    context.user_data.clear()
    await _send_latest_message(
        message,
        context,
        "That draft has expired, but your setup is still saved. Start a new case and I'll rebuild it with you.",
        reply_markup=_build_next_step_keyboard(user_id),
    )
    return ConversationHandler.END

# Kaizen portfolio profile → form types available.
# These are RCEM/Kaizen portfolio buckets, not the user's exact grade/year.
TRAINING_LEVEL_FORMS = {
    "ST3": [
        "CBD", "DOPS", "MINI_CEX", "ACAT", "MSF", "QIAT", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH",
        "COMPLAINT", "SERIOUS_INC",
        "REFLECT_LOG", "TEACH_OBS", "ESLE_ASSESS", "TEACH_CONFID", "APPRAISAL", "CLIN_GOV",
        "CRIT_INCIDENT", "AUDIT", "RESEARCH", "EDU_MEETING", "EDU_MEETING_SUPP", "PDP",
    ],
    "ST4": [
        "CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "MSF", "QIAT", "PROC_LOG", "SDL", "EDU_ACT",
        "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC",
        "REFLECT_LOG", "TEACH_OBS", "ESLE_ASSESS", "TEACH_CONFID", "APPRAISAL", "CLIN_GOV",
        "CRIT_INCIDENT", "AUDIT", "RESEARCH", "EDU_MEETING", "EDU_MEETING_SUPP", "PDP",
        "BUSINESS_CASE", "COST_IMPROVE", "EQUIP_SERVICE",
    ],
    "ST5": [
        "CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "STAT", "MSF", "QIAT", "JCF", "PROC_LOG", "SDL",
        "EDU_ACT", "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC",
        "REFLECT_LOG", "TEACH_OBS", "ESLE_ASSESS", "TEACH_CONFID", "APPRAISAL", "CLIN_GOV",
        "CRIT_INCIDENT", "AUDIT", "RESEARCH", "EDU_MEETING", "EDU_MEETING_SUPP", "PDP",
        "BUSINESS_CASE", "COST_IMPROVE", "EQUIP_SERVICE",
        "MGMT_ROTA", "MGMT_RISK", "MGMT_PROJECT",
        "MGMT_RECRUIT", "MGMT_RISK_PROC", "MGMT_TRAINING_EVT", "MGMT_GUIDELINE", "MGMT_INFO",
        "MGMT_INDUCTION", "MGMT_EXPERIENCE", "MGMT_REPORT", "MGMT_COMPLAINT",
    ],
    "ST6": [
        "CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "STAT", "MSF", "QIAT", "JCF", "PROC_LOG", "SDL",
        "EDU_ACT", "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC",
        "REFLECT_LOG", "TEACH_OBS", "ESLE_ASSESS", "TEACH_CONFID", "APPRAISAL", "CLIN_GOV",
        "CRIT_INCIDENT", "AUDIT", "RESEARCH", "EDU_MEETING", "EDU_MEETING_SUPP", "PDP",
        "BUSINESS_CASE", "COST_IMPROVE", "EQUIP_SERVICE",
        "MGMT_ROTA", "MGMT_RISK", "MGMT_PROJECT",
        "MGMT_RECRUIT", "MGMT_RISK_PROC", "MGMT_TRAINING_EVT", "MGMT_GUIDELINE", "MGMT_INFO",
        "MGMT_INDUCTION", "MGMT_EXPERIENCE", "MGMT_REPORT", "MGMT_COMPLAINT",
    ],
    # Non-training (SAS / CESR) draws from the same shared form family as the
    # trainee profiles — the difference is curriculum visibility, not a separate
    # catalogue. SAS is pinned to the 2021 curriculum (see
    # ``_effective_curriculum``), so these base codes resolve to their _2021
    # variants at display/filing time where a variant exists and stay base
    # (shared 2021/2025) where it does not.
    "SAS": [
        "CBD", "DOPS", "MINI_CEX", "ACAT", "ACAF", "MSF", "LAT", "QIAT",
        "JCF", "PROC_LOG", "AUDIT", "REFLECT_LOG", "SDL", "EDU_ACT",
        "FORMAL_COURSE", "TEACH", "STAT", "TEACH_OBS", "TEACH_CONFID",
        "COMPLAINT", "SERIOUS_INC", "APPRAISAL", "CLIN_GOV", "CRIT_INCIDENT",
        "US_CASE", "ESLE_ASSESS", "RESEARCH", "PDP", "EDU_MEETING", "EDU_MEETING_SUPP",
        "BUSINESS_CASE", "COST_IMPROVE", "EQUIP_SERVICE",
        "MGMT_ROTA", "MGMT_RISK", "MGMT_PROJECT",
        "MGMT_RECRUIT", "MGMT_RISK_PROC", "MGMT_TRAINING_EVT", "MGMT_GUIDELINE",
        "MGMT_INFO", "MGMT_INDUCTION", "MGMT_EXPERIENCE", "MGMT_REPORT",
        "MGMT_COMPLAINT",
    ],
}


# Kaizen catalogue drift ledger. This is intentionally separate from
# TRAINING_LEVEL_FORMS: only fully wired, user-selectable forms go in the
# profile catalogues and category picker. Entries here are visible in Kaizen
# evidence/admin lists but are hidden until UUID + schema + deterministic filer
# plumbing exists, or because they are utility/admin surfaces rather than WPBAs.
KAIZEN_CATALOGUE_STATUS = {
    "ASAT": {
        "label": "ACCS Simulation Assessment Tool",
        "profiles": ["ACCS"],
        "status": "unsupported-pending-schema",
        "reason": "ACCS selector UUID known; no FORM_SCHEMAS/FORM_FIELD_MAP user-facing support.",
    },
    "EPA1": {
        "label": "Entrustable Professional Activity 1",
        "profiles": ["ACCS"],
        "status": "unsupported-pending-schema",
        "reason": "ACCS selector UUID known; no FORM_SCHEMAS/FORM_FIELD_MAP user-facing support.",
    },
    "EPA2": {
        "label": "Entrustable Professional Activity 2",
        "profiles": ["ACCS"],
        "status": "unsupported-pending-schema",
        "reason": "ACCS selector UUID known; no FORM_SCHEMAS/FORM_FIELD_MAP user-facing support.",
    },
    "ACCS_PROGRESS": {
        "label": "ACCS Progression",
        "profiles": ["ACCS"],
        "status": "unsupported-pending-schema",
        "reason": "Kaizen-visible progression form without complete user-facing filing plumbing.",
    },
    "INTERMEDIATE_PROGRESS": {
        "label": "Intermediate Progression",
        "profiles": ["INTERMEDIATE"],
        "status": "unsupported-pending-schema",
        "reason": "Kaizen-visible progression form without complete user-facing filing plumbing.",
    },
    "MCR_MTR_ACCS": {
        "label": "MCR/MTR ACCS",
        "profiles": ["ACCS"],
        "status": "unsupported-pending-schema",
        "reason": "Kaizen-visible ACCS review form without complete user-facing filing plumbing.",
    },
    "HALO_ICM": {
        "label": "HALO ICM",
        "profiles": ["ACCS"],
        "status": "unsupported-pending-schema",
        "reason": "ACCS selector UUID known; no FORM_SCHEMAS/FORM_FIELD_MAP user-facing support.",
    },
    "HALO_PROCEDURAL_SEDATION": {
        "label": "HALO Procedural Sedation",
        "profiles": ["ACCS"],
        "status": "unsupported-pending-schema",
        "reason": "Kaizen-visible HALO form without complete user-facing filing plumbing.",
    },
    "IAC": {
        "label": "Initial Assessment of Competence",
        "profiles": ["ACCS"],
        "status": "unsupported-pending-schema",
        "reason": "ACCS selector UUID known; no FORM_SCHEMAS/FORM_FIELD_MAP user-facing support.",
    },
    "EDUCATIONAL_AGREEMENT": {
        "label": "Educational Agreement",
        "profiles": ["ACCS", "INTERMEDIATE", "HIGHER"],
        "status": "unsupported-pending-schema",
        "reason": "Kaizen-visible meeting/agreement variant without complete filing plumbing.",
    },
    "ADD_POST": {
        "label": "Add a Post",
        "profiles": ["ACCS", "INTERMEDIATE", "HIGHER"],
        "status": "supported-hidden-utility",
        "reason": "Administrative route-recognised form; not portfolio evidence.",
    },
    "ADD_SUPERVISOR": {
        "label": "Add a Supervisor",
        "profiles": ["ACCS", "INTERMEDIATE", "HIGHER"],
        "status": "supported-hidden-utility",
        "reason": "Administrative route-recognised form; not portfolio evidence.",
    },
    "FILE_UPLOAD": {
        "label": "File Upload",
        "profiles": ["ACCS", "INTERMEDIATE", "HIGHER"],
        "status": "supported-hidden-utility",
        "reason": "Administrative/document upload route; not a WPBA picker entry.",
    },
    "OOP": {
        "label": "Out of Programme",
        "profiles": ["ACCS", "INTERMEDIATE", "HIGHER"],
        "status": "supported-hidden-utility",
        "reason": "Administrative route-recognised form; not portfolio evidence.",
    },
    "HIGHER_PROG": {
        "label": "Higher Progression",
        "profiles": ["HIGHER"],
        "status": "supported-hidden-utility",
        "reason": "Progression/admin route-recognised form; not a draft WPBA button.",
    },
    "ABSENCE": {
        "label": "Absence",
        "profiles": ["ACCS", "INTERMEDIATE", "HIGHER"],
        "status": "supported-hidden-utility",
        "reason": "Administrative route-recognised form; not portfolio evidence.",
    },
    "CCT": {
        "label": "CCT Application",
        "profiles": ["HIGHER"],
        "status": "unsupported-out-of-scope",
        "reason": "Credential/admin application workflow, not a draft WPBA filing target.",
    },
}

TRAINING_LEVEL_FORMS["ACCS"] = list(TRAINING_LEVEL_FORMS["ST3"])
TRAINING_LEVEL_FORMS["ACCS"].extend(["DOPS_ACCS", "PROCEDURAL_LOG_ACCS"])
TRAINING_LEVEL_FORMS["INTERMEDIATE"] = list(TRAINING_LEVEL_FORMS["ST3"])
TRAINING_LEVEL_FORMS["HIGHER"] = TRAINING_LEVEL_FORMS["ST6"]

# Kaizen portfolio profile groups. Legacy ST3/ST4/ST5/ST6 values are still
# accepted for old profiles, but they should not be treated as exact current
# training years when auto-detected from Kaizen.
TRAINING_LEVEL_LABELS = {
    "ACCS": "ACCS Profile",
    "INTERMEDIATE": "Intermediate Profile",
    "HIGHER": "HST Profile",
    "SAS": "Non-Training Profile",
    "ST3": "Intermediate Profile",
    "ST4": "HST Profile",
    "ST5": "HST Profile",
    "ST6": "HST Profile",
}


def _training_level_label(level: str | None) -> str:
    return TRAINING_LEVEL_LABELS.get(level or "", "Unknown")


# Raw Kaizen role string → granular settings label. Only the non-training
# variants override the bucket-derived label today; trainee roles fall back
# to ``_training_level_label`` for the saved bucket.
_KAIZEN_ROLE_GRANULAR_LABELS = {
    "non_training_higher": "Non-Training Profile (Higher level)",
    "non_training_unknown": "Non-Training Profile (level unknown)",
}


def _portfolio_settings_label(
    training_level: str | None, kaizen_role: str | None
) -> str:
    """Return the user-facing portfolio label shown in /settings.

    For non-training shapes the raw ``kaizen_role`` carries the verified
    stage signal (``non_training_higher`` when Kaizen labels the surface
    Non-Trainee Higher; ``non_training_unknown`` otherwise). For every
    other shape we fall back to the bucket label so ACCS / Intermediate
    / HST trainees see the same string they've always seen.
    """
    if kaizen_role and kaizen_role in _KAIZEN_ROLE_GRANULAR_LABELS:
        return _KAIZEN_ROLE_GRANULAR_LABELS[kaizen_role]
    return _training_level_label(training_level)


# Setup/login path: detected Kaizen role → local portfolio-profile bucket.
# ACCS and Intermediate are separate Kaizen portfolio types and stay
# separate here. ``accs_intermediate`` is the dual-access storage alias
# (one trainee with access to both ACCS and Intermediate); it collapses
# to the ``INTERMEDIATE`` bucket today and is **not** a standalone Kaizen
# portfolio type. ``assessor`` has no personal portfolio and falls back
# to ``HIGHER`` for UX continuity — the supervisor workflow keys off the
# raw role, not this bucket.
_DETECTED_ROLE_TO_TRAINING_LEVEL = {
    "hst": "HIGHER",
    "accs": "ACCS",
    "intermediate": "INTERMEDIATE",
    "accs_intermediate": "INTERMEDIATE",
    "sas": "SAS",
    # Non-training Higher and Non-training Unknown share the purpose-built
    # non-trainee form catalogue under the ``SAS`` bucket. Splitting them
    # at the detection surface (not at the catalogue) preserves the user-
    # visible Non-training Higher label while avoiding any silent mapping
    # into HST / ACCS / Intermediate when the stage is unknown.
    "non_training_higher": "SAS",
    "non_training_unknown": "SAS",
    "assessor": "HIGHER",
}


def detected_role_to_training_level(detected_role: str | None) -> str | None:
    """Map a detected Kaizen role string to the local ``training_level`` bucket.

    Returns ``None`` for unknown/empty roles so the setup flow falls through
    to the manual portfolio-profile picker instead of guessing a bucket.
    Pure helper — raw-role storage (``profile_store.store_kaizen_role``) and
    the bucket map are deliberately decoupled.
    """
    if not detected_role:
        return None
    return _DETECTED_ROLE_TO_TRAINING_LEVEL.get(detected_role)


def _stage_value_from_training_level(level: str | None, form_type: str) -> str:
    """Return the Kaizen stage value implied by the saved portfolio profile.

    This is a best-effort form default from the profile bucket. It is not a
    substitute for a future explicit user-selected grade/year field.
    """
    if not level:
        return ""
    normalised = level.upper()
    schema_fields = FORM_SCHEMAS.get(schema_form_type(form_type), {}).get("fields", [])
    stage_field = next((field for field in schema_fields if field.get("key") == "stage_of_training"), {})
    options = set(stage_field.get("options") or [])

    # Most WPBAs use grouped stage values; QIAT-style forms use individual
    # years. Keep the value aligned with the actual schema for the chosen form.
    if any(option.startswith("Higher/") for option in options):
        if normalised in {"HIGHER", "ST4", "ST5", "ST6"}:
            return "Higher/ST4-ST6"
        if normalised in {"INTERMEDIATE", "ST3"}:
            return "Intermediate/ST3"
        if normalised == "ACCS" or normalised in {"ST1", "ST2", "CT1", "CT2"}:
            return "ACCS ST1-ST2/CT1-CT2"
        if normalised == "PEM":
            return "PEM Sub-specialty"

    if normalised in options:
        return normalised
    if normalised == "INTERMEDIATE":
        return "ST3/CT3" if "ST3/CT3" in options else ""
    if normalised == "ACCS":
        return "ST1/CT1" if "ST1/CT1" in options else ""
    return ""


def _apply_profile_training_stage(draft, user_id: int, form_type: str) -> None:
    schema_fields = FORM_SCHEMAS.get(schema_form_type(form_type), {}).get("fields", [])
    if not any(field.get("key") == "stage_of_training" for field in schema_fields):
        return
    stage_value = _stage_value_from_training_level(get_training_level(user_id), form_type)
    if not stage_value:
        return
    if isinstance(draft, FormDraft):
        if not draft.fields.get("stage_of_training"):
            draft.fields["stage_of_training"] = stage_value
    elif hasattr(draft, "stage_of_training") and not getattr(draft, "stage_of_training", None):
        try:
            setattr(draft, "stage_of_training", stage_value)
        except Exception:
            pass


def _apply_default_dates(draft, form_type: str) -> None:
    """Default missing required date fields to today.

    The preview is generated before filing, so without this the user sees
    `_— needs your detail_` for the date even though filing would have
    defaulted it anyway via `apply_common_header_defaults`. Mirror that
    behaviour at draft time so what the user previews matches what gets filed.
    """
    from datetime import date as _date

    schema_fields = FORM_SCHEMAS.get(schema_form_type(form_type), {}).get("fields", [])
    if not schema_fields:
        return
    today_iso = _date.today().isoformat()
    for field in schema_fields:
        if field.get("type") != "date" or not field.get("required"):
            continue
        key = field["key"]
        if isinstance(draft, FormDraft):
            if _is_missing_field_value(draft.fields.get(key)):
                draft.fields[key] = today_iso
        elif hasattr(draft, key) and _is_missing_field_value(getattr(draft, key, None)):
            try:
                setattr(draft, key, today_iso)
            except Exception:
                pass


def _default_allowed_forms_for_unknown_training() -> list[str]:
    seen = set()
    forms = []
    for group_forms in TRAINING_LEVEL_FORMS.values():
        for form in group_forms:
            if form not in seen:
                seen.add(form)
                forms.append(form)
    return forms


def _allowed_forms_for_training_level(training_level: str | None) -> list[str]:
    """Return the allowed-form catalogue for a saved ``training_level``.

    Known portfolio buckets use explicit catalogues. SAS / CESR has a
    purpose-built non-trainee catalogue rather than inheriting trainee SLEs
    from HST/ST5. Unknown levels still fall through to the union of every
    explicit level's catalogue (``_default_allowed_forms_for_unknown_training``),
    not to the ST5/HST superset.
    """
    if not training_level:
        return _default_allowed_forms_for_unknown_training()
    forms = TRAINING_LEVEL_FORMS.get(training_level)
    if forms is None:
        return _default_allowed_forms_for_unknown_training()
    return list(forms)


def _filter_recommendations_for_allowed_forms(
    recommendations,
    allowed_forms,
    case_text: str = "",
    *,
    excluded_form: str = "",
):
    """Filter recommendations and keep QI/audit work from falling into Teaching.

    Every supported portfolio shape exposes QIAT, so when the extractor has
    surfaced a QI/audit project but only returned Teaching, promote QIAT to
    the top of the list.

    When the LLM's picks are all blocked by the profile catalogue (e.g. the
    user already filed the recommended form and excluded it, leaving nothing
    allowed), fall back to the closest profile-allowed equivalents —
    REFLECT_LOG for procedural blockers, CBD for bedside-observation /
    acute-take blockers. Both are in every catalogue today, so this turns the
    previous "Nothing left to recommend" dead-end into a sensible default
    rather than a guess.
    """
    excluded = _normalise_form_type(excluded_form)
    allowed = set(allowed_forms)
    filtered = [
        r for r in recommendations
        if r.form_type in allowed and _normalise_form_type(r.form_type) != excluded
    ]
    qiat_available = "QIAT" in allowed and excluded != "QIAT"
    first_available = filtered[0].form_type if filtered else ""
    if (
        qiat_available
        and first_available == "TEACH"
        and _has_qi_project_signal(case_text)
        and not any(r.form_type == "QIAT" for r in filtered)
    ):
        return [
            FormTypeRecommendation(
                form_type="QIAT",
                rationale=(
                    "This is a quality improvement project with audit/re-audit "
                    "signals; teaching is only an intervention detail."
                ),
                uuid=FORM_UUIDS.get("QIAT"),
            ),
            *filtered,
        ]
    if recommendations and not filtered:
        return _profile_blocked_fallback_recommendations(
            recommendations, allowed, excluded
        )
    return filtered


# Procedural SLEs that map to a reflective fallback when every recommended
# form is blocked (procedural skills carry their learning into a reflective log).
_PROCEDURAL_BLOCKED_FORMS = frozenset({"DOPS", "PROC_LOG", "US_CASE"})

# Observation / acute-take SLEs that map to a case-based-discussion fallback
# when every recommended form is blocked (bedside encounters become a CBD write-up).
_OBSERVATION_BLOCKED_FORMS = frozenset({"MINI_CEX", "ACAT", "ESLE", "ESLE_ASSESS"})


def _profile_blocked_fallback_recommendations(original_recs, allowed, excluded):
    """Return profile-allowed substitutions when every LLM pick is blocked.

    The recommender is profile-agnostic — it returns the best-fit forms for
    the *case*, not the user's saved portfolio. If a profile genuinely lacks
    every recommended form, or the user has excluded the only fit, the list
    can come back all-blocked. Without a fallback the user sees a dead
    "Nothing left to recommend" state; with it they get REFLECT_LOG / CBD —
    both present in every supported profile catalogue — with a rationale that
    names what was blocked.
    """
    blocked_types = [r.form_type for r in original_recs]
    blocked_pretty = ", ".join(_form_display_name(ft) for ft in blocked_types)

    procedural_first = any(ft in _PROCEDURAL_BLOCKED_FORMS for ft in blocked_types)
    observation_first = (
        any(ft in _OBSERVATION_BLOCKED_FORMS for ft in blocked_types)
        and not procedural_first
    )

    reflect_candidate = (
        "REFLECT_LOG",
        f"Your saved portfolio profile doesn't accept {blocked_pretty}; "
        "a reflective log captures the learning from this event.",
    )
    cbd_candidate = (
        "CBD",
        f"Your saved portfolio profile doesn't accept {blocked_pretty}; "
        "a case-based discussion is the closest fit available on this profile.",
    )
    # Always offer both fallbacks so an excluded primary (e.g. user
    # already filed REFLECT_LOG and is now reusing the case) still
    # leaves CBD on the table. Order by case type so the better fit
    # leads.
    candidates = (
        [cbd_candidate, reflect_candidate]
        if observation_first
        else [reflect_candidate, cbd_candidate]
    )

    fallbacks = []
    seen: set[str] = set()
    for form_type, rationale in candidates:
        if form_type in seen:
            continue
        if form_type not in allowed:
            continue
        if _normalise_form_type(form_type) == excluded:
            continue
        seen.add(form_type)
        fallbacks.append(FormTypeRecommendation(
            form_type=form_type,
            rationale=rationale,
            uuid=FORM_UUIDS.get(form_type),
        ))
    return fallbacks

# Category groupings for "See all forms" navigation
FORM_CATEGORIES = {
    "🩺 Clinical": ["CBD", "DOPS", "DOPS_ACCS", "MINI_CEX", "ACAT", "LAT", "LAT_2021", "ACAF", "STAT", "MSF", "QIAT", "QIAT_2021", "JCF", "JCF_2021", "ESLE_ASSESS", "AUDIT", "AUDIT_2021"],
    "📝 Reflective": ["REFLECT_LOG", "REFLECT_LOG_2021", "COMPLAINT", "SERIOUS_INC", "CRIT_INCIDENT", "PDP", "APPRAISAL"],
    "👨‍🏫 Teaching": ["TEACH", "TEACH_OBS", "TEACH_CONFID", "SDL", "EDU_ACT", "EDU_MEETING", "EDU_MEETING_SUPP", "FORMAL_COURSE"],
    "🔬 Procedural": ["PROC_LOG", "PROCEDURAL_LOG_ACCS", "US_CASE"],
    "🔍 Quality": ["RESEARCH", "CLIN_GOV", "COST_IMPROVE", "EQUIP_SERVICE", "BUSINESS_CASE"],
    "🏛️ Management": ["MGMT_ROTA", "MGMT_RISK", "MGMT_RECRUIT", "MGMT_PROJECT", "MGMT_RISK_PROC", "MGMT_TRAINING_EVT", "MGMT_GUIDELINE", "MGMT_INFO", "MGMT_INDUCTION", "MGMT_EXPERIENCE", "MGMT_REPORT", "MGMT_COMPLAINT"],
}

# Slug mapping for callback data (Telegram limits callback_data to 64 bytes)
_CAT_SLUGS = {
    "🩺 Clinical": "CLINICAL",
    "📝 Reflective": "REFLECTIVE",
    "👨‍🏫 Teaching": "TEACHING",
    "🔬 Procedural": "PROCEDURAL",
    "🔍 Quality": "QUALITY",
    "🏛️ Management": "MANAGEMENT",
}
_SLUG_TO_CAT = {v: k for k, v in _CAT_SLUGS.items()}

WELCOME_MSG = render_message("welcome_disconnected")

WELCOME_MSG_CONNECTED = render_message("welcome_connected")

_WHAT_IS_THIS_FORM_COUNT = max(len(v) for v in TRAINING_LEVEL_FORMS.values())

WHAT_IS_THIS_MSG = render_message("what_is_this")

FILE_CASE_PROMPT = render_message("file_case_prompt")

FLOW_STATE_LABELS = {
    "captured": "Captured",
    "drafted": "Drafted",
    "needs_you": "Needs you",
    "filed_as_draft": "Filed as draft",
    "blocked": "Failed / blocked",
}

CAPTURED_ACK = render_message("captured_ack")

def _format_proof_report(
    status: str,
    form_name: str,
    input_source: str | None,
    filled: list,
    skipped: list,
    error: str | None = None,
) -> str:
    """Trust-layer summary shown after filing attempts."""
    if status == "success":
        state = FLOW_STATE_LABELS["filed_as_draft"]
        needs_review = "Review in Kaizen before you submit or assign an assessor."
    elif status == "partial" and not error:
        state = FLOW_STATE_LABELS["filed_as_draft"]
        needs_review = "Complete the blank fields in Kaizen before submission."
    elif status == "partial":
        state = FLOW_STATE_LABELS["blocked"]
        needs_review = "Check whether the draft saved, then retry if needed."
    else:
        state = FLOW_STATE_LABELS["blocked"]
        needs_review = "No final submission was made. Retry or file manually."

    source = input_source or "case input"
    filled_text = ", ".join(str(f).replace("_", " ") for f in filled[:6]) if filled else "none confirmed"
    if len(filled) > 6:
        filled_text += f", +{len(filled) - 6} more"
    skipped_text = ", ".join(str(f).replace("_", " ") for f in skipped[:4]) if skipped else "none reported"
    if len(skipped) > 4:
        skipped_text += f", +{len(skipped) - 4} more"

    lines = [
        "",
        "Portfolio Guru proof report",
        f"Status: {state}",
        f"WPBA type: {form_name}",
        f"Source: {source}",
        f"Fields completed: {filled_text}",
        f"Needs your review: {needs_review}",
        "Not done: no supervisor request sent, no final submission made",
    ]
    if skipped:
        lines.insert(6, f"Left blank / skipped: {skipped_text}")
    if error:
        lines.append(f"Blocker: {error}")
    return "\n".join(lines)


def _format_failed_filing_summary(
    error: str | None,
    skipped: list,
) -> str:
    """Compact trust-layer summary for failed filing attempts."""
    lines = [
        "What happened",
        "Saved in Kaizen: not confirmed",
        "Final submission: not sent",
        "Supervisor request: not sent",
    ]
    if skipped:
        skipped_text = ", ".join(sanitize_internal_form_codes(str(f).replace("_", " ")) for f in skipped[:4])
        if len(skipped) > 4:
            skipped_text += f", +{len(skipped) - 4} more"
        lines.append(f"Needs manual check: {skipped_text}")
    if error:
        lines.append(f"Blocker: {sanitize_internal_form_codes(error)}")
    return "\n".join(lines)



async def _safe_kaizen_sync_status(user_id: int) -> KaizenSyncStatus | None:
    """Fetch the Kaizen sync snapshot, swallowing any storage error.

    /settings must keep rendering even if the index DB is unreachable. The
    row is omitted in that case rather than failing the whole dashboard.
    """
    try:
        return await get_kaizen_sync_status(user_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"Kaizen sync status unavailable: {exc}")
        return None


_KAIZEN_SYNC_RUNNING_STALE_AFTER = timedelta(minutes=30)
_KAIZEN_SYNC_STATUS_LABELS = {
    "ok": "synced",
    "partial": "partly synced",
    "drift": "needs mapping check",
    "auth_required": "needs reconnecting",
    "failed": "last sync failed",
}


def _parse_sync_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None


def _format_kaizen_sync_row(status: KaizenSyncStatus | None) -> str | None:
    """Read-only one-liner describing Kaizen evidence sync state.

    The manual sync workflow is exposed separately as a guarded secondary
    utility. This row stays a compact status summary so /settings remains
    scannable.
    """
    if status is None:
        return None
    if status.last_run is None:
        return "🔄 Kaizen evidence: not synced yet"
    last_run = status.last_run
    if last_run.status == "running":
        started_at = (last_run.started_at or "").strip()
        pretty_started = _format_user_local_timestamp(started_at)
        started_dt = _parse_sync_timestamp(started_at)
        if started_dt and datetime.now(UTC) - started_dt > _KAIZEN_SYNC_RUNNING_STALE_AFTER:
            return (
                f"🔄 Kaizen evidence: sync timed out after starting {pretty_started}. "
                f"Items indexed: {status.items_indexed}"
            )
        return (
            f"🔄 Kaizen evidence: syncing now, started {pretty_started}. "
            f"Items indexed: {status.items_indexed}"
        )
    when = (last_run.finished_at or last_run.started_at or "").strip()
    pretty_when = _format_user_local_timestamp(when)
    status_label = _KAIZEN_SYNC_STATUS_LABELS.get(last_run.status, last_run.status)
    return (
        f"🔄 Kaizen evidence: {status_label} {pretty_when}. "
        f"Items indexed: {status.items_indexed}"
    )


def _format_user_local_timestamp(value: str) -> str:
    if not value:
        return "unknown"
    try:
        from datetime import UTC
        from zoneinfo import ZoneInfo

        parsed = __import__("datetime").datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        local = parsed.astimezone(ZoneInfo("Europe/London"))
        return local.strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return value.replace("T", " ").split(".")[0]


def _refresh_portfolio_confirm_text() -> str:
    return (
        "🔄 Sync Kaizen evidence?\n\n"
        "This will read your Kaizen timeline and saved-draft activity into "
        "Portfolio Guru so /health can use real portfolio evidence.\n\n"
        "Safety boundary:\n"
        "• no saving or submitting\n"
        "• no signing or supervisor requests\n"
        "• no deleting or editing Kaizen\n"
        "• no new drafts created"
    )


def _refresh_portfolio_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sync now", callback_data="ACTION|confirm_refresh_portfolio")],
        [InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")],
    ])


def _health_refresh_confirm_text() -> str:
    return (
        "📊 Portfolio Health needs fresh Kaizen data\n\n"
        "I can refresh your portfolio read-only, then show the health result.\n\n"
        "Safety boundary:\n"
        "• no saving or submitting\n"
        "• no signing or supervisor requests\n"
        "• no deleting or editing Kaizen\n"
        "• no new drafts created"
    )


def _health_refresh_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Refresh and show health", callback_data="ACTION|confirm_refresh_for_health")],
        [InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")],
    ])


def _health_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ File missing evidence", callback_data="ACTION|file")],
        [InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")],
    ])


def _refresh_portfolio_result_keyboard(status: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status in {"ok", "partial"}:
        rows.append([InlineKeyboardButton("📊 View portfolio health", callback_data="ACTION|health")])
    elif status == "auth_required":
        rows.append([InlineKeyboardButton("🔗 Reconnect Kaizen", callback_data="ACTION|setup")])
    else:
        rows.append([InlineKeyboardButton("🔄 Try again", callback_data="ACTION|refresh_portfolio")])
    rows.append([InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")])
    return InlineKeyboardMarkup(rows)


def _format_refresh_portfolio_result(result, status: KaizenSyncStatus | None = None) -> str:
    run_status = getattr(result, "status", "failed")
    total_items = status.items_indexed if status is not None else None
    rows_written = getattr(result, "rows_written", 0)
    rows_seen = getattr(result, "rows_seen", 0)
    rows_drifted = getattr(result, "rows_drifted", 0)

    if run_status == "ok":
        lines = [
            "✅ Kaizen evidence synced",
            "",
            f"Read from Kaizen: {rows_seen} items",
            f"Added or updated: {rows_written} items",
        ]
        if total_items is not None:
            lines.append(f"Portfolio Guru now has: {total_items} indexed items")
        lines.extend(["", "Next: open Portfolio Health to see the updated view."])
        return "\n".join(lines)

    if run_status == "partial":
        lines = [
            "⚠️ Kaizen evidence partly synced",
            "",
            f"Read from Kaizen: {rows_seen} items",
            f"Added or updated: {rows_written} items",
        ]
        if rows_drifted:
            lines.append(f"Needs a mapping check: {rows_drifted} items")
        if total_items is not None:
            lines.append(f"Portfolio Guru now has: {total_items} indexed items")
        lines.extend(["", "You can still view Portfolio Health, but I may need to map one changed Kaizen screen."])
        return "\n".join(lines)

    if run_status == "auth_required":
        return (
            "🔗 Kaizen needs reconnecting\n\n"
            "I could not refresh your portfolio because the Kaizen session needs a fresh login.\n\n"
            "Next: reconnect Kaizen, then come back to Sync Kaizen evidence."
        )

    if run_status == "drift":
        return (
            "⚠️ Sync blocked by a Kaizen screen change\n\n"
            "I found a Kaizen page that no longer matches the safe read-only map, so I stopped rather than guessing.\n\n"
            "Next: I need to update the map before this screen can be indexed safely."
        )

    return (
        "⚠️ Sync did not complete\n\n"
        "Portfolio Guru could not finish the read-only Kaizen refresh. Nothing was changed in Kaizen.\n\n"
        "You can try again from settings."
    )


def _settings_view_components(
    user_id: int,
    *,
    tier: str | None = None,
    used: int | None = None,
    connected: bool | None = None,
    is_beta: bool = False,
    kaizen_sync: KaizenSyncStatus | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Render the settings page text + keyboard.

    This is also the merged "status" view. When tier/used/connected are
    supplied, a plan + usage + connection block is rendered at the top.
    When ``kaizen_sync`` is supplied, a Kaizen evidence status row is appended
    to that block. Connected users get Portfolio Health as the primary action
    and guarded manual sync as a secondary utility.
    """
    curriculum = get_curriculum(user_id) or "2025"
    curriculum_label = "2021 Curriculum" if curriculum == "2021" else "2025 Update"
    training_level = _portfolio_settings_label(
        get_training_level(user_id), get_kaizen_role(user_id)
    )
    pathway_label = _pathway_label(_get_or_default_health_profile(user_id).pathway)
    voice_profile = get_voice_profile(user_id)
    voice_status = "Active" if voice_profile else "Not set"
    voice_cta = f"✍️ Writing style: {voice_status}"
    voice_hint = "Helps drafts sound like you." if not voice_profile else "Drafts are already styled to your voice."

    plan_lines = []
    if connected is False:
        plan_lines.append("🔗 Kaizen: not connected")
    if is_beta:
        plan_lines.append("⭐ Plan: Beta (unlimited)")
        if used is not None:
            plan_lines.append(f"📋 Cases filed: {used} this month")
    elif tier is not None:
        tier_pretty = {"free": "Free", "pro": "Pro", "pro_plus": "Unlimited"}.get(tier, tier.title())
        plan_lines.append(f"⭐ Plan: {tier_pretty}")
        if used is not None:
            limit = TIER_LIMITS.get(tier, 5)
            if limit == -1:
                plan_lines.append(f"📋 Cases filed: {used} this month")
            else:
                plan_lines.append(f"📋 Usage: {used}/{limit} cases this month")
    kaizen_row = _format_kaizen_sync_row(kaizen_sync)
    if kaizen_row:
        plan_lines.append(kaizen_row)
    plan_block = ("\n".join(plan_lines) + "\n\n") if plan_lines else ""

    setup_button_label = "🔗 Connect Kaizen" if connected is False else "🔗 Update Kaizen login"

    buttons: list[list[InlineKeyboardButton]] = []
    if connected is True:
        buttons.append([InlineKeyboardButton("📊 Portfolio health", callback_data="ACTION|health")])
    buttons.extend([
        [InlineKeyboardButton(voice_cta, callback_data="ACTION|voice")],
        [InlineKeyboardButton(f"🎓 Portfolio: {training_level}", callback_data="ACTION|change_level")],
        [InlineKeyboardButton(f"📊 Pathway: {pathway_label}", callback_data="ACTION|change_pathway")],
        [InlineKeyboardButton(f"📚 Curriculum: {curriculum_label}", callback_data="ACTION|change_curriculum")],
    ])
    buttons.extend([
        [InlineKeyboardButton(setup_button_label, callback_data="ACTION|setup")],
        [InlineKeyboardButton("🔙 Back", callback_data="ACTION|back_to_menu"),
         InlineKeyboardButton("🗑️ Delete data", callback_data="ACTION|delete")],
    ])
    text = (
        f"⚙️ Your settings\n\n"
        f"{plan_block}"
        f"✍️ Writing style: {voice_status}\n"
        f"   {voice_hint}\n\n"
        f"🎓 Portfolio: {training_level}\n"
        f"📊 Pathway: {pathway_label}\n"
        f"📚 Curriculum: {curriculum_label}\n\n"
        f"Pick what you want to change."
    )
    return text, InlineKeyboardMarkup(buttons)


def _build_welcome_keyboard(connected: bool = False):
    """Return the welcome inline keyboard, or None when no button is needed.

    Connected users: no buttons. The welcome text says "send your case" — that
    IS the action. Settings/Health/Help live in the Telegram Menu (☰).
    Disconnected users: a single Connect Kaizen button — the one required
    action before anything else is possible.
    """
    if connected:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup")],
    ])


FORM_EMOJIS = {
    "CBD": "🩺", "DOPS": "🔪", "DOPS_ACCS": "🔪", "MINI_CEX": "🏥", "ACAT": "📋",
    "MSF": "👥", "QIAT": "🎓", "LAT": "📖", "JCF": "💼",
    "ACAF": "✅", "STAT": "📊",
    "TEACH": "👨‍🏫", "PROC_LOG": "🔬", "PROCEDURAL_LOG_ACCS": "🔬", "SDL": "📖", "US_CASE": "🔊",
    "COMPLAINT": "📝", "SERIOUS_INC": "🚨",
    "EDU_ACT": "🎓", "FORMAL_COURSE": "📋",
    # Newly visible forms
    "REFLECT_LOG": "💭", "TEACH_OBS": "👁️", "ESLE_ASSESS": "⚠️",
    "TEACH_CONFID": "🔒", "APPRAISAL": "📝", "CLIN_GOV": "🏛️",
    "CRIT_INCIDENT": "🚨", "AUDIT": "📊", "RESEARCH": "🔬",
    "EDU_MEETING": "📅", "EDU_MEETING_SUPP": "📅", "PDP": "🎯",
    "BUSINESS_CASE": "💼", "COST_IMPROVE": "💰", "EQUIP_SERVICE": "🔧",
    "MGMT_RECRUIT": "👤", "MGMT_RISK_PROC": "⚠️", "MGMT_TRAINING_EVT": "🎓",
    "MGMT_GUIDELINE": "📋", "MGMT_INFO": "ℹ️", "MGMT_INDUCTION": "🤝",
    "MGMT_EXPERIENCE": "🧭", "MGMT_REPORT": "📄", "MGMT_COMPLAINT": "📝",
}

FORM_BUTTON_LABELS = {
    # Core WPBAs — official RCEM codes
    "CBD": "CBD",
    "DOPS": "DOPS",
    "DOPS_ACCS": "DOPS ACCS",
    "MINI_CEX": "Mini-CEX",
    "ACAT": "ACAT",
    "ACAF": "ACAF",
    "LAT": "LAT",
    "STAT": "STAT",
    "MSF": "MSF",
    "QIAT": "QIAT",
    # Teaching & Education
    "JCF": "Journal Club",
    "TEACH": "Teaching Session",
    "EDU_ACT": "Educational Activity",
    "FORMAL_COURSE": "Formal Course",
    "SDL": "Self-directed Learning",

    # Procedures & Clinical
    "DOPS_PROC": "DOPS Procedure",
    "PROC_LOG": "Procedural Log",
    "PROCEDURAL_LOG_ACCS": "Procedural Log ACCS",
    "US_CASE": "Ultrasound Case",
    # Reflection & Incidents
    "SERIOUS_INC": "Serious Incident",
    "COMPLAINT": "Complaint",
    # Management (new)
    "MGMT_ROTA": "Rota",
    "MGMT_RISK": "Risk",
    "MGMT_PROJECT": "QI Project",
    # Other
    "BUSINESS_CASE": "Business Case",
    "RESEARCH": "Research",
    "REFLECTIVE": "Reflective Practice",
    "PDP": "PDP",
    "RPL": "Reflective Practice Log",
    # Newly visible forms
    "REFLECT_LOG": "Reflective Log",
    "TEACH_OBS": "Teaching Observation",
    "ESLE_ASSESS": "ESLE",
    "TEACH_CONFID": "Confidentiality",
    "APPRAISAL": "Appraisal",
    "CLIN_GOV": "Governance",
    "CRIT_INCIDENT": "Critical Incident",
    "AUDIT": "Audit",
    "EDU_MEETING": "ES Meeting",
    "EDU_MEETING_SUPP": "ES Meeting (Supp)",
    "COST_IMPROVE": "Cost Improvement",
    "EQUIP_SERVICE": "Equipment/Service",
    "MGMT_RECRUIT": "Recruitment",
    "MGMT_RISK_PROC": "Risk Process",
    "MGMT_TRAINING_EVT": "Training Event",
    "MGMT_GUIDELINE": "Guideline",
    "MGMT_INFO": "Information",
    "MGMT_INDUCTION": "Induction",
    "MGMT_EXPERIENCE": "Experience",
    "MGMT_REPORT": "Report",
    "MGMT_COMPLAINT": "Complaint",
}

FIELD_EMOJIS = {
    "date_of_encounter":      "📅",
    "date":                   "📅",
    "clinical_setting":       "🏥",
    "setting":                "🏥",
    "patient_presentation":   "🩺",
    "presentation":           "🩺",
    "procedure":              "🔪",
    "procedure_performed":    "🔪",
    "clinical_reasoning":     "🗒️",
    "case_discussion":        "🗒️",
    "reflection":             "💭",
    "supervisor_name":        "👤",
    "assessor":               "👤",
    "level_of_supervision":   "🎚️",
    "stage_of_training":      "📈",
    "trainee_role":           "👨‍⚕️",
    "leadership_context":     "🧭",
    "journal":                "📰",
    "article_title":          "📰",
    "qi_project":             "📊",
    "reflection_title":       "📝",
    "learning_activity_type": "📋",
    "resource_details":       "📎",
    "teaching_topic":         "📖",
    "teaching_setting":       "🏫",
    "teaching_methods":       "🧑‍🏫",
    "audience":               "👥",
    "learning_objectives":    "🎯",
    "feedback_received":      "💬",
    "course_name":            "📋",
    "course_provider":        "🏛️",
    "course_duration":        "⏱️",
    "learning_points":        "💡",
    "complaint_summary":      "📝",
    "incident_summary":       "📝",
    "actions_taken":          "✅",
    "lessons_learned":        "💡",
    "esle_description":       "📝",
    "us_findings":            "🔊",
    "us_indication":          "🔊",
    "procedure_type":         "🔬",
    "complications":          "⚠️",
    "outcome":                "📊",
    "description":            "📝",
}

def _form_display_name(form_type: str) -> str:
    """Human-readable form name from schema, falling back to code if not found."""
    return public_form_name(form_type)


_DEFAULT_CURRICULUM_SUFFIX_RE = re.compile(
    r"\s*(?:[-–—]\s*)?(?:\((?:Update\s*)?2025(?:\s*Update)?\)|\b(?:Update\s*)?2025\s*Update\b|\b2025\s*Update\b)\s*",
    re.IGNORECASE,
)


def _strip_default_curriculum_suffix(text: str) -> str:
    """Remove default-2025 curriculum suffixes from user-facing form labels."""
    clean = str(text or "").strip()
    clean = _DEFAULT_CURRICULUM_SUFFIX_RE.sub(" ", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip(" -–—")
    return clean or str(text or "").strip()


def _recommendation_form_display_name(form_type: str, curriculum: str = "2025") -> str:
    name = _form_display_name(_normalise_form_type(form_type))
    if curriculum == "2025":
        return _strip_default_curriculum_suffix(name)
    return name


def _track_funnel_event(context, event: str, **metadata) -> None:
    """Log PHI-free UX funnel events for friction analysis."""
    safe = {
        key: value
        for key, value in metadata.items()
        if key in {"source", "form_type", "state", "count", "has_draft", "has_missing"}
    }
    try:
        context.user_data["last_funnel_event"] = event
    except Exception:
        pass
    logger.info("Portfolio Guru funnel event=%s metadata=%s", event, safe)


def _log_filing_attempt(
    *,
    user_id: int,
    username: str | None,
    form_type: str,
    status: str,
    error: str | None = None,
    filled: list | None = None,
    skipped: list | None = None,
    method: str | None = None,
    verified: bool | None = None,
) -> None:
    """Append a per-user filing-attempt record to filing-log.ndjson.

    Keyed by Telegram user_id so we can answer "what errors did user X hit
    this week" without grepping the main bot log. PHI-free: records form
    type, status, and the error string from the filer (which is a generic
    message, not case content).

    Thin wrapper around :mod:`filing_attempt_log`; the heavy lifting (error
    categorisation, synthetic-user flagging, durable path) lives there so it
    can be tested without booting the bot.
    """
    from filing_attempt_log import log_attempt
    try:
        training_level = get_training_level(user_id)
        portfolio_shape = training_level if training_level == "SAS" else (get_kaizen_role(user_id) or training_level)
    except Exception:
        logger.debug("Could not resolve portfolio shape for filing-attempt log", exc_info=True)
        portfolio_shape = None
    log_attempt(
        user_id=user_id,
        username=username,
        form_type=form_type,
        status=status,
        error=error,
        filled=filled,
        skipped=skipped,
        method=method,
        verified=verified,
        portfolio_shape=portfolio_shape,
    )


_2021_CURRICULUM_FORM_ALIASES = {
    # User-facing assessed ESLE is named ESLE_ASSESS in Portfolio Guru, but the
    # Kaizen 2021 curriculum form is keyed as ESLE_2021.
    "ESLE_ASSESS": "ESLE_2021",
    "DOPS_ACCS": "DOPS_ACCS_2021",
    "PROCEDURAL_LOG_ACCS": "PROCEDURAL_LOG_ACCS_2021",
}


def _default_curriculum_for_training_level(training_level: str | None) -> str:
    """Profile default curriculum. Non-training (SAS / CESR) portfolios only
    expose the 2021 family in Kaizen, so they default to 2021; every trainee
    profile defaults to the 2025 update."""
    return "2021" if training_level == "SAS" else "2025"


def _effective_curriculum(user_id) -> str:
    """Curriculum to use for form visibility and filing for a given user.

    SAS / non-training portfolios are pinned to the 2021 family because Kaizen
    only surfaces 2021 forms for them — so the stored toggle is ignored and we
    always resolve to 2021. Trainee profiles honour the user's stored choice,
    falling back to 2025 when unset.
    """
    training_level = get_training_level(user_id)
    if training_level == "SAS":
        return "2021"
    return get_curriculum(user_id) or "2025"


def _form_type_for_curriculum(form_type: str, curriculum: str = "2025") -> str:
    from extractor import FORM_UUIDS

    if curriculum != "2021" or form_type.endswith("_2021"):
        return form_type
    if form_type in _2021_CURRICULUM_FORM_ALIASES:
        return _2021_CURRICULUM_FORM_ALIASES[form_type]
    variant = f"{form_type}_2021"
    return variant if variant in FORM_UUIDS else form_type


def _filing_form_type_for_user(user_id: int, form_type: str) -> str:
    """Resolve stale draft form codes to the active user's Kaizen curriculum.

    Drafts can outlive a recommendation/catalogue fix. Re-resolving at filing
    time keeps old base codes such as REFLECT_LOG from opening an inaccessible
    2025 UUID for SAS/non-training accounts that only expose 2021 forms.
    """
    return _form_type_for_curriculum(form_type, _effective_curriculum(user_id))


def _filtered_recommendations_for_curriculum(recommendations, curriculum="2025"):
    from extractor import FORM_UUIDS

    filtered = []
    for rec in recommendations:
        ft = rec.form_type
        ft_for_curriculum = _form_type_for_curriculum(ft, curriculum)
        if curriculum == "2021" and ft_for_curriculum != ft:
            from models import FormTypeRecommendation
            filtered.append(FormTypeRecommendation(
                form_type=ft_for_curriculum,
                rationale=rec.rationale,
                uuid=FORM_UUIDS.get(ft_for_curriculum),
            ))
        elif curriculum == "2025" and ft.endswith("_2021"):
            continue
        else:
            filtered.append(rec)
    return filtered


def _build_form_choice_keyboard(recommendations, curriculum="2025"):
    """Build inline keyboard for form type selection — AI suggestions + See all forms escape hatch.
    Filters recommendations by curriculum preference."""
    filtered = _filtered_recommendations_for_curriculum(recommendations, curriculum)

    buttons = []
    for rec in filtered:
        base_ft = rec.form_type.replace("_2021", "") if rec.form_type.endswith("_2021") else rec.form_type
        emoji = FORM_EMOJIS.get(base_ft, "📄")
        label = FORM_BUTTON_LABELS.get(rec.form_type) or FORM_BUTTON_LABELS.get(base_ft) or _recommendation_form_display_name(base_ft, curriculum)[:24]
        if rec.uuid:
            buttons.append(InlineKeyboardButton(f"{emoji} {label}", callback_data=f"FORM|{rec.form_type}"))
        else:
            buttons.append(InlineKeyboardButton(f"{emoji} {label} (soon)", callback_data="FORM|disabled"))

    rows = []
    best = next((rec for rec in filtered if getattr(rec, "uuid", None)), None)
    if best:
        base_ft = best.form_type.replace("_2021", "") if best.form_type.endswith("_2021") else best.form_type
        best_label = FORM_BUTTON_LABELS.get(best.form_type) or FORM_BUTTON_LABELS.get(base_ft) or _recommendation_form_display_name(base_ft, curriculum)
        rows.append([InlineKeyboardButton(f"✅ Use best fit: {best_label}", callback_data="FORM|best")])
        buttons = [button for button in buttons if button.callback_data != f"FORM|{best.form_type}"]

    rows.extend([buttons[i:i+2] for i in range(0, len(buttons), 2)])
    rows.append([
        InlineKeyboardButton("📋 See all forms", callback_data="FORM|show_all"),
        InlineKeyboardButton("❌ Cancel", callback_data="CANCEL|form"),
    ])
    return InlineKeyboardMarkup(rows)


def _filter_forms_by_curriculum(form_types, curriculum):
    """Filter form list based on curriculum preference.
    - 2025: exclude _2021 suffixed forms
    - 2021: swap base forms that have _2021 variants with those variants
    """
    if curriculum == "2021":
        result = []
        for ft in form_types:
            result.append(_form_type_for_curriculum(ft, curriculum))
        return result
    else:
        # 2025 (default) — base forms only
        return [ft for ft in form_types if not ft.endswith("_2021")]


def _get_allowed_forms(user_id):
    """Get the allowed form list for a user (portfolio profile + curriculum filtered)."""
    from extractor import FORM_UUIDS
    training_level = get_training_level(user_id)
    curriculum = _effective_curriculum(user_id)
    allowed = _allowed_forms_for_training_level(training_level)
    allowed = _filter_forms_by_curriculum(allowed, curriculum)
    # Only include forms that have UUIDs
    return [ft for ft in allowed if FORM_UUIDS.get(ft)]


def _build_category_picker_keyboard(user_id):
    """Build the level-1 category picker keyboard, hiding empty categories."""
    allowed = set(_get_allowed_forms(user_id))
    curriculum = _effective_curriculum(user_id)
    rows = []
    row = []
    for cat_name, cat_forms in FORM_CATEGORIES.items():
        # Check if any form in this category is available to this user
        # Need to check both base and curriculum-specific variants.
        has_forms = any(ft in allowed or _form_type_for_curriculum(ft, curriculum) in allowed for ft in cat_forms)
        if not has_forms:
            continue
        slug = _CAT_SLUGS[cat_name]
        row.append(InlineKeyboardButton(cat_name, callback_data=f"FORM|cat_{slug}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="FORM|back")])
    return InlineKeyboardMarkup(rows)


def _build_category_forms_keyboard(user_id, cat_slug):
    """Build the level-2 keyboard showing forms within a category."""
    from extractor import FORM_UUIDS
    cat_name = _SLUG_TO_CAT[cat_slug]
    cat_forms = FORM_CATEGORIES[cat_name]
    allowed = set(_get_allowed_forms(user_id))
    curriculum = _effective_curriculum(user_id)
    buttons = []
    for ft in cat_forms:
        # Check if this form (or its curriculum-specific variant) is in the allowed set
        if ft in allowed:
            actual_ft = ft
        else:
            curriculum_ft = _form_type_for_curriculum(ft, curriculum)
            if curriculum_ft in allowed:
                actual_ft = curriculum_ft
            else:
                continue
        base_ft = actual_ft.replace("_2021", "") if actual_ft.endswith("_2021") else actual_ft
        emoji = FORM_EMOJIS.get(base_ft, "📄")
        label = FORM_BUTTON_LABELS.get(actual_ft) or FORM_BUTTON_LABELS.get(base_ft) or _form_display_name(base_ft)[:24]
        buttons.append(InlineKeyboardButton(f"{emoji} {label}", callback_data=f"FORM|{actual_ft}"))
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅️ Back to categories", callback_data="FORM|show_all")])
    return InlineKeyboardMarkup(rows)


def _build_curriculum_keyboard(callback_prefix: str = "SET_CURRICULUM"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📘 2025 Update", callback_data=f"{callback_prefix}|2025"),
         InlineKeyboardButton("📗 2021 Curriculum", callback_data=f"{callback_prefix}|2021")],
    ])


def _build_template_review_keyboard():
    return InlineKeyboardMarkup([
        [_BTN_CONTINUE_THIN],
        [_BTN_CANCEL],
    ])


def _build_explicit_form_keyboard(form_type: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Draft {_form_display_name(form_type)}", callback_data=f"FORM|{form_type}")],
        [InlineKeyboardButton("📋 See all forms", callback_data="FORM|show_all")],
        [_BTN_CANCEL],
    ])


def _build_approval_keyboard(improved_once: bool = False, can_back_to_missing: bool = False):
    rows = []
    if improved_once:
        # After Quick Improve is used, remove the improve button entirely
        rows.append([
            InlineKeyboardButton("📤 Save as draft", callback_data="APPROVE|draft"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("📤 Save as draft", callback_data="APPROVE|draft"),
            InlineKeyboardButton("✨ Quick improve", callback_data="IMPROVE|reflection"),
        ])
    if can_back_to_missing:
        rows.append(_nav_row("⬅️ Back to missing details", "ACTION|back_to_missing", "❌ Cancel", "CANCEL|draft"))
    else:
        rows.append([InlineKeyboardButton("❌ Cancel", callback_data="CANCEL|draft")])
    return InlineKeyboardMarkup(rows)


def _build_amend_keyboard(improved_once: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("📤 Save updated draft", callback_data="APPROVE|draft")]]
    if not improved_once:
        rows[0].append(InlineKeyboardButton("✨ Quick improve", callback_data="IMPROVE|reflection"))
    rows.append([InlineKeyboardButton("❌ Cancel amend", callback_data="AMEND|cancel")])
    return InlineKeyboardMarkup(rows)


def _build_doc_intent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Read as case info", callback_data="DOCUSE|info"),
            InlineKeyboardButton("📎 Attach only", callback_data="DOCUSE|attach"),
        ],
        [
            InlineKeyboardButton("📎 Read + attach", callback_data="DOCUSE|both"),
            InlineKeyboardButton("❌ Cancel", callback_data="CANCEL|doc_intent"),
        ],
    ])


def _active_draft_keyboard(context) -> InlineKeyboardMarkup:
    if context.user_data.get("amend_mode"):
        return _build_amend_keyboard(improved_once=context.user_data.get("quick_improve_used", False))
    return _build_approval_keyboard(improved_once=context.user_data.get("quick_improve_used", False))


def _build_amend_new_case_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Update this draft", callback_data="AMEND|update_current")],
        [InlineKeyboardButton("📋 Start new case", callback_data="AMEND|start_new")],
        [InlineKeyboardButton("❌ Cancel", callback_data="AMEND|cancel_choice")],
    ])


def _has_retryable_failed_filing_draft(context) -> bool:
    """True when a failed/uncertain filing left a draft active for retry."""
    if context.user_data.get("last_filing_status") not in {"failed", "partial"}:
        return False
    return bool(_load_draft(context) or _restore_retryable_draft(context))


def _build_failed_filing_input_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Retry filing this draft", callback_data="ACTION|retry_filing")],
        [InlineKeyboardButton("✏️ Keep editing this draft", callback_data="CASE|improve")],
        [InlineKeyboardButton("📋 Start new case", callback_data="CASE|new")],
        [InlineKeyboardButton("❌ Cancel current draft", callback_data="ACTION|cancel")],
    ])


async def _show_failed_filing_input_gate(
    target,
    context,
    incoming_text: str,
    *,
    edit: bool = False,
) -> int:
    """Ask whether new input after a failed filing is retry/edit/new/cancel."""
    context.user_data["pending_new_case_text"] = (incoming_text or "").strip()
    form_name = context.user_data.get("last_filing_form_name") or _form_display_name(context.user_data.get("chosen_form", ""))
    form_line = f" for the {form_name}" if form_name else ""
    prompt_text = (
        f"The Kaizen save did not complete{form_line}, so I have kept that draft open.\n\n"
        "Do you want this new information to update the same draft, or should I start a separate case?"
    )
    markup = _build_failed_filing_input_gate_keyboard()
    if edit:
        await target.edit_text(prompt_text, reply_markup=markup)
    else:
        await target.reply_text(prompt_text, reply_markup=markup)
    return AWAIT_APPROVAL


def _looks_like_explicit_new_case_request(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    patterns = [
        r"\b(start|file|create|open)\s+(a\s+)?(new|another|different)\s+(case|wpba|ticket)\b",
        r"\b(new|another|different|separate)\s+(case|wpba|ticket|patient)\b",
        r"\bthis\s+is\s+(a\s+)?(new|another|different|separate)\s+(case|wpba|ticket)\b",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def _build_post_review_keyboard(improved_once: bool = False):
    """Keyboard shown after lightweight draft improvement."""
    return _build_approval_keyboard(improved_once=improved_once)


_POST_FILING_SAME_CASE_LABEL = "💾 Save as another WBA"
_POST_FILING_NEW_CASE_LABEL = "📋 File another case"


def _build_post_filing_keyboard(
    form_type: str,
    status: str,
    *,
    uncertain: bool = False,
    same_case_available: bool = False,
    saved_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Flat keyboard shown after a filing attempt — no More-options split.

    Every useful follow-up sits on this one keyboard so the user never has to
    drill into a secondary set and back out. Settings and a Main-menu reset
    are intentionally absent: nothing about a just-saved draft makes a
    settings change immediately relevant, and the welcome-style "Portfolio
    Guru is ready" message reads like a context wipe right after the user
    filed a case.

    saved_url: when the deterministic filer captured the post-save Kaizen URL
    (e.g. /events/fillin/<doc-id>?autosave=...), link directly to that draft.
    Without it, the fallback links to /activities — the user has to find the
    saved draft there, but at least the bot doesn't open a NEW blank form
    while claiming to open the filed draft.
    """
    rows: list[list[InlineKeyboardButton]] = []

    if status == "failed":
        rows.append([
            InlineKeyboardButton("🔄 Try again", callback_data="ACTION|retry_filing"),
            InlineKeyboardButton(_POST_FILING_NEW_CASE_LABEL, callback_data="ACTION|file"),
            _BTN_CANCEL,
        ])
        return InlineKeyboardMarkup(rows)

    if status == "partial" or uncertain:
        if saved_url:
            rows.append([InlineKeyboardButton("🔗 Open saved draft", url=saved_url)])
        elif FORM_UUIDS.get(form_type):
            rows.append([InlineKeyboardButton("🔗 Open Kaizen", url="https://kaizenep.com/activities")])
        if same_case_available and not uncertain:
            rows.append([
                InlineKeyboardButton(_POST_FILING_SAME_CASE_LABEL, callback_data="ACTION|same_case_another"),
                InlineKeyboardButton(_POST_FILING_NEW_CASE_LABEL, callback_data="ACTION|file"),
            ])
        else:
            rows.append([InlineKeyboardButton(_POST_FILING_NEW_CASE_LABEL, callback_data="ACTION|file")])
        if uncertain:
            rows.append([InlineKeyboardButton("🔄 Try again", callback_data="ACTION|retry_filing")])
        if uncertain:
            rows.append([_BTN_CANCEL])
        return InlineKeyboardMarkup(rows)

    if saved_url:
        rows.append([InlineKeyboardButton("🔗 Open saved draft", url=saved_url)])
    elif FORM_UUIDS.get(form_type):
        rows.append([InlineKeyboardButton("🔗 Open Kaizen", url="https://kaizenep.com/activities")])
    if same_case_available:
        rows.append([
            InlineKeyboardButton(_POST_FILING_SAME_CASE_LABEL, callback_data="ACTION|same_case_another"),
            InlineKeyboardButton(_POST_FILING_NEW_CASE_LABEL, callback_data="ACTION|file"),
        ])
    else:
        rows.append([InlineKeyboardButton(_POST_FILING_NEW_CASE_LABEL, callback_data="ACTION|file")])
    return InlineKeyboardMarkup(rows)


def _build_edit_field_keyboard(draft=None):
    """Build edit field keyboard. For FormDraft, generates buttons dynamically from schema."""
    if draft and isinstance(draft, FormDraft):
        schema = FORM_SCHEMAS.get(schema_form_type(draft.form_type), {})
        fields = schema.get("fields", [])
        # Only editable fields (text/date, skip kc_tick)
        editable = [f for f in fields if f["type"] in ("text", "date", "dropdown")][:6]
        buttons = []
        for field in editable:
            label = field["label"][:20]
            buttons.append(InlineKeyboardButton(label, callback_data=f"FIELD|{field['key']}"))
        # Arrange in rows of 2
        rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("↩️ Cancel edit", callback_data="CANCEL|edit")])
        return InlineKeyboardMarkup(rows)
    # Default CBD keyboard
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Date", callback_data="FIELD|date_of_encounter"),
            InlineKeyboardButton("🏥 Setting", callback_data="FIELD|clinical_setting"),
        ],
        [
            InlineKeyboardButton("🩺 Presentation", callback_data="FIELD|patient_presentation"),
            InlineKeyboardButton("📝 Case discussion", callback_data="FIELD|clinical_reasoning"),
        ],
        [
            InlineKeyboardButton("💭 Reflection", callback_data="FIELD|reflection"),
            InlineKeyboardButton("📚 SLOs", callback_data="FIELD|curriculum_links"),
        ],
        [InlineKeyboardButton("↩️ Cancel edit", callback_data="CANCEL|edit")],
    ])


def _normalise_form_type(form_type: str) -> str:
    return form_type[:-5] if form_type.endswith("_2021") else form_type


def _draft_form_type(draft) -> str:
    return draft.form_type if isinstance(draft, FormDraft) else "CBD"


def _chosen_form_reason(context, form_type: str) -> str | None:
    base_form_type = _normalise_form_type(form_type)
    for rec in context.user_data.get("form_recommendations", []):
        if _normalise_form_type(rec.form_type) == base_form_type and getattr(rec, "rationale", None):
            return _safe_markdown_text(rec.rationale)
    return None


def _safe_markdown_text(text: str) -> str:
    return str(text).replace("_", " ").replace("*", "").replace("`", "").replace("[", "").replace("]", "")


def _short_plain_text(text: str, max_words: int = 16) -> str:
    clean = _safe_markdown_text(text or "").strip()
    clean = clean.replace("...", "").replace("…", "")
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    words = clean.split()
    if len(words) <= max_words:
        return clean + "." if clean and clean[-1] not in ".!?" else clean
    return " ".join(words[:max_words]).rstrip(",;:.") + "."


def _recommendation_line(rec, *, index: int, total: int, curriculum: str) -> str:
    name = _recommendation_form_display_name(rec.form_type, curriculum)
    rationale = _short_plain_text(sanitize_internal_form_codes(getattr(rec, "rationale", "")))
    if index == 0 and total > 1:
        alternative_count = total - 1
        comparison = (
            "Strongest fit because it matches the main event better than the alternative."
            if alternative_count == 1
            else "Strongest fit because it matches the main event better than the alternatives."
        )
        if rationale:
            rationale = f"{rationale} {comparison}"
        else:
            rationale = comparison
    if not rationale:
        rationale = "Fits the main portfolio evidence in this case."
    return sanitize_internal_form_codes(f"- {name}: {rationale}")


def _privacy_nudge_for_source(input_source: str | None) -> str:
    if input_source not in {"photo", "image"}:
        return ""
    return render_message("photo_privacy_nudge")


def _build_form_recommendation_text(
    recommendations,
    *,
    input_source: str | None = None,
    curriculum: str = "2025",
    opening: str = "📋 Forms that fit your case:",
    closing: str = "Tap Use best fit for the fastest draft, or choose another form.",
) -> str:
    visible_recommendations = [r for r in recommendations if getattr(r, "uuid", None)]
    rationale_lines = [
        _recommendation_line(
            r,
            index=index,
            total=len(visible_recommendations),
            curriculum=curriculum,
        )
        for index, r in enumerate(visible_recommendations)
    ]
    rationale_text = "\n".join(rationale_lines) if rationale_lines else "- Case-Based Discussion: Clinical case discussion."
    return render_message(
        "form_recommendation",
        opening=opening,
        recommendations=rationale_text,
        closing=closing,
        privacy_nudge=_privacy_nudge_for_source(input_source),
    )


def _find_reflection_keys(fields: dict, form_type: str | None = None) -> list[str]:
    if not isinstance(fields, dict):
        return []
    if schema_form_type(form_type or "") == "FORMAL_COURSE":
        return [key for key in ("reflective_notes", "resources_used", "lessons_learned") if key in fields]

    keys: list[str] = []
    if "reflection" in fields:
        keys.append("reflection")
    preferred = (
        "reflective_notes",
        "lessons_learned",
        "learning_points",
        "learning_outcomes",
        "learned",
    )
    for key in preferred:
        if key in fields and key not in keys:
            keys.append(key)
    for key in fields:
        normalised = str(key).lower()
        if (
            "reflection" in normalised
            or "reflective" in normalised
            or "lesson" in normalised
            or "learned" in normalised
            or "learning" in normalised
        ) and key not in keys:
            keys.append(key)
    return keys


def _find_reflection_key(fields: dict, form_type: str | None = None) -> str | None:
    keys = _find_reflection_keys(fields, form_type)
    if keys:
        return keys[0]
    return None


def _draft_reflection_text(draft) -> str:
    if isinstance(draft, CBDData):
        return draft.reflection or ""
    if isinstance(draft, FormDraft):
        key = _find_reflection_key(draft.fields, draft.form_type)
        return str(draft.fields.get(key) or "") if key else ""
    return ""


def _draft_coach_note(draft) -> str:
    """Return a coach note only when the reflection genuinely needs help.
    Returns "" for solid reflections so the preview isn't padded with noise."""
    reflection = _draft_reflection_text(draft).strip()
    if not reflection:
        return "Coach note: Tap Quick improve to draft a stronger reflection."
    if len(reflection.split()) < 18:
        return "Coach note: Reflection is short — Quick improve can flesh it out."
    return ""


def _draft_header(title: str) -> list[str]:
    """Compact status header before the draft body."""
    return [
        f"🟢 *{FLOW_STATE_LABELS['drafted']} — {title} ready*",
        "",
    ]


def _format_draft_preview(draft, reason: str | None = None) -> str:
    """Format draft data as a preview message. Dispatches based on type."""
    if isinstance(draft, FormDraft):
        preview = _format_generic_draft(draft)
        return preview + _draft_coach_note_suffix(draft)
    preview = _format_cbd_draft(draft)
    return preview + _draft_coach_note_suffix(draft)


def _draft_coach_note_suffix(draft) -> str:
    """Render the optional coach note as a single 💡 line, if any."""
    coach = _draft_coach_note(draft)
    if not coach:
        return ""
    return f"\n💡 {coach.removeprefix('Coach note: ').strip()}"


def _draft_fields_for_review(draft) -> dict:
    if isinstance(draft, CBDData):
        return draft.model_dump()
    if isinstance(draft, FormDraft):
        return draft.fields
    return {}


def _is_missing_field_value(value) -> bool:
    if value is None:
        return True
    if isinstance(value, list):
        return not any(str(item).strip() for item in value)
    if isinstance(value, str):
        return not value.strip()
    return False


# Marker rendered when a required field has no content. Surfacing this in the
# preview is part of the anti-fabrication strategy: the user sees exactly
# what's missing rather than a falsely-complete-looking draft. See
# `feedback-no-fabrication` memory.
_MISSING_MARKER = "_— needs your detail_"

# Appended to draft previews shown with the approval keyboard so the user
# knows they can reply to refine, instead of relying on a removed Edit button.
_REPLY_HINT_SUFFIX = render_message("draft_reply_hint")

# Visual divider separating portfolio content from bot guidance/rationale in
# draft previews (after draft body). Post-filing confirmations should stay
# clean because they are outcome messages, not draft-plus-instruction messages.
_DRAFT_DIVIDER = "━━━━━━━━━━━━━━"

_NARRATIVE_PREVIEW_KEYS = {
    "actions_taken",
    "case_discussion",
    "case_observed",
    "case_to_be_discussed",
    "clinical_reasoning",
    "clinical_scenario",
    "description",
    "different_outcome",
    "esle_description",
    "feedback_received",
    "focussing_on",
    "further_action",
    "how_used",
    "incident_summary",
    "learned",
    "learning_objectives",
    "learning_outcomes",
    "learning_points",
    "lessons_learned",
    "patient_presentation",
    "project_description",
    "reflective_comments",
    "reflective_notes",
    "reflection",
    "replay_differently",
    "trainee_performance",
    "trainee_role",
    "us_findings",
    "why",
}


def _summarise_field_value(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if str(item).strip())
    return str(value).strip()


def _split_sentences_for_preview(text: str) -> list[str]:
    import re as _re
    return [
        sentence.strip()
        for sentence in _re.split(r"(?<=[.!?])\s+", text)
        if sentence.strip()
    ]


def _short_preview_paragraphs(text: str, *, max_words: int = 45) -> list[str]:
    """Split long preview prose into readable paragraphs without rewriting it."""
    paragraphs: list[str] = []
    for raw_paragraph in str(text).splitlines():
        clean = " ".join(raw_paragraph.split())
        if not clean:
            continue
        if len(clean.split()) <= max_words:
            paragraphs.append(clean)
            continue

        current: list[str] = []
        current_words = 0
        for sentence in _split_sentences_for_preview(clean):
            sentence_words = len(sentence.split())
            if current and current_words + sentence_words > max_words:
                paragraphs.append(" ".join(current))
                current = [sentence]
                current_words = sentence_words
            else:
                current.append(sentence)
                current_words += sentence_words
        if current:
            paragraphs.append(" ".join(current))

    return paragraphs


def _format_preview_text_value(key: str, value) -> str:
    """Display-only readability guard for draft previews.

    This intentionally returns formatted text for Telegram review only. It must
    not mutate `draft.fields` or CBDData values, because Kaizen filing should
    keep the extracted field values exactly as approved.
    """
    if _is_missing_field_value(value):
        return _MISSING_MARKER

    text = str(value).strip()
    if key not in _NARRATIVE_PREVIEW_KEYS:
        return text

    if len(text.split()) < 70 and "\n" not in text:
        return text

    paragraphs = _short_preview_paragraphs(text)
    return "\n\n".join(paragraphs) if paragraphs else text


def _template_requirements(form_type: str):
    schema = FORM_SCHEMAS.get(schema_form_type(form_type), {})
    required = []
    optional = []
    for field in schema.get("fields", []):
        if field["type"] == "kc_tick" or field["key"] == "key_capabilities":
            continue
        target = required if field.get("required") else optional
        target.append(field)
    return required, optional


def _missing_template_fields(draft, form_type: str):
    fields = _draft_fields_for_review(draft)
    required, optional = _template_requirements(form_type)
    missing_required = [field for field in required if _is_missing_field_value(fields.get(field["key"]))]
    missing_optional = [field for field in optional if _is_missing_field_value(fields.get(field["key"]))]
    present_fields = [field for field in required + optional if not _is_missing_field_value(fields.get(field["key"]))]
    return missing_required, missing_optional, present_fields


def _draft_has_useful_content(draft, form_type: str) -> bool:
    """True when there is enough extracted content to review a partial draft."""
    fields = _draft_fields_for_review(draft)
    ignored_keys = {"curriculum_links", "key_capabilities", "curriculum_section", "section_of_curriculum"}
    present_values = [
        value for key, value in fields.items()
        if key not in ignored_keys and not _is_missing_field_value(value)
    ]
    if len(present_values) >= 2:
        return True

    narrative_keys = {
        "patient_presentation",
        "clinical_reasoning",
        "case_discussion",
        "case_observed",
        "case_to_be_discussed",
        "indication",
        "trainee_performance",
        "reflection",
    }
    for key in narrative_keys:
        value = fields.get(key)
        if isinstance(value, str) and len(value.split()) >= 5:
            return True

    _, _, present_fields = _missing_template_fields(draft, form_type)
    if len(present_fields) >= 2:
        return True
    return False


async def _show_draft_review(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    draft,
    form_type: str,
    *,
    edit: bool = True,
) -> int:
    _store_draft(context, draft)
    missing_required, missing_optional, _ = _missing_template_fields(draft, form_type)
    has_missing = bool(missing_required or missing_optional)
    _track_funnel_event(context, "draft_shown", form_type=form_type, has_missing=has_missing)
    preview = _format_draft_preview(draft, _chosen_form_reason(context, form_type))
    text = preview + _REPLY_HINT_SUFFIX
    keyboard = _build_approval_keyboard(
        improved_once=context.user_data.get("quick_improve_used", False),
    )
    if edit:
        await _safe_edit_text(
            message,
            text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    else:
        await _send_latest_message(
            message,
            context,
            text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    return AWAIT_APPROVAL


def _detail_request_message_key(form_type: str | None) -> str:
    base_form = _normalise_form_type(form_type or "")
    if base_form == "SDL":
        return "thin_sdl_detail_request"
    return "thin_case_detail_request"


async def _ask_for_more_detail_before_draft(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    form_type: str | None = None,
    edit: bool = True,
) -> int:
    text = render_message(_detail_request_message_key(form_type))
    if edit:
        await _safe_edit_text(message, text, reply_markup=_KB_CANCEL)
    else:
        await _send_latest_message(message, context, text, reply_markup=_KB_CANCEL)
    return AWAIT_CASE_INPUT


def _universal_pre_file_gate(form_type: str, fields: dict) -> list[str]:
    """Return friendly labels for missing schema-required fields.

    The universal gate warns but never blocks.
    """
    base_form = _normalise_form_type(form_type)
    adapted_fields = dict(fields or {})
    if base_form == "DOPS":
        from dops_filing import normalise_dops_fields
        adapted_fields = normalise_dops_fields(adapted_fields)

    schema = FORM_SCHEMAS.get(schema_form_type(form_type), {})
    missing: list[str] = []
    for field in schema.get("fields", []):
        if field.get("type") == "kc_tick" or field.get("key") == "key_capabilities":
            continue
        if field.get("required"):
            val = adapted_fields.get(field["key"])
            if _is_missing_field_value(val):
                label = field["label"]
                if base_form == "DOPS" and field["key"] in ("procedure_name", "procedural_skill"):
                    label = "Procedure / procedural skill"
                elif base_form == "DOPS" and field["key"] == "trainee_performance":
                    label = "Trainee Performance"
                elif base_form == "DOPS" and field["key"] == "clinical_setting":
                    label = "Clinical Setting"
                elif base_form == "DOPS" and field["key"] == "date_of_encounter":
                    label = "Date"

                if label not in missing:
                    missing.append(label)
    return missing






def _format_template_review(form_type: str, draft) -> str:
    form_name = _form_display_name(form_type)
    required, optional = _template_requirements(form_type)
    missing_required, missing_optional, present_fields = _missing_template_fields(draft, form_type)
    fields = _draft_fields_for_review(draft)

    lines = [
        f"🧩 *{form_name} template*",
        f"🟡 *{FLOW_STATE_LABELS['needs_you']}* — I found some useful detail. Add the gaps below for a stronger ticket, or show the draft anyway.",
        "",
        "*Required fields*",
        *[f"• {field['label']}" for field in required],
    ]

    if optional:
        lines.extend([
            "",
            "*Optional fields*",
            *[f"• {field['label']}" for field in optional],
        ])

    if present_fields:
        lines.extend(["", "*Picked up from your case*"])
        for field in present_fields[:6]:
            value = _summarise_field_value(fields.get(field["key"]))
            if len(value) > 120:
                value = f"{value[:117].rstrip()}..."
            lines.append(f"• {field['label']}: {value}")

    lines.extend(["", "*Missing detail that would improve this*"])
    if missing_required:
        lines.extend(f"• {field['label']}" for field in missing_required)
    else:
        lines.append("• No required fields")

    if missing_optional:
        lines.extend(["", "*Helpful extras if you want a stronger reflection*"])
        lines.extend(f"• {field['label']}" for field in missing_optional[:3])

    lines.extend([
        "",
        "💬 Send text, voice, or another image to add detail.",
    ])
    return "\n".join(lines)

def _format_curriculum_hierarchy(curriculum_links, key_capabilities) -> str:
    """Render SLOs with their KCs nested underneath as a hierarchy."""
    import re as _re
    if not curriculum_links:
        return "  • None"

    # Build a safe display label for each SLO (no underscores that break Markdown)
    def slo_label(slo: str) -> str:
        labels = {
            "SLO1": "SLO1 — Stable adult patients",
            "SLO3": "SLO3 — Clinical questions & decisions",
            "SLO4": "SLO4 — Injured patients",
            "SLO5": "SLO5 — Resuscitation & stabilisation",
            "SLO6_PAEDS": "SLO6 — Paediatric care",
            "SLO6_PROC": "SLO6 — Procedural skills",
            "SLO7": "SLO7 — Complex situations",
            "SLO8": "SLO8 — Lead the ED shift",
            "SLO9_TEACH": "SLO9 — Teaching & supervision",
            "SLO9_RESEARCH": "SLO9 — Research",
            "SLO10": "SLO10 — Research",
            "SLO11": "SLO11 — Quality improvement & safety",
            "SLO12": "SLO12 — Lead & manage",
        }
        return labels.get(slo.upper(), slo.replace("_", " "))

    # Group KCs by parent SLO — match on full key first, then numeric prefix
    slo_kcs: dict = {slo: [] for slo in curriculum_links}
    for kc in (key_capabilities or []):
        kc_upper = kc.upper()
        matched = False
        # Try full key match first (e.g. SLO6_PROC)
        for slo in curriculum_links:
            if kc_upper.startswith(slo.upper() + " ") or kc_upper.startswith(slo.upper() + "_KC"):
                slo_kcs[slo].append(kc)
                matched = True
                break
        if not matched:
            # Fall back to numeric prefix (e.g. SLO6 matches SLO6_PROC or SLO6_PAEDS)
            m = _re.match(r'^(SLO\d+)', kc_upper)
            if m:
                num_prefix = m.group(1)
                for slo in curriculum_links:
                    if slo.upper().startswith(num_prefix):
                        slo_kcs[slo].append(kc)
                        matched = True
                        break

    lines = []
    for slo in curriculum_links:
        lines.append(f"• *{slo_label(slo)}*")
        for kc in slo_kcs.get(slo, []):
            # Extract KC number
            num_match = _re.search(r'KC(\d+)', kc, _re.IGNORECASE)
            kc_num = f"KC{num_match.group(1)}" if num_match else "KC"
            # Strip code prefix, get full description
            kc_text = _re.sub(r'^SLO\w+\s+KC\d+:\s*', '', kc, flags=_re.IGNORECASE).strip()
            # Summarise: first 6 words + ellipsis
            words = kc_text.split()
            summary = " ".join(words[:6]) + ("…" if len(words) > 6 else "")
            lines.append(f"  ↳ {kc_num}: {summary}")
    return "\n".join(lines)


def _format_cbd_draft(cbd_data) -> str:
    """Format CBD data as a preview message. Empty required fields are
    rendered with an explicit "needs your detail" marker so the user never
    sees a falsely-complete-looking draft."""
    def display(key, value):
        return _format_preview_text_value(key, value)

    date_str = cbd_data.date_of_encounter
    if _is_missing_field_value(date_str):
        date_display = _MISSING_MARKER
    else:
        try:
            from datetime import datetime
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            date_display = dt.strftime("%-d %b %Y")
        except (ValueError, AttributeError):
            date_display = date_str

    curriculum = _format_curriculum_hierarchy(cbd_data.curriculum_links, cbd_data.key_capabilities)
    if not curriculum.strip():
        curriculum = _MISSING_MARKER

    lines = _draft_header("CBD draft")
    lines.extend([
        f"📅 *Date:* {date_display}",
        f"🏥 *Setting:* {display('clinical_setting', cbd_data.clinical_setting)}",
        f"🩺 *Presentation:* {display('patient_presentation', cbd_data.patient_presentation)}",
        "",
        f"🗒️ *Case narrative:*\n{display('clinical_reasoning', cbd_data.clinical_reasoning)}",
        "",
        f"💭 *Reflection:*\n{display('reflection', cbd_data.reflection)}",
        "",
        f"📚 *Curriculum:*\n{curriculum}",
    ])
    return "\n".join(lines)


def _format_generic_draft(draft: FormDraft) -> str:
    """Format a generic FormDraft as a preview message."""
    schema_key = schema_form_type(draft.form_type)
    schema = FORM_SCHEMAS.get(schema_key, {})
    form_name = public_form_name(draft.form_type) or schema.get("name", draft.form_type)
    emoji = FORM_EMOJIS.get(schema_key, FORM_EMOJIS.get(_normalise_form_type(draft.form_type), "📋"))

    lines = _draft_header(f"{form_name} draft")
    lines[0] = f"{emoji} *{form_name} draft ready*"

    fields = schema.get("fields", [])
    for field in fields:
        key = field["key"]
        field_type = field["type"]
        is_required = field.get("required", False)

        # These fields are rendered via the curriculum hierarchy — never render separately
        if key in ("key_capabilities", "curriculum_section", "section_of_curriculum"):
            continue

        value = draft.fields.get(key)
        empty = _is_missing_field_value(value)
        if empty and not is_required:
            # Optional + empty: skip silently. Required + empty: render the
            # missing-marker so the user knows what to complete.
            continue
        if empty:
            label = field["label"]
            fe = FIELD_EMOJIS.get(key, "📌")
            lines.append(f"{fe} *{label}:* {_MISSING_MARKER}\n")
            continue
        label = field["label"]

        # Format date nicely
        if field_type == "date" and isinstance(value, str):
            try:
                from datetime import datetime
                dt = datetime.strptime(value, "%Y-%m-%d")
                value = dt.strftime("%-d %b %Y")
            except (ValueError, AttributeError):
                pass

        # curriculum_links: render as unified SLO→KC hierarchy
        if key == "curriculum_links" and isinstance(value, list):
            import re as _re
            key_caps = draft.fields.get("key_capabilities") or []
            # Derive SLO list — curriculum_links may contain SLO codes OR full KC strings
            slos_seen = []
            for item in value:
                m = _re.match(r'^(SLO\w+)', item, _re.IGNORECASE)
                slo = m.group(1).upper() if m else item.upper()
                if slo not in slos_seen:
                    slos_seen.append(slo)
            # Also pull SLOs from key_caps in case curriculum_links only has codes
            for kc in key_caps:
                m = _re.match(r'^(SLO\w+)', kc, _re.IGNORECASE)
                if m:
                    slo = m.group(1).upper()
                    if slo not in slos_seen:
                        slos_seen.append(slo)
            # Use key_caps for KC lines — deduplicate against curriculum_links to avoid doubling
            kc_strings = key_caps if key_caps else [v for v in value if _re.match(r'^SLO\w+\s+KC', v, _re.IGNORECASE)]
            formatted = _format_curriculum_hierarchy(slos_seen, kc_strings)
            lines.append(f"📚 *Curriculum:*\n{formatted}\n")
            continue

        # Prefix label with emoji if available
        fe = FIELD_EMOJIS.get(key, "📌")
        label_str = f"{fe} *{label}:*"

        # Format other lists (multi_select)
        if isinstance(value, list):
            if value:
                value = "\n".join(f"  • {v}" for v in value)
                lines.append(f"{label_str}\n{value}\n")
            else:
                lines.append(f"{label_str}\n  • None\n")
        else:
            formatted_value = _format_preview_text_value(key, value)
            if len(str(value)) > 100 or "\n" in formatted_value:
                lines.append(f"{label_str}\n{formatted_value}\n")
            else:
                lines.append(f"{label_str} {formatted_value}\n")

    return "\n".join(lines)


# === COMMAND HANDLERS ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /start — welcome message + deep links.

    Dedup guard: PTB fires ALL matching handler registrations across all groups.
    /start is registered in multiple places (conversation fallbacks + group=1),
    so the same update can fire start() twice. We guard by storing the update_id
    *before* user_data.clear() so the second invocation (which runs after the
    first finishes, since group=0 handlers are awaited) sees it and returns early.
    """
    dedup_key = "_start_dedup"
    if context.user_data.get(dedup_key) == update.update_id:
        # Already handled this /start update — skip duplicate
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data[dedup_key] = update.update_id

    # Handle deep links: /start setup, /start file
    # These return ConversationHandler.END so the state doesn't leak into
    # case_conv -- setup_start manages setup_conv conversation state on its
    # own, and AWAIT_CASE_INPUT would set a state we can't process from
    # case_conv's entry point.
    if context.args:
        deep_link = context.args[0].lower()
        if deep_link == "setup":
            await setup_start(update, context)
            return ConversationHandler.END
        elif deep_link == "file":
            if has_credentials(update.effective_user.id):
                await update.message.reply_text(FILE_CASE_PROMPT)
                return AWAIT_CASE_INPUT

    connected = has_credentials(update.effective_user.id)
    msg = WELCOME_MSG_CONNECTED if connected else WELCOME_MSG
    await update.message.reply_text(msg, reply_markup=_build_welcome_keyboard(connected=connected))
    return ConversationHandler.END




# === SETUP FLOW ===

async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Redirect to DM if in a group chat
    chat = update.effective_chat
    if chat.type != "private":
        bot_username = (await context.bot.get_me()).username
        await (update.callback_query.message if update.callback_query else update.message).reply_text(
            f"🔒 For security, set up your credentials in a private chat.\n\n"
            f"👉 [Open private chat](https://t.me/{bot_username}?start=setup)",
            parse_mode="Markdown"
        )
        if update.callback_query:
            await update.callback_query.answer()
        return ConversationHandler.END

    # Can be triggered by command or callback. Anchor the setup flow message.
    if update.callback_query:
        await update.callback_query.answer()
    _flow_done(context, "setup")  # fresh start — drop any stale anchor
    await _flow_msg(
        update, context,
        "📧 What's your Kaizen username (email)?\n\n"
        "🔒 Stored encrypted. Used only to file your drafts on Kaizen — never shared.",
        flow_key="setup",
    )
    return AWAIT_USERNAME


async def setup_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if "@" not in text or "." not in text:
        await _flow_msg(update, context, "⚠️ That doesn't look like an email. What's your Kaizen username?", flow_key="setup")
        return AWAIT_USERNAME
    return await _prompt_kaizen_password(update, context, text)


async def _prompt_kaizen_password(update: Update, context: ContextTypes.DEFAULT_TYPE, username: str) -> int:
    context.user_data["setup_username"] = username
    context.user_data["_setup_state_hint"] = "password"
    await _flow_msg(
        update, context,
        "🔒 What's your Kaizen password?\n\n"
        "_I'll delete this message right after you send it._",
        parse_mode="Markdown",
        flow_key="setup",
    )
    return AWAIT_PASSWORD


_EMAIL_PATTERN = re.compile(r"(?<![A-Z0-9._%+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![A-Z0-9._%+-])", re.IGNORECASE)


def _extract_setup_email_candidate(text: str) -> str | None:
    """Return a Kaizen email candidate from disconnected free text.

    This is intentionally narrow: a disconnected user who sends an email-like
    message is almost certainly trying to reconnect Kaizen, but clinical case
    text must still fall through to the normal "Connect Kaizen first" guard.
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 240:
        return None
    matches = _EMAIL_PATTERN.findall(stripped)
    if len(matches) != 1:
        return None
    email = matches[0]
    lowered = stripped.lower()
    credential_words = {"kaizen", "login", "username", "email", "connect", "reconnect"}
    if stripped == email or any(word in lowered for word in credential_words):
        return email
    return None


async def _test_kaizen_login(username: str, password: str) -> bool | str:
    """Test Kaizen credentials via the engine and detect portfolio type.
    Returns role string ("hst", "accs", "assessor") or False on failure."""
    from engine.providers.kaizen import KaizenProvider
    provider = KaizenProvider(username, password)
    if not provider.connect():
        return False
    role = provider.portfolio_type
    provider.disconnect()
    return role if role != "unknown" else "unknown"


async def setup_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = context.user_data.get("setup_username", "")
    password = update.message.text.strip()
    user_id = update.effective_user.id

    # Delete password message for security
    try:
        await update.message.delete()
    except Exception:
        # Deletion failed — could be missing admin rights (groups) or a Telegram
        # API error (private chat). Warn the user in ALL chat types: a plaintext
        # password sitting visible in a private chat is still a privacy risk.
        # Clear the flow anchor so the Testing status arrives as a fresh bottom
        # message rather than a silent edit above the still-visible password.
        _flow_done(context, "setup")
        try:
            if update.effective_chat.type != "private":
                _warn = (
                    "⚠️ I couldn't delete your password message — I need admin rights in groups. "
                    "Please delete it manually for security."
                )
            else:
                _warn = (
                    "⚠️ I couldn't delete your password message from the chat — "
                    "please delete it manually."
                )
            await update.effective_chat.send_message(text=_warn)
        except Exception:
            pass

    # User has submitted password (now deleted). From here until done, no more
    # typed input is expected — only progress updates and a final result. Edit
    # the password prompt in place so it morphs into "Testing..." → final state.
    await _flow_edit(update, context, "🔄 Testing your Kaizen login…", flow_key="setup")

    async def _progress_updates():
        try:
            await asyncio.sleep(15)
            try:
                await _flow_edit(update, context, "🔄 Still checking — Kaizen can be slow to respond…", flow_key="setup")
            except Exception:
                pass
            await asyncio.sleep(20)
            try:
                await _flow_edit(update, context, "🔄 Almost there — finalising the login check…", flow_key="setup")
            except Exception:
                pass
        except asyncio.CancelledError:
            pass

    progress_task = asyncio.create_task(_progress_updates())
    try:
        login_ok = await asyncio.wait_for(
            _test_kaizen_login(username, password), timeout=60
        )
    except asyncio.TimeoutError:
        progress_task.cancel()
        await _flow_edit(
            update, context,
            "⏱ Kaizen took too long to respond. This is usually a brief outage on their side.\n\n"
            "Tap Try again, or send the case anyway and connect later.",
            reply_markup=_KB_RETRY_SETUP,
            flow_key="setup",
        )
        context.user_data.pop("setup_username", None)
        _flow_done(context, "setup")
        return ConversationHandler.END
    except Exception as exc:
        # Browser-harness / CDP / subprocess failures land here (raised as
        # KaizenInfrastructureError by the provider). They are *not* bad
        # credentials — keep this branch distinct from the "Login failed"
        # path below or users will retype passwords that are actually fine.
        progress_task.cancel()
        logger.warning("Credential test errored: %s", exc, exc_info=True)
        await _flow_edit(
            update, context,
            "⚠️ Couldn't reach Kaizen to verify the login. Try again in a moment.\n\n"
            "Tap Try again, or type your Kaizen email below.",
            reply_markup=_KB_RETRY_SETUP,
            flow_key="setup",
        )
        context.user_data.pop("setup_username", None)
        return AWAIT_USERNAME
    finally:
        progress_task.cancel()

    if not login_ok:
        await _flow_edit(
            update, context,
            "❌ Login failed — please check your username and password.\n\n"
            "Tap Try again, or type your Kaizen email below.",
            reply_markup=_KB_RETRY_SETUP,
            flow_key="setup",
        )
        context.user_data.pop("setup_username", None)
        return AWAIT_USERNAME

    store_credentials(user_id, username, password)
    context.user_data.pop("setup_username", None)
    context.user_data.pop("_setup_state_hint", None)

    # Auto-detect Kaizen portfolio profile from the engine. This is a portfolio
    # bucket such as ACCS/HST, not an exact user grade/year.
    detected_role = login_ok if isinstance(login_ok, str) else ""
    # Cache the canonical Kaizen account role separately from the portfolio
    # profile (an assessor has no personal portfolio; training_level falls back to
    # HIGHER for UX continuity but the supervisor workflow keys off this).
    # Demotion-safe: a transient "unknown" probe never overwrites an
    # already-cached assessor / trainee.
    try:
        from supervisor_workflow import set_role_if_better
        set_role_if_better(user_id, detected_role)
    except Exception:
        # Cache failure must never block the login success path.
        logger.warning("Kaizen role cache update failed", exc_info=True)
    label_map = {
        "hst": "HST Portfolio Profile",
        "accs": "ACCS Portfolio Profile",
        "intermediate": "Intermediate Portfolio Profile",
        "accs_intermediate": "ACCS + Intermediate Portfolio Profile",
        "sas": "Non-Training Profile",
        "non_training_higher": "Non-Training Profile (Higher level)",
        "non_training_unknown": "Non-Training Profile (level unknown)",
        "assessor": "Clinical Supervisor",
    }

    auto_level = detected_role_to_training_level(detected_role)
    if auto_level:
        store_training_level(user_id, auto_level)

    auto_pathway = _autoset_health_pathway_from_role(user_id, detected_role)

    if not get_curriculum(user_id):
        store_curriculum(user_id, _default_curriculum_for_training_level(auto_level))

    if auto_level:
        role_name = label_map.get(detected_role, detected_role)
        pathway_line = (
            f"📊 Portfolio Health pathway: *{_pathway_label(auto_pathway)}* (change with /pathway).\n\n"
            if auto_pathway is not None
            else ""
        )
        await _flow_edit(
            update, context,
            f"✅ Kaizen connected — detected as *{role_name}*.\n\n"
            f"{pathway_line}"
            f"{render_message('welcome_connected')}\n\n"
            "Use the *Menu* (☰ bottom-left) any time for Settings or Voice profile.\n"
            f"Use */settings* if your portfolio is different.",
            parse_mode="Markdown",
            flow_key="setup",
        )
    else:
        await _flow_edit(
            update, context,
            "✅ Kaizen connected! I couldn't auto-detect your portfolio.\n\nWhich Kaizen portfolio applies to you?",
            flow_key="setup",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ACCS Profile", callback_data="SETLEVEL|ACCS")],
                [InlineKeyboardButton("Intermediate Profile", callback_data="SETLEVEL|INTERMEDIATE")],
                [InlineKeyboardButton("HST Profile", callback_data="SETLEVEL|HIGHER")],
                [InlineKeyboardButton("Non-Training Profile", callback_data="SETLEVEL|SAS")],
            ])
        )
        return AWAIT_TRAINING_LEVEL

    _flow_done(context, "setup")
    return ConversationHandler.END


async def setup_training_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Manual portfolio-profile pick after auto-detect couldn't classify the
    Kaizen role. Mirror the auto-detect success path: confirm Kaizen
    connected, accept the chosen profile, end the conversation. Curriculum
    is defaulted per profile (2021 for non-training, 2025 for trainees) here
    and in setup_password, so no follow-up question. Do NOT emit the
    settings-handler "Back to settings" copy —
    this is mid-setup, not a /settings round-trip."""
    query = update.callback_query
    await query.answer()
    level = query.data.split("|")[1]
    user_id = update.effective_user.id
    store_training_level(user_id, level)
    if not get_curriculum(user_id):
        store_curriculum(user_id, _default_curriculum_for_training_level(level))
    context.user_data.pop("_setup_state_hint", None)
    await _safe_edit_text(
        query.message,
        f"✅ Kaizen connected — *{_training_level_label(level)}*.\n\n"
        f"{render_message('welcome_connected')}\n\n"
        "Use the *Menu* (☰ bottom-left) any time for Settings or Voice profile.\n"
        "Use */settings* if your portfolio is different.",
        parse_mode="Markdown",
    )
    _flow_done(context, "setup")
    return ConversationHandler.END


async def setup_curriculum(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle curriculum selection during setup."""
    query = update.callback_query
    await query.answer()
    curriculum = query.data.split("|")[1]
    user_id = update.effective_user.id
    store_curriculum(user_id, curriculum)
    label = "2025 curriculum" if curriculum == "2025" else "2021 curriculum"
    context.user_data.pop("_setup_state_hint", None)
    await query.edit_message_text(
        f"✅ Setup complete. You're on {label}.\n\n"
        f"{render_message('welcome_connected')}\n\n"
        f"Use the *Menu* (☰ bottom-left) any time for Settings or Voice profile.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def _setup_wrong_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle non-text input during setup (photo, voice, document, etc.)."""
    state = context.user_data.get("_setup_state_hint", "username")
    if state == "password":
        await update.message.reply_text("⚠️ Please type your Kaizen password — I can't read photos or voice notes here.")
        return AWAIT_PASSWORD
    await update.message.reply_text("⚠️ Please type your Kaizen email — I can't read photos or voice notes here.")
    return AWAIT_USERNAME


async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    text = _cancelled_next_step_text(update.effective_user.id, "Setup cancelled")
    keyboard = _build_next_step_keyboard(update.effective_user.id)
    await _flow_edit(update, context, text, reply_markup=keyboard, flow_key="setup")
    context.user_data.clear()
    return ConversationHandler.END


# === VOICE PROFILE FLOW ===

VOICE_CHOICE_FRESH_COPY = (
    "⭐ *Voice Profile Setup*\n\n"
    "Drafts can sound like you instead of a generic bot. Pick how you'd like to teach me your voice:\n\n"
    "🤖 *Learn from my Kaizen entries* — I'll read your previous portfolio entries only, read-only, "
    "to learn your tone and structure. I won't create, edit, submit, sign, or share anything.\n\n"
    "✍️ *Add examples manually* — paste, photo, or voice-note 3-5 of your own portfolio entries."
)

VOICE_CHOICE_REBUILD_COPY = (
    "✍️ You already have a voice profile active. Your drafts are styled to match your writing.\n\n"
    "Want to rebuild it? Pick a source:"
)

VOICE_MANUAL_INTRO_COPY = (
    "✍️ *Add examples manually*\n\n"
    "Send 3-5 examples of real portfolio writing. Best examples are reflections or WPBA text you would actually submit.\n\n"
    "You can send:\n"
    "• pasted text — best quality\n"
    "• screenshots/photos — I'll extract the text\n"
    "• voice notes — useful, but pasted examples are cleaner\n\n"
    "I'll learn your tone, structure, reflection depth and phrases. Send the first example now."
)

VOICE_KAIZEN_SAMPLE_COPY = (
    "📚 *Pick a sample size*\n\n"
    "Bigger samples give a steadier voice match. This stays read-only: I'll only read existing portfolio entries — "
    "no creating, editing, submitting, signing, or sharing.\n\n"
    "Choose how far back I should look:"
)


def _voice_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Learn from Kaizen entries", callback_data="VOICE|path_kaizen")],
        [InlineKeyboardButton("✍️ Add examples manually", callback_data="VOICE|path_manual")],
        [InlineKeyboardButton("🔙 Back to settings", callback_data="VOICE|back_to_settings")],
    ])


def _voice_rebuild_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Learn from Kaizen entries", callback_data="VOICE|path_kaizen")],
        [InlineKeyboardButton("✍️ Add examples manually", callback_data="VOICE|path_manual")],
        [InlineKeyboardButton("🗑️ Remove Profile", callback_data="VOICE|remove")],
        [InlineKeyboardButton("🔙 Back to settings", callback_data="VOICE|back_to_settings")],
    ])


def _voice_kaizen_sample_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Recent 10 entries", callback_data="VOICE|kaizen_sample|recent_10")],
        [InlineKeyboardButton("📅 Last 6 months", callback_data="VOICE|kaizen_sample|last_6m")],
        [InlineKeyboardButton("📅 Last 12 months", callback_data="VOICE|kaizen_sample|last_12m")],
        [InlineKeyboardButton("🔙 Back", callback_data="VOICE|back_to_choice")],
    ])


async def _voice_show_choice_screen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show the two-path voice setup choice screen.

    Used by ``/voice``, ``ACTION|voice``, and ``VOICE|back_to_choice``. Button
    entries edit the current voice anchor in place so back-and-forth navigation
    does not append duplicate messages; the typed ``/voice`` command sends a
    fresh message because there is nothing to edit.
    """
    user_id = update.effective_user.id
    existing = get_voice_profile(user_id)
    context.user_data.pop("voice_examples", None)
    context.user_data.pop("pending_voice_profile", None)
    context.user_data.pop("voice_kaizen_path_started", None)

    show = _flow_edit if update.callback_query is not None else _flow_msg
    copy = VOICE_CHOICE_REBUILD_COPY if existing else VOICE_CHOICE_FRESH_COPY
    keyboard = _voice_rebuild_keyboard() if existing else _voice_choice_keyboard()

    await show(
        update, context,
        copy,
        parse_mode="Markdown",
        reply_markup=keyboard,
        flow_key="voice",
    )
    return AWAIT_VOICE_EXAMPLES


async def _voice_show_settings_screen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    context.user_data.pop("voice_examples", None)
    context.user_data.pop("pending_voice_profile", None)
    context.user_data.pop("voice_kaizen_path_started", None)

    try:
        used = await get_cases_this_month(user_id)
    except Exception:
        used = 0
    text, keyboard = _settings_view_components(
        user_id,
        tier=await get_user_tier(user_id),
        used=used,
        connected=has_credentials(user_id),
        is_beta=await is_beta_tester(user_id),
        kaizen_sync=await _safe_kaizen_sync_status(user_id),
    )
    await _flow_edit(update, context, text, reply_markup=keyboard, flow_key="voice")
    _flow_done(context, "voice")
    return ConversationHandler.END


async def voice_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start voice profile collection — /voice command or ACTION|voice."""
    query = update.callback_query
    if query is not None:
        # Clear Telegram's spinner before any async work, otherwise the
        # tapped button stays in a loading state until the query times out.
        await query.answer()
        # Adopt the tapped message as the new voice anchor so the choice
        # screen edits in place instead of appending a duplicate message.
        msg = query.message
        if msg is not None:
            context.user_data["_flow_anchor_voice"] = (msg.chat_id, msg.message_id)
    else:
        _flow_done(context, "voice")  # /voice command — drop any stale anchor
    return await _voice_show_choice_screen(update, context)


async def voice_collect_example(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Collect a voice profile example from the user."""
    msg = update.message
    examples = context.user_data.get("voice_examples", [])

    # Handle callback queries (button clicks → edit anchor in place)
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "VOICE|cancel":
            context.user_data.pop("voice_examples", None)
            context.user_data.pop("pending_voice_profile", None)
            context.user_data.pop("voice_kaizen_path_started", None)
            await _flow_edit(
                update, context,
                _cancelled_next_step_text(update.effective_user.id, "Voice profile setup cancelled"),
                reply_markup=_build_next_step_keyboard(update.effective_user.id),
                flow_key="voice",
            )
            _flow_done(context, "voice")
            return ConversationHandler.END

        if data == "VOICE|back_to_choice":
            context.user_data.pop("voice_kaizen_path_started", None)
            return await _voice_show_choice_screen(update, context)

        if data == "VOICE|back_to_settings":
            return await _voice_show_settings_screen(update, context)

        if data == "VOICE|path_manual":
            context.user_data["voice_examples"] = []
            context.user_data.pop("voice_kaizen_path_started", None)
            await _flow_edit(
                update, context,
                VOICE_MANUAL_INTRO_COPY,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="VOICE|back_to_choice")],
                ]),
                flow_key="voice",
            )
            return AWAIT_VOICE_EXAMPLES

        if data == "VOICE|path_kaizen":
            context.user_data["voice_kaizen_path_started"] = True
            await _flow_edit(
                update, context,
                VOICE_KAIZEN_SAMPLE_COPY,
                parse_mode="Markdown",
                reply_markup=_voice_kaizen_sample_keyboard(),
                flow_key="voice",
            )
            return AWAIT_VOICE_EXAMPLES

        if data.startswith("VOICE|kaizen_sample|"):
            return await _voice_run_kaizen_sample(update, context, data)

        if data == "VOICE|preview_accept":
            # New flow activates the profile immediately inside
            # _build_voice_profile, so this branch only runs for stale buttons.
            # Backwards-compat: if an old in-flight pending profile is still in
            # user_data, save it cleanly instead of dropping the user's work.
            profile_json = context.user_data.get("pending_voice_profile")
            if profile_json:
                store_voice_profile(update.effective_user.id, profile_json, len(examples))
            context.user_data.pop("pending_voice_profile", None)
            context.user_data.pop("voice_examples", None)
            context.user_data.pop("voice_profile_retry_source", None)
            context.user_data.pop("voice_kaizen_path_started", None)
            await _flow_edit(
                update, context,
                "✅ Your voice profile is already active. Send a case to try it, "
                "or update the profile if you want to tune the voice.",
                reply_markup=_voice_post_activation_keyboard(),
                flow_key="voice",
            )
            _flow_done(context, "voice")
            return ConversationHandler.END

        if data == "VOICE|preview_reject":
            # The "Not quite — try again" gate is gone. Old taps land here and
            # should recover cleanly: the profile is already active (saved on
            # build), and the user can update or start drafting.
            context.user_data.pop("pending_voice_profile", None)
            context.user_data.pop("voice_examples", None)
            context.user_data.pop("voice_profile_retry_source", None)
            context.user_data.pop("voice_kaizen_path_started", None)
            await _flow_edit(
                update, context,
                "Your voice profile is already saved. Want to tune it with more "
                "examples or rebuild from different samples?",
                reply_markup=_voice_post_activation_keyboard(),
                flow_key="voice",
            )
            _flow_done(context, "voice")
            return ConversationHandler.END

        if data == "VOICE|remove":
            clear_voice_profile(update.effective_user.id)
            context.user_data.pop("voice_examples", None)
            context.user_data.pop("pending_voice_profile", None)
            await _flow_edit(
                update, context,
                "🗑️ Voice profile removed. Drafts will use standard clinical style.",
                flow_key="voice",
            )
            _flow_done(context, "voice")
            return ConversationHandler.END

        if data == "VOICE|rebuild":
            # No current button emits VOICE|rebuild, but historical chat
            # messages may still carry one. Redirect gracefully to the
            # two-path choice screen instead of crashing.
            return await _voice_show_choice_screen(update, context)

        if data == "VOICE|done":
            return await _build_voice_profile(update, context)

        return AWAIT_VOICE_EXAMPLES

    # Track whether this turn started with a typed text reply — that decides
    # whether the next-step prompt should be a fresh message (so the user's
    # typed reply stays paired with the question they answered) or an edit of
    # the "Reading…/Transcribing…" anchor we just put up for media uploads.
    via_typed_text = False

    # Text example
    if msg and msg.text:
        text = msg.text.strip()
        if text.lower() in ("/cancel", "/done"):
            if text.lower() == "/done" and len(examples) >= 3:
                return await _build_voice_profile(update, context)
            context.user_data.pop("voice_examples", None)
            await _flow_msg(
                update, context,
                _cancelled_next_step_text(update.effective_user.id, "Voice profile setup cancelled"),
                reply_markup=_build_next_step_keyboard(update.effective_user.id),
                flow_key="voice",
            )
            _flow_done(context, "voice")
            return ConversationHandler.END

        # Long text = likely a clinical case, not a voice example.
        # Exit voice setup and route to filing instead.
        if len(text) > 300:
            context.user_data.pop("voice_examples", None)
            context.user_data.pop("pending_voice_profile", None)
            context.user_data.pop("voice_kaizen_path_started", None)
            _flow_done(context, "voice")
            await _flow_msg(
                update, context,
                "You were in Voice Profile setup — I've exited so you can file your case. "
                "Send it and I'll help you draft it.\n\n"
                "You can come back to Voice Profile from /settings any time.",
                reply_markup=_build_next_step_keyboard(update.effective_user.id),
                flow_key="voice",
            )
            return ConversationHandler.END

        examples.append(text)
        via_typed_text = True

    # Photo example — extract text from image. "Reading image…" is a fresh
    # ack message; the next-step prompt then edits it into the result.
    elif msg and msg.photo:
        from vision import extract_from_image
        await _flow_msg(update, context, "📷 Reading image…", flow_key="voice")
        try:
            photo = msg.photo[-1]
            photo_file = await photo.get_file()
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
                await photo_file.download_to_drive(tmp_path)
                text = await extract_from_image(tmp_path)
            import os
            os.unlink(tmp_path)
            if text and text.strip() != "NOT_CLINICAL":
                examples.append(text)
            else:
                await _flow_edit(update, context, "⚠️ Couldn't extract text from that image. Try another.", flow_key="voice")
                return AWAIT_VOICE_EXAMPLES
        except Exception:
            await _flow_edit(update, context, "⚠️ Couldn't read image. Try pasting text instead.", flow_key="voice")
            return AWAIT_VOICE_EXAMPLES

    # Voice note
    elif msg and msg.voice:
        await _flow_msg(update, context, "🎙️ Transcribing voice note…", flow_key="voice")
        try:
            voice_file = await msg.voice.get_file()
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp_path = tmp.name
                await voice_file.download_to_drive(tmp_path)
                text = await transcribe_voice(tmp_path)
            import os
            os.unlink(tmp_path)
            if text:
                examples.append(text)
            else:
                await _flow_edit(update, context, "⚠️ Couldn't transcribe voice note. Try pasting text instead.", flow_key="voice")
                return AWAIT_VOICE_EXAMPLES
        except Exception:
            await _flow_edit(update, context, "⚠️ Couldn't transcribe voice note. Try pasting text instead.", flow_key="voice")
            return AWAIT_VOICE_EXAMPLES

    context.user_data["voice_examples"] = examples

    if len(examples) >= 5:
        return await _build_voice_profile(update, context)

    # Typed text → new message (user's reply stays paired with previous prompt).
    # Photo/voice → edit the "Reading…/Transcribing…" ack into the next-step.
    next_step = _flow_msg if via_typed_text else _flow_edit

    if len(examples) >= 3:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Build Profile ({len(examples)} examples)", callback_data="VOICE|done")],
            [InlineKeyboardButton("➕ Add More", callback_data="VOICE|more")],
        ])
        await next_step(
            update, context,
            f"Got {len(examples)} examples. More examples make the voice match better — send up to 5, or build now.",
            reply_markup=keyboard,
            flow_key="voice",
        )
    else:
        remaining = 3 - len(examples)
        await next_step(
            update, context,
            f"Got it — example {len(examples)} captured. Send {remaining} more so I can build a reliable voice profile.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="VOICE|back_to_choice")],
            ]),
            flow_key="voice",
        )

    return AWAIT_VOICE_EXAMPLES


async def _voice_run_kaizen_sample(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    callback_data: str,
) -> int:
    """Invoke the read-only Kaizen sampler for the chosen window.

    Honours the path gate so a stale button can never bypass the user's
    Kaizen-learning choice. Calls into ``voice_sampler.sample_kaizen_entries`` which is
    isolated from any live Kaizen action.
    """
    from voice_sampler import (
        SamplerStatus,
        WINDOW_LABELS,
        parse_window,
        sample_kaizen_entries,
    )

    if not context.user_data.get("voice_kaizen_path_started"):
        await _flow_edit(
            update, context,
            "⚠️ That sample option is no longer active. Pick a voice source again:",
            reply_markup=_voice_choice_keyboard(),
            flow_key="voice",
        )
        return AWAIT_VOICE_EXAMPLES

    token = callback_data.split("|", 2)[-1]
    window = parse_window(token)
    if window is None:
        await _flow_edit(
            update, context,
            "⚠️ That sample option is no longer valid. Pick again:",
            reply_markup=_voice_kaizen_sample_keyboard(),
            flow_key="voice",
        )
        return AWAIT_VOICE_EXAMPLES

    await _flow_edit(
        update, context,
        f"🔍 Reading {WINDOW_LABELS[window]} — read-only, no changes…",
        flow_key="voice",
    )

    result = await sample_kaizen_entries(update.effective_user.id, window)

    if result.status == SamplerStatus.OK and result.has_samples:
        context.user_data["voice_examples"] = list(result.samples)
        context.user_data["voice_profile_retry_source"] = "kaizen"
        context.user_data.pop("voice_kaizen_path_started", None)
        return await _build_voice_profile(update, context)

    if result.status == SamplerStatus.NO_SAMPLES:
        body = (
            f"📭 I couldn't find any entries in the {WINDOW_LABELS[window]} window. "
            "Try a wider window or add examples manually."
        )
    else:
        body = result.message or (
            "Kaizen learning isn't switched on yet. Try adding examples manually instead."
        )

    rows = []
    if getattr(result, "reason", None) in {
        "login_required",
        "credentials_missing",
        "credentials_unavailable",
        "credentials_rejected",
    }:
        rows.append([InlineKeyboardButton("🔗 Reconnect Kaizen", callback_data="ACTION|setup")])
    rows.extend([
        [InlineKeyboardButton("✍️ Add examples manually", callback_data="VOICE|path_manual")],
        [InlineKeyboardButton("🔙 Back", callback_data="VOICE|back_to_choice")],
    ])

    await _flow_edit(
        update, context,
        body,
        reply_markup=InlineKeyboardMarkup(rows),
        flow_key="voice",
    )
    return AWAIT_VOICE_EXAMPLES


async def _generate_voice_preview(profile_json: str) -> str:
    """Generate a sample draft entry using the voice profile for preview."""
    from voice_profile import build_voice_instruction
    voice_block = build_voice_instruction(profile_json)

    generic_case = "50-year-old male with chest pain and SOB. ECG showed anterior STEMI. Gave aspirin, ticagrelor. Sent to primary PCI."

    prompt = f"""Using the following writing style guidance, write a realistic CBD-style portfolio entry for the case below.

{voice_block}

CASE:
{generic_case}

Write a short portfolio entry (2-3 paragraphs) in this doctor's voice. Include a brief clinical summary and a reflective paragraph. Do not wrap in JSON — just the entry text."""

    from extractor import _generate
    return await _generate(prompt)


def _clean_voice_preview_text(text: str) -> str:
    """Strip model-added Markdown from preview text before showing in chat."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
    cleaned = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", "", cleaned)
    return cleaned.strip()


def _voice_post_activation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Start drafting", callback_data="ACTION|file")],
        [InlineKeyboardButton("🔄 Update voice profile", callback_data="ACTION|voice")],
    ])


async def _build_voice_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Build, save, and activate the voice profile from collected examples.

    The profile is stored immediately — there is no separate user-approval gate.
    A single sample draft is rendered as a demo so the user can see the voice in
    action; the profile itself is built from patterns across all examples.
    """
    examples = context.user_data.get("voice_examples", [])

    # Triggered by a button (VOICE|done / /done after 3 examples) → edit anchor.
    # Triggered by auto-rollover on 5th typed example → send fresh so the user's
    # typed reply stays paired with the previous prompt.
    from_button = update.callback_query is not None
    initial = _flow_edit if from_button else _flow_msg

    await initial(update, context, "🔍 Analysing your writing style…", flow_key="voice")

    try:
        from voice_profile import generate_voice_profile
        profile_json = await asyncio.wait_for(
            generate_voice_profile(examples), timeout=30
        )
        store_voice_profile(update.effective_user.id, profile_json, len(examples))
        # Pending state is obsolete — never set it on the happy path, and clear
        # any leftover from older flows so stale callbacks can't bring it back.
        context.user_data.pop("pending_voice_profile", None)
        context.user_data.pop("voice_examples", None)
        context.user_data.pop("voice_profile_retry_source", None)
        context.user_data.pop("voice_kaizen_path_started", None)

        sample_draft = _clean_voice_preview_text(await _generate_voice_preview(profile_json))
        example_count = len(examples)

        # No user input between "Analysing…" and the preview — always edit.
        await _flow_edit(
            update, context,
            f"✅ Voice profile activated. Future drafts will match your writing voice.\n\n"
            f"I learned your voice from patterns across all {example_count} examples combined — "
            f"not from any single case.\n\n"
            f"🔍 Here's a sample draft as a demo of what it sounds like:\n\n"
            f"{sample_draft}",
            reply_markup=_voice_post_activation_keyboard(),
            flow_key="voice",
        )
        _flow_done(context, "voice")
        return ConversationHandler.END
    except asyncio.TimeoutError:
        logger.warning("Voice profile generation timed out (30s)")
        context.user_data.pop("pending_voice_profile", None)
        context.user_data.pop("voice_examples", None)
        await _flow_edit(
            update, context,
            "⚠️ Analysis took too long — please try again. This usually works on a second attempt.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|voice")],
            ]),
            flow_key="voice",
        )
        _flow_done(context, "voice")
    except Exception as e:
        logger.error(f"Voice profile generation failed: {e}", exc_info=True)
        context.user_data.pop("pending_voice_profile", None)
        context.user_data.pop("voice_examples", None)
        await _flow_edit(
            update, context,
            "⚠️ Couldn't analyse your writing style. Try again or send different examples.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|voice")],
            ]),
            flow_key="voice",
        )
        _flow_done(context, "voice")

    return ConversationHandler.END


async def handle_info_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle INFO| buttons from any message, regardless of conversation state."""
    query = update.callback_query
    await query.answer()

    # Post-delete state is stable and idempotent: the storage explainer here
    # must describe what is not retained after /delete, and Back must return
    # to the exact same all-clear card rather than the generic product explainer.
    if query.data == "INFO|stored_after_delete":
        await query.message.edit_text(
            _DATA_CLEAR_TEXT,
            reply_markup=_build_data_clear_keyboard(),
        )
        return

    user_id = update.effective_user.id
    primary = [_BTN_SETUP] if not has_credentials(user_id) else []
    rows = []
    if primary:
        rows.append(primary)
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="ACTION|back_to_menu")])
    await query.message.edit_text(
        WHAT_IS_THIS_MSG,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def handle_same_case_another(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 'Same case, new WPBA' — reuse case text for another form type.

    Registered as a ConversationHandler entry point so the returned state
    (AWAIT_FORM_CHOICE via _process_case_text) is tracked correctly. If this
    runs from the top-level handler instead, the state transition is lost
    and the subsequent FORM|*/CANCEL|* taps silently no-op.
    """
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    case_text = (
        context.user_data.get("last_filed_case_text")
        or context.user_data.get("case_text")
        or ""
    ).strip()
    filed_form = (
        context.user_data.get("last_filed_form_type")
        or context.user_data.get("excluded_form_type")
        or ""
    )
    if not case_text:
        await query.message.reply_text(
            "That same-case shortcut has expired. Send the case again, or start a new case and I’ll draft the next WPBA.",
            reply_markup=_build_next_step_keyboard(user_id),
        )
        return ConversationHandler.END

    # Selective cleanup — preserve conversation handler state keys
    for key in list(context.user_data.keys()):
        if key not in ("case_text", "excluded_form_type"):
            del context.user_data[key]
    context.user_data["case_text"] = case_text
    context.user_data["excluded_form_type"] = filed_form

    ack = await query.message.reply_text(
        "🔁 Reusing the same case. Finding other WPBA options…"
    )
    _track_latest_message(context, ack)
    return await _process_case_text(query.message, context, user_id, case_text, "same case")


async def handle_action_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all ACTION| buttons — universal dispatcher for button-first UX."""
    query = update.callback_query
    action = query.data.split("|", 1)[1] if "|" in query.data else ""
    user_id = update.effective_user.id
    logger.info(
        "Global ACTION callback: action=%s user=%s state=%s",
        action,
        user_id,
        _case_review_state_snapshot(context),
    )

    if action == "retry_filing":
        context.user_data["retry_filing_requested"] = True
        return await handle_approval_approve(update, context)

    await query.answer()

    if action == "setup":
        # force_reconnect is set when the user lands here after a Login failed
        # filing — their saved credentials no longer work, so the
        # "already connected" branch would leave them stuck.
        force_reconnect = context.user_data.pop("force_reconnect", False)
        if has_credentials(user_id) and not force_reconnect:
            await query.message.reply_text(
                "Your Kaizen account is already connected. Just send your next case to file it."
            )
        else:
            await setup_start(update, context)

    elif action == "reset" or action == "cancel":
        # ACTION|reset kept as a quiet alias of cancel so stale "Start fresh"
        # buttons in old chat history still work gracefully.
        context.user_data.clear()
        context.user_data["post_reset"] = True
        try:
            await query.message.edit_text(
                _cancelled_next_step_text(user_id),
                reply_markup=_build_next_step_keyboard(user_id),
            )
        except Exception:
            await query.message.reply_text(
                _cancelled_next_step_text(user_id),
                reply_markup=_build_next_step_keyboard(user_id),
            )
        return ConversationHandler.END

    elif action == "file":
        if not has_credentials(user_id):
            await query.message.reply_text(
                "🔗 Connect your Kaizen account first.",
                reply_markup=InlineKeyboardMarkup([[_BTN_SETUP]])
            )
        else:
            context.user_data.clear()
            await query.message.reply_text(WELCOME_MSG_CONNECTED)
        return AWAIT_CASE_INPUT

    elif action == "same_case_another":
        # Handled by dedicated ConversationHandler entry point (handle_same_case_another).
        # Reaching here means the top-level pattern accidentally captured it.
        logger.warning("same_case_another reached top-level handler (fallback)")
        return ConversationHandler.END

    elif action.startswith("post_file_more|"):
        parts = action.split("|")
        form_type = parts[1] if len(parts) > 1 else "unknown"
        status = parts[2] if len(parts) > 2 else "unknown"
        await query.edit_message_reply_markup(reply_markup=_build_post_filing_keyboard(
            form_type,
            status,
            uncertain=status == "partial",
            same_case_available=bool(context.user_data.get("last_filed_case_text") and status in ("success", "partial")),
        ))

    elif action == "help":
        await query.message.edit_text(
            WHAT_IS_THIS_MSG,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="ACTION|back_to_menu")],
            ]),
        )

    elif action == "unsigned":
        if not has_credentials(user_id):
            await query.message.reply_text(
                "🔗 Connect your Kaizen account first.",
                reply_markup=InlineKeyboardMarkup([[_BTN_SETUP]])
            )
            return ConversationHandler.END
        tier = await get_user_tier(user_id)
        if tier != "pro_plus":
            await query.message.reply_text(
                "📬 Unsigned ticket scanning is included in Portfolio Guru Unlimited.\n\n"
                "Upgrade to see all your pending assessments grouped by assessor, with chase guardrails (14-day cooldown, max 3 per assessor).",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⭐⭐ Upgrade to Unlimited", callback_data="UPGRADE|pro_plus")],
                ]),
            )
            return ConversationHandler.END
        await _show_unsigned_range_picker(query.message, context)

    elif action == "health":
        # Inline pathway-aware health check — morphs the settings screen in
        # place and returns there, not to the generic filing menu.
        back_btn = InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")
        back_markup = InlineKeyboardMarkup([[back_btn]])

        if not has_credentials(user_id):
            await query.message.edit_text(
                "🔗 Connect your Kaizen account first.",
                reply_markup=InlineKeyboardMarkup([[_BTN_SETUP], [back_btn]]),
            )
            return ConversationHandler.END

        if not await _health_gate_check(user_id):
            await query.message.edit_text(
                "📊 Portfolio Health is included in Portfolio Guru Unlimited.\n\nUpgrade to get gap analysis and readiness scoring.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⭐⭐ Upgrade to Unlimited", callback_data="UPGRADE|pro_plus")],
                    [back_btn],
                ]),
            )
            return ConversationHandler.END

        async def send_progress():
            try:
                await query.message.edit_text("🔍 Analysing your portfolio…")
            except Exception:
                pass

        async def send_result(text, reply_markup):
            await _safe_edit_text(
                query.message,
                text,
                parse_mode="Markdown",
                reply_markup=reply_markup or back_markup,
            )

        async def send_photo_fn(fh):
            try:
                await query.message.chat.send_photo(photo=fh)
            except Exception:
                pass

        async def fail_fn(text):
            await query.message.edit_text(text, reply_markup=back_markup)

        await _run_health_analysis(
            user_id=user_id,
            chat=query.message.chat,
            send_progress=send_progress,
            send_result=send_result,
            send_photo_fn=send_photo_fn,
            fail_fn=fail_fn,
        )
        return ConversationHandler.END

    elif action == "settings":
        tier = await get_user_tier(user_id)
        try:
            used = await get_cases_this_month(user_id)
        except Exception:
            used = 0
        text, keyboard = _settings_view_components(
            user_id,
            tier=tier,
            used=used,
            connected=has_credentials(user_id),
            kaizen_sync=await _safe_kaizen_sync_status(user_id),
        )
        await query.message.edit_text(text, reply_markup=keyboard)

    elif action == "refresh_portfolio":
        if not has_credentials(user_id):
            await query.message.edit_text(
                "🔗 Connect your Kaizen account first, then you can sync Kaizen evidence.",
                reply_markup=InlineKeyboardMarkup([
                    [_BTN_SETUP],
                    [InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")],
                ]),
            )
            return ConversationHandler.END

        await query.message.edit_text(
            _refresh_portfolio_confirm_text(),
            reply_markup=_refresh_portfolio_confirm_keyboard(),
        )

    elif action == "confirm_refresh_portfolio":
        if not has_credentials(user_id):
            await query.message.edit_text(
                "🔗 Connect your Kaizen account first, then you can sync Kaizen evidence.",
                reply_markup=InlineKeyboardMarkup([
                    [_BTN_SETUP],
                    [InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")],
                ]),
            )
            return ConversationHandler.END

        try:
            await query.message.edit_text("🔄 Syncing Kaizen evidence…")
        except Exception:
            pass

        try:
            result = await sync_kaizen_portfolio_index_for_user(user_id)
        except Exception as exc:
            logger.warning("Kaizen portfolio refresh failed: %s", exc, exc_info=True)
            result = SimpleNamespace(
                status="failed",
                rows_seen=0,
                rows_written=0,
                rows_drifted=0,
                notes=[],
            )

        status = await _safe_kaizen_sync_status(user_id)
        result_text = _format_refresh_portfolio_result(result, status)
        await _safe_edit_text(
            query.message,
            result_text,
            reply_markup=_refresh_portfolio_result_keyboard(getattr(result, "status", "failed")),
        )
        return ConversationHandler.END

    elif action == "confirm_refresh_for_health":
        back_btn = InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")
        back_markup = InlineKeyboardMarkup([[back_btn]])

        if not has_credentials(user_id):
            await query.message.edit_text(
                "🔗 Connect your Kaizen account first.",
                reply_markup=InlineKeyboardMarkup([[_BTN_SETUP], [back_btn]]),
            )
            return ConversationHandler.END

        if not await _health_gate_check(user_id):
            await query.message.edit_text(
                "📊 Portfolio Health is included in Portfolio Guru Unlimited.\n\nUpgrade to get gap analysis and readiness scoring.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⭐⭐ Upgrade to Unlimited", callback_data="UPGRADE|pro_plus")],
                    [back_btn],
                ]),
            )
            return ConversationHandler.END

        try:
            await query.message.edit_text("🔄 Refreshing Kaizen so I can show Portfolio Health…")
        except Exception:
            pass

        try:
            result = await sync_kaizen_portfolio_index_for_user(user_id)
        except Exception as exc:
            logger.warning("Kaizen portfolio refresh before health failed: %s", exc, exc_info=True)
            result = SimpleNamespace(
                status="failed",
                rows_seen=0,
                rows_written=0,
                rows_drifted=0,
                notes=[],
            )

        if getattr(result, "status", "failed") not in {"ok", "partial"}:
            status = await _safe_kaizen_sync_status(user_id)
            await _safe_edit_text(
                query.message,
                _format_refresh_portfolio_result(result, status),
                reply_markup=_refresh_portfolio_result_keyboard(getattr(result, "status", "failed")),
            )
            return ConversationHandler.END

        async def send_progress():
            try:
                await query.message.edit_text("🔍 Analysing your portfolio…")
            except Exception:
                pass

        async def send_result(text, reply_markup):
            await _safe_edit_text(
                query.message,
                text,
                parse_mode="Markdown",
                reply_markup=reply_markup or back_markup,
            )

        async def send_photo_fn(fh):
            try:
                await query.message.chat.send_photo(photo=fh)
            except Exception:
                pass

        async def fail_fn(text):
            await query.message.edit_text(text, reply_markup=back_markup)

        await _run_health_analysis(
            user_id=user_id,
            chat=query.message.chat,
            send_progress=send_progress,
            send_result=send_result,
            send_photo_fn=send_photo_fn,
            fail_fn=fail_fn,
        )
        return ConversationHandler.END

    elif action == "change_curriculum":
        await query.message.edit_text(
            "📚 Which curriculum should I use for form choices and links?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("2025 Update", callback_data="SET_CURRICULUM|2025")],
                [InlineKeyboardButton("2021 Curriculum", callback_data="SET_CURRICULUM|2021")],
                [InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")],
            ]),
        )

    elif action == "change_level":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("ACCS Profile", callback_data="SETLEVEL|ACCS")],
            [InlineKeyboardButton("Intermediate Profile", callback_data="SETLEVEL|INTERMEDIATE")],
            [InlineKeyboardButton("HST Profile", callback_data="SETLEVEL|HIGHER")],
            [InlineKeyboardButton("Non-Training Profile", callback_data="SETLEVEL|SAS")],
            [InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")],
        ])
        await query.message.edit_text(
            "🎓 Which Kaizen portfolio applies to you?",
            reply_markup=keyboard,
        )

    elif action == "change_pathway":
        profile = _get_or_default_health_profile(user_id)
        await query.message.edit_text(
            f"📊 Current Portfolio Health pathway: {_pathway_label(profile.pathway)}\n\n"
            "Which pathway should /health use?",
            reply_markup=_build_pathway_keyboard(from_settings=True),
        )

    elif action == "back_to_delete_clear":
        await query.message.edit_text(
            _DATA_CLEAR_TEXT,
            reply_markup=_build_data_clear_keyboard(),
        )

    elif action == "back_to_menu":
        connected = has_credentials(user_id)
        msg_text = WELCOME_MSG_CONNECTED if connected else WELCOME_MSG
        await query.message.edit_text(
            msg_text,
            reply_markup=_build_welcome_keyboard(connected=connected),
        )

    elif action == "delete":
        # Confirm before deleting
        await query.message.edit_text(
            "⚠️ This wipes your saved Kaizen login, portfolio, curriculum choice, and voice profile. It does not affect cases already saved in Kaizen. Are you sure?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ Yes, delete", callback_data="CONFIRM|delete"),
                 InlineKeyboardButton("❌ No, keep", callback_data="ACTION|cancel")],
            ])
        )


async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 👍/👎 feedback after filing — logs to Notion DB."""
    query = update.callback_query
    await query.answer("Thanks!")
    parts = query.data.split("|")
    sentiment = parts[1]  # "good" or "bad"
    form_type = parts[2] if len(parts) > 2 else "unknown"
    filing_status = parts[3] if len(parts) > 3 else "unknown"
    user_id = update.effective_user.id

    result_label = "👍 Worked" if sentiment == "good" else "👎 Did not work"
    form_name = _form_display_name(form_type)

    # Log to Notion feedback DB
    import subprocess, os
    from datetime import datetime, timezone
    try:
        token_proc = subprocess.run(
            ["/Users/moeedahmed/.cargo/bin/bws", "secret", "get",
             "c4589dbf-029a-4005-b174-b3f3002bcbbb", "--output", "json"],
            capture_output=True, text=True,
            env={**os.environ, "BWS_ACCESS_TOKEN": open(os.path.expanduser("~/.openclaw/.bws-token")).read().strip()}
        )
        import json as _json
        notion_token = _json.loads(token_proc.stdout)["value"]

        import urllib.request
        payload = _json.dumps({
            "parent": {"database_id": "32bcfc10-fc57-8107-af4c-efc3c10df5e3"},
            "properties": {
                "Name": {"title": [{"text": {"content": f"{result_label} — {form_name}"}}]},
                "Result": {"select": {"name": result_label}},
                "Form": {"select": {"name": form_name}},
                "User ID": {"rich_text": [{"text": {"content": str(user_id)}}]},
                "Filed At": {"date": {"start": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}},
                "Notes": {"rich_text": [{"text": {"content": f"Filing status: {filing_status}"}}]},
            }
        }).encode()
        req = urllib.request.Request(
            "https://api.notion.com/v1/pages",
            data=payload,
            headers={
                "Authorization": f"Bearer {notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as _e:
        logger.warning("Feedback Notion log failed: %s", _e)

    # Disarm the feedback buttons only — leave next-step rows intact.
    try:
        current_markup = query.message.reply_markup
        if current_markup:
            remaining_rows = [
                row for row in current_markup.inline_keyboard
                if not any((button.callback_data or "").startswith("FEEDBACK|") for button in row)
            ]
            new_markup = InlineKeyboardMarkup(remaining_rows) if remaining_rows else None
            await query.edit_message_reply_markup(reply_markup=new_markup)
    except Exception:
        pass


async def handle_filing_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'Something missing?' button after filing — ask which field was missed."""
    query = update.callback_query
    await query.answer()

    # Parse form_type from callback: FILING|feedback|<form_type>
    parts = query.data.split("|")
    form_type = parts[2] if len(parts) > 2 else "unknown"

    # Store form_type for the follow-up message
    context.user_data["pushback_form_type"] = form_type

    # Common fields that might be missed
    field_buttons = [
        [InlineKeyboardButton("Curriculum links / SLOs", callback_data=f"PUSHBACK|{form_type}|curriculum_links")],
        [InlineKeyboardButton("Key Capabilities", callback_data=f"PUSHBACK|{form_type}|key_capabilities")],
        [InlineKeyboardButton("Reflection", callback_data=f"PUSHBACK|{form_type}|reflection")],
        [InlineKeyboardButton("Date", callback_data=f"PUSHBACK|{form_type}|date_of_encounter")],
        [InlineKeyboardButton("Other field", callback_data=f"PUSHBACK|{form_type}|other")],
    ]

    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(field_buttons))


async def handle_pushback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record which field was missed — feeds back into coverage tracker."""
    query = update.callback_query
    await query.answer("Got it — noted for next time.")

    parts = query.data.split("|")
    form_type = parts[1] if len(parts) > 1 else "unknown"
    field_name = parts[2] if len(parts) > 2 else "unknown"

    from filing_coverage import record_pushback
    record_pushback(form_type, field_name)

    # Disarm — remove all buttons, feedback noted
    await query.edit_message_reply_markup(reply_markup=None)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Top-level /cancel — wipes flow state and returns the user to a clean
    'ready to file' state. No menu keyboard for connected users; the next
    typed/sent message is treated as a fresh case."""
    user_id = update.effective_user.id
    context.user_data.clear()
    context.user_data["post_reset"] = True
    await update.message.reply_text(
        _cancelled_next_step_text(user_id),
        reply_markup=_build_next_step_keyboard(user_id),
    )
    return ConversationHandler.END


async def delete_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Delete all stored data for this user — credentials, profile, conversation state."""
    user_id = update.effective_user.id
    context.user_data.clear()

    # Delete credentials
    from credentials import engine as cred_engine, UserCredential
    from profile_store import engine as prof_engine, UserProfile
    from sqlmodel import Session, select

    with Session(cred_engine) as session:
        cred = session.exec(select(UserCredential).where(UserCredential.telegram_user_id == user_id)).first()
        if cred:
            session.delete(cred)
            session.commit()
            try:
                from kaizen_form_filer import invalidate_session_cache
                invalidate_session_cache(user_id)
            except Exception:
                logger.warning("Could not clear Kaizen session cache during data deletion", exc_info=True)

    with Session(prof_engine) as session:
        profile = session.exec(select(UserProfile).where(UserProfile.telegram_user_id == user_id)).first()
        if profile:
            session.delete(profile)
            session.commit()

    await update.message.reply_text(
        _DATA_CLEAR_TEXT,
        reply_markup=_build_data_clear_keyboard(),
    )
    return ConversationHandler.END


HELP_MSG = """📖 *Portfolio Guru — Help*

*How it works:*
📝 Describe → 🔍 I pick the form → ✅ You approve → 📤 Filed to Kaizen

*What you can send:*
Text, voice note, photo, or document (PDF, PPTX, Word)

*What I do:*
Suggest the best form, extract all the fields, show you a draft to review and edit, then save to Kaizen as a draft when you approve.

*45 RCEM forms supported* — assessments, reflections, teaching, management, audit, research, and more.

*Commands:*
/start — Main menu
/setup — Connect or update Kaizen credentials
/voice — Set up your writing style profile
/settings — Plan, usage, preferences
/upgrade — View subscription plans
/help — This message"""


async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Consume a /link <token> from the EM Gurus Hub web app and bind this
    Telegram user to that auth.users id. Tokens are generated at
    emgurus.com/portfolio and have a short TTL."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "To link your bot to your EM Gurus Hub account:\n\n"
            "1. Open https://emgurus.com/portfolio\n"
            "2. Sign in and tap 'Link Telegram'\n"
            "3. Copy the code shown there\n"
            "4. Send `/link <code>` here\n\n"
            "After linking, your portfolio data is visible at emgurus.com/portfolio.",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

    token = args[0].strip()
    try:
        from supabase_sync import consume_link_token
        ok, message = consume_link_token(token, update.effective_user.id)
    except Exception as exc:
        logger.warning("link_command errored: %s", exc, exc_info=True)
        ok, message = False, "Couldn't reach the web service. Try again in a moment."

    icon = "✅" if ok else "⚠️"
    await update.message.reply_text(f"{icon} {message}")
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        HELP_MSG,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [_BTN_SETUP, _BTN_VOICE],
            [InlineKeyboardButton("⚙️ Settings", callback_data="ACTION|settings")],
        ])
    )
    return ConversationHandler.END


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /settings — single dashboard for plan, usage, connection, preferences."""
    user_id = update.effective_user.id
    tier = await get_user_tier(user_id)
    try:
        used = await get_cases_this_month(user_id)
    except Exception:
        used = 0
    text, keyboard = _settings_view_components(
        user_id,
        tier=tier,
        used=used,
        connected=has_credentials(user_id),
        is_beta=await is_beta_tester(user_id),
        kaizen_sync=await _safe_kaizen_sync_status(user_id),
    )
    await update.message.reply_text(text, reply_markup=keyboard)
    return ConversationHandler.END


ADMIN_USER_ID = 6912896590

TIER_LABELS = {"free": "Free", "pro": "Pro", "pro_plus": "Unlimited"}


def _upgrade_buttons(current_tier: str) -> list:
    """Build upgrade option buttons based on current tier.

    Single paid tier: Unlimited. The legacy Pro tier is no longer offered to
    new users, so anyone below Unlimited sees one upgrade target.
    """
    if current_tier == "pro_plus":
        return []
    return [
        [InlineKeyboardButton("⭐⭐ Upgrade to Unlimited (£9.99/mo)", callback_data="UPGRADE|pro_plus")],
    ]


async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /upgrade and /plan — show current tier and upgrade options."""
    user_id = update.effective_user.id
    tier = await get_user_tier(user_id)
    used = await get_cases_this_month(user_id)
    limit = TIER_LIMITS.get(tier, 5)
    limit_str = "unlimited" if limit == -1 else str(limit)

    _flow_done(context, "upgrade")  # fresh start — drop any stale anchor

    if tier == "pro_plus":
        await _flow_msg(
            update, context,
            "You're on the top plan! 🎉\n\nPortfolio Guru Unlimited — unlimited Kaizen WPBA filing, AI extraction, draft review, auto-filing, Portfolio Health, and unsigned-ticket scanning.",
            flow_key="upgrade",
        )
        _flow_done(context, "upgrade")
        return ConversationHandler.END

    text = f"📊 Your plan: {TIER_LABELS.get(tier, tier)} ({used}/{limit_str} cases used this month)\n\n"
    text += (
        "⭐⭐ *Portfolio Guru Unlimited* — £9.99/month\n"
        "• Unlimited Kaizen WPBA filing\n"
        "• Draft Review (AI critique before filing)\n"
        "• Portfolio Health — pathway-aware analysis (Training/CCT ARCP readiness check or CESR / Portfolio Pathway evidence plan)\n"
        "• Unsigned ticket scanning with chase guardrails\n"
        "• Bulk filing\n"
    )

    await _flow_msg(
        update, context,
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(_upgrade_buttons(tier)),
        flow_key="upgrade",
    )
    return ConversationHandler.END


async def handle_upgrade_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle upgrade button press — create Stripe Checkout session."""
    query = update.callback_query
    await query.answer()

    tier = query.data.split("|")[1]  # "pro" (legacy) or "pro_plus"
    # Pro tier is no longer sold. Redirect any stale UPGRADE|pro taps from old
    # chat history to the single current paid tier.
    if tier == "pro":
        tier = "pro_plus"
    tier_label = "Portfolio Guru Unlimited"

    try:
        from stripe_handler import create_checkout_session
        url = await create_checkout_session(update.effective_user.id, tier)
        await _flow_edit(
            update, context,
            f"⭐ Upgrade to {tier_label}\n\nTap below to complete payment:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Complete payment", url=url)],
            ]),
            flow_key="upgrade",
        )
        _flow_done(context, "upgrade")
    except Exception as e:
        logger.error("Stripe checkout failed: %s", e)
        await _flow_edit(
            update, context,
            f"⚠️ Payment setup unavailable right now. Contact support or try /settier for testing.",
            flow_key="upgrade",
        )
        _flow_done(context, "upgrade")


async def settier_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /settier — admin-only manual tier override for testing."""
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("🚫 Admin only.")
        return ConversationHandler.END

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("Usage: /settier <user_id> <tier>\nTiers: free, pro, pro_plus")
        return ConversationHandler.END

    try:
        target_id = int(args[0])
        tier = args[1].lower()
    except ValueError:
        await update.message.reply_text("Invalid user ID. Usage: /settier <user_id> <tier>")
        return ConversationHandler.END

    if tier not in ("free", "pro", "pro_plus"):
        await update.message.reply_text(f"Unknown tier '{tier}'. Options: free, pro, pro_plus")
        return ConversationHandler.END

    await set_user_tier(target_id, tier)
    await update.message.reply_text(f"✅ Set user {target_id} to {TIER_LABELS.get(tier, tier)}.")
    return ConversationHandler.END


async def setbeta_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /setbeta <user_id> <on|off> — admin-only beta_tester flag toggle."""
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("🚫 Admin only.")
        return ConversationHandler.END

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("Usage: /setbeta <user_id> <on|off>")
        return ConversationHandler.END

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Usage: /setbeta <user_id> <on|off>")
        return ConversationHandler.END

    state = args[1].lower()
    if state not in ("on", "off"):
        await update.message.reply_text("State must be 'on' or 'off'. Usage: /setbeta <user_id> <on|off>")
        return ConversationHandler.END

    flag = state == "on"
    await set_beta_tester(target_id, flag)
    label = "Beta (unlimited)" if flag else "off (back to tier limits)"
    await update.message.reply_text(f"✅ Set user {target_id} beta_tester → {label}.")
    return ConversationHandler.END


async def beta_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /beta — request beta tester access."""
    user_id = update.effective_user.id
    username = update.effective_user.username or ""

    # Check if already on an upgraded plan
    tier = await get_user_tier(user_id)
    if tier in ("pro", "pro_plus"):
        await update.message.reply_text("You're already on an upgraded plan! No need to request beta.")
        return ConversationHandler.END

    # Check for existing pending request
    from supabase_sync import get_beta_request_by_username
    existing = get_beta_request_by_username(username) if username else None
    if existing:
        await update.message.reply_text("Your beta request is already pending. Share your @username with the Portfolio Guru team.")
        return ConversationHandler.END

    # Store the request
    from supabase_sync import store_beta_request
    store_beta_request(user_id, username)

    # Notify the configured admin account directly.
    try:
        name = (update.effective_user.first_name or "") + " " + (update.effective_user.last_name or "")
        name = name.strip() or "Unknown"
        tag = f"@{username}" if username else "(no @username)"
        user_id = update.effective_user.id
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID,
            text=(
                f"📩 Beta request from {name} ({tag})\n"
                f"ID: `{user_id}`\n\n"
                f"/setbeta {user_id} on"
            ),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    reply = "✅ Beta request submitted. The Portfolio Guru team will upgrade you shortly."
    await update.message.reply_text(reply)
    return ConversationHandler.END


async def listusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Admin-only — list all users with credentials."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("🚫 Admin only.")
        return ConversationHandler.END

    from credentials import engine as cred_engine, UserCredential
    from sqlmodel import Session, select
    lines = []
    try:
        with Session(cred_engine) as session:
            creds = session.exec(select(UserCredential).order_by(UserCredential.telegram_user_id)).all()
        for c in creds:
            b = await is_beta_tester(c.telegram_user_id)
            badge = "⭐ " if b else ""
            lines.append(f"{badge}ID: `{c.telegram_user_id}`")
        if not lines:
            lines.append("No users have connected Kaizen credentials yet.")
    except Exception as e:
        lines = [f"Error: {e}"]

    header = "📋 Users with credentials:\n\n"
    msg = header + "\n".join(lines)
    await update.message.reply_text(msg, parse_mode="Markdown")
    return ConversationHandler.END


async def filingreport_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Admin-only — render the filing-reliability report from the durable
    NDJSON log. Use ``/filingreport all`` to include synthetic test traffic.
    """
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("🚫 Admin only.")
        return ConversationHandler.END

    args = [arg.strip().lower() for arg in (context.args or [])]
    include_synthetic = bool(args) and args[0] in {"all", "synthetic", "full"}

    try:
        from filing_attempt_log import build_report
        report = build_report(include_synthetic=include_synthetic)
    except Exception as exc:
        logger.error("filingreport failed: %s", exc, exc_info=True)
        await update.message.reply_text(f"⚠️ Could not build filing report: {exc}")
        return ConversationHandler.END

    # Telegram message cap is 4096 chars. The report is bounded by category
    # and recent-failure counts, but truncate as a belt-and-braces guard.
    if len(report) > 3900:
        report = report[:3900] + "\n…(truncated)"
    await update.message.reply_text(report)
    return ConversationHandler.END


async def gather_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Toggle multi-message gathering for the current user. Default is on."""
    args = [arg.strip().lower() for arg in (context.args or [])]

    if args and args[0] in {"on", "enable", "enabled", "yes"}:
        context.user_data["gathering_mode"] = True
        _clear_gathering_case(context)
        await update.message.reply_text(
            'Gathering mode is on. Send a case across messages, then say "draft it" when ready.'
        )
        return ConversationHandler.END

    if args and args[0] in {"off", "disable", "disabled", "no"}:
        context.user_data["gathering_mode"] = False
        _clear_gathering_case(context)
        await update.message.reply_text("Gathering mode is off. I’ll draft from each case message instead.")
        return ConversationHandler.END

    status = "on" if _gathering_enabled(context) else "off"
    await update.message.reply_text(f"Gathering mode is {status}. Use /gather on or /gather off.")
    return ConversationHandler.END


async def assignbeta_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /assignbeta @username — admin-only beta approval."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("🚫 Admin only.")
        return ConversationHandler.END

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /assignbeta @username")
        return ConversationHandler.END

    username = args[0].lstrip("@")

    from supabase_sync import get_beta_request_by_username, approve_beta_request
    request = get_beta_request_by_username(username)

    if not request:
        await update.message.reply_text(
            f"❌ No pending beta request found for @{username}. Ask them to send /beta first."
        )
        return ConversationHandler.END

    target_id = request["user_id"]

    # Approve: set tier to pro (100/month)
    await set_user_tier(target_id, "pro")
    approve_beta_request(target_id)

    # Notify the approved user
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="🎉 You've been upgraded to Portfolio Guru Beta! You can now file up to 100 cases per month. Happy filing!",
        )
    except Exception:
        logger.warning("Could not notify user %s about beta approval", target_id)

    await update.message.reply_text(
        f"✅ @{username} upgraded to 100 cases/month. They've been notified."
    )
    return ConversationHandler.END


async def _resolve_health_evidence(user_id: int):
    """Return (evidence_items, history_records, source) for /health.

    Source priority follows ``docs/PORTFOLIO_HEALTH_SPEC.md`` Phase 2: indexed
    Kaizen evidence wins when present, otherwise we fall back to the existing
    PG case history. ``history_records`` is the raw ``get_case_history``
    output, still required by the LLM ARCP narrative path.
    """
    try:
        indexed_rows = await list_evidence_items(user_id)
    except Exception as exc:  # pragma: no cover - defensive: never break /health
        logger.warning(f"Kaizen index unavailable, falling back to case history: {exc}")
        indexed_rows = []

    history = await get_case_history(user_id, months=6)
    if indexed_rows:
        return evidence_rows_to_health_items(indexed_rows), history, "kaizen_index"
    return case_history_to_evidence_items(history), history, "case_history"


def _sync_status_is_fresh(status: KaizenSyncStatus | None) -> bool:
    """Return True when /health can trust the local Kaizen index."""
    if status is None or status.last_run is None or status.items_indexed <= 0:
        return False
    if status.last_run.status not in {"ok", "partial"}:
        return False

    finished_at = (status.last_run.finished_at or status.last_run.started_at or "").strip()
    if not finished_at:
        return False

    try:
        from datetime import UTC, datetime, timedelta

        parsed = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return datetime.now(UTC) - parsed <= timedelta(hours=24)
    except Exception:
        return False


async def _health_needs_kaizen_refresh(user_id: int) -> bool:
    """Gate the primary /health journey through refresh when data is stale."""
    if not has_credentials(user_id):
        return False
    return not _sync_status_is_fresh(await _safe_kaizen_sync_status(user_id))


async def _run_health_analysis(
    user_id: int,
    chat,
    send_progress,
    send_result,
    send_photo_fn,
    fail_fn,
) -> None:
    """Shared portfolio health pipeline.

    Callers supply message-sending callbacks so the same logic powers both
    the /health command (new progress message) and the inline Portfolio
    Health button (morph the menu in place):
      - send_progress(): show the "analysing" status
      - send_result(text, reply_markup): render the final analysis text
      - send_photo_fn(file_handle): send the chart image
      - fail_fn(text): render an error after the analysis call fails
    """
    await chat.send_action(constants.ChatAction.TYPING)

    profile = _get_or_default_health_profile(user_id)
    is_cesr = profile.pathway == Pathway.cesr_portfolio
    pathway_label = (
        "CESR / Portfolio Pathway"
        if is_cesr
        else "Training (CCT) pathway · ARCP readiness check"
    )
    empty_state_label = (
        "CESR / Portfolio Pathway"
        if is_cesr
        else "ARCP readiness within the Training (CCT) pathway"
    )

    training_level = get_training_level(user_id)
    if not training_level:
        training_level = "HIGHER"
        level_note = "\n\n_Note: No portfolio set — using HST defaults. Use /settings to update._"
    else:
        level_note = ""

    evidence_items, history, _ = await _resolve_health_evidence(user_id)
    if not evidence_items:
        await send_result(
            f"📊 *Portfolio Health — {pathway_label}*\n\n"
            f"No cases filed yet. Start filing cases and come back to check your {empty_state_label} readiness.\n\n"
            "Tip: Send a clinical case to get started.",
            None,
        )
        return

    snapshot = compute_snapshot(profile, evidence_items)

    await send_progress()

    from datetime import datetime as _dt
    month_label = _dt.now().strftime("%B %Y")

    if profile.pathway == Pathway.cesr_portfolio:
        msg = _format_cesr_health_message(snapshot, history, month_label)
        await send_result(msg, _health_result_keyboard())
        await _send_health_chart(user_id, send_photo_fn)
        return

    try:
        analysis = await asyncio.wait_for(
            analyse_portfolio_health(history, training_level), timeout=45
        )
    except asyncio.TimeoutError:
        logger.warning("Portfolio health analysis timed out (45s)")
        await send_result(
            _format_arcp_deterministic_health_message(
                snapshot,
                history,
                month_label,
                level_note,
                "AI ARCP narrative timed out; deterministic health is shown below.",
            ),
            _health_result_keyboard(),
        )
        await _send_health_chart(user_id, send_photo_fn)
        return
    except Exception as e:
        logger.error(f"Portfolio health analysis failed: {e}", exc_info=True)
        await send_result(
            _format_arcp_deterministic_health_message(
                snapshot,
                history,
                month_label,
                level_note,
                "AI ARCP narrative is temporarily unavailable; deterministic health is shown below.",
            ),
            _health_result_keyboard(),
        )
        await _send_health_chart(user_id, send_photo_fn)
        return

    msg = _format_arcp_action_plan_message(
        snapshot=snapshot,
        history=history,
        month_label=month_label,
        level_note=level_note,
        analysis=analysis,
    )
    await send_result(msg, _health_result_keyboard())
    await _send_health_chart(user_id, send_photo_fn)


async def _send_health_chart(user_id: int, send_photo_fn) -> None:
    chart_path = None
    try:
        from portfolio_chart import generate_health_chart_async
        chart_path = await generate_health_chart_async(user_id)
    except ImportError:
        logger.info("matplotlib not installed; skipping portfolio chart image")
    except Exception as e:
        logger.warning(f"Portfolio chart generation failed: {e}", exc_info=True)

    if not chart_path:
        return

    try:
        with open(chart_path, "rb") as fh:
            await send_photo_fn(fh)
    except Exception as e:
        logger.warning(f"Sending portfolio chart failed: {e}", exc_info=True)
    finally:
        try:
            os.remove(chart_path)
        except OSError:
            pass


def _get_or_default_health_profile(user_id: int) -> HealthProfile:
    stored = get_health_profile(user_id)
    if stored:
        return stored
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    return HealthProfile(
        user_id=str(user_id),
        pathway=Pathway.training_arcp,
        pathway_config={},
        created_at=now,
        updated_at=now,
    )


_TRAINEE_DETECTED_ROLES = frozenset({"hst", "accs", "accs_intermediate", "intermediate"})


def _pathway_for_detected_role(detected_role: str) -> Pathway | None:
    """Map a Kaizen-detected role to a Portfolio Health pathway.

    SAS / CESR / non-trainee accounts → CESR / Portfolio Pathway.
    Trainee accounts → Training (CCT); ARCP is a yearly review checkpoint
    inside that pathway, not the pathway itself.
    Ambiguous probes (``unknown``, ``assessor``, empty) return None so the
    caller falls back to existing manual selection rather than guessing.
    """
    if detected_role == "sas":
        return Pathway.cesr_portfolio
    if detected_role in _TRAINEE_DETECTED_ROLES:
        return Pathway.training_arcp
    return None


def _autoset_health_pathway_from_role(user_id: int, detected_role: str) -> Pathway | None:
    """Save a HealthProfile derived from ``detected_role``; no-op when ambiguous."""
    pathway = _pathway_for_detected_role(detected_role)
    if pathway is None:
        return None
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    existing = get_health_profile(user_id)
    profile = HealthProfile(
        user_id=str(user_id),
        pathway=pathway,
        pathway_config=existing.pathway_config if existing else {},
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    save_health_profile(profile)
    return pathway


def _pathway_label(pathway: Pathway) -> str:
    return "Portfolio (CESR)" if pathway == Pathway.cesr_portfolio else "Training (CCT)"


def _build_pathway_keyboard(*, from_settings: bool = False) -> InlineKeyboardMarkup:
    prefix = "PATHWAY_SETTINGS" if from_settings else "PATHWAY"
    rows = [
        [InlineKeyboardButton("Training (CCT)", callback_data=f"{prefix}|{Pathway.training_arcp.value}")],
        [InlineKeyboardButton("Portfolio (CESR)", callback_data=f"{prefix}|{Pathway.cesr_portfolio.value}")],
    ]
    if from_settings:
        rows.append([InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")])
    return InlineKeyboardMarkup(rows)


async def pathway_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Standalone /pathway command — choose Training (CCT) or CESR / Portfolio Pathway.

    ARCP is a yearly review checkpoint inside the Training (CCT) pathway,
    not a pathway in its own right.
    """
    user_id = update.effective_user.id
    profile = _get_or_default_health_profile(user_id)
    await update.message.reply_text(
        f"Current Portfolio Health pathway: {_pathway_label(profile.pathway)}\n\n"
        "Pick the pathway /health should follow:\n"
        "• Training (CCT) — the training programme; ARCP is the yearly review "
        "checkpoint inside it.\n"
        "• Portfolio (CESR) — the long-term Portfolio Pathway to specialist "
        "registration; yearly evidence plan, no ARCP deadline.",
        reply_markup=_build_pathway_keyboard(),
    )
    return AWAIT_PATHWAY


async def handle_pathway_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    from_settings = query.data.startswith("PATHWAY_SETTINGS|")
    raw_pathway = query.data.split("|", 1)[1]
    try:
        pathway = Pathway(raw_pathway)
    except ValueError:
        await query.edit_message_text("⚠️ Unknown pathway. Use /settings to try again.")
        return ConversationHandler.END

    from datetime import UTC, datetime
    now = datetime.now(UTC)
    current = get_health_profile(update.effective_user.id)
    profile = HealthProfile(
        user_id=str(update.effective_user.id),
        pathway=pathway,
        pathway_config=current.pathway_config if current else {},
        created_at=current.created_at if current else now,
        updated_at=now,
    )
    save_health_profile(profile)
    if from_settings:
        try:
            used = await get_cases_this_month(update.effective_user.id)
        except Exception:
            used = 0
        text, keyboard = _settings_view_components(
            update.effective_user.id,
            tier=await get_user_tier(update.effective_user.id),
            used=used,
            connected=has_credentials(update.effective_user.id),
            is_beta=await is_beta_tester(update.effective_user.id),
            kaizen_sync=await _safe_kaizen_sync_status(update.effective_user.id),
        )
        await query.edit_message_text(text, reply_markup=keyboard)
        return ConversationHandler.END

    await query.edit_message_text(
        f"✅ Portfolio Health pathway set to {_pathway_label(pathway)}.\n\n"
        "Run /health to view the updated analysis."
    )
    return ConversationHandler.END


def _format_deterministic_health_section(snapshot) -> str:
    return (
        f"Deterministic health score: {_health_score_label(snapshot.health_score)}\n"
        f"Domain coverage: {_format_domain_counts(snapshot.domain_counts)}"
    )


_HEALTH_DOMAIN_LABELS = {
    HealthDomain.clinical: "clinical",
    HealthDomain.cpd: "CPD",
    HealthDomain.qi: "QI",
    HealthDomain.teaching: "teaching",
    HealthDomain.leadership: "leadership",
    HealthDomain.reflection: "reflection",
}


def _format_arcp_action_plan_message(
    *,
    snapshot,
    history: list[dict],
    month_label: str,
    level_note: str = "",
    fallback_note: str = "",
    analysis: dict | None = None,
) -> str:
    risk = _health_score_label(snapshot.health_score)
    reason = _format_arcp_risk_reason(snapshot)
    actions = _top_health_actions(snapshot, analysis)
    strong = _strong_domain_lines(snapshot)
    missing = _missing_domain_labels(snapshot)

    lines = [
        "📊 *Portfolio Health — Training (CCT) pathway · ARCP readiness check*",
        month_label,
        "",
    ]
    if fallback_note:
        lines.extend([f"_{fallback_note}_", ""])

    lines.extend([
        f"*ARCP risk:* {risk}",
        f"*Why:* {reason}",
        "",
        "*Next 3 urgent filing actions before ARCP*",
        _numbered_list(actions),
        "",
        "*Already strong*",
        _bullet_list(strong) if strong else "• Not enough evidence yet",
        "",
        "*Missing domains*",
        " · ".join(missing) if missing else "None obvious from current evidence",
        "",
        f"Cases filed in Portfolio Guru: {len(history)} in the last 6 months",
    ])
    if level_note:
        lines.append(level_note)
    return "\n".join(lines)


def _format_arcp_risk_reason(snapshot) -> str:
    risk = _health_score_label(snapshot.health_score).replace("🔴 ", "").replace("🟡 ", "").replace("🟢 ", "").replace("⚪ ", "")
    missing = _missing_domain_labels(snapshot)
    strong = _strong_domain_labels(snapshot)

    if missing and strong:
        return (
            f"{risk} because {_plain_join(missing)} evidence is missing, "
            f"despite evidence in {_plain_join(strong)}."
        )
    if missing:
        return f"{risk} because {_plain_join(missing)} evidence is missing."
    if strong:
        return f"{risk} because the main evidence domains are covered; keep them recent before ARCP."
    return "No portfolio evidence is visible yet, so ARCP readiness cannot be judged."


def _top_health_actions(snapshot, analysis: dict | None = None) -> list[str]:
    values: list[str] = []
    values.extend(snapshot.next_actions or [])
    if analysis:
        values.extend(analysis.get("suggestions", []) or [])
    if not values:
        values.append("Add evidence before your next ARCP review")
    return _dedupe_text(values)[:3]


def _strong_domain_lines(snapshot) -> list[str]:
    return [
        f"{_HEALTH_DOMAIN_LABELS[domain]}: {count}"
        for domain, count in snapshot.domain_counts.items()
        if count > 0
    ]


def _strong_domain_labels(snapshot) -> list[str]:
    return [
        _HEALTH_DOMAIN_LABELS[domain]
        for domain, count in snapshot.domain_counts.items()
        if count > 0
    ]


def _missing_domain_labels(snapshot) -> list[str]:
    return [
        _HEALTH_DOMAIN_LABELS[domain]
        for domain in HealthDomain
        if snapshot.domain_counts.get(domain, 0) == 0
    ]


def _numbered_list(values: list[str]) -> str:
    return "\n".join(f"{index}. {value}" for index, value in enumerate(values, start=1))


def _plain_join(values: list[str]) -> str:
    if len(values) <= 1:
        return "".join(values)
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])} and {values[-1]}"


def _dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            deduped.append(text)
            seen.add(text)
    return deduped


def _format_cesr_health_message(snapshot, history: list[dict], month_label: str) -> str:
    gaps = snapshot.gap_summary or ["No major gaps yet"]
    actions = snapshot.next_actions or ["Keep adding recent evidence"]
    readiness_label = _cesr_readiness_label(snapshot.health_score)
    readiness_reason = _cesr_readiness_reason(snapshot)
    history_breakdown = _cesr_wpba_breakdown(history)
    snapshot_breakdown = snapshot.pathway_readiness.get("wpba_breakdown") or {}
    wpba_breakdown = {
        "dops": snapshot_breakdown.get("dops", history_breakdown["dops"]),
        "mini_cex": snapshot_breakdown.get("mini_cex", history_breakdown["mini_cex"]),
        "cbd": snapshot_breakdown.get("cbd", history_breakdown["cbd"]),
    }
    wpba_count = snapshot.pathway_readiness.get("wpba_count", history_breakdown["total"])
    recent_count = snapshot.pathway_readiness.get("recent_evidence_count", 0)
    missing = _missing_domain_labels(snapshot)

    lines = [
        "📊 *Portfolio Health — CESR / Portfolio Pathway*",
        month_label,
        "",
        f"*Long-term CESR readiness:* {readiness_label}",
        f"*Why:* {readiness_reason}",
        "",
        f"*WPBA progress toward 36:* {wpba_count}/36 "
        f"(DOPS {wpba_breakdown['dops']}/12 · "
        f"Mini-CEX {wpba_breakdown['mini_cex']}/12 · "
        f"CBD {wpba_breakdown['cbd']}/12)",
        f"*Evidence window:* {recent_count} item(s) within the last year; "
        "CESR weighs evidence from the last 5 years.",
        "",
        "*This year's evidence plan (next 3–12 months)*",
        _numbered_list(actions[:5]),
        "",
        "*Domain balance*",
        _format_domain_counts(snapshot.domain_counts),
        "",
        "*Missing domains*",
        " · ".join(missing) if missing else "None obvious from current evidence",
        "",
        "*Evidence gaps to close before applying*",
        _bullet_list(gaps),
        "",
        "Target: 36 WPBAs (12 DOPS · 12 Mini-CEX · 12 CBD) plus structured "
        "consultant reports, CPD, and SLO/CiP-level coverage within the 5-year "
        "evidence window. Build this as a multi-year portfolio, not an annual deadline.",
    ]
    return "\n".join(lines)


def _cesr_readiness_label(score) -> str:
    value = getattr(score, "value", str(score))
    labels = {
        "green": "🟢 On track for application",
        "amber": "🟡 Building toward application",
        "red": "🔴 Early — significant evidence still to gather",
        "grey": "⚪ Not enough evidence yet to judge readiness",
    }
    return labels.get(value, value.title())


def _cesr_readiness_reason(snapshot) -> str:
    missing = _missing_domain_labels(snapshot)
    strong = _strong_domain_labels(snapshot)
    if missing and strong:
        return (
            f"evidence is present in {_plain_join(strong)}, but "
            f"{_plain_join(missing)} still need to be built up over the "
            "next 12 months."
        )
    if missing:
        return (
            f"{_plain_join(missing)} evidence is missing — plan to build "
            "these domains across the year."
        )
    if strong:
        return (
            f"evidence covers {_plain_join(strong)}; keep it current within "
            "the 5-year window and add structured consultant reports."
        )
    return "no portfolio evidence is visible yet — start a long-term evidence plan."


_WPBA_BREAKDOWN_FORMS = {
    "dops": {"DOPS"},
    "mini_cex": {"MINI_CEX"},
    "cbd": {"CBD"},
}


def _cesr_wpba_breakdown(history: list[dict]) -> dict[str, int]:
    counts = {"total": _wpba_count_from_history(history), "dops": 0, "mini_cex": 0, "cbd": 0}
    for record in history:
        form_type = str(record.get("form_type", "")).strip().upper()
        for key, members in _WPBA_BREAKDOWN_FORMS.items():
            if form_type in members:
                counts[key] += 1
    return counts


def _format_arcp_deterministic_health_message(
    snapshot,
    history: list[dict],
    month_label: str,
    level_note: str,
    fallback_note: str,
) -> str:
    return _format_arcp_action_plan_message(
        snapshot=snapshot,
        history=history,
        month_label=month_label,
        level_note=level_note,
        fallback_note=fallback_note,
    )


def _health_score_label(score) -> str:
    labels = {
        "green": "🟢 Green",
        "amber": "🟡 Amber",
        "red": "🔴 Red",
        "grey": "⚪ Grey",
    }
    value = getattr(score, "value", str(score))
    return labels.get(value, value.title())


def _format_domain_counts(domain_counts: dict) -> str:
    return " · ".join(f"{_HEALTH_DOMAIN_LABELS[domain]} {domain_counts.get(domain, 0)}" for domain in HealthDomain)


def _bullet_list(values: list[str]) -> str:
    return "\n".join(f"• {value}" for value in values)


def _wpba_count_from_history(history: list[dict]) -> int:
    wpba_forms = {"CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "PROC_LOG", "US_CASE", "ESLE", "ESLE_ASSESS", "ESLE_PART1_2"}
    return sum(1 for record in history if str(record.get("form_type", "")).strip().upper() in wpba_forms)


async def _health_gate_check(user_id: int) -> bool:
    """Check if user can access health. Returns True if allowed."""
    tier = await get_user_tier(user_id)
    return tier == "pro_plus" or await is_beta_tester(user_id)


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /health — analyse portfolio health against the user's selected
    pathway (Training/CCT, with ARCP as a checkpoint inside it, or CESR /
    Portfolio Pathway)."""
    user_id = update.effective_user.id

    if not await _health_gate_check(user_id):
        await update.message.reply_text(
            "📊 Portfolio Health is included in Portfolio Guru Unlimited.\n\n"
            "Upgrade to get monthly portfolio readiness analysis tailored to "
            "your Training (CCT) pathway (ARCP readiness check) or "
            "CESR / Portfolio Pathway view.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐⭐ Upgrade to Unlimited", callback_data="UPGRADE|pro_plus")],
            ]),
        )
        return ConversationHandler.END

    progress_holder: dict = {}

    async def send_progress():
        progress_holder["msg"] = await update.message.reply_text("📊 Analysing your portfolio...")

    async def send_result(text, reply_markup):
        msg = progress_holder.get("msg")
        if msg is not None:
            await _safe_edit_text(msg, text, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            try:
                await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
            except BadRequest as exc:
                if "can't parse entities" in str(exc).lower():
                    await update.message.reply_text(text, reply_markup=reply_markup)
                else:
                    raise

    async def send_photo_fn(fh):
        await update.message.reply_photo(photo=fh)

    async def fail_fn(text):
        msg = progress_holder.get("msg")
        if msg is not None:
            await msg.edit_text(text)
        else:
            await update.message.reply_text(text)

    await _run_health_analysis(
        user_id=user_id,
        chat=update.effective_chat,
        send_progress=send_progress,
        send_result=send_result,
        send_photo_fn=send_photo_fn,
        fail_fn=fail_fn,
    )
    return ConversationHandler.END


async def curriculum_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Standalone /curriculum command — change curriculum anytime."""
    user_id = update.effective_user.id
    current = get_curriculum(user_id)
    label = "2025 curriculum" if current == "2025" else "2021 curriculum"
    await update.message.reply_text(
        f"Currently set to: {label}\n\nWhich curriculum are you working under?",
        reply_markup=_build_curriculum_keyboard()
    )
    return ConversationHandler.END


async def handle_set_curriculum(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle SET_CURRICULUM| callback from /curriculum command (top-level handler)."""
    query = update.callback_query
    await query.answer()
    curriculum = query.data.split("|")[1]
    user_id = update.effective_user.id
    store_curriculum(user_id, curriculum)
    label = "2025 curriculum" if curriculum == "2025" else "2021 curriculum"
    await query.edit_message_text(
        f"✅ Set to {label} — I'll only show you the relevant forms.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")],
        ]),
    )


async def handle_set_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle SETLEVEL| callback from settings → change portfolio profile."""
    query = update.callback_query
    await query.answer()
    level = query.data.split("|")[1]
    user_id = update.effective_user.id
    store_training_level(user_id, level)
    await query.edit_message_text(
        f"✅ Portfolio set to {_training_level_label(level)}.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")],
        ]),
    )


# === CALLBACK QUERY HANDLERS ===

_MIN_CASE_WORDS = 6


def _looks_like_clinical_case(case_text: str) -> bool:
    """Deterministic gate: does the input have enough content to plausibly
    contain clinical detail? Used as a last-line check before extraction so
    the LLM never sees a one-word or fragmented input that would force it
    to invent fields. False negatives are tolerable — the user gets asked
    for more detail rather than a fabricated draft."""
    if not case_text or not case_text.strip():
        return False
    return len(case_text.split()) >= _MIN_CASE_WORDS


async def _analyse_selected_form(context: ContextTypes.DEFAULT_TYPE, user_id: int, case_text: str, form_type: str):
    """Create an explicit-only draft snapshot for the selected form.

    `form_type` is the *user-selected* code (e.g. ``MINI_CEX`` or ``MINI_CEX_2021``).
    The base form (``MINI_CEX``) picks which extractor + schema to use, but the
    full code must be passed into ``extract_form_data`` so the resulting draft
    carries the correct Kaizen UUID for the chosen curriculum variant.
    """
    # Set tier for provider chain gating
    os.environ["CURRENT_USER_TIER"] = context.user_data.get("user_tier", "free")
    vp = get_voice_profile(user_id) or ""
    base_form_type = form_type[:-5] if form_type.endswith("_2021") else form_type
    if base_form_type == "CBD":
        draft = await asyncio.wait_for(
            extract_cbd_data(
                case_text,
                voice_profile_json=vp,
                leave_missing_blank=True,
                preserve_original_content=True,
                input_source=context.user_data.get("case_input_source", "text"),
            ),
            timeout=45,
        )
    else:
        draft = await asyncio.wait_for(
            extract_form_data(
                case_text,
                form_type,
                voice_profile_json=vp,
                leave_missing_blank=True,
                preserve_original_content=True,
                input_source=context.user_data.get("case_input_source", "text"),
            ),
            timeout=45,
        )
    _apply_profile_training_stage(draft, user_id, form_type)
    _apply_default_dates(draft, form_type)
    _store_pending_draft(context, draft)
    context.user_data["chosen_form"] = form_type
    context.user_data["awaiting_detail"] = True
    return draft


async def _handle_reuse_request(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, raw_text: str) -> int:
    """Route a 'use the same case for X' message through the same flow as the
    🔁 button. We re-extract from last_filed_case_text rather than from the
    user's instruction — otherwise the extractor would hallucinate clinical
    content to fill the WPBA fields. See `feedback-no-fabrication` memory."""
    last_case = context.user_data.get("last_filed_case_text", "")
    if not last_case:
        await update.message.reply_text(
            "I don't have a previous case to reuse here. Send the case details "
            "(text, voice, photo, or document) and I'll draft it for you."
        )
        return ConversationHandler.END

    filed_form = context.user_data.get("last_filed_form_type", "")
    # The reuse phrase IS the intent — match form codes without the standard
    # intent-phrase gate so "use the same case for DOPS" picks up DOPS.
    explicit_form = extract_explicit_form_type(raw_text, require_intent=False)

    # Wipe stale flow state but preserve the reuse-source.
    context.user_data.clear()
    context.user_data["case_text"] = last_case
    context.user_data["last_filed_case_text"] = last_case
    context.user_data["last_filed_form_type"] = filed_form
    context.user_data["excluded_form_type"] = filed_form

    if explicit_form:
        context.user_data["chosen_form"] = explicit_form
        await update.message.reply_text(
            f"🔁 Reusing your previous case for *{_form_display_name(explicit_form)}*.\n\n"
            f"Tap Draft to extract the fields from the case you already filed — "
            f"I won't invent any new clinical details.",
            parse_mode="Markdown",
            reply_markup=_build_explicit_form_keyboard(explicit_form),
        )
        return AWAIT_FORM_CHOICE

    # Reuse intent but no form named — show recommendations (excluding the
    # one already filed) so the user picks.
    await update.message.reply_text(
        "🔁 Reusing your previous case. Pick which WPBA type to file it as — "
        "I'll skip the one you already filed."
    )
    return await _process_case_text(update.message, context, user_id, last_case, "same case")


async def _process_case_text(message, context: ContextTypes.DEFAULT_TYPE, user_id: int, case_text: str, input_source: str) -> int:
    """Store case text, suggest form types, or move directly to the chosen template review."""
    _track_funnel_event(context, "input_received", source=input_source)
    # Anti-fabrication gate: never feed a too-thin input to the recommender or
    # the field extractor. The LLM is instructed not to fabricate, but with no
    # content to ground against it can still hallucinate plausible-sounding
    # clinical details. Better to ask the user for more.
    context.user_data["case_text"] = case_text
    context.user_data["case_input_source"] = input_source

    explicit_form = extract_explicit_form_type(case_text)
    if explicit_form:
        context.user_data["chosen_form"] = explicit_form
        await _send_latest_message(
            message,
            context,
            f"I’ll use *{_form_display_name(explicit_form)}* for this entry.\n\nTap Draft to extract the fields from what you sent.",
            reply_markup=_build_explicit_form_keyboard(explicit_form),
            parse_mode="Markdown",
        )
        return AWAIT_FORM_CHOICE

    if not _looks_like_clinical_case(case_text):
        await message.reply_text(
            render_message("thin_case_detail_request")
        )
        return ConversationHandler.END

    training_level = get_training_level(user_id)
    allowed_forms = _allowed_forms_for_training_level(training_level)

    await message.chat.send_action(constants.ChatAction.TYPING)
    try:
        recommendations = await asyncio.wait_for(
            recommend_form_types(case_text, input_source=input_source),
            timeout=30,
        )
        excluded_form = _normalise_form_type(context.user_data.get("excluded_form_type", ""))
        recommendations = _filter_recommendations_for_allowed_forms(
            recommendations,
            allowed_forms,
            case_text,
            excluded_form=excluded_form,
        )
        if not recommendations:
            _track_funnel_event(context, "recommendation_empty", source=input_source)
            await _send_latest_message(
                message, context,
                "Nothing left to recommend for this case — browse all types below.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 See all forms", callback_data="FORM|show_all")],
                    [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|retry_recommend")],
                ]),
            )
            return AWAIT_FORM_CHOICE
        context.user_data["form_recommendations"] = recommendations
    except Exception as exc:
        logger.error("Form recommendation failed across all providers: %s", exc)
        _track_funnel_event(context, "recommendation_failed", source=input_source)
        await _send_latest_message(
            message, context,
            render_message("ai_temporarily_unavailable"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|retry_recommend")],
                [InlineKeyboardButton("📋 Pick form manually", callback_data="FORM|show_all")],
            ]),
        )
        return AWAIT_FORM_CHOICE

    status_msg = context.user_data.pop("status_msg_id", None)
    status_chat = context.user_data.pop("status_msg_chat", None)

    prompt_text = _build_form_recommendation_text(
        recommendations,
        input_source=input_source,
        curriculum=_effective_curriculum(user_id),
    )
    context.user_data["form_recommendations_text"] = prompt_text
    _track_funnel_event(
        context,
        "recommendation_shown",
        source=input_source,
        count=len(recommendations),
    )

    if status_msg and status_chat:
        try:
            await context.bot.edit_message_text(
                chat_id=status_chat,
                message_id=status_msg,
                text=prompt_text,
                reply_markup=_build_form_choice_keyboard(recommendations, curriculum=_effective_curriculum(user_id)),
            )
            context.user_data["last_bot_msg_id"] = status_msg
            context.user_data["last_bot_chat_id"] = status_chat
            context.user_data["status_msg_id"] = status_msg
            context.user_data["status_msg_chat"] = status_chat
        except Exception:
            msg = await message.reply_text(
                prompt_text,
                reply_markup=_build_form_choice_keyboard(recommendations, curriculum=_effective_curriculum(user_id)),
            )
            _track_latest_message(context, msg)
    else:
        msg = await message.reply_text(
            prompt_text,
            reply_markup=_build_form_choice_keyboard(recommendations, curriculum=_effective_curriculum(user_id)),
        )
        _track_latest_message(context, msg)
    return AWAIT_FORM_CHOICE

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route callback queries based on prefix."""
    query = update.callback_query
    data = query.data
    logger.info(
        "Conversation callback: data=%s user=%s state=%s",
        data,
        update.effective_user.id,
        _case_review_state_snapshot(context),
    )

    if data.startswith("INFO|"):
        await query.answer()
        await query.message.reply_text(WHAT_IS_THIS_MSG)
        return ConversationHandler.END

    elif data == "ACTION|setup":
        # Handled entirely by setup_conv — clear case_conv state and let it through
        await query.answer()
        context.user_data.clear()
        return ConversationHandler.END

    elif data == "ACTION|file":
        await query.answer()
        user_id = update.effective_user.id
        if not has_credentials(user_id):
            await query.message.reply_text(
                "Connect your Kaizen account first.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup")]
                ])
            )
            return ConversationHandler.END
        else:
            context.user_data.clear()
            await query.message.reply_text(WELCOME_MSG_CONNECTED)
            return AWAIT_CASE_INPUT

    elif data == "ACTION|add_detail":
        await query.answer("Send the missing detail as text, voice, or another image.")
        return AWAIT_TEMPLATE_REVIEW

    elif data == "ACTION|continue_thin":
        await query.answer()
        draft = _load_pending_draft(context)
        chosen_form = context.user_data.get("chosen_form")
        if not draft or not chosen_form:
            return await _resume_paused_flow(
                update,
                context,
                "That earlier button is no longer active.",
            )
        _store_draft(context, draft)
        context.user_data.pop("awaiting_detail", None)
        _track_funnel_event(context, "draft_shown", form_type=chosen_form, has_missing=True)
        preview = _format_draft_preview(draft, _chosen_form_reason(context, chosen_form))
        await _safe_edit_text(
            query.message,
            preview + _REPLY_HINT_SUFFIX,
            reply_markup=_build_approval_keyboard(
                improved_once=context.user_data.get("quick_improve_used", False),
                can_back_to_missing=True,
            ),
            parse_mode="Markdown",
        )
        return AWAIT_APPROVAL

    elif data == "ACTION|back_to_missing":
        await query.answer()
        draft = _load_pending_draft(context)
        chosen_form = context.user_data.get("chosen_form")
        if not draft or not chosen_form:
            return await _resume_paused_flow(
                update,
                context,
                "That earlier button is no longer active.",
            )
        review_text = _format_template_review(chosen_form, draft)
        await _safe_edit_text(
            query.message,
            review_text,
            reply_markup=_build_template_review_keyboard(),
            parse_mode="Markdown",
        )
        context.user_data["last_bot_msg_id"] = query.message.message_id
        context.user_data["last_bot_chat_id"] = query.message.chat_id
        return AWAIT_TEMPLATE_REVIEW

    elif data == "ACTION|retry_recommend":
        await query.answer("Retrying…")
        case_text = context.user_data.get("case_text", "")
        user_id = update.effective_user.id
        if case_text:
            return await _process_case_text(query.message, context, user_id, case_text, "retry")
        await query.edit_message_text("No case text found. Please send a new case.")
        return AWAIT_CASE_INPUT

    elif data == "ACTION|retry_filing":
        context.user_data["retry_filing_requested"] = True
        return await handle_approval_approve(update, context)

    elif data == "ACTION|retry_template":
        await query.answer("Retrying…")
        chosen_form = context.user_data.get("chosen_form")
        case_text = context.user_data.get("case_text", "")
        user_id = update.effective_user.id
        if not chosen_form or not case_text:
            await query.edit_message_text(
                "That earlier request expired. Send the case again and I’ll draft it.",
                reply_markup=_build_next_step_keyboard(user_id),
            )
            return ConversationHandler.END
        await query.edit_message_text(f"🧩 Reviewing {_form_display_name(chosen_form)} template…")
        try:
            draft = await _analyse_selected_form(context, user_id, case_text, chosen_form)
        except asyncio.TimeoutError:
            logger.error("Template review retry timed out for %s", chosen_form)
            await query.edit_message_text(
                "⏳ Template review timed out again. Try once more in a moment.",
                reply_markup=_KB_RETRY_TEMPLATE,
            )
            return AWAIT_FORM_CHOICE
        except Exception as exc:
            logger.error("Template review retry failed for %s: %s", chosen_form, exc, exc_info=True)
            if _is_transient_llm_error(exc):
                await query.edit_message_text(
                    "⏳ Still rate-limited or briefly unavailable. Wait a moment and try again.",
                    reply_markup=_KB_RETRY_TEMPLATE,
                )
                return AWAIT_FORM_CHOICE
            await query.edit_message_text("⚠️ Could not review that template.", reply_markup=_KB_CANCEL)
            return ConversationHandler.END
        if not _draft_has_useful_content(draft, chosen_form):
            return await _ask_for_more_detail_before_draft(query.message, context)
        return await _show_draft_review(query.message, context, draft, chosen_form)

    elif data == "CASE|new":
        await query.answer()
        new_text = context.user_data.pop("pending_new_case_text", "")
        context.user_data.clear()
        if new_text:
            user_id = update.effective_user.id
            return await _process_case_text(query.message, context, user_id, new_text, "text")
        await query.edit_message_text("This case is closed.", reply_markup=None)
        await query.message.reply_text(
            "Send me the new case whenever you're ready.",
            reply_markup=_build_next_step_keyboard(update.effective_user.id),
        )
        return AWAIT_CASE_INPUT

    elif data == "CASE|improve":
        await query.answer()
        old_case = context.user_data.get("case_text", "")
        new_detail = context.user_data.pop("pending_new_case_text", "")
        merged = f"{old_case}\n\nAdditional context:\n{new_detail}"
        chosen_form = context.user_data.get("chosen_form")
        user_id = update.effective_user.id
        if chosen_form:
            context.user_data["case_text"] = merged
            await query.edit_message_text(f"🧩 Updating {_form_display_name(chosen_form)} draft…")
            try:
                draft = await _analyse_selected_form(context, user_id, merged, chosen_form)
            except Exception as exc:
                logger.error("Template review failed for improve: %s", exc, exc_info=True)
                await query.edit_message_text("⚠️ Could not refresh that template.", reply_markup=_KB_CANCEL)
                return AWAIT_TEMPLATE_REVIEW
            if not _draft_has_useful_content(draft, chosen_form):
                return await _ask_for_more_detail_before_draft(query.message, context)
            return await _show_draft_review(query.message, context, draft, chosen_form)
        else:
            context.user_data.clear()
            return await _process_case_text(query.message, context, user_id, merged, "text")

    elif data.startswith("DOCUSE|"):
        return await handle_document_intent(update, context)

    elif data.startswith("FORM|"):
        return await handle_form_choice(update, context)

    elif data == "APPROVE|submit":
        return await handle_approval_submit(update, context)

    elif data.startswith("APPROVE|"):
        return await handle_approval_approve(update, context)

    elif data.startswith("IMPROVE|"):
        return await handle_quick_improve(update, context)

    elif data.startswith("EDIT|"):
        return await handle_approval_edit(update, context)

    elif data.startswith("FIELD|"):
        return await handle_edit_field(update, context)

    elif data.startswith("CANCEL|") or data in {"ACTION|reset", "ACTION|cancel"}:
        await query.answer()
        # Disarm buttons immediately — prevents double-tap
        await query.edit_message_reply_markup(reply_markup=None)
        user_id = update.effective_user.id
        _track_funnel_event(context, "cancel_reset", state=data)
        context.user_data.clear()
        context.user_data["post_reset"] = True
        await query.message.reply_text(
            _cancelled_next_step_text(user_id),
            reply_markup=_build_next_step_keyboard(user_id),
        )
        return ConversationHandler.END


async def handle_document_intent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Apply the user's explicit document intent: read, attach, or both."""
    query = update.callback_query
    await query.answer()
    mode = (query.data or "").split("|", 1)[1] if "|" in (query.data or "") else ""
    pending_doc = context.user_data.pop("_pending_doc", None) or {}
    pending_doc_context = context.user_data.pop("_pending_doc_context", "")
    file_path = pending_doc.get("path")
    file_name = pending_doc.get("name") or "document"

    if mode not in {"info", "attach", "both"} or not file_path or not os.path.exists(file_path):
        await query.edit_message_text(
            "That document choice has expired. Send the file again when you're ready."
        )
        return AWAIT_CASE_INPUT

    if mode in {"attach", "both"}:
        context.user_data["attachment_path"] = file_path
        context.user_data["attachment_name"] = file_name

    if mode == "attach":
        await query.edit_message_text(
            f"📎 *{file_name}* will be attached to the Kaizen draft.\n\n"
            "Now send the anonymised case details you want drafted.",
            parse_mode="Markdown",
        )
        return AWAIT_CASE_INPUT

    await query.edit_message_text(f"📄 Reading *{file_name}*…", parse_mode="Markdown")
    try:
        case_text = await extract_from_document(file_path)
    except Exception as exc:
        logger.error("Document extraction failed after DOCUSE=%s: %s", mode, exc, exc_info=True)
        case_text = ""

    if not case_text or not case_text.strip():
        if pending_doc_context.strip():
            case_text = pending_doc_context.strip()
            if mode == "info":
                try:
                    os.unlink(file_path)
                except OSError:
                    pass
            context.user_data["document_name"] = file_name
            await query.edit_message_text(CAPTURED_ACK, parse_mode="Markdown")
            _track_latest_message(context, query.message)
            return await _process_case_text(query.message, context, update.effective_user.id, case_text, "document")
        if mode == "both":
            await query.edit_message_text(
                f"📎 *{file_name}* will still be attached, but I couldn't read useful text from it.\n\n"
                "Send the anonymised case details in text and I'll draft from that.",
                parse_mode="Markdown",
            )
            return AWAIT_CASE_INPUT
        await query.edit_message_text(
            f"⚠️ Couldn't extract text from *{file_name}*. "
            "The file may be scanned or password-protected.\n\n"
            "Send the case details in text, or upload again and choose Attach only.",
            parse_mode="Markdown",
        )
        return AWAIT_CASE_INPUT

    max_chars = 15000
    if len(case_text) > max_chars:
        case_text = case_text[:max_chars] + "\n\n[Document truncated — using first 15,000 characters]"

    if pending_doc_context.strip():
        case_text = f"{pending_doc_context.strip()}\n\nDocument text:\n{case_text}".strip()

    if mode == "info":
        try:
            os.unlink(file_path)
        except OSError:
            pass
    context.user_data["document_name"] = file_name

    if _gathering_enabled(context) and not (context.user_data.get("chosen_form") and context.user_data.get("awaiting_detail")):
        if not _gathering_case_active(context):
            existing_attachment_path = context.user_data.get("attachment_path")
            existing_attachment_name = context.user_data.get("attachment_name")
            _clear_case_review_state(context, keep_case=False)
            if existing_attachment_path:
                context.user_data["attachment_path"] = existing_attachment_path
                context.user_data["attachment_name"] = existing_attachment_name
        _append_gathering_case(context, case_text, "document")
        reply_text, reply_markup = _gathering_reply(context)
        await query.edit_message_text(reply_text, reply_markup=reply_markup)
        context.user_data["gathering_msg_id"] = query.message.message_id
        context.user_data["gathering_chat_id"] = query.message.chat_id
        context.user_data["_gathering_ack_used"] = True
        return AWAIT_GATHERING

    await query.edit_message_text(CAPTURED_ACK, parse_mode="Markdown")
    _track_latest_message(context, query.message)
    return await _process_case_text(query.message, context, update.effective_user.id, case_text, "document")


# === IMPLICIT CASE ACCUMULATION ===

async def _accumulate_and_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE, new_text: str) -> int:
    """Append new_text to accumulated case context and re-extract the template."""
    user_id = update.effective_user.id
    chosen_form = context.user_data.get("chosen_form", "")
    initial_case = context.user_data.get("case_text", "")

    if not chosen_form or not initial_case:
        return await handle_case_input(update, context)

    # Track accumulation additions
    additions = context.user_data.get("accumulation_additions", [])
    additions.append(new_text)
    context.user_data["accumulation_additions"] = additions
    context.user_data["accumulating_case"] = True

    # Combine all inputs
    combined = combine_case_inputs(initial_case, additions)
    context.user_data["case_text"] = combined

    form_name = _form_display_name(chosen_form)
    await update.effective_chat.send_action(constants.ChatAction.TYPING)
    ack = await _send_latest_message(
        update.message,
        context,
        f"🧩 Updating {form_name} draft…",
    )

    try:
        draft = await _analyse_selected_form(context, user_id, combined, chosen_form)
    except asyncio.TimeoutError:
        await ack.edit_text("⏳ Template review timed out. Please try again.")
        return AWAIT_TEMPLATE_REVIEW
    except Exception as exc:
        logger.error("Accumulation refresh failed for %s: %s", chosen_form, exc, exc_info=True)
        await ack.edit_text("⚠️ Could not refresh that template.", reply_markup=_KB_CANCEL)
        return AWAIT_TEMPLATE_REVIEW

    if not _draft_has_useful_content(draft, chosen_form):
        return await _ask_for_more_detail_before_draft(ack, context)

    context.user_data.pop("accumulating_case", None)
    context.user_data.pop("accumulation_additions", None)
    return await _show_draft_review(ack, context, draft, chosen_form)


async def _regenerate_active_draft_with_feedback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    feedback_text: str,
    *,
    append_to_case: bool = False,
    input_source: str = "text",
) -> int:
    """Regenerate an active approval draft using extra text or extracted media."""
    msg = update.message or update.callback_query.message
    draft = _load_draft(context)
    case_text = context.user_data.get("case_text", "")
    if not draft or not case_text:
        return await handle_case_input(update, context)

    _track_funnel_event(context, "refine_reply_received", source=input_source, has_draft=True)
    feedback_text = (feedback_text or "").strip()
    if not feedback_text:
        await msg.reply_text("I couldn't read any useful extra detail from that. Try again with text, voice, image, or document.")
        return AWAIT_APPROVAL

    if append_to_case:
        case_text = combine_case_inputs(case_text, [feedback_text])
        context.user_data["case_text"] = case_text
        previous_source = context.user_data.get("case_input_source", "text")
        context.user_data["case_input_source"] = previous_source if previous_source == input_source else "mixed"

    ack = await msg.reply_text("✏️ Regenerating draft with your extra information…")
    try:
        form_type = draft.form_type if isinstance(draft, FormDraft) else "CBD"
        current_draft_text = _format_draft_preview(draft)
        vp = get_voice_profile(update.effective_user.id) or ""

        if form_type == "CBD":
            updated = await asyncio.wait_for(
                extract_cbd_data(
                    case_text,
                    edit_feedback=feedback_text,
                    current_draft=current_draft_text,
                    voice_profile_json=vp,
                    input_source=context.user_data.get("case_input_source", "text"),
                ),
                timeout=45,
            )
        else:
            updated = await asyncio.wait_for(
                extract_form_data(
                    case_text,
                    form_type,
                    edit_feedback=feedback_text,
                    current_draft=current_draft_text,
                    voice_profile_json=vp,
                    input_source=context.user_data.get("case_input_source", "text"),
                ),
                timeout=45,
            )
        _store_draft(context, updated)
    except asyncio.TimeoutError:
        await ack.edit_text(
            "⏳ That took too long. Your previous draft is still ready above — try again or use the buttons.",
        )
        return AWAIT_APPROVAL
    except Exception as exc:
        logger.error("Inline feedback regeneration failed: %s", exc, exc_info=True)
        await ack.edit_text(
            "⚠️ Couldn't regenerate the draft with that feedback. Your previous draft is still ready above.",
        )
        return AWAIT_APPROVAL

    preview = _format_draft_preview(updated)
    await _safe_edit_text(
        ack,
        preview + _REPLY_HINT_SUFFIX,
        reply_markup=_active_draft_keyboard(context),
        parse_mode="Markdown",
    )
    return AWAIT_APPROVAL


async def handle_template_review_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle voice, photo, video, or document during AWAIT_TEMPLATE_REVIEW — implicit accumulation."""
    msg = update.message
    extracted_text = None

    voice_media = _voice_media_from_message(msg)
    if voice_media:
        ack = await msg.reply_text("🎙️ Transcribing voice note…")
        tmp_path = None
        try:
            voice_file = await voice_media.get_file()
            with tempfile.NamedTemporaryFile(suffix=_voice_media_suffix(voice_media), delete=False) as tmp:
                tmp_path = tmp.name
                await voice_file.download_to_drive(tmp_path)
                extracted_text = await transcribe_voice(tmp_path)
            await ack.edit_text("🎙️ Got it — updating template…")
        except Exception:
            await ack.edit_text("⚠️ Couldn't transcribe voice note. Try again or send text.")
            return AWAIT_TEMPLATE_REVIEW
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    elif msg.photo:
        ack = await msg.reply_text("📷 Reading image…")
        tmp_path = None
        try:
            photo = msg.photo[-1]
            photo_file = await photo.get_file()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
                await photo_file.download_to_drive(tmp_path)
                extracted_text = await extract_from_image(tmp_path)
            if extracted_text and extracted_text.strip() == "NOT_CLINICAL":
                extracted_text = None
                await ack.edit_text("That image doesn't look clinical — send text or another photo.")
                return AWAIT_TEMPLATE_REVIEW
            await ack.edit_text("📷 Got it — updating template…")
        except Exception:
            await ack.edit_text("⚠️ Couldn't read image. Try again or send text.")
            return AWAIT_TEMPLATE_REVIEW
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    elif msg.video:
        ack = await msg.reply_text("🎬 Extracting audio from video…")
        tmp_path = None
        try:
            video_file = await msg.video.get_file()
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name
                await video_file.download_to_drive(tmp_path)
                extracted_text = await transcribe_voice(tmp_path)
            await ack.edit_text("🎬 Got it — updating template…")
        except Exception:
            await ack.edit_text("⚠️ Couldn't extract audio from video. Try again or send text.")
            return AWAIT_TEMPLATE_REVIEW
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    elif msg.document:
        doc = msg.document
        file_name = doc.file_name or "document"
        if not is_supported_document(file_name):
            await msg.reply_text("Got it — added to your case.")
            return AWAIT_TEMPLATE_REVIEW
        ack = await msg.reply_text(f"📄 Reading *{file_name}*…", parse_mode="Markdown")
        tmp_path = None
        try:
            doc_file = await doc.get_file()
            suffix = os.path.splitext(file_name)[1] or ".tmp"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name
                await doc_file.download_to_drive(tmp_path)
                extracted_text = await extract_from_document(tmp_path)
            if not extracted_text or not extracted_text.strip():
                await ack.edit_text("⚠️ Couldn't extract text from that file.")
                return AWAIT_TEMPLATE_REVIEW
            max_chars = 15000
            if len(extracted_text) > max_chars:
                extracted_text = extracted_text[:max_chars]
            await ack.edit_text(f"📄 Got it — updating template…")
        except Exception:
            await ack.edit_text("⚠️ Couldn't read that file. Try again or send text.")
            return AWAIT_TEMPLATE_REVIEW
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    else:
        await msg.reply_text("Got it — added to your case.")
        return AWAIT_TEMPLATE_REVIEW

    if not extracted_text or not extracted_text.strip():
        return AWAIT_TEMPLATE_REVIEW

    return await _accumulate_and_refresh(update, context, extracted_text)


async def handle_approval_media_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle voice, photo, video, or document replies once a draft is awaiting approval."""
    msg = update.message
    extracted_text = None
    input_source = "media"

    voice_media = _voice_media_from_message(msg)
    if voice_media:
        input_source = "voice"
        ack = await msg.reply_text("🎙️ Transcribing voice note…")
        tmp_path = None
        try:
            voice_file = await voice_media.get_file()
            with tempfile.NamedTemporaryFile(suffix=_voice_media_suffix(voice_media), delete=False) as tmp:
                tmp_path = tmp.name
                await voice_file.download_to_drive(tmp_path)
                extracted_text = await transcribe_voice(tmp_path)
            await ack.edit_text("🎙️ Got it — updating draft…")
        except Exception:
            await ack.edit_text("⚠️ Couldn't transcribe voice note. Type your feedback instead.")
            return AWAIT_APPROVAL
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    elif msg.photo:
        input_source = "photo"
        ack = await msg.reply_text("📷 Reading image…")
        tmp_path = None
        try:
            photo = msg.photo[-1]
            photo_file = await photo.get_file()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
                await photo_file.download_to_drive(tmp_path)
                extracted_text = await extract_from_image(tmp_path)
            if extracted_text and extracted_text.strip() == "NOT_CLINICAL":
                await ack.edit_text("That image doesn't look clinical — send text or another photo.")
                return AWAIT_APPROVAL
            caption = (msg.caption or "").strip()
            if caption:
                extracted_text = combine_case_inputs(caption, [extracted_text or ""])
            await ack.edit_text("📷 Got it — updating draft…")
        except Exception:
            await ack.edit_text("⚠️ Couldn't read image. Type your feedback instead.")
            return AWAIT_APPROVAL
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    elif msg.video:
        input_source = "video"
        ack = await msg.reply_text("🎬 Extracting audio from video…")
        tmp_path = None
        try:
            video_file = await msg.video.get_file()
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name
                await video_file.download_to_drive(tmp_path)
                extracted_text = await transcribe_voice(tmp_path)
            await ack.edit_text("🎬 Got it — updating draft…")
        except Exception:
            await ack.edit_text("⚠️ Couldn't extract audio from video. Type your feedback instead.")
            return AWAIT_APPROVAL
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    elif msg.document:
        input_source = "document"
        doc = msg.document
        file_name = doc.file_name or "document"
        if not is_supported_document(file_name):
            await msg.reply_text("I can only read PDF, PowerPoint, Word, and text files here.")
            return AWAIT_APPROVAL
        ack = await msg.reply_text(f"📄 Reading *{file_name}*…", parse_mode="Markdown")
        tmp_path = None
        try:
            doc_file = await doc.get_file()
            suffix = os.path.splitext(file_name)[1] or ".tmp"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name
                await doc_file.download_to_drive(tmp_path)
                extracted_text = await extract_from_document(tmp_path)
            if not extracted_text or not extracted_text.strip():
                await ack.edit_text("⚠️ Couldn't extract text from that file.")
                return AWAIT_APPROVAL
            max_chars = 15000
            if len(extracted_text) > max_chars:
                extracted_text = extracted_text[:max_chars]
            await ack.edit_text("📄 Got it — updating draft…")
        except Exception:
            await ack.edit_text("⚠️ Couldn't read that file. Type your feedback instead.")
            return AWAIT_APPROVAL
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    else:
        await msg.reply_text("Send text, voice, image, or a document with the extra detail.")
        return AWAIT_APPROVAL

    if _has_retryable_failed_filing_draft(context):
        return await _show_failed_filing_input_gate(
            ack,
            context,
            extracted_text or "",
            edit=True,
        )

    return await _regenerate_active_draft_with_feedback(
        update,
        context,
        extracted_text or "",
        append_to_case=True,
        input_source=input_source,
    )


# === TEMPLATE REVIEW TEXT HANDLER ===

async def handle_template_review_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text during AWAIT_TEMPLATE_REVIEW — implicit accumulation with intent check."""
    if context.user_data.pop("post_reset", False):
        return await handle_case_input(update, context)

    raw_text = update.message.text.strip()
    case_text = context.user_data.get("case_text", "")
    current_form = context.user_data.get("chosen_form", "")

    try:
        intent = await classify_intent(raw_text, case_context=case_text)
    except Exception:
        intent = "edit_detail"

    form_name = _form_display_name(current_form) if current_form else "your form"

    if intent == "chitchat":
        await update.message.reply_text(
            f"Still here! Your {form_name} template is ready — send more detail or tap below."
        )
        return AWAIT_TEMPLATE_REVIEW

    elif intent == "question_general":
        try:
            answer = style_grounded_answer(await answer_question(raw_text))
            await update.message.reply_text(
                f"{answer}\n\n💬 Your case is still open — send more detail or tap Cancel."
            )
        except Exception:
            await update.message.reply_text(
                f"💬 Your case is still open — send more detail or tap below."
            )
        return AWAIT_TEMPLATE_REVIEW

    elif intent == "question_about_case":
        # User doubts the form choice — re-run recommendations and show the SAME suggestion UI
        await update.effective_chat.send_action(constants.ChatAction.TYPING)

        try:
            training_level = get_training_level(update.effective_user.id)
            allowed_forms = _allowed_forms_for_training_level(training_level)
            recommendations = await asyncio.wait_for(
                recommend_form_types(
                    case_text,
                    input_source=context.user_data.get("case_input_source", "text"),
                ),
                timeout=30,
            )
            excluded_form = _normalise_form_type(context.user_data.get("excluded_form_type", ""))
            recommendations = _filter_recommendations_for_allowed_forms(
                recommendations,
                allowed_forms,
                case_text,
                excluded_form=excluded_form,
            )

            if recommendations:
                prompt_text = _build_form_recommendation_text(
                    recommendations,
                    input_source=context.user_data.get("case_input_source"),
                    curriculum=_effective_curriculum(update.effective_user.id),
                    opening="📋 Other options that fit:",
                    closing=f"Pick one to switch, or keep going with {form_name}.",
                )
                context.user_data["form_recommendations"] = recommendations
                context.user_data["form_recommendations_text"] = prompt_text
                await update.message.reply_text(
                    prompt_text,
                    reply_markup=_build_form_choice_keyboard(recommendations, curriculum=_effective_curriculum(update.effective_user.id)),
                )
                return AWAIT_FORM_CHOICE
            else:
                await update.message.reply_text(
                    f"Based on your case, {form_name} is still the best fit. Tap Show me the draft to carry on, or Cancel to start again."
                )
                return AWAIT_TEMPLATE_REVIEW
        except Exception:
            await update.message.reply_text(
                f"Happy to help — what specifically feels off about {form_name} for this case?"
            )
            return AWAIT_TEMPLATE_REVIEW

    elif intent == "new_case":
        has_draft = bool(context.user_data.get("current_draft") or context.user_data.get("case_text"))
        if has_draft:
            context.user_data["pending_new_case_text"] = raw_text
            prompt_text = "Looks like a new case — start fresh or fold it into the current one?"
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🆕 New case", callback_data="CASE|new"),
                InlineKeyboardButton("✏️ Improve current", callback_data="CASE|improve"),
            ]])
            await _edit_last_bot_msg(
                context,
                update.effective_chat.id,
                prompt_text,
                reply_markup=markup,
            )
            return AWAIT_TEMPLATE_REVIEW
        else:
            context.user_data.clear()
            return await handle_case_input(update, context)

    else:
        # edit_detail — implicit accumulation: append and re-extract
        return await _accumulate_and_refresh(update, context, raw_text)


# === EDIT VALUE WITH INTENT HANDLER ===

async def handle_edit_value_with_intent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text during AWAIT_EDIT_VALUE — check intent for short non-clinical messages."""
    msg = update.message

    # Non-text messages (voice, photo, document) — pass straight through
    if not msg.text:
        return await handle_edit_value(update, context)

    raw_text = msg.text.strip()
    word_count = len(raw_text.split())

    # Long messages are almost certainly real edit content — skip intent check
    if word_count >= 6:
        return await handle_edit_value(update, context)

    try:
        intent = await classify_intent(raw_text)
    except Exception:
        intent = "edit_detail"  # default to treating as edit content

    field = context.user_data.get("edit_field", "this field")

    if intent == "chitchat":
        await msg.reply_text(
            f"Still in edit mode — send me the new value for *{field}*, or tap Cancel to exit.",
            parse_mode="Markdown"
        )
        return AWAIT_EDIT_VALUE

    if intent in ("question_general", "question_about_case"):
        try:
            case_text = context.user_data.get("case_text", "")
            answer = style_grounded_answer(
                sanitize_internal_form_codes(await answer_question(raw_text, case_context=case_text))
            )
            await msg.reply_text(
                f"{answer}\n\nStill in edit mode — send your new value for *{field}* when ready.",
                parse_mode="Markdown"
            )
        except Exception:
            await msg.reply_text(
                f"Still in edit mode — send me the new value for *{field}*, or tap Cancel to exit.",
                parse_mode="Markdown"
            )
        return AWAIT_EDIT_VALUE

    # edit_detail / new_case — treat as edit content
    return await handle_edit_value(update, context)


# === CASE INPUT HANDLER ===

async def handle_case_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text, voice, photo, or document input for case description."""
    user_id = update.effective_user.id
    _start_conversational_router_shadow(update, "handle_case_input")
    attachment_path_to_save = None
    attachment_name_to_save = None

    # If the user just tapped "Custom range" in the /unsigned picker, the next
    # text reply is their date range — intercept it before treating as a case.
    if context.user_data.get("awaiting_unsigned_range") and update.message and update.message.text:
        text = update.message.text.strip()
        parsed = _parse_unsigned_range(text)
        if not parsed:
            await update.message.reply_text(
                "❌ Couldn't read that as a date range. Try again, like `01/04/2025 to 31/03/2026`.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="UNSIGNED|cancel")],
                ]),
            )
            return ConversationHandler.END
        from_date, to_date = parsed
        if from_date > to_date:
            from_date, to_date = to_date, from_date
        context.user_data.pop("awaiting_unsigned_range", None)
        label = f"{from_date.strftime('%d/%m/%Y')} – {to_date.strftime('%d/%m/%Y')}"
        await _run_unsigned_scan(update.message, context, user_id, from_date, to_date, label)
        return ConversationHandler.END

    # Reuse-last-case intent: "use the same case for DOPS", "file as Mini-CEX
    # too", etc. We must route this BEFORE extraction — otherwise the LLM
    # would try to extract clinical fields from the instruction text and
    # hallucinate content. See feedback-no-fabrication memory.
    if update.message and update.message.text and context.user_data.get("last_filed_case_text"):
        raw_text = update.message.text.strip()
        if is_reuse_request(raw_text):
            return await _handle_reuse_request(update, context, user_id, raw_text)

    # Clear post_reset flag if set (belt and braces)
    context.user_data.pop("post_reset", None)

    # Clear stale status state from previous sessions, but keep the active
    # bundle message editable while the user is sending multiple files.
    if not context.user_data.get("pending_case_bundle"):
        context.user_data.pop("status_msg_id", None)
        context.user_data.pop("status_msg_chat", None)
        context.user_data.pop("last_bot_msg_id", None)
        context.user_data.pop("last_bot_chat_id", None)

    # Check credentials
    if not has_credentials(user_id):
        if update.message and update.message.text:
            email = _extract_setup_email_candidate(update.message.text)
            if email:
                context.user_data.clear()
                context.user_data["kaizen_reconnect_hint"] = True
                return await _prompt_kaizen_password(update, context, email)
        context.user_data.clear()
        await update.message.reply_text(
            "Connect your Kaizen account first.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup")]
            ])
        )
        return ConversationHandler.END

    # Tier enforcement — check usage limit
    allowed, used, limit, tier = await check_can_file(user_id)
    if not allowed:
        limit_msg = await update.message.reply_text(
            f"📊 You've used all {limit} free cases this month.\n\n"
            "Upgrade to Portfolio Guru Unlimited (£9.99/mo) for unlimited filing and premium features — or wait until next month.",
            reply_markup=InlineKeyboardMarkup(_upgrade_buttons(tier)),
        )
        # Store as upgrade flow anchor so the upgrade button edits this message in place
        context.user_data["_flow_anchor_upgrade"] = (limit_msg.chat_id, limit_msg.message_id)
        return ConversationHandler.END
    context.user_data["user_tier"] = tier

    # Determine input type and extract text
    case_text = None

    if update.message.text:
        raw_text = update.message.text.strip()
        if _is_text_filing_approval(raw_text):
            if _load_draft(context):
                return await handle_approval_approve(update, context)
            if _restore_retryable_draft(context):
                return await handle_approval_approve(update, context)
            if context.user_data.get("last_filing_status") == "success":
                form_name = context.user_data.get("last_filing_form_name", "that draft")
                await update.message.reply_text(
                    f"✅ {form_name} was already saved to Kaizen as a draft. Send a new case when you're ready."
                )
                return ConversationHandler.END

        if _is_recent_filing_status_question(raw_text) and context.user_data.get("last_filing_status"):
            form_name = context.user_data.get("last_filing_form_name", "your last draft")
            status = context.user_data.get("last_filing_status")
            if status == "success":
                await update.message.reply_text(
                    f"✅ Yes — {form_name} was saved to Kaizen as a draft."
                )
            elif status == "partial":
                await update.message.reply_text(
                    f"⚠️ {form_name} was partly filed, but needs checking in Kaizen before you rely on it."
                )
            else:
                await update.message.reply_text(
                    f"❌ {form_name} did not complete. Try again from the latest draft or start fresh."
                )
            return ConversationHandler.END

        if context.user_data.get("pending_case_bundle"):
            if _looks_like_new_case_start(raw_text) and _pending_case_bundle_is_stale(context):
                _clear_pending_case_bundle(context)
                context.user_data.pop("last_bot_msg_id", None)
                context.user_data.pop("last_bot_chat_id", None)
                context.user_data.pop("status_msg_id", None)
                context.user_data.pop("status_msg_chat", None)
            else:
                if _is_case_bundle_done(raw_text):
                    case_text, input_source = _combined_pending_case_bundle(context)
                    _clear_pending_case_bundle(context)
                    if not case_text:
                        await update.message.reply_text("⚠️ I was waiting for the rest of the case, but nothing readable came through.\n\nSend the case again when ready.")
                        return AWAIT_CASE_INPUT
                    ack = await update.message.reply_text(CAPTURED_ACK, parse_mode="Markdown")
                    _track_latest_message(context, ack)
                    return await _process_case_text(update.message, context, user_id, case_text, input_source)

                # If the user already sent images in this bundle (count 1+ photos),
                # new text is likely extra detail — auto-release without needing "done"
                if _pending_case_bundle_source_count(context, "photo") > 0:
                    _append_pending_case_bundle(context, raw_text, "text")
                    case_text, input_source = _combined_pending_case_bundle(context)
                    _clear_pending_case_bundle(context)
                    ack = await update.message.reply_text(CAPTURED_ACK, parse_mode="Markdown")
                    _track_latest_message(context, ack)
                    return await _process_case_text(update.message, context, user_id, case_text, input_source)

                _append_pending_case_bundle(context, raw_text, "text")
                await _send_pending_bundle_status(
                    update.message,
                    context,
                    "Added. I’ll keep waiting — send `done` when you’ve shared everything.",
                    parse_mode="Markdown",
                )
                return AWAIT_CASE_INPUT

        if not (context.user_data.get("awaiting_detail") and context.user_data.get("chosen_form")) and _is_waiting_for_media_request(raw_text):
            _append_pending_case_bundle(context, raw_text, "text")
            ack = await update.message.reply_text(
                "Got it — I’ll wait for the images/files before drafting. Send `done` when you’ve shared everything.",
                parse_mode="Markdown",
            )
            _track_pending_bundle_message(context, ack)
            return AWAIT_CASE_INPUT

        if context.user_data.get("awaiting_detail") and context.user_data.get("chosen_form"):
            case_text = raw_text
        else:
            words_lower = raw_text.lower()
            word_count = len(raw_text.split())

            if _is_submit_inquiry(raw_text):
                await update.message.reply_text(
                    _DRAFT_ONLY_CLARIFICATION,
                    parse_mode="Markdown",
                )
                return ConversationHandler.END

            # Fast-path: question patterns (before clinical heuristic)
            # Catches "Do you have...", "Can you...", "What about...", "Is X mapped?"
            _QUESTION_PATTERNS = [
                r"^do you ", r"^do we ", r"^can you ", r"^can we ",
                r"^is ", r"^are ", r"^what ", r"^how ", r"^why ",
                r"^where ", r"^when ", r"^who ", r"^which ",
                r"\bmapped\b", r"\bsupported\b", r"\bavailable\b",
                r"\bdo you have\b", r"\bdo we have\b",
            ]
            import re
            is_question_pattern = any(re.search(p, words_lower) for p in _QUESTION_PATTERNS)

            _CLINICAL_KEYWORDS = {"patient", "presented", "diagnosed", "examined", "management",
                                  "symptoms", "clinical", "assessment", "treatment", "referred",
                                  "history", "examination", "investigation", "procedure", "resuscitation",
                                  "chest pain", "shortness of breath", "abdominal", "fracture", "suture",
                                  "intubation", "cannulation", "triage", "observations", "bloods"}
            clinical_hits = sum(1 for kw in _CLINICAL_KEYWORDS if kw in words_lower)

            # Menu intent router: short non-clinical text may be a navigation
            # command ("stats", "settings", "how many cases this month") rather
            # than a case or a generic question. Runs before the question-pattern
            # fast-path so "how many ..." routes to /status, not answer_question.
            # Gated on menu-ish keywords so short clinical notes ("stitched lac")
            # don't pay 2-3s of LLM latency for nothing.
            _MENU_HINTS = (
                "setting", "stat", "stats", "help", "menu", "usage", "limit",
                "password", "credential", "login", "kaizen account", "reconnect",
                "how many", "how much", "what's my", "whats my", "show me", "show my",
                "this month", "this week", "tier", "upgrade", "plan", "subscription",
                "voice profile", "curriculum", "training level", "portfolio profile",
            )
            looks_menu_ish = any(hint in words_lower for hint in _MENU_HINTS)
            nav_intent = None
            if looks_menu_ish and 1 <= word_count < 14 and clinical_hits == 0:
                try:
                    await update.effective_chat.send_action(constants.ChatAction.TYPING)
                    nav_intent = await classify_menu_intent(raw_text)
                except Exception:
                    nav_intent = None

            if nav_intent == "show_stats":
                context.user_data.clear()
                tier = await get_user_tier(user_id)
                try:
                    used = await get_cases_this_month(user_id)
                except Exception:
                    used = 0
                stats_text, stats_kb = _settings_view_components(
                    user_id,
                    tier=tier,
                    used=used,
                    connected=has_credentials(user_id),
                    is_beta=await is_beta_tester(user_id),
                    kaizen_sync=await _safe_kaizen_sync_status(user_id),
                )
                await update.message.reply_text(stats_text, reply_markup=stats_kb)
                return ConversationHandler.END
            if nav_intent == "show_help":
                context.user_data.clear()
                return await help_command(update, context)
            if nav_intent == "open_settings":
                context.user_data.clear()
                tier = await get_user_tier(user_id)
                try:
                    used = await get_cases_this_month(user_id)
                except Exception:
                    used = 0
                settings_text, settings_kb = _settings_view_components(
                    user_id,
                    tier=tier,
                    used=used,
                    connected=has_credentials(user_id),
                    is_beta=await is_beta_tester(user_id),
                    kaizen_sync=await _safe_kaizen_sync_status(user_id),
                )
                await update.message.reply_text(settings_text, reply_markup=settings_kb)
                return ConversationHandler.END
            if nav_intent == "manage_credentials":
                context.user_data.clear()
                await update.message.reply_text(
                    "Want to update your Kaizen login?",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup")]
                    ]),
                )
                return ConversationHandler.END

            pre_capture_route = _standalone_pre_capture_route(raw_text)
            if pre_capture_route == "safe_redirect":
                await update.message.reply_text(_standalone_safe_redirect_text(raw_text))
                return ConversationHandler.END
            if pre_capture_route == "answer":
                try:
                    answer = style_grounded_answer(await answer_question(raw_text))
                    await update.message.reply_text(answer)
                except Exception:
                    await update.message.reply_text(
                        style_grounded_answer(
                            "I help draft portfolio evidence from anonymised text, voice, photos, or documents. Send a case when you want me to start."
                        )
                    )
                return ConversationHandler.END

            if is_question_pattern and word_count < 15:
                # Short question — answer directly without classify
                intent = "question"
            else:
                # Fast heuristic: long clinical-sounding messages skip classify entirely
                # Saves ~3-5s of Gemini latency for obvious cases
                if word_count > 30 and clinical_hits >= 2:
                    intent = "case"
                elif word_count < 8 and clinical_hits == 0:
                    intent = "chitchat" if word_count < 4 else "case"
                else:
                    await update.effective_chat.send_action(constants.ChatAction.TYPING)
                    try:
                        intent = await classify_intent(raw_text)
                    except Exception:
                        intent = "case"

            if intent == "chitchat":
                await update.message.reply_text(
                    "Hey! Ready when you are. Send me a clinical case and I'll draft it for your portfolio."
                )
                return ConversationHandler.END

            if intent in ("question", "question_general"):
                try:
                    answer = style_grounded_answer(await answer_question(raw_text))
                    await update.message.reply_text(answer)
                except Exception:
                    await update.message.reply_text(
                        "I help you file clinical cases to your Kaizen e-portfolio. "
                        "Send me a case description by text, voice note, photo, or document."
                    )
                return ConversationHandler.END

            case_text = raw_text

    elif _voice_media_from_message(update.message):
        voice_media = _voice_media_from_message(update.message)
        ack = await update.message.reply_text("🎙️ Transcribing voice note…")
        tmp_path = None
        try:
            voice_file = await voice_media.get_file()
            with tempfile.NamedTemporaryFile(suffix=_voice_media_suffix(voice_media), delete=False) as tmp:
                tmp_path = tmp.name
                await voice_file.download_to_drive(tmp_path)
                case_text = await transcribe_voice(tmp_path)
            if _gathering_enabled(context) and not (context.user_data.get("chosen_form") and context.user_data.get("awaiting_detail")):
                reply_text, reply_markup = _gathering_reply(context)
                await ack.edit_text(reply_text, reply_markup=reply_markup)
                context.user_data["gathering_msg_id"] = ack.message_id
                context.user_data["gathering_chat_id"] = ack.chat_id
                context.user_data["_gathering_ack_used"] = True
            else:
                await ack.edit_text("🎙️ Voice note read. Finding matching forms…")
            _track_latest_message(context, ack)
        except Exception as e:
            await ack.edit_text(
                "⚠️ Couldn't transcribe voice note. Try again or describe the case in text.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="ACTION|cancel")],
                ]),
            )
            # Stay in AWAIT_CASE_INPUT so the next voice/text/photo continues the flow
            return AWAIT_CASE_INPUT
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    elif update.message.photo:
        bundling = bool(context.user_data.get("pending_case_bundle"))
        ack_text = "📷 Reading images…" if bundling else "📷 Reading image…"
        ack = await _send_pending_bundle_status(update.message, context, ack_text) if bundling else await update.message.reply_text(ack_text)
        typing_stop = asyncio.Event()
        typing_task = asyncio.create_task(_typing_until(update.effective_chat, typing_stop))
        ocr_done = asyncio.Event()
        progress_task = asyncio.create_task(
            _run_image_progress(ack, ocr_done=ocr_done)
        )
        tmp_path = None
        try:
            photo = update.message.photo[-1]
            photo_file = await photo.get_file()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
                await photo_file.download_to_drive(tmp_path)
                case_text = await extract_from_image(tmp_path)
            ocr_done.set()
            progress_task.cancel()
            if case_text.strip() == "NOT_CLINICAL":
                await ack.edit_text("This image doesn't look like a clinical case. Send a text description or a photo of clinical notes/findings.")
                return ConversationHandler.END
            caption = (update.message.caption or "").strip()
            if caption:
                case_text = f"{caption}\n\n{case_text}".strip()
            if bundling:
                # User said they'd send images — they just did. Auto-release.
                _append_pending_case_bundle(context, case_text, "photo")
                case_text, input_source = _combined_pending_case_bundle(context)
                _clear_pending_case_bundle(context)
                await ack.edit_text("📷 Images read. Finding matching forms…")
                _track_latest_message(context, ack)
                return await _process_case_text(update.message, context, user_id, case_text, input_source)
            else:
                if _gathering_enabled(context) and not (context.user_data.get("chosen_form") and context.user_data.get("awaiting_detail")):
                    reply_text, reply_markup = _gathering_reply(context)
                    await ack.edit_text(reply_text, reply_markup=reply_markup)
                    context.user_data["gathering_msg_id"] = ack.message_id
                    context.user_data["gathering_chat_id"] = ack.chat_id
                    context.user_data["_gathering_ack_used"] = True
                else:
                    await ack.edit_text("📷 Image read. Finding matching forms…")
            _track_latest_message(context, ack)
        except Exception as e:
            ocr_done.set()
            progress_task.cancel()
            context.user_data.clear()
            await ack.edit_text("⚠️ Couldn't read image. Try a clearer photo or describe the case in text.")
            return ConversationHandler.END
        finally:
            typing_stop.set()
            typing_task.cancel()
            progress_task.cancel()
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    elif update.message.document:
        # Handle document files (PDF, PPTX, DOCX)
        doc = update.message.document
        file_name = doc.file_name or "document"

        if not is_supported_document(file_name):
            await update.message.reply_text(
                f"📄 *{file_name}*\n\nI can read PDF, PowerPoint (.pptx), Word (.docx), and text files. "
                "This file type isn't supported yet.",
                parse_mode="Markdown"
            )
            return ConversationHandler.END

        await _delete_previous_gathering_message(context)
        ack = await update.message.reply_text(f"📄 Receiving *{file_name}*…", parse_mode="Markdown")
        tmp_path = None
        try:
            import shutil
            doc_file = await doc.get_file()
            suffix = os.path.splitext(file_name)[1] or ".tmp"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name
                await doc_file.download_to_drive(tmp_path)

            # Cache the file before extraction so the intent callback can use it.
            cache_dir = os.path.join(tempfile.gettempdir(), "portfolio_guru_cache")
            os.makedirs(cache_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=suffix, delete=False) as cached_file:
                cached_path = cached_file.name
            shutil.copy2(tmp_path, cached_path)
        except Exception as e:
            logger.error(f"Document download failed: {e}", exc_info=True)
            context.user_data.clear()
            await ack.edit_text(
                f"⚠️ Couldn't receive *{file_name}*. Try again or describe the case in text.",
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        context.user_data["_pending_doc"] = {"path": cached_path, "name": file_name}
        await ack.edit_text(
            f"📄 *{file_name}* — how would you like to use this document?",
            reply_markup=_build_doc_intent_keyboard(),
            parse_mode="Markdown",
        )
        _track_latest_message(context, ack)
        return AWAIT_DOC_INTENT

    if not case_text:
        context.user_data.clear()
        await update.message.reply_text("💬 Send a text message, voice note, photo, or document.")
        return ConversationHandler.END

    # If the user is refining a chosen template, keep the original wording and append the new detail.
    if context.user_data.get("awaiting_detail") and context.user_data.get("chosen_form"):
        previous_case = context.user_data.get("case_text", "").strip()
        if previous_case:
            case_text = f"{previous_case}\n\n{case_text}".strip()

    if update.message.photo:
        input_source = "photo"
    elif _voice_media_from_message(update.message):
        input_source = "voice"
    elif update.message.document:
        input_source = "document"
    else:
        input_source = "text"

    if context.user_data.get("pending_case_bundle"):
        _append_pending_case_bundle(context, case_text, input_source)
        if input_source != "photo":
            await _send_pending_bundle_status(
                update.message,
                context,
                "Added. I’ll keep waiting — send `done` when you’ve shared everything.",
                parse_mode="Markdown",
            )
        return AWAIT_CASE_INPUT

    chosen_form = context.user_data.get("chosen_form")
    if _gathering_enabled(context) and not (chosen_form and context.user_data.get("awaiting_detail")):
        existing_attachment_path = context.user_data.get("attachment_path")
        existing_attachment_name = context.user_data.get("attachment_name")
        if not _gathering_case_active(context):
            _clear_case_review_state(context, keep_case=False)
        if attachment_path_to_save:
            context.user_data["attachment_path"] = attachment_path_to_save
            context.user_data["attachment_name"] = attachment_name_to_save
        elif existing_attachment_path:
            context.user_data["attachment_path"] = existing_attachment_path
            context.user_data["attachment_name"] = existing_attachment_name
        _append_gathering_case(context, case_text, input_source)
        if context.user_data.pop("_gathering_ack_used", False):
            return AWAIT_GATHERING
        await _delete_previous_gathering_message(context)
        reply_text, reply_markup = _gathering_reply(context)
        gathering_msg = await update.message.reply_text(reply_text, reply_markup=reply_markup)
        context.user_data["gathering_msg_id"] = gathering_msg.message_id
        context.user_data["gathering_chat_id"] = gathering_msg.chat_id
        return AWAIT_GATHERING

    if chosen_form and context.user_data.get("awaiting_detail"):
        context.user_data["case_text"] = case_text
        context.user_data["case_input_source"] = input_source
        await update.effective_chat.send_action(constants.ChatAction.TYPING)
        ack = await update.message.reply_text(f"🧩 Updating {_form_display_name(chosen_form)} draft…")
        context.user_data["last_bot_msg_id"] = ack.message_id
        context.user_data["last_bot_chat_id"] = ack.chat_id
        try:
            draft = await _analyse_selected_form(context, user_id, case_text, chosen_form)
        except asyncio.TimeoutError:
            await ack.edit_text("⏳ Template review timed out. Please try again.")
            return AWAIT_TEMPLATE_REVIEW
        except Exception as exc:
            logger.error("Template review refresh failed for %s: %s", chosen_form, exc, exc_info=True)
            await ack.edit_text("⚠️ Could not refresh that template.", reply_markup=_KB_CANCEL)
            return AWAIT_TEMPLATE_REVIEW

        if not _draft_has_useful_content(draft, chosen_form):
            return await _ask_for_more_detail_before_draft(ack, context)
        return await _show_draft_review(ack, context, draft, chosen_form)

    active_msg_id = context.user_data.get("last_bot_msg_id")
    active_chat_id = context.user_data.get("last_bot_chat_id")
    active_status_id = context.user_data.get("status_msg_id")
    active_status_chat = context.user_data.get("status_msg_chat")
    existing_attachment_path = context.user_data.get("attachment_path")
    existing_attachment_name = context.user_data.get("attachment_name")
    _clear_case_review_state(context, keep_case=False)
    if attachment_path_to_save:
        context.user_data["attachment_path"] = attachment_path_to_save
        context.user_data["attachment_name"] = attachment_name_to_save
    elif existing_attachment_path:
        context.user_data["attachment_path"] = existing_attachment_path
        context.user_data["attachment_name"] = existing_attachment_name
    if active_msg_id and active_chat_id:
        context.user_data["last_bot_msg_id"] = active_msg_id
        context.user_data["last_bot_chat_id"] = active_chat_id
        context.user_data["status_msg_id"] = active_status_id or active_msg_id
        context.user_data["status_msg_chat"] = active_status_chat or active_chat_id
        await _send_latest_message(update.message, context, CAPTURED_ACK, parse_mode="Markdown")
    elif not context.user_data.get("last_bot_msg_id"):
        ack = await update.message.reply_text(CAPTURED_ACK, parse_mode="Markdown")
        _track_latest_message(context, ack)
    return await _process_case_text(update.message, context, user_id, case_text, input_source)


async def _delete_previous_gathering_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove the prior gathering bubble so only one is on screen at a time."""
    old_msg_id = context.user_data.pop("gathering_msg_id", None)
    old_chat_id = context.user_data.pop("gathering_chat_id", None)
    if old_msg_id and old_chat_id:
        try:
            await context.bot.delete_message(chat_id=old_chat_id, message_id=old_msg_id)
        except Exception:
            pass


async def _finish_gathering_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    case_text, input_source = _combined_gathering_case(context)
    gathering_msg_id = context.user_data.pop("gathering_msg_id", None)
    gathering_chat_id = context.user_data.pop("gathering_chat_id", None)
    _clear_gathering_case(context)
    message = update.effective_message
    if not case_text:
        await message.reply_text("⚠️ I was gathering the case, but nothing readable came through.\n\nSend it again when ready.")
        return AWAIT_CASE_INPUT
    edited_in_place = False
    if gathering_msg_id and gathering_chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=gathering_chat_id,
                message_id=gathering_msg_id,
                text=CAPTURED_ACK,
                parse_mode="Markdown",
            )
            context.user_data["last_bot_msg_id"] = gathering_msg_id
            context.user_data["last_bot_chat_id"] = gathering_chat_id
            context.user_data["status_msg_id"] = gathering_msg_id
            context.user_data["status_msg_chat"] = gathering_chat_id
            edited_in_place = True
        except Exception:
            edited_in_place = False
    if not edited_in_place:
        ack = await message.reply_text(CAPTURED_ACK, parse_mode="Markdown")
        _track_latest_message(context, ack)
    return await _process_case_text(message, context, user_id, case_text, input_source)


async def gather_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if not _gathering_case_active(context):
        _clear_gathering_case(context)
        context.user_data.pop("gathering_msg_id", None)
        context.user_data.pop("gathering_chat_id", None)
        try:
            await query.edit_message_text(
                "⚠️ I do not have a case captured for that button. Send case details when you are ready to draft."
            )
        except Exception:
            await update.effective_message.reply_text(
                "⚠️ I do not have a case captured for that button. Send case details when you are ready to draft."
            )
        return ConversationHandler.END
    return await _finish_gathering_case(update, context)


async def handle_gathering_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle extra messages while the opt-in gathering mode is active."""
    if not _gathering_enabled(context):
        _clear_gathering_case(context)
        return await handle_case_input(update, context)

    if not update.message or not update.message.text:
        return await handle_case_input(update, context)

    raw_text = update.message.text.strip()
    if not raw_text:
        return AWAIT_GATHERING

    if _is_case_bundle_done(raw_text):
        return await _finish_gathering_case(update, context)

    decision = await decide_gathering_turn(raw_text, answer_question=answer_question)
    if decision.kind is GatheringTurnKind.FINISH_CASE:
        return await _finish_gathering_case(update, context)

    reply = decision.reply
    markup = to_telegram_keyboard(reply)

    if not decision.add_to_case:
        # Side question / capability answer — stay in gathering, point the
        # user back to the case. Case workspace is left untouched.
        await update.message.reply_text(reply.full_text(), reply_markup=markup)
        return AWAIT_GATHERING

    _append_gathering_case(context, raw_text, "text")
    await _delete_previous_gathering_message(context)
    gathering_msg = await update.message.reply_text(reply.full_text(), reply_markup=_gathering_done_keyboard())
    context.user_data["gathering_msg_id"] = gathering_msg.message_id
    context.user_data["gathering_chat_id"] = gathering_msg.chat_id
    return AWAIT_GATHERING


async def handle_form_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text input during form search — filter forms by name substring."""
    from extractor import FORM_UUIDS
    user_id = update.effective_user.id
    query_text = update.message.text.strip().lower()
    allowed = _get_allowed_forms(user_id)

    # Match against display names
    matches = []
    for ft in allowed:
        base_ft_search = ft.replace("_2021", "") if ft.endswith("_2021") else ft
        label = FORM_BUTTON_LABELS.get(ft) or FORM_BUTTON_LABELS.get(base_ft_search) or _form_display_name(base_ft_search)
        if query_text in label.lower() or query_text in ft.lower():
            base_ft = ft.replace("_2021", "") if ft.endswith("_2021") else ft
            emoji = FORM_EMOJIS.get(base_ft, "📄")
            matches.append(InlineKeyboardButton(
                f"{emoji} {label}", callback_data=f"FORM|{ft}"
            ))

    if matches:
        rows = [matches[i:i+2] for i in range(0, len(matches), 2)]
        rows.append([InlineKeyboardButton("⬅️ Back to categories", callback_data="FORM|show_all")])
        await update.message.reply_text(
            f"Found {len(matches)} form{'s' if len(matches) != 1 else ''} matching \"{update.message.text.strip()}\":",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    else:
        await update.message.reply_text(
            "No forms matched — try another term.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to categories", callback_data="FORM|show_all")]
            ]),
        )
    return AWAIT_FORM_CHOICE


async def handle_form_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle form type selection."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "FORM|disabled":
        await query.answer("Coming soon — choose another form.", show_alert=False)
        return AWAIT_FORM_CHOICE

    if not context.user_data.get("case_text"):
        _restore_last_filed_case_context(context)

    if data == "FORM|show_all":
        # Level 1 — category picker
        user_id = update.effective_user.id
        curriculum = _effective_curriculum(user_id)
        cur_label = "2025 curriculum" if curriculum == "2025" else "2021 curriculum"
        await query.edit_message_text(
            f"Pick a category ({cur_label}):",
            reply_markup=_build_category_picker_keyboard(user_id),
        )
        return AWAIT_FORM_CHOICE

    if data.startswith("FORM|cat_"):
        # Level 2 — forms within a category
        cat_slug = data.split("FORM|cat_")[1]
        if cat_slug not in _SLUG_TO_CAT:
            return AWAIT_FORM_CHOICE
        user_id = update.effective_user.id
        cat_name = _SLUG_TO_CAT[cat_slug]
        await query.edit_message_text(
            f"{cat_name} — pick a form:",
            reply_markup=_build_category_forms_keyboard(user_id, cat_slug),
        )
        return AWAIT_FORM_CHOICE

    if data == "FORM|search":
        await query.edit_message_text(
            "Type part of the form name to search:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back to categories", callback_data="FORM|show_all")]
            ]),
        )
        return AWAIT_FORM_SEARCH

    if data == "FORM|switch_curriculum":
        user_id = update.effective_user.id
        current = get_curriculum(user_id)
        new_cur = "2021" if current == "2025" else "2025"
        store_curriculum(user_id, new_cur)
        effective = _effective_curriculum(user_id)
        cur_label = "2025 curriculum" if effective == "2025" else "2021 curriculum"
        await query.edit_message_text(
            f"Pick a category ({cur_label}):",
            reply_markup=_build_category_picker_keyboard(user_id),
        )
        return AWAIT_FORM_CHOICE

    if data == "FORM|back":
        # Restore the AI recommendations screen
        recommendations = context.user_data.get("form_recommendations", [])
        saved_text = context.user_data.get("form_recommendations_text", "Which form would you like to create?")
        await query.edit_message_text(
            saved_text,
            reply_markup=_build_form_choice_keyboard(recommendations, curriculum=_effective_curriculum(update.effective_user.id))
        )
        return AWAIT_FORM_CHOICE

    if data == "FORM|best":
        recommendations = context.user_data.get("form_recommendations", [])
        filtered = _filtered_recommendations_for_curriculum(
            recommendations,
            curriculum=_effective_curriculum(update.effective_user.id),
        )
        best = filtered[0] if filtered else None
        if not best:
            await query.edit_message_text(
                "I couldn't find a best-fit form from the recommendations. Pick one manually:",
                reply_markup=_build_category_picker_keyboard(update.effective_user.id),
            )
            return AWAIT_FORM_CHOICE
        form_type = best.form_type
        _track_funnel_event(context, "best_fit_chosen", form_type=form_type)
    else:
        form_type = data.split("|")[1]
        _track_funnel_event(context, "form_chosen", form_type=form_type)

    excluded_form = _normalise_form_type(context.user_data.get("excluded_form_type", ""))
    if excluded_form and _normalise_form_type(form_type) == excluded_form:
        await query.answer("You already filed that WPBA for this case — choose a different type.", show_alert=True)
        return AWAIT_FORM_CHOICE

    # Stale button guard — if case_text is gone, this button belongs to an old flow
    case_text = context.user_data.get("case_text", "")
    if not case_text:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        context.user_data.clear()
        await _send_latest_message(
            query.message,
            context,
            "That form list has expired. Start a new case and I’ll rebuild the form choices.",
            reply_markup=_build_next_step_keyboard(update.effective_user.id),
        )
        return ConversationHandler.END

    context.user_data["chosen_form"] = form_type
    context.user_data.pop("quick_improve_used", None)

    await query.edit_message_text(
        f"🧩 Reviewing {_form_display_name(form_type)} template…",
        reply_markup=None,
    )

    try:
        draft = await _analyse_selected_form(context, update.effective_user.id, case_text, form_type)
    except asyncio.TimeoutError:
        logger.error("Template review timed out after 45s for %s", form_type)
        await query.edit_message_text(
            "⏳ Template review timed out. The model took too long — try again in a moment.",
            reply_markup=_KB_RETRY_TEMPLATE,
        )
        return AWAIT_FORM_CHOICE
    except Exception as e:
        logger.error("Template review failed in form_choice: %s", e, exc_info=True)
        if _is_transient_llm_error(e):
            await query.edit_message_text(
                "⏳ The drafting model is rate-limited or briefly unavailable. Try again in a moment.",
                reply_markup=_KB_RETRY_TEMPLATE,
            )
            return AWAIT_FORM_CHOICE
        await query.edit_message_text("⚠️ Could not review that template.", reply_markup=_KB_CANCEL)
        return ConversationHandler.END

    if not _draft_has_useful_content(draft, form_type):
        return await _ask_for_more_detail_before_draft(query.message, context, form_type=form_type)

    return await _show_draft_review(query.message, context, draft, form_type)


_FIELD_FRIENDLY = {
    "curriculum_links": "SLO links",
    "key_capabilities": "Key Capabilities",
    "year_of_training": "Year of training",
    "age_of_patient": "Patient age",
    "end_date": "End date",
    "date_of_activity": "Date of activity",
    "date_of_encounter": "Date of encounter",
    "stage_of_training": "Stage of training",
    "higher_procedural_skill": "Procedural skill (Higher)",
    "intermediate_procedural_skill": "Procedural skill (Intermediate)",
    "accs_procedural_skill": "Procedural skill (ACCS)",
    "reflective_comments": "Reflection",
    "reflection": "Reflection",
    "clinical_setting": "Clinical setting",
    "patient_presentation": "Patient presentation",
    "placement": "Placement",
    "procedure_name": "Procedure",
    "procedural_skill": "Procedural skill",
    "indication": "Indication",
    "clinical_reasoning": "Case discussion",
    "trainee_performance": "Trainee performance",
}


def _friendly_field_name(key) -> str:
    key_str = str(key)
    return _FIELD_FRIENDLY.get(key_str, key_str.replace("_", " ").capitalize())


def _classify_filing_failure(
    error: str | None,
    skipped: list,
    status: str,
    filled: list,
) -> str:
    """Bucket a filing result into a recovery class.

    LOGIN_FAILED — Kaizen rejected the credentials or session expired.
    SAVE_FAILURE — Fields filled but save click or confirmation did not land.
    FIELD_FAILURE — One or more fields couldn't be filled; save itself is fine.
    UNKNOWN — Generic failure with no clear bucket.

    The classifier inspects the error string for marker phrases the filer
    raises ("Login failed", "Save button not found", "could not confirm").
    Save-related markers take precedence over field skips because a missed
    save invalidates the whole draft regardless of how many fields filled.
    """
    err = (error or "").lower()
    if any(token in err for token in (
        "login failed", "could not log in", "log in to kaizen",
        "session expired", "auth.kaizenep.com", "redirected to https://auth",
    )):
        return "LOGIN_FAILED"
    save_markers = (
        "save button", "save may have failed", "save was clicked",
        "could not confirm",
    )
    if error and len(filled) > 0 and any(marker in err for marker in save_markers):
        return "SAVE_FAILURE"
    editable_skipped = [
        s for s in skipped if "attachment" not in str(s).lower()
    ]
    if editable_skipped:
        return "FIELD_FAILURE"
    return "UNKNOWN"


def _build_field_edit_buttons(skipped: list) -> list[list[InlineKeyboardButton]]:
    """Inline buttons that re-enter AWAIT_EDIT_VALUE for a specific field.

    Caps at 5 buttons so the keyboard doesn't dominate the message.
    Attachment skip markers are filtered out — they're not editable fields.
    """
    rows: list[list[InlineKeyboardButton]] = []
    seen: set[str] = set()
    for key in skipped:
        key_str = str(key)
        if "attachment" in key_str.lower() or key_str in seen:
            continue
        seen.add(key_str)
        label = f"✏️ Edit {_friendly_field_name(key_str)}"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"FIELD|{key_str}")])
        if len(rows) >= 5:
            break
    return rows


async def handle_approval_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 'File this draft' approval."""
    query = update.callback_query
    is_callback = query is not None

    # Idempotency guard for a one-shot external effect: saving a Kaizen draft.
    # Re-entry happens via double-taps and stale retry/save buttons still sitting
    # in older chat messages. The check-and-set is synchronous — no await between
    # the read and the write — so two near-simultaneous callbacks cannot both
    # pass it and start duplicate Kaizen filings. Released in the finally around
    # route_filing (and by user_data.clear() on success) so a deliberate retry
    # after a failure isn't blocked forever.
    if context.user_data.get("filing_in_progress"):
        if query:
            await query.answer("⏳ Already saving your draft — give it a moment.")
        return None
    context.user_data["filing_in_progress"] = True

    if query:
        await query.answer()

    # Disarm buttons immediately — prevents double-tap filing
    if query:
        await query.edit_message_reply_markup(reply_markup=None)
        source_message = query.message
    else:
        source_message = update.message

    user_id = update.effective_user.id
    creds = get_credentials(user_id)
    if not creds:
        context.user_data.clear()
        await source_message.reply_text(
            "⚠️ Credentials not found.",
            reply_markup=InlineKeyboardMarkup([[_BTN_SETUP]])
        )
        return ConversationHandler.END

    username, password = creds
    draft = _load_draft(context)
    if not draft:
        context.user_data.pop("filing_in_progress", None)
        return await _resume_paused_flow(
            update,
            context,
            "That earlier draft is no longer active.",
        )
    _track_funnel_event(context, "save_attempted", has_draft=True)

    # Handle FormDraft (non-CBD forms)
    # Unified filing for ALL forms (CBD and non-CBD)
    if isinstance(draft, FormDraft):
        form_type = _filing_form_type_for_user(user_id, draft.form_type)
        fields = draft.fields
        curriculum_links = draft.fields.get("curriculum_links", [])
    else:
        # CBDData hard-codes form_type="CBD". When the user picked the 2021
        # variant via the category picker, chosen_form preserves that — route
        # the filing there so the right Kaizen UUID is used.
        chosen_form = context.user_data.get("chosen_form") or "CBD"
        form_type = _filing_form_type_for_user(
            user_id,
            chosen_form if chosen_form in ("CBD", "CBD_2021") else "CBD",
        )
        fields = {
            "date_of_encounter": draft.date_of_encounter,
            "end_date": draft.date_of_encounter,
            "date_of_event": draft.date_of_encounter,
            "stage_of_training": draft.stage_of_training,
            "clinical_reasoning": draft.clinical_reasoning,
            "reflection": draft.reflection,
        }
        curriculum_links = draft.curriculum_links or []

    schema = FORM_SCHEMAS.get(schema_form_type(form_type), {})
    form_name = public_form_name(form_type) or schema.get("name", form_type)
    form_emoji = FORM_EMOJIS.get(schema_form_type(form_type), FORM_EMOJIS.get(_normalise_form_type(form_type), "📋"))



    # Save local JSON backup
    import json as _json
    import pathlib
    from datetime import date
    try:
        drafts_dir = pathlib.Path.home() / ".openclaw/data/portfolio-guru/drafts"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{user_id}_{form_type}_{date.today()}.json"
        with open(drafts_dir / filename, "w") as f:
            _json.dump({"form_type": form_type, "fields": fields}, f, indent=2)
    except OSError:
        logger.warning("Local draft JSON backup failed; continuing with Kaizen filing", exc_info=True)

    # Save draft preview and data for later restore + amend feature
    draft_preview_text = _format_draft_preview(draft, _chosen_form_reason(context, form_type)) + _REPLY_HINT_SUFFIX
    amend_draft_data = _serialise_draft(draft)
    amend_case_text = context.user_data.get("case_text", "")
    amend_chosen_form_type = form_type

    # Determine platform (default: kaizen; future: from user profile)
    platform = "kaizen"
    saving_text = f"📤 Saving {form_name} as a Kaizen draft…"
    # Send a new progress message so the draft preview survives in the chat
    # history — the user can scroll up to verify what was filed. The progress
    # message is later edited with the final result.
    ack = await source_message.reply_text(saving_text)

    reuse_existing_draft = bool(context.user_data.pop("retry_filing_requested", False))

    # Retrieve attachment path and validate existence / support
    attachment_path = context.user_data.get("attachment_path")
    attachment_skipped_reason = None
    if attachment_path:
        if not os.path.exists(attachment_path):
            attachment_skipped_reason = "attachment (file missing)"
            attachment_path = None
        elif not is_supported_document(context.user_data.get("attachment_name", "")):
            attachment_skipped_reason = "attachment (unsupported type)"
            attachment_path = None

    # Progress edits during the long filing wait so the user doesn't see a
    # static message for up to 5 minutes. Started here — immediately before
    # route_filing — so earlier early-returns (gate, missing creds) never pay
    # the asyncio.create_task overhead.
    typing_stop = asyncio.Event()
    typing_task = asyncio.create_task(_typing_until(update.effective_chat, typing_stop))

    async def _filing_progress():
        try:
            await asyncio.sleep(20)
            try:
                await ack.edit_text(f"📤 Still saving {form_name} — Kaizen is loading the form…")
            except Exception:
                pass
            await asyncio.sleep(40)
            try:
                await ack.edit_text(f"📤 Filling fields in {form_name} — almost there…")
            except Exception:
                pass
            await asyncio.sleep(60)
            try:
                await ack.edit_text(f"📤 Verifying the save on Kaizen — this is the last step…")
            except Exception:
                pass
        except asyncio.CancelledError:
            pass

    progress_task = asyncio.create_task(_filing_progress())

    try:
        result = await asyncio.wait_for(
            route_filing(
                platform=platform,
                form_type=form_type,
                fields=fields,
                credentials={"username": username, "password": password},
                curriculum_links=curriculum_links,
                form_name=form_name,
                reuse_draft=reuse_existing_draft,
                attachment_path=attachment_path,
                telegram_user_id=user_id,
            ),
            timeout=300,  # 5 min — browser-use path may take longer
        )
        if attachment_skipped_reason:
            if "skipped" not in result:
                result["skipped"] = []
            if attachment_skipped_reason not in result["skipped"]:
                result["skipped"].append(attachment_skipped_reason)
    except asyncio.TimeoutError:
        typing_stop.set()
        progress_task.cancel()
        _log_filing_attempt(
            user_id=user_id,
            username=getattr(update.effective_user, "username", None),
            form_type=form_type,
            status="timeout",
            error="Filing exceeded 300s timeout",
        )
        kaizen_url = f"https://kaizenep.com/events/new-section/{FORM_UUIDS.get(form_type, '')}" if FORM_UUIDS.get(form_type) else "https://kaizenep.com/activities"
        timeout_msg = (
            "⏱ Filing took too long — Kaizen may be slow right now. "
            "Tap 'Open in Kaizen' to finish manually, or retry."
        )
        retry_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|retry_filing")],
            [InlineKeyboardButton("🔗 Open in Kaizen", url=kaizen_url)],
            [InlineKeyboardButton("🆕 Start fresh", callback_data="ACTION|reset")],
        ])
        try:
            await ack.edit_text(timeout_msg, reply_markup=retry_keyboard, parse_mode="Markdown")
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=timeout_msg,
                reply_markup=retry_keyboard,
                parse_mode="Markdown",
            )
        # Keep draft so user can retry without re-typing the case
        return AWAIT_APPROVAL
    except Exception as e:
        typing_stop.set()
        progress_task.cancel()
        logger.error(f"Filer error for {form_type}: {e}", exc_info=True)
        _log_filing_attempt(
            user_id=user_id,
            username=getattr(update.effective_user, "username", None),
            form_type=form_type,
            status="exception",
            error=str(e),
        )
        # Keep draft data for retry — do NOT clear user_data
        retry_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|retry_filing")],
            [InlineKeyboardButton("🆕 Start fresh", callback_data="ACTION|reset")],
        ])
        try:
            await ack.edit_text("❌ Filing failed. Try again or start fresh.", reply_markup=retry_keyboard)
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Filing failed. Try again or start fresh.",
                reply_markup=retry_keyboard,
            )
        return AWAIT_APPROVAL  # Stay in approval state so retry can pick up draft
    finally:
        # route_filing has returned or raised — the one-shot effect is no
        # longer in flight, so release the guard. Every failure path below
        # (timeout, exception, login failure, uncertain partial, classified
        # failure) returns AWAIT_APPROVAL with a "Try Again" button; leaving
        # the flag set would make that retry hit the in-progress short-circuit
        # forever. The success path clears all of user_data anyway.
        context.user_data.pop("filing_in_progress", None)
        typing_stop.set()
        typing_task.cancel()
        progress_task.cancel()

    status = result["status"]
    filled = result.get("filled", [])
    skipped = result.get("skipped", [])
    error = result.get("error")

    # SAVE_FAILURE auto-rescue (max 1 retry per filing).
    # When the filer reports that fields filled but the save click/verify
    # didn't land, reuse the half-saved draft and try again with a fresh
    # save pass. Bounded to one extra attempt — if the second pass still
    # fails the user gets the structured save-failure message and a manual
    # retry button.
    if (
        status in ("failed", "partial")
        and len(filled) > 0
        and _classify_filing_failure(error, skipped, status, filled) == "SAVE_FAILURE"
    ):
        try:
            await ack.edit_text(
                "📤 Save wasn't confirmed — trying an alternative method…"
            )
        except Exception:
            pass
        retry_typing_stop = asyncio.Event()
        retry_typing_task = asyncio.create_task(
            _typing_until(update.effective_chat, retry_typing_stop)
        )
        context.user_data["filing_in_progress"] = True
        try:
            retry_result = await asyncio.wait_for(
                route_filing(
                    platform=platform,
                    form_type=form_type,
                    fields=fields,
                    credentials={"username": username, "password": password},
                    curriculum_links=curriculum_links,
                    form_name=form_name,
                    reuse_draft=True,
                    attachment_path=attachment_path,
                    telegram_user_id=user_id,
                ),
                timeout=180,
            )
            if attachment_skipped_reason:
                if "skipped" not in retry_result:
                    retry_result["skipped"] = []
                if attachment_skipped_reason not in retry_result["skipped"]:
                    retry_result["skipped"].append(attachment_skipped_reason)
            result = retry_result
            status = result["status"]
            filled = result.get("filled", [])
            skipped = result.get("skipped", [])
            error = result.get("error")
        except Exception as retry_exc:
            logger.warning(
                "Save-rescue retry for %s failed: %s", form_type, retry_exc
            )
        finally:
            context.user_data.pop("filing_in_progress", None)
            retry_typing_stop.set()
            retry_typing_task.cancel()

    defaulted_fields = set(result.get("defaulted_fields") or [])
    date_default_note = ""
    if "date_of_encounter" in defaulted_fields:
        from datetime import date as _today
        default_date = result.get("activity_date") or fields.get("date_of_encounter") or fields.get("date_of_event")
        # Suppress the note when today's date is already shown in the header
        if default_date != _today.today().isoformat():
            date_default_note = (
                f"\n📅 I used today's date"
                f"{f' ({default_date})' if default_date else ''} because no case date was given."
            )
    required_labels = {field["label"] for field in _template_requirements(form_type)[0]}
    required_keys = {field["key"] for field in _template_requirements(form_type)[0]}
    skipped_required = [s for s in skipped if s in required_keys or s in required_labels]
    if status == "success" and skipped_required:
        status = "partial"
        error = error or "Required fields were skipped during filing: " + ", ".join(str(s) for s in skipped_required[:4])
    _log_filing_attempt(
        user_id=user_id,
        username=getattr(update.effective_user, "username", None),
        form_type=form_type,
        status=status,
        error=error,
        filled=filled,
        skipped=[str(s) for s in skipped],
        method=result.get("method"),
        verified=result.get("verified"),
    )
    proof_report = _format_proof_report(
        status,
        form_name,
        context.user_data.get("case_input_source"),
        filled,
        skipped,
        error,
    )

    method = result.get("method", "deterministic")

    uncertain_save = status == "partial" and bool(error)
    filed_case_text = context.user_data.get("case_text", "")
    if status in ("success", "partial") and not uncertain_save:
        context.user_data.clear()
        if filed_case_text:
            context.user_data["last_filed_case_text"] = filed_case_text
            context.user_data["last_filed_form_type"] = form_type
        # Store amend data after clear so it's available for the button
        context.user_data["last_draft_preview"] = draft_preview_text
        context.user_data["last_amend_draft"] = amend_draft_data
        context.user_data["last_amend_case_text"] = amend_case_text
        context.user_data["last_amend_chosen_form"] = amend_chosen_form_type

    # Only treat the post-save URL as a real draft link when the save itself
    # was credible. Partial-with-error means the save may not have landed, so
    # any URL we captured could point at a half-formed draft — link to the
    # activities list instead so the user can verify.
    raw_saved_url = result.get("saved_url")
    saved_url = raw_saved_url if (status in ("success", "partial") and not uncertain_save and raw_saved_url) else None

    end_keyboard = _build_post_filing_keyboard(
        form_type,
        status,
        uncertain=uncertain_save,
        same_case_available=bool(filed_case_text and status in ("success", "partial") and not uncertain_save),
        saved_url=saved_url,
    )

    # Track usage for successful filings
    usage_line = ""
    observation_line = ""
    if status in ("success", "partial"):
        try:
            await record_case_filed(user_id, form_type, "filed")
            allowed, used, limit, tier = await check_can_file(user_id)
            tier_label = {"free": "Free tier", "pro": "Pro", "pro_plus": "Unlimited"}.get(tier, tier)
            if limit == -1:
                usage_line = f"\n\n📊 {used} cases this month ({tier_label})"
            else:
                usage_line = f"\n\n📊 {used}/{limit} cases this month ({tier_label})"
        except Exception:
            logger.warning("Usage tracking failed", exc_info=True)

    if status == "success":
        try:
            kcs = fields.get("key_capabilities") or fields.get("curriculum_links")
            if kcs:
                await save_kc_coverage(user_id, form_type, kcs)
        except Exception:
            logger.warning("KC coverage save failed", exc_info=True)

        # Mirror the filed case to Supabase portfolio_cases so the web app's
        # case browser can render it. Encrypt the case_text with the same
        # Fernet key used for credentials — plaintext clinical narratives
        # never leave the bot. See feedback-no-fabrication for context.
        try:
            from supabase_sync import mirror_case
            from credentials import _fernet
            case_text_encrypted = None
            if filed_case_text:
                try:
                    case_text_encrypted = _fernet().encrypt(filed_case_text.encode())
                except Exception:
                    case_text_encrypted = None
            mirror_case(
                user_id,
                form_type=form_type,
                status=status,
                kaizen_event_id=result.get("event_id") or result.get("kaizen_event_id"),
                case_text_encrypted=case_text_encrypted,
                extracted_fields=fields,
                curriculum_links=fields.get("curriculum_links") or curriculum_links or [],
                key_capabilities=fields.get("key_capabilities") or [],
            )
        except Exception:
            logger.debug("Case mirror to Supabase failed (non-fatal)", exc_info=True)

    # One-line portfolio observation after a clean save. Skip for brand-new
    # users — there's nothing meaningful to say with one or two cases on file.
    if status == "success":
        try:
            history = await get_case_history(user_id, months=3)
            if len(history) >= 3:
                observation = await summarise_recent_activity(history, form_type)
                if observation:
                    observation_line = f"\n\n💡 {observation}"
        except Exception:
            logger.warning("Post-file observation failed", exc_info=True)

    if status == "success":
        date_val = result.get("activity_date") or fields.get("date_of_encounter", fields.get("date_of_event", ""))
        slo_str = ", ".join(curriculum_links) if curriculum_links else ""
        summary = f"\n📅 {date_val}" if date_val else ""
        if slo_str:
            summary += f"  ·  📚 {slo_str}"
        # Clean success message — no verbose proof report. Lead with a step
        # header so the confirmation reads as a new phase, distinct from the
        # draft preview the user just approved.
        filled_count = len(filled)
        fields_summary = f"\n{filled_count} field{'s' if filled_count != 1 else ''} completed." if filled_count > 0 else ""
        attachment_skipped_msg = ""
        for s in skipped:
            if "attachment" in str(s).lower():
                attachment_skipped_msg = f"\n\n⚠️ Attachment skipped: {str(s).replace('attachment (', '').replace(')', '')}."
                break
        msg = (
            f"✅ Kaizen draft saved\n"
            f"{form_name} saved as a Kaizen draft.{summary}{fields_summary}"
            f"{date_default_note}"
            f"{usage_line}{observation_line}"
            f"{attachment_skipped_msg}"
        )
        status_line = "✅ Draft saved."
    elif status == "partial":
        skipped_names = [_friendly_field_name(s) for s in skipped]
        if len(skipped_names) > 3:
            skipped_display = ", ".join(skipped_names[:3]) + f" and {len(skipped_names) - 3} other{'s' if len(skipped_names) - 3 != 1 else ''}"
        else:
            skipped_display = ", ".join(skipped_names)
        if error:
            # Partial with error — save may not have worked. Don't link to
            # /events/new-section (that opens a BLANK form and the user could
            # easily mistake it for the draft we just talked about). Prefer
            # the captured saved-draft URL when we have one; otherwise point
            # at the activities list so the user verifies before retrying.
            if raw_saved_url:
                link_text = f"\n\nFind this draft in Kaizen:\n{raw_saved_url}"
            elif platform == "kaizen":
                link_text = "\n\n[Check your Kaizen drafts](https://kaizenep.com/activities)"
            else:
                link_text = ""
            fields_filled_str = f"{len(filled)} field{'s' if len(filled) != 1 else ''} filled"
            recovery_block = (
                "Fields filled but save wasn't confirmed. "
                "The draft may not have saved — open Kaizen to verify before retrying."
            )
            msg = (
                f"⚠️ Filing had issues — check Kaizen\n"
                f"{form_name}\n\n"
                f"{fields_filled_str}.\n\n"
                f"{_DRAFT_DIVIDER}\n\n"
                f"{recovery_block}{link_text}{usage_line}"
            )
            status_line = "⚠️ Filing needs attention."
        else:
            fields_filled_str = f"{len(filled)} field{'s' if len(filled) != 1 else ''} filled"
            review_clause = (
                f"{len(skipped)} field{'s' if len(skipped) != 1 else ''} "
                f"need{'s' if len(skipped) == 1 else ''} your review"
            )
            # The action line tracks what the keyboard button actually does:
            # if we have the saved-draft URL, "Open saved draft" lands on the
            # exact draft; otherwise the fallback opens the activities list
            # and the user finds the draft from there.
            if raw_saved_url:
                action_line = (
                    "Open the saved draft to fill the missing detail, "
                    "then assign an assessor."
                )
            else:
                action_line = (
                    "Open Kaizen and find your saved draft to fill the missing "
                    "detail, then assign an assessor."
                )
            # Lead with a distinct "Draft saved in Kaizen" step header so the
            # confirmation doesn't visually merge with the approved draft
            # above it, and keep the review guidance in its own block so it
            # reads as guidance, not as draft content.
            attachment_skipped_msg = ""
            for s in skipped:
                if "attachment" in str(s).lower():
                    attachment_skipped_msg = f"\n\n⚠️ Attachment skipped: {str(s).replace('attachment (', '').replace(')', '')}."
                    break
            msg = (
                f"📥 Draft saved in Kaizen\n"
                f"{form_name}\n\n"
                f"⚠️ Needs your review\n"
                f"{fields_filled_str} from your case. "
                f"{review_clause}: {skipped_display}.\n\n"
                f"{action_line}{date_default_note}{usage_line}"
                f"{attachment_skipped_msg}"
            )
            status_line = "⚠️ Filing needs manual review."
    else:
        # Login failures need a dedicated path: the generic "try again / file
        # another / cancel" keyboard gives the user no way to re-enter
        # credentials, so they get stuck. Detect the marker the filers raise
        # ("Login failed", see browser_filer.py and kaizen_form_filer.py) and
        # swap in a reconnect-first keyboard with a clear session-expired
        # message. force_reconnect lets the ACTION|setup handler bypass the
        # "already connected" short-circuit when the user taps Reconnect.
        is_login_failure = bool(error) and (
            "Login failed" in error
            or "Could not log in to Kaizen" in error
            or "could not log in" in error.lower()
        )
        if is_login_failure and platform == "kaizen":
            context.user_data["force_reconnect"] = True
            msg = (
                f"❌ Filing didn't complete\n"
                f"{form_name}\n\n"
                "Your Kaizen session has expired. Tap 'Reconnect Kaizen' to "
                "re-enter your credentials, or try again if it was a "
                "temporary issue."
            )
            end_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔑 Reconnect Kaizen", callback_data="ACTION|setup")],
                [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|retry_filing")],
                [InlineKeyboardButton("🆕 Start fresh", callback_data="ACTION|reset")],
            ])
            status_line = "❌ Filing stopped."
            context.user_data["last_filing_status"] = status
            context.user_data["last_filing_form_name"] = form_name
            context.user_data["last_filing_report"] = msg
            try:
                await ack.edit_text(msg, reply_markup=end_keyboard)
            except Exception:
                logger.warning("Could not edit login-failure report, falling back to fresh message", exc_info=True)
                try:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=msg,
                        reply_markup=end_keyboard,
                    )
                except Exception:
                    logger.warning("Could not send fallback login-failure report", exc_info=True)
            return AWAIT_APPROVAL

        # Structured failure messaging — bucket the error and tailor the copy
        # plus keyboard so the user has a concrete next step instead of a
        # generic "try again" wall. The bucketing rules live in
        # _classify_filing_failure(); see that helper for the marker phrases
        # each filer raises.
        classification = _classify_filing_failure(error, skipped, status, filled)
        kaizen_url = (
            f"https://kaizenep.com/events/new-section/{FORM_UUIDS[form_type]}"
            if platform == "kaizen" and FORM_UUIDS.get(form_type)
            else None
        )
        details_suffix = (
            f"\n\nDetails: {sanitize_internal_form_codes(error)}" if error else ""
        )

        if classification == "SAVE_FAILURE":
            body = (
                "Fields filled but save wasn't confirmed. "
                "Tapping retry will try an alternative save method."
            )
            msg = f"❌ Filing didn't complete\n{form_name}\n\n{body}{details_suffix}"
            rows = [[InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|retry_filing")]]
            if kaizen_url:
                rows.append([InlineKeyboardButton("🔗 Open in Kaizen", url=kaizen_url)])
            rows.append([
                InlineKeyboardButton(_POST_FILING_NEW_CASE_LABEL, callback_data="ACTION|file"),
                _BTN_CANCEL,
            ])
            end_keyboard = InlineKeyboardMarkup(rows)
            status_line = "❌ Save not confirmed."
        elif classification == "FIELD_FAILURE":
            editable_skipped = [
                s for s in skipped if "attachment" not in str(s).lower()
            ]
            field_names = [_friendly_field_name(s) for s in editable_skipped[:5]]
            field_list = ", ".join(field_names) if field_names else "some fields"
            body = (
                f"Some fields need attention: {field_list}. "
                f"Tap 'Edit [field]' to fix one, or retry the whole filing."
            )
            msg = f"❌ Filing didn't complete\n{form_name}\n\n{body}{details_suffix}"
            rows = _build_field_edit_buttons(skipped)
            rows.append([InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|retry_filing")])
            if kaizen_url:
                rows.append([InlineKeyboardButton("🔗 Open in Kaizen", url=kaizen_url)])
            rows.append([
                InlineKeyboardButton(_POST_FILING_NEW_CASE_LABEL, callback_data="ACTION|file"),
                _BTN_CANCEL,
            ])
            end_keyboard = InlineKeyboardMarkup(rows)
            status_line = "❌ Some fields didn't fill."
        else:  # UNKNOWN
            body = (
                "Try again, or open the form in Kaizen and fill it manually."
                if kaizen_url
                else "Try again, or fill the form manually in your portfolio."
            )
            msg = f"❌ Filing didn't complete\n{form_name}\n\n{body}{details_suffix}"
            rows = [[InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|retry_filing")]]
            if kaizen_url:
                rows.append([InlineKeyboardButton("🔗 Open in Kaizen", url=kaizen_url)])
            rows.append([
                InlineKeyboardButton(_POST_FILING_NEW_CASE_LABEL, callback_data="ACTION|file"),
                _BTN_CANCEL,
            ])
            end_keyboard = InlineKeyboardMarkup(rows)
            status_line = "❌ Filing stopped."

    context.user_data["last_filing_status"] = status
    context.user_data["last_filing_form_name"] = form_name
    context.user_data["last_filing_report"] = msg

    if status == "success":
        # The draft preview is visible above — a clean success message with
        # reply-hint text is sufficient. No amend/extra buttons needed.
        pass

    # Keep the reviewed draft visible. The temporary progress message becomes
    # the final report so completion stays to one message.
    try:
        await ack.edit_text(msg, reply_markup=end_keyboard)
    except Exception:
        logger.warning("Could not edit filing report, falling back to fresh message", exc_info=True)
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=msg,
                reply_markup=end_keyboard,
            )
        except Exception:
            logger.warning("Could not send fallback filing report", exc_info=True)
    if status == "failed" or uncertain_save:
        return AWAIT_APPROVAL
    return ConversationHandler.END


async def handle_approval_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Legacy submit callback. Live submission is intentionally disabled."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    draft = _load_draft(context)
    if not draft:
        return await _resume_paused_flow(
            update,
            context,
            "That earlier draft is no longer active.",
        )
    await query.message.reply_text(
        "Portfolio Guru only saves Kaizen entries as drafts. Use Save as draft when you're ready.",
        reply_markup=_build_approval_keyboard(),
    )
    return AWAIT_APPROVAL


async def handle_review_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle '📝 Review draft' — AI review of draft quality before filing."""
    query = update.callback_query
    await query.answer()

    # Gate: Unlimited (and legacy Pro subscribers, who already paid for it).
    tier = await get_user_tier(update.effective_user.id)
    if tier == "free":
        await query.message.reply_text(
            "📝 Draft Review is included in Portfolio Guru Unlimited.\n\n"
            "Upgrade to unlock AI critique of your entries before filing — catches missed reflections, weak reasoning, and curriculum mismatches.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐⭐ Upgrade to Unlimited", callback_data="UPGRADE|pro_plus")],
            ]),
        )
        return AWAIT_APPROVAL

    draft = _load_draft(context)
    if not draft:
        return await _resume_paused_flow(
            update, context, "That earlier button is no longer active.",
        )

    # Determine form_type and fields
    if isinstance(draft, FormDraft):
        form_type = draft.form_type
        fields = draft.fields
    else:
        form_type = "CBD"
        fields = {
            "date_of_encounter": draft.date_of_encounter,
            "clinical_reasoning": draft.clinical_reasoning,
            "reflection": draft.reflection,
            "stage_of_training": draft.stage_of_training,
        }
        if draft.curriculum_links:
            fields["curriculum_links"] = draft.curriculum_links
        if draft.key_capabilities:
            fields["key_capabilities"] = draft.key_capabilities

    case_text = context.user_data.get("case_text", "")
    schema = FORM_SCHEMAS.get(schema_form_type(form_type), {})
    form_name = public_form_name(form_type) or schema.get("name", form_type)

    # Show typing while reviewing
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=constants.ChatAction.TYPING,
    )

    try:
        review = await review_draft(form_type, fields, case_text)
    except Exception as e:
        logger.error("Draft review failed: %s", e)
        await query.message.reply_text(
            "⚠️ Review failed — you can still file your draft.",
            reply_markup=_build_post_review_keyboard(),
        )
        return AWAIT_APPROVAL

    # Format the review message
    overall = review.get("overall_score", 0)
    scores = review.get("scores", {})
    top_suggestion = review.get("top_suggestion", "")
    verdict = review.get("verdict", "improve")

    verdict_display = {
        "ready": "✅ Ready to file",
        "improve": "🔶 Could be stronger — consider editing",
        "weak": "🔴 Needs work before filing",
    }

    star = "⭐"
    lines = [f"📝 *Draft Review — {form_name}*", ""]
    lines.append(f"Overall: {star * round(overall)} ({overall}/5)")
    lines.append("")

    criteria = [
        ("🔍 Reflection", "reflection_depth"),
        ("🧠 Clinical reasoning", "clinical_reasoning"),
        ("📚 SLO coverage", "slo_coverage"),
        ("👨\u200d⚕️ Assessor readiness", "assessor_readiness"),
        ("✍️ Language", "language_quality"),
    ]
    for label, key in criteria:
        entry = scores.get(key, {})
        s = entry.get("score", 0)
        fb = entry.get("feedback", "")
        lines.append(f"{label}: {star * s}/5 — {fb}")

    lines.append("")
    lines.append(f"💡 {top_suggestion}")
    lines.append("")
    lines.append(f"Verdict: {verdict_display.get(verdict, verdict)}")

    await query.message.reply_text(
        "\n".join(lines),
        reply_markup=_build_post_review_keyboard(),
        parse_mode="Markdown",
    )
    return AWAIT_APPROVAL


async def _restore_active_draft_buttons(query, context) -> None:
    """Re-attach the approval keyboard to the original draft message after a
    failed Quick Improve, so the user can retry or save without losing it."""
    try:
        await query.edit_message_reply_markup(reply_markup=_active_draft_keyboard(context))
    except Exception:
        logger.debug("Failed to restore active draft keyboard after quick improve error", exc_info=True)


async def _dismiss_quick_improve_ack(ack) -> None:
    """Remove the tiny 'Tightening…' status line once the in-place edit lands.

    Falls back to a quiet confirmation edit if delete is unavailable (older
    messages, restricted permissions) so the chat never keeps a stale status."""
    try:
        await ack.delete()
        return
    except Exception:
        logger.debug("Could not delete quick-improve status message", exc_info=True)
    try:
        await ack.edit_text("✨ Done")
    except Exception:
        logger.debug("Could not edit quick-improve status message to Done", exc_info=True)


async def handle_quick_improve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Improve the reflection only, keeping the rest of the draft stable."""
    query = update.callback_query
    await query.answer()
    if query.data == "IMPROVE|used" or context.user_data.get("quick_improve_used"):
        await query.answer("Already improved once — save, edit, or cancel this draft.", show_alert=False)
        return AWAIT_APPROVAL
    await query.edit_message_reply_markup(reply_markup=None)

    draft = _load_draft(context)
    if not draft:
        return await _resume_paused_flow(
            update,
            context,
            "That earlier draft is no longer active.",
        )

    form_type = _draft_form_type(draft)
    reflection_keys = ["reflection"] if isinstance(draft, CBDData) else _find_reflection_keys(draft.fields, form_type)
    if not reflection_keys:
        await query.message.reply_text(
            "This form does not have a reflection field to improve. You can still save it as a draft or tap Edit.",
            reply_markup=_active_draft_keyboard(context),
        )
        return AWAIT_APPROVAL

    ack = await query.message.reply_text("✨ Tightening the reflection only…")
    case_text = context.user_data.get("case_text", "")
    current_draft_text = _format_draft_preview(draft, _chosen_form_reason(context, form_type))
    feedback = (
        "Improve only the reflection or reflection-like narrative fields. Keep the clinical facts, "
        "date, setting, curriculum links, and every non-reflection field unchanged. Make the "
        "reflection concise, first-person, specific, and useful for a UK EM WPBA assessor. "
        "If the case involves a judgement issue, "
        "frame it as initially narrow reasoning or a plan changed after senior challenge, not as "
        "a blunt admission of wrong judgement. Anchor the learning to the available evidence and "
        "the concrete practice change."
    )
    vp = get_voice_profile(update.effective_user.id) or ""

    try:
        if isinstance(draft, CBDData):
            regenerated = await asyncio.wait_for(
                extract_cbd_data(
                    case_text,
                    edit_feedback=feedback,
                    current_draft=current_draft_text,
                    voice_profile_json=vp,
                    input_source=context.user_data.get("case_input_source", "text"),
                ),
                timeout=45,
            )
            improved_reflection = (regenerated.reflection or "").strip()
            if not improved_reflection:
                raise ValueError("Improved reflection was empty")
            updated = draft.model_copy(update={"reflection": improved_reflection})
        else:
            regenerated = await asyncio.wait_for(
                extract_form_data(
                    case_text,
                    form_type,
                    edit_feedback=feedback,
                    current_draft=current_draft_text,
                    voice_profile_json=vp,
                    input_source=context.user_data.get("case_input_source", "text"),
                ),
                timeout=45,
            )
            fields = dict(draft.fields)
            improved_any = False
            for reflection_key in reflection_keys:
                improved_value = str(regenerated.fields.get(reflection_key) or "").strip()
                if improved_value:
                    fields[reflection_key] = improved_value
                    improved_any = True
            if not improved_any:
                raise ValueError("Improved reflection was empty")
            updated = FormDraft(form_type=draft.form_type, fields=fields, uuid=draft.uuid)
        _store_draft(context, updated)
        context.user_data["quick_improve_used"] = True
    except asyncio.TimeoutError:
        await _restore_active_draft_buttons(query, context)
        await ack.edit_text("⏳ Quick improve timed out. Your original draft is still ready.")
        return AWAIT_APPROVAL
    except Exception as exc:
        logger.error("Quick improve failed for %s: %s", form_type, exc, exc_info=True)
        await _restore_active_draft_buttons(query, context)
        await ack.edit_text("⚠️ Quick improve failed. Your original draft is still ready.")
        return AWAIT_APPROVAL

    # Edit the ORIGINAL draft message in place so the chat keeps a single
    # living draft instead of stacking a second full preview underneath. The
    # tiny status line is dismissed once the in-place edit lands.
    preview = _format_draft_preview(updated, _chosen_form_reason(context, form_type))
    header = "✨ *Revised draft* — polished from the first version.\n\n"
    await _safe_edit_text(
        query.message,
        header + preview + _REPLY_HINT_SUFFIX,
        reply_markup=_active_draft_keyboard(context),
        parse_mode="Markdown",
    )
    await _dismiss_quick_improve_ack(ack)
    return AWAIT_APPROVAL


async def handle_approval_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 'Edit' button — ask for free-text feedback to improve the draft."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_reply_markup(reply_markup=None)

    draft = _load_draft(context)
    if not draft:
        return await _resume_paused_flow(
            update,
            context,
            "That earlier button is no longer active.",
        )

    await query.message.reply_text(
        "What would you like to change? Describe it in plain English — e.g. \"the reflection needs more learning points\" or \"add the SLO for shift leadership\".\n\nI'll regenerate the draft with your feedback."
    )
    return AWAIT_EDIT_VALUE


async def handle_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Enter per-field edit mode after a failed/partial filing.

    Sets edit_field so the next user reply is routed back through
    handle_edit_value, which regenerates the draft with the new value
    folded into the case context. Returns AWAIT_EDIT_VALUE so the
    conversation handler accepts text/voice/photo replies.
    """
    query = update.callback_query
    if query is None:
        return AWAIT_EDIT_VALUE
    await query.answer()
    field_key = query.data.split("|", 1)[1] if "|" in (query.data or "") else ""
    if not field_key:
        return AWAIT_EDIT_VALUE
    context.user_data["edit_field"] = field_key
    label = _friendly_field_name(field_key)
    await query.message.reply_text(
        f"What should *{label}* be? Send the corrected text, a voice note, or a photo "
        f"and I'll regenerate the draft with that change.",
        parse_mode="Markdown",
    )
    return AWAIT_EDIT_VALUE


async def handle_amend_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle amend-mode buttons for the last filed draft."""
    query = update.callback_query
    action = query.data.split("|", 1)[1] if "|" in query.data else "amend"

    if action == "cancel":
        await query.answer("Amend cancelled")
        for key in (
            "amend_mode",
            "draft_data",
            "case_text",
            "chosen_form",
            "amend_pending_feedback",
            "quick_improve_used",
        ):
            context.user_data.pop(key, None)
        await query.message.reply_text("Amend cancelled. Your filed Kaizen draft is unchanged.")
        return ConversationHandler.END

    if action == "cancel_choice":
        await query.answer("Still amending")
        context.user_data.pop("amend_pending_feedback", None)
        await query.message.reply_text(
            "No change made. You're still amending the current draft.",
            reply_markup=_active_draft_keyboard(context),
        )
        return AWAIT_APPROVAL

    if action == "update_current":
        await query.answer("Updating draft")
        pending = context.user_data.pop("amend_pending_feedback", "")
        return await _regenerate_active_draft_with_feedback(
            update,
            context,
            pending,
            append_to_case=True,
            input_source="text",
        )

    if action == "start_new":
        await query.answer("Starting new case")
        pending = context.user_data.pop("amend_pending_feedback", "")
        context.user_data.clear()
        if pending:
            await query.message.reply_text("Starting a new case from that message.")
            return await _process_case_text(query.message, context, update.effective_user.id, pending, "text")
        await query.message.reply_text(FILE_CASE_PROMPT)
        return AWAIT_CASE_INPUT

    await query.answer("Loading last draft...")

    amend_draft = context.user_data.get("last_amend_draft")
    amend_case = context.user_data.get("last_amend_case_text")
    amend_form = context.user_data.get("last_amend_chosen_form")

    if not amend_draft or not amend_case:
        await query.edit_message_text(
            "That draft is no longer available. Send a new case to start fresh."
        )
        return AWAIT_CASE_INPUT

    # Reload the draft data
    context.user_data["case_text"] = amend_case
    context.user_data["chosen_form"] = amend_form
    context.user_data["draft_data"] = amend_draft
    context.user_data["amend_mode"] = True
    context.user_data["quick_improve_used"] = False

    # Show the draft and enter approval state
    draft = _load_draft(context)
    preview = _format_draft_preview(draft, _chosen_form_reason(context, amend_form))
    await query.message.reply_text(
        "✏️ *Amending this draft.* Send changes, then tap *Save updated draft* or *Cancel amend*.\n\n"
        + preview,
        reply_markup=_build_amend_keyboard(improved_once=False),
        parse_mode="Markdown",
    )
    return AWAIT_APPROVAL


async def handle_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text, voice, photo, or document feedback — regenerate draft using original case + feedback."""
    draft = _load_draft(context)
    case_text = context.user_data.get("case_text", "")

    if not draft:
        return await _resume_paused_flow(
            update,
            context,
            "That earlier draft is no longer active.",
        )

    # Resolve feedback from any input modality (including forwarded messages)
    msg = update.message
    voice = _voice_media_from_message(msg)
    photo = msg.photo

    if voice:
        ack = await msg.reply_text("🎙️ Transcribing voice note…")
        tmp_path = None
        try:
            voice_file = await voice.get_file()
            with tempfile.NamedTemporaryFile(suffix=_voice_media_suffix(voice), delete=False) as tmp:
                tmp_path = tmp.name
                await voice_file.download_to_drive(tmp_path)
                feedback = await transcribe_voice(tmp_path)
            await ack.edit_text("✏️ Regenerating draft with your feedback…")
        except Exception as e:
            logger.error(f"Voice transcription in edit failed: {e}", exc_info=True)
            await ack.edit_text("⚠️ Couldn't transcribe voice note. Type your feedback instead.")
            return AWAIT_EDIT_VALUE
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
    elif photo:
        ack = await msg.reply_text("📷 Reading image…")
        tmp_path = None
        try:
            photo_file = await photo[-1].get_file()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
                await photo_file.download_to_drive(tmp_path)
                feedback = await extract_from_image(tmp_path)
            await ack.edit_text("✏️ Regenerating draft with your feedback…")
        except Exception as e:
            logger.error(f"Photo extraction in edit failed: {e}", exc_info=True)
            await ack.edit_text("⚠️ Couldn't read image. Type your feedback instead.")
            return AWAIT_EDIT_VALUE
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
    elif msg.text:
        feedback = msg.text.strip()
        ack = await msg.reply_text("✏️ Regenerating draft with your feedback…")
    else:
        # Unknown message type (sticker, gif, etc.) — stay in edit mode
        await msg.reply_text("💬 Send text, a voice note, or a photo with your feedback.")
        return AWAIT_EDIT_VALUE

    try:
        form_type = draft.form_type if isinstance(draft, FormDraft) else "CBD"
        current_draft_text = _format_draft_preview(draft)
        vp = get_voice_profile(update.effective_user.id) or ""

        if form_type == "CBD":
            updated = await asyncio.wait_for(extract_cbd_data(
                case_text,
                edit_feedback=feedback,
                current_draft=current_draft_text,
                voice_profile_json=vp,
                input_source=context.user_data.get("case_input_source", "text"),
            ), timeout=45)
        else:
            updated = await asyncio.wait_for(extract_form_data(
                case_text,
                form_type,
                edit_feedback=feedback,
                current_draft=current_draft_text,
                voice_profile_json=vp,
                input_source=context.user_data.get("case_input_source", "text"),
            ), timeout=45)
        _store_draft(context, updated)
    except asyncio.TimeoutError:
        await ack.edit_text("⏳ Regeneration timed out.", reply_markup=_active_draft_keyboard(context))
        return AWAIT_APPROVAL
    except Exception as e:
        await ack.edit_text("⚠️ Couldn't regenerate.", reply_markup=_active_draft_keyboard(context))
        return AWAIT_APPROVAL

    preview = _format_draft_preview(updated)
    await _safe_edit_text(ack, preview + _REPLY_HINT_SUFFIX, reply_markup=_active_draft_keyboard(context), parse_mode="Markdown")
    return AWAIT_APPROVAL


async def handle_mid_conversation_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle unexpected text messages mid-conversation (AWAIT_APPROVAL, AWAIT_EDIT_FIELD, AWAIT_FORM_CHOICE)."""
    # After a reset, treat ANY incoming message as a fresh case
    if context.user_data.pop("post_reset", False):
        context.user_data.clear()
        return await handle_case_input(update, context)

    _start_conversational_router_shadow(update, "handle_mid_conversation_text")

    raw_text = update.message.text.strip()
    case_text = context.user_data.get("case_text", "")
    has_draft = bool(_load_draft(context))
    has_pending = bool(context.user_data.get("pending_draft_data"))
    in_flow = has_draft or has_pending or bool(case_text) or bool(context.user_data.get("_pending_doc"))

    if context.user_data.get("_pending_doc"):
        existing = context.user_data.get("_pending_doc_context", "").strip()
        context.user_data["_pending_doc_context"] = (
            f"{existing}\n\n{raw_text}".strip() if existing else raw_text
        )
        await update.message.reply_text(
            "I've kept that as extra context. Your document choice is still pending - use the buttons above to choose whether to read it, attach it, or both."
        )
        return AWAIT_DOC_INTENT

    if _is_submit_inquiry(raw_text):
        if context.user_data.get("_pending_doc"):
            suffix = "\n\nYour document is still waiting above — choose how you want to use it."
            next_state = AWAIT_DOC_INTENT
        elif has_draft:
            suffix = "\n\nYour draft is still ready above — tap *Save as draft* when you have reviewed it."
            next_state = AWAIT_APPROVAL
        elif has_pending:
            suffix = "\n\nYour case is still in progress — use the buttons above to continue."
            next_state = AWAIT_TEMPLATE_REVIEW
        elif in_flow:
            suffix = "\n\nYour case is still in progress — use the buttons above to continue."
            next_state = AWAIT_FORM_CHOICE
        else:
            suffix = ""
            next_state = AWAIT_CASE_INPUT
        await update.message.reply_text(
            _DRAFT_ONLY_CLARIFICATION + suffix,
            parse_mode="Markdown",
        )
        return next_state

    if _is_text_filing_approval(raw_text):
        if _load_draft(context) or _restore_retryable_draft(context):
            return await handle_approval_approve(update, context)

    if (
        _has_retryable_failed_filing_draft(context)
        and not _is_recent_filing_status_question(raw_text)
    ):
        return await _show_failed_filing_input_gate(
            update.message,
            context,
            raw_text,
        )

    try:
        intent = await classify_intent(raw_text, case_context=case_text)
    except Exception:
        intent = "new_case"

    # Check if we're in a state with an active draft
    amend_mode = bool(context.user_data.get("amend_mode") and has_draft)

    if _is_text_filing_approval(raw_text) and context.user_data.get("last_filing_status") == "success":
        form_name = context.user_data.get("last_filing_form_name", "that draft")
        await update.message.reply_text(
            f"✅ {form_name} was already saved to Kaizen as a draft. Send a new case when you're ready."
        )
        return ConversationHandler.END

    if _is_recent_filing_status_question(raw_text) and context.user_data.get("last_filing_status"):
        form_name = context.user_data.get("last_filing_form_name", "your last draft")
        status = context.user_data.get("last_filing_status")
        if status == "success":
            await update.message.reply_text(f"✅ Yes — {form_name} was saved to Kaizen as a draft.")
        elif status == "partial":
            await update.message.reply_text(
                f"⚠️ {form_name} was partly filed, but needs checking in Kaizen before you rely on it."
            )
        else:
            await update.message.reply_text(
                f"❌ {form_name} did not complete. Try again from the latest draft or start fresh."
            )
        return ConversationHandler.END

    if amend_mode:
        if _looks_like_explicit_new_case_request(raw_text):
            context.user_data["amend_pending_feedback"] = raw_text
            await update.message.reply_text(
                "Looks like this may be a new case. Do you want to update this draft or start a new case?",
                reply_markup=_build_amend_new_case_choice_keyboard(),
            )
            return AWAIT_APPROVAL
        return await _regenerate_active_draft_with_feedback(
            update,
            context,
            raw_text,
            append_to_case=True,
            input_source="text",
        )

    if intent == "chitchat":
        if has_draft:
            await update.message.reply_text(
                "Still here — your draft is ready above. Tap *Save as draft* when ready, or *Edit* to change it.",
                parse_mode="Markdown"
            )
            return AWAIT_APPROVAL
        if in_flow:
            await update.message.reply_text(
                "Still here! Your case is in progress — use the buttons above to continue."
            )
            # Return current state — don't clear anything
            if has_pending:
                return AWAIT_TEMPLATE_REVIEW
            return AWAIT_FORM_CHOICE
        await update.message.reply_text(
            "Hey! Ready when you are. Send me a clinical case and I'll draft it for your portfolio."
        )
        return AWAIT_CASE_INPUT

    elif intent in ("question_general", "question_about_case"):
        try:
            answer = style_grounded_answer(await answer_question(raw_text, case_context=case_text))
            if has_draft:
                await update.message.reply_text(
                    f"{answer}\n\nYour draft is ready above — tap Save as draft when ready."
                )
                return AWAIT_APPROVAL
            if in_flow:
                await update.message.reply_text(
                    f"{answer}\n\nYour case is still in progress — use the buttons above to continue."
                )
                return AWAIT_FORM_CHOICE
            await update.message.reply_text(answer)
        except Exception:
            await update.message.reply_text(
                "I help you file clinical cases to your Kaizen e-portfolio. "
                "Send me a case description by text, voice note, photo, or document."
            )
        return AWAIT_CASE_INPUT

    else:
        # When the user has an active draft awaiting approval, try treating
        # "edit_detail" as a natural-language edit instruction first
        # (e.g. "change the date to last Tuesday", "set patient age to 67").
        if intent == "edit_detail" and has_draft:
            draft = _load_draft(context)
            chosen_form = context.user_data.get("chosen_form") or getattr(draft, "form_type", None)
            try:
                updates = await extract_field_updates(
                    chosen_form or "",
                    dict(draft.fields) if hasattr(draft, "fields") else {},
                    raw_text,
                )
            except Exception:
                logger.warning("extract_field_updates threw", exc_info=True)
                updates = {}

            summary = updates.pop("__summary__", "") if isinstance(updates, dict) else ""
            if updates:
                for field_name, new_value in updates.items():
                    draft.fields[field_name] = new_value
                _store_draft(context, draft)
                preview = _format_draft_preview(draft, _chosen_form_reason(context, chosen_form))
                ack_line = f"✏️ Updated: {summary}\n\n" if summary else "✏️ Draft updated.\n\n"
                await update.message.reply_text(
                    ack_line + preview + _REPLY_HINT_SUFFIX,
                    reply_markup=_active_draft_keyboard(context),
                    parse_mode="Markdown",
                )
                return AWAIT_APPROVAL
            # No matched fields — fall through to regeneration below.

        # Treat any text reply as edit feedback when we have a draft to refine.
        # (Explicit "new_case" intent still routes to the warning path below.)
        if has_draft and intent != "new_case" and case_text:
            return await _regenerate_active_draft_with_feedback(update, context, raw_text)

        # Before a form has been chosen there is no draft to abandon yet. In
        # practice Telegram users often send a long case in chunks; fold the
        # next clinical-looking message into the active case and refresh the
        # recommendation instead of making them cancel twice.
        if (
            case_text
            and not has_draft
            and not has_pending
            and (
                intent == "new_case"
                or _looks_like_new_case_start(raw_text)
                or (len(raw_text.split()) >= 10 and _looks_like_clinical_case(raw_text))
            )
        ):
            combined_case = f"{case_text.strip()}\n\n{raw_text}".strip()
            return await _process_case_text(
                update.message,
                context,
                update.effective_user.id,
                combined_case,
                context.user_data.get("case_input_source", "text"),
            )

        # new_case — looks like a new case
        if in_flow:
            await update.message.reply_text(
                "It looks like you want to file a new case. Tap Cancel to abandon the current draft, then send your new case.",
                reply_markup=_KB_CANCEL,
            )
            return AWAIT_CASE_INPUT
        else:
            # No active draft/case — go straight to fresh case
            context.user_data.clear()
            return await handle_case_input(update, context)


# === BULK / UNSIGNED / CHASE COMMANDS ===

async def bulk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /bulk — disabled for now, coming in a future update."""
    await update.message.reply_text("📦 Bulk filing is coming soon. For now, send cases one at a time.")
    return
    # --- Original implementation below (disabled) ---
    user_id = update.effective_user.id
    creds = get_credentials(user_id)
    if not creds:
        await update.message.reply_text("Connect your Kaizen account first with /setup")
        return

    text = (update.message.text or "").replace("/bulk", "", 1).strip()
    if not text:
        await update.message.reply_text(
            "Usage: /bulk followed by a JSON array of entries.\n"
            'Each entry: {"form_type": "CBD", "fields": {...}}'
        )
        return

    try:
        import json as _json
        entries = _json.loads(text)
        if not isinstance(entries, list):
            await update.message.reply_text("Expected a JSON array of entries.")
            return
    except _json.JSONDecodeError as e:
        await update.message.reply_text(f"Invalid JSON: {e}")
        return

    msg = await update.message.reply_text(f"Filing {len(entries)} entries...")
    credentials = {"username": creds[0], "password": creds[1]}

    results = await bulk_file(entries, credentials)

    # Send progress summary
    success = sum(1 for r in results if r["status"] in ("success", "partial"))
    failed = sum(1 for r in results if r["status"] == "failed")
    lines = [f"Filed {success}/{len(entries)} — {failed} failed"]
    for r in results:
        status_icon = "✅" if r["status"] in ("success", "partial") else "❌"
        err = f": {r['error']}" if r.get("error") else ""
        lines.append(f"{status_icon} {r['form_type']}{err}")

    await msg.edit_text("\n".join(lines))


async def _show_unsigned_range_picker(target_message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the date-range picker for the unsigned-tickets scan."""
    context.user_data.pop("awaiting_unsigned_range", None)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Last 3 months", callback_data="UNSIGNED|3m"),
         InlineKeyboardButton("📅 Last 6 months", callback_data="UNSIGNED|6m")],
        [InlineKeyboardButton("📅 Last 12 months", callback_data="UNSIGNED|12m"),
         InlineKeyboardButton("📅 All-time", callback_data="UNSIGNED|all")],
        [InlineKeyboardButton("✏️ Custom range", callback_data="UNSIGNED|custom")],
        [InlineKeyboardButton("❌ Cancel", callback_data="UNSIGNED|cancel")],
    ])
    await target_message.reply_text(
        "📅 Pick a date range for the unsigned-ticket scan.",
        reply_markup=keyboard,
    )


def _parse_unsigned_range(text: str) -> tuple["datetime | None", "datetime | None"] | None:
    """Parse a typed date range like '01/04/2025 to 31/03/2026'.

    Accepts d/m/yyyy or dd/mm/yyyy with " to " or "-" or "→" between dates.
    Returns (from_date, to_date) or None if unparseable.
    """
    from datetime import datetime
    cleaned = text.strip().lower().replace("→", " to ").replace(" - ", " to ").replace(" – ", " to ")
    parts = [p.strip() for p in cleaned.split(" to ") if p.strip()]
    if len(parts) != 2:
        return None
    formats = ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"]
    parsed = []
    for part in parts:
        d = None
        for fmt in formats:
            try:
                d = datetime.strptime(part, fmt)
                break
            except ValueError:
                continue
        if not d:
            return None
        parsed.append(d)
    return parsed[0], parsed[1]


async def _run_unsigned_scan(
    target_message,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    from_date,
    to_date,
    label: str,
) -> None:
    """Run the unsigned-tickets scan with the given date range and report results."""
    creds = get_credentials(user_id)
    msg = await target_message.reply_text(
        f"🔍 Scanning Kaizen for unsigned tickets ({label}) — this can take up to a minute…"
    )
    try:
        tickets = await asyncio.wait_for(
            scrape_unsigned_tickets(creds[0], creds[1], from_date=from_date, to_date=to_date),
            timeout=90,
        )
    except asyncio.TimeoutError:
        await msg.edit_text("⏱ Kaizen took too long to respond. Try again in a moment.")
        return
    except Exception as exc:
        logger.warning("Unsigned scrape errored: %s", exc, exc_info=True)
        await msg.edit_text("Could not scan Kaizen — try again in a moment.")
        return

    if not tickets:
        await msg.edit_text(f"✅ No unsigned tickets found ({label}).")
        return

    by_assessor: dict[str, list] = {}
    for t in tickets:
        name = t.get("assessor_name") or "Unknown"
        by_assessor.setdefault(name, []).append(t)

    lines = [f"📬 *Unsigned tickets — {label}: {len(tickets)} total*\n"]
    for assessor, tix in sorted(by_assessor.items(), key=lambda kv: -len(kv[1])):
        dates = [t["event_date"] for t in tix if t.get("event_date")]
        oldest = min(dates) if dates else "?"
        allowed, reason = chase_guard.check_allowed(assessor)
        chase_icon = "🟢" if allowed else "🔴"
        lines.append(f"{chase_icon} *{assessor}* — {len(tix)} ticket(s), oldest {oldest}")
        lines.append(f"   _{reason}_")
    lines.append("\n🟢 = chase allowed   🔴 = chase blocked (cooldown / cap reached)")
    lines.append("\nOpen Kaizen to send a reminder: https://kaizenep.com/activities")
    await msg.edit_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


async def handle_unsigned_range_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle UNSIGNED|<choice> callback — runs preset scans or prompts for custom range."""
    from datetime import datetime, timedelta
    query = update.callback_query
    await query.answer()
    choice = query.data.split("|", 1)[1] if "|" in query.data else ""
    user_id = update.effective_user.id

    if choice == "cancel":
        context.user_data.pop("awaiting_unsigned_range", None)
        await query.edit_message_text("Cancelled.")
        return

    if choice == "custom":
        context.user_data["awaiting_unsigned_range"] = True
        await query.edit_message_text(
            "✏️ Send the date range, like:\n\n"
            "`01/04/2025 to 31/03/2026`\n\n"
            "Format: dd/mm/yyyy on each side, separated by ' to '.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="UNSIGNED|cancel")],
            ]),
        )
        return

    today = datetime.now()
    if choice == "3m":
        from_date, to_date, label = today - timedelta(days=92), today, "last 3 months"
    elif choice == "6m":
        from_date, to_date, label = today - timedelta(days=183), today, "last 6 months"
    elif choice == "12m":
        from_date, to_date, label = today - timedelta(days=365), today, "last 12 months"
    elif choice == "all":
        from_date, to_date, label = None, None, "all-time"
    else:
        await query.edit_message_text("Unknown choice.")
        return

    # Replace the picker with an acknowledgement; the scan reply is a fresh message.
    try:
        await query.edit_message_text(f"📅 Scanning {label}…")
    except Exception:
        pass
    await _run_unsigned_scan(query.message, context, user_id, from_date, to_date, label)


async def unsigned_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /unsigned — Unlimited feature. Shows date-range picker, then scans Kaizen."""
    user_id = update.effective_user.id

    if not has_credentials(user_id):
        await update.message.reply_text(
            "🔗 Connect your Kaizen account first.\n\nUse /setup to get started.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⚙️ Connect Kaizen", callback_data="ACTION|setup")
            ]])
        )
        return

    tier = await get_user_tier(user_id)
    if tier != "pro_plus":
        await update.message.reply_text(
            "📬 Unsigned ticket scanning is included in Portfolio Guru Unlimited.\n\n"
            "Upgrade to see all your pending assessments grouped by assessor, with chase guardrails (14-day cooldown, max 3 per assessor).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐⭐ Upgrade to Unlimited", callback_data="UPGRADE|pro_plus")],
            ]),
        )
        return

    await _show_unsigned_range_picker(update.message, context)


async def chase_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /chase — coming soon."""
    await update.message.reply_text(
        "📬 Assessor reminders are coming soon.\n\n"
        "This feature will let you send reminders directly through Kaizen for unsigned tickets."
    )
    return
    # --- Original implementation below (needs unsigned ticket integration) ---
    text = (update.message.text or "").replace("/chase", "", 1).strip()
    if not text:
        await update.message.reply_text("Usage: /chase <assessor_email>")
        return

    email = text.split()[0]
    allowed, reason = chase_guard.check_allowed(email)

    if not allowed:
        await update.message.reply_text(f"🔴 {reason}")
        return

    # Build chase template
    chases = chase_guard.get_assessor_chases(email)
    chase_num = len(chases) + 1
    template = (
        f"🟢 Chase #{chase_num} allowed for {email}\n\n"
        f"Suggested message:\n"
        f"---\n"
        f"Dear colleague,\n\n"
        f"I hope you are well. I have an outstanding portfolio entry awaiting your review "
        f"on Kaizen. I would be very grateful if you could sign it at your convenience.\n\n"
        f"Many thanks.\n"
        f"---\n\n"
        f"Send the chase yourself, then tap Confirm to log it."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm chase sent", callback_data=f"CHASE_LOG|{email}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="CHASE_LOG|cancel")],
    ])
    await update.message.reply_text(template, reply_markup=keyboard)


async def handle_chase_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle chase confirmation callback."""
    query = update.callback_query
    await query.answer()
    data = query.data.replace("CHASE_LOG|", "")

    if data == "cancel":
        await query.edit_message_text("Chase request closed.")
        await query.message.reply_text(
            _cancelled_next_step_text(update.effective_user.id),
            reply_markup=_build_next_step_keyboard(update.effective_user.id),
        )
        return

    email = data
    entry = chase_guard.log_chase(email=email, name=email, method="manual", telegram_user_id=update.effective_user.id)
    await query.edit_message_text(
        f"✅ Chase #{entry['chase_number']} logged for {email} on {entry['date']}"
    )


# === APPLICATION BUILDER ===

def build_application() -> Application:
    """Build and return the Telegram bot Application with all handlers registered."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN env var not set")

    persistence_path = os.path.expanduser("~/.openclaw/data/portfolio-guru/bot_persistence")
    os.makedirs(os.path.dirname(persistence_path), exist_ok=True)
    persistence = PicklePersistence(filepath=persistence_path)

    application = (
        Application.builder()
        .token(token)
        .persistence(persistence)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    # Main conversation handler for case filing flow
    case_conv = ConversationHandler(
        entry_points=[
            # /start enters the conversation so it's handled by a single handler,
            # not duplicated across group=0 fallbacks + group=1 standalone.
            CommandHandler("start", start),
            # Let thin-case buttons re-enter the case conversation even if the
            # user taps them after the active state has been lost.
            CallbackQueryHandler(handle_callback, pattern=r"^ACTION\|(?:file|reset|cancel|continue_thin|unsigned|status|health|help|voice)$"),
            CallbackQueryHandler(handle_same_case_another, pattern=r"^ACTION\|same_case_another$"),
            CallbackQueryHandler(handle_amend_draft, pattern=r"^AMEND\|"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_case_input),
            MessageHandler(filters.VOICE, handle_case_input),
            MessageHandler(filters.AUDIO, handle_case_input),
            MessageHandler(filters.PHOTO, handle_case_input),
            MessageHandler(filters.Document.ALL, handle_case_input),
        ],
        states={
            AWAIT_CASE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_case_input),
                MessageHandler(filters.VOICE, handle_case_input),
                MessageHandler(filters.AUDIO, handle_case_input),
                MessageHandler(filters.PHOTO, handle_case_input),
                MessageHandler(filters.Document.ALL, handle_case_input),
                CallbackQueryHandler(handle_callback, pattern=r"^ACTION\|continue_thin$"),
            ],
            AWAIT_GATHERING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_gathering_input),
                MessageHandler(filters.VOICE, handle_case_input),
                MessageHandler(filters.AUDIO, handle_case_input),
                MessageHandler(filters.PHOTO, handle_case_input),
                MessageHandler(filters.Document.ALL, handle_case_input),
                CallbackQueryHandler(gather_done_callback, pattern=r"^GATHER\|done$"),
                CallbackQueryHandler(handle_callback, pattern=r"^ACTION\|cancel$"),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
            ],
            AWAIT_DOC_INTENT: [
                CallbackQueryHandler(handle_document_intent, pattern=r"^DOCUSE\|"),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mid_conversation_text),
            ],
            AWAIT_FORM_CHOICE: [
                CallbackQueryHandler(handle_form_choice, pattern=r"^FORM\|"),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
                CallbackQueryHandler(handle_callback, pattern=r"^ACTION\|(?:retry_recommend|retry_template)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mid_conversation_text),
                MessageHandler(filters.VOICE, handle_case_input),
                MessageHandler(filters.AUDIO, handle_case_input),
                MessageHandler(filters.PHOTO, handle_case_input),
                MessageHandler(filters.Document.ALL, handle_case_input),
            ],
            AWAIT_FORM_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_form_search_text),
                MessageHandler(filters.VOICE, handle_case_input),
                MessageHandler(filters.AUDIO, handle_case_input),
                MessageHandler(filters.PHOTO, handle_case_input),
                MessageHandler(filters.Document.ALL, handle_case_input),
                CallbackQueryHandler(handle_form_choice, pattern=r"^FORM\|"),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
            ],
            AWAIT_TEMPLATE_REVIEW: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_template_review_text),
                MessageHandler(filters.VOICE, handle_template_review_media),
                MessageHandler(filters.AUDIO, handle_template_review_media),
                MessageHandler(filters.PHOTO, handle_template_review_media),
                MessageHandler(filters.VIDEO, handle_template_review_media),
                MessageHandler(filters.Document.ALL, handle_template_review_media),
                CallbackQueryHandler(handle_callback, pattern=r"^CASE\|"),
                CallbackQueryHandler(handle_form_choice, pattern=r"^FORM\|"),
                CallbackQueryHandler(handle_callback, pattern=r"^ACTION\|continue_thin$"),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
            ],
            AWAIT_APPROVAL: [
                CallbackQueryHandler(handle_approval_submit, pattern=r"^APPROVE\|submit$"),
                CallbackQueryHandler(handle_approval_approve, pattern=r"^APPROVE\|draft$"),
                CallbackQueryHandler(handle_quick_improve, pattern=r"^IMPROVE\|reflection$"),
                CallbackQueryHandler(handle_review_draft, pattern=r"^REVIEW\|draft$"),
                CallbackQueryHandler(handle_approval_edit, pattern=r"^EDIT\|"),
                CallbackQueryHandler(handle_edit_field, pattern=r"^FIELD\|"),
                CallbackQueryHandler(handle_amend_draft, pattern=r"^AMEND\|"),
                CallbackQueryHandler(handle_callback, pattern=r"^ACTION\|retry_filing$"),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mid_conversation_text),
                MessageHandler(filters.VOICE, handle_approval_media_feedback),
                MessageHandler(filters.AUDIO, handle_approval_media_feedback),
                MessageHandler(filters.PHOTO, handle_approval_media_feedback),
                MessageHandler(filters.VIDEO, handle_approval_media_feedback),
                MessageHandler(filters.Document.ALL, handle_approval_media_feedback),
            ],
            AWAIT_EDIT_FIELD: [
                CallbackQueryHandler(handle_edit_field, pattern=r"^FIELD\|"),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mid_conversation_text),
            ],
            AWAIT_EDIT_VALUE: [
                # Text goes through intent check; non-text (voice, photo, doc) passes straight through
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_value_with_intent),
                MessageHandler(~filters.COMMAND & ~filters.TEXT, handle_edit_value),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("help", help_command),
            CommandHandler("settings", settings_command),
            CommandHandler("gather", gather_command),
            CommandHandler("cancel", setup_cancel),
            CallbackQueryHandler(
                handle_callback,
                pattern=r"^(?:INFO\|.*|CANCEL\|.*|ACTION\|(?:file|reset|cancel|continue_thin|retry_filing|retry_recommend|retry_template))$",
            ),
        ],
        per_message=False,
        allow_reentry=False,
        persistent=True,
        name="case_conv",
    )

    # Setup conversation handler
    setup_conv = ConversationHandler(
        entry_points=[
            CommandHandler("setup", setup_start),
            CallbackQueryHandler(setup_start, pattern=r"^ACTION\|setup$"),
        ],
        states={
            AWAIT_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, setup_username),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, _setup_wrong_input),
            ],
            AWAIT_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, setup_password),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, _setup_wrong_input),
            ],
            AWAIT_TRAINING_LEVEL: [CallbackQueryHandler(setup_training_level, pattern=r"^SETLEVEL\|")],
            AWAIT_CURRICULUM: [CallbackQueryHandler(setup_curriculum, pattern=r"^SETUP_CURRICULUM\|")],
        },
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", setup_cancel)],
        allow_reentry=True,
    )

    # Register handlers
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("delete", delete_data))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("link", link_command))
    application.add_handler(CommandHandler("bulk", bulk_command))
    application.add_handler(CommandHandler("unsigned", unsigned_command))
    application.add_handler(CommandHandler("chase", chase_command))
    application.add_handler(CommandHandler("curriculum", curriculum_command))
    application.add_handler(CommandHandler("health", health_command))
    application.add_handler(CommandHandler("upgrade", upgrade_command))
    application.add_handler(CommandHandler("plan", upgrade_command))
    application.add_handler(CommandHandler("settier", settier_command))
    application.add_handler(CommandHandler("setbeta", setbeta_command))
    application.add_handler(CommandHandler("beta", beta_command))
    application.add_handler(CommandHandler("listusers", listusers_command))
    application.add_handler(CommandHandler("filingreport", filingreport_command))
    application.add_handler(CommandHandler("gather", gather_command))
    application.add_handler(CommandHandler("assignbeta", assignbeta_command))
    application.add_handler(CallbackQueryHandler(handle_upgrade_button, pattern=r"^UPGRADE\|"))
    application.add_handler(CallbackQueryHandler(handle_unsigned_range_pick, pattern=r"^UNSIGNED\|"))
    application.add_handler(CallbackQueryHandler(handle_set_curriculum, pattern=r"^SET_CURRICULUM\|"))
    # NOTE: handle_set_level (global SETLEVEL handler for /settings → change
    # portfolio) is registered AFTER setup_conv below. setup_password's manual
    # fallback uses the same SETLEVEL|* callback prefix; if the global handler
    # is checked first PTB picks it before the conv handler and the user
    # silently exits setup with "Back to settings". setup_conv only matches
    # when the user is in AWAIT_TRAINING_LEVEL, so when no conv is active the
    # global handler still services the /settings flow.
    application.add_handler(CallbackQueryHandler(handle_pathway_choice, pattern=r"^PATHWAY_SETTINGS\|"))
    application.add_handler(CallbackQueryHandler(handle_chase_log, pattern=r"^CHASE_LOG\|"))
    # Top-level handlers that must work regardless of conversation state
    application.add_handler(CallbackQueryHandler(handle_info_button, pattern=r"^INFO\|"))
    application.add_handler(
        CallbackQueryHandler(
            handle_action_button,
            pattern=r"^ACTION\|(?!file$|reset$|cancel$|continue_thin$|setup$|voice$|same_case_another$).+",
        )
    )
    application.add_handler(CallbackQueryHandler(handle_feedback, pattern=r"^FEEDBACK\|"))
    application.add_handler(CallbackQueryHandler(handle_filing_feedback, pattern=r"^FILING\|feedback\|"))
    application.add_handler(CallbackQueryHandler(handle_pushback, pattern=r"^PUSHBACK\|"))

    # Clinical Supervisor read-only callbacks (Open / Skip / Later on assessor
    # notifications, plus Review / Re-record / Cancel for the post-Open local
    # draft). Inert until a user is cached as kaizen_role=="assessor" by
    # supervisor_workflow.set_role_if_better.
    from supervisor_bot import (
        CALLBACK_PATTERN as SUPERVISOR_CALLBACK_PATTERN,
        handle_assessor_intent_capture,
        handle_supervisor_callback,
    )
    application.add_handler(
        CallbackQueryHandler(handle_supervisor_callback, pattern=SUPERVISOR_CALLBACK_PATTERN)
    )
    # Assessor intent capture runs in a higher-priority group so the
    # supervisor's text/voice note is consumed by the assessor draft
    # pipeline when a session is active. Inert (returns without raising)
    # for any user without an active session, so trainee flows in the
    # default group keep working untouched. Commands are excluded so
    # global `/cancel`, `/start`, etc. always reach the trainee handlers.
    application.add_handler(
        MessageHandler(
            (filters.TEXT & ~filters.COMMAND) | filters.VOICE | filters.AUDIO,
            handle_assessor_intent_capture,
        ),
        group=-1,
    )

    voice_conv = ConversationHandler(
        entry_points=[
            CommandHandler("voice", voice_start),
            # Settings → "Set up voice profile" tap also enters the conv so the
            # follow-up VOICE|* clicks land in AWAIT_VOICE_EXAMPLES.
            CallbackQueryHandler(voice_start, pattern=r"^ACTION\|voice$"),
        ],
        states={
            AWAIT_VOICE_EXAMPLES: [
                CallbackQueryHandler(voice_collect_example, pattern=r"^VOICE\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, voice_collect_example),
                MessageHandler(filters.PHOTO, voice_collect_example),
                MessageHandler(filters.VOICE, voice_collect_example),
            ],
        },
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", setup_cancel)],
        allow_reentry=True,
    )

    pathway_conv = ConversationHandler(
        entry_points=[CommandHandler("pathway", pathway_command)],
        states={
            AWAIT_PATHWAY: [CallbackQueryHandler(handle_pathway_choice, pattern=r"^PATHWAY\|")],
        },
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", setup_cancel)],
        allow_reentry=True,
    )

    application.add_handler(setup_conv)
    # Register the global SETLEVEL handler AFTER setup_conv so the conv
    # handler gets first crack at SETLEVEL|* clicks while the user is in
    # AWAIT_TRAINING_LEVEL. Outside the conv state the conv handler returns
    # None and PTB falls through to handle_set_level, preserving the
    # /settings → change portfolio flow.
    application.add_handler(CallbackQueryHandler(handle_set_level, pattern=r"^SETLEVEL\|"))
    application.add_handler(voice_conv)
    application.add_handler(pathway_conv)
    application.add_handler(case_conv)
    # /start is handled by case_conv entry point (when not in a conversation) or
    # case_conv fallback (when in case_conv). Do NOT add a standalone handler at
    # group=1 — PTB fires ALL matching handler registrations across all groups,
    # and a duplicate would cause duplicate welcome messages.

    # NOTE: CallbackQueryHandler already registered in case_conv fallbacks.
    # Do NOT add a second one here — causes duplicate message delivery.

    return application


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and notify user with context-appropriate messages."""
    logger.error("Exception while handling an update:", exc_info=context.error)

    error_msg = str(context.error).lower() if context.error else ""

    # Stale callback query — button tapped after Telegram's ~30s window
    if "query is too old" in error_msg or "query id is invalid" in error_msg:
        logger.info("Stale callback query — ignoring gracefully")
        # Can't answer the query (it's expired), but we can send a message
        if update and hasattr(update, 'effective_message') and update.effective_message:
            callback_data = getattr(getattr(update, "callback_query", None), "data", "") or ""
            if callback_data.startswith("VOICE|"):
                await _send_latest_message(
                    update.effective_message,
                    context,
                    "That older voice-profile button is no longer active. Your setup is still saved — open Voice Profile and I'll rebuild it with you.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✍️ Open Voice Profile", callback_data="ACTION|voice")],
                    ]),
                )
                return
            await _resume_paused_flow(
                update,
                context,
                "That earlier button is no longer active.",
            )
        return

    # Conflict error from dual bot instances — silent, self-resolving
    if "conflict" in error_msg and "terminated by other" in error_msg:
        logger.warning("409 Conflict — another bot instance running, will self-resolve")
        return

    # Generic fallback — preserve draft if we're in approval state
    if update and hasattr(update, 'effective_message') and update.effective_message:
        # Check if we have a draft in user_data (means we're in approval flow)
        draft = None
        if hasattr(context, 'user_data'):
            draft = _load_draft(context)
        
        if draft:
            # We have a draft — offer retry + start fresh
            retry_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|retry_filing")],
                [InlineKeyboardButton("🆕 Start fresh", callback_data="ACTION|reset")],
            ])
            await _edit_last_bot_msg(
                context,
                update.effective_message.chat_id,
                "Something went wrong while filing. Try again or start fresh.",
                reply_markup=retry_keyboard,
            )
        else:
            # No draft — just start fresh
            await _edit_last_bot_msg(
                context,
                update.effective_message.chat_id,
                "Something went wrong. Use the latest message to start again.",
                reply_markup=_build_next_step_keyboard(update.effective_user.id),
            )


def main():
    """Entry point for local development - runs in polling mode."""
    import requests as _req
    import subprocess as _subprocess
    import portalocker

    # Process lock to prevent duplicate instances/polling conflicts
    lock_file = os.path.join(os.path.dirname(__file__), "portfolio-guru-bot.lock")
    global _lock_fp
    _lock_fp = open(lock_file, "w")
    try:
        portalocker.lock(_lock_fp, portalocker.LOCK_EX | portalocker.LOCK_NB)
        logger.info("Successfully acquired process lock. No duplicate instances.")
    except portalocker.exceptions.LockException:
        logger.error("FATAL: Another instance of Portfolio Guru bot is already running! (Failed to acquire file lock)")
        sys.exit(1)

    init()
    init_profile_db()

    # Clear any existing webhook so polling works
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    _req.post(f"https://api.telegram.org/bot{token}/deleteWebhook", json={"drop_pending_updates": True})
    logger.info("Webhook cleared - polling mode active")

    application = build_application()
    application.add_error_handler(error_handler)

    # Weekly portfolio digest — Sunday 20:00 UK time (handles BST/GMT).
    # JobQueue.run_daily uses ISO weekdays: Monday=0 … Sunday=6.
    from datetime import time as _dtime
    try:
        from zoneinfo import ZoneInfo as _ZoneInfo
        _uk_tz = _ZoneInfo("Europe/London")
    except Exception:
        _uk_tz = None  # fall back to UTC if tzdata is unavailable
    application.job_queue.run_daily(
        weekly_push,
        time=_dtime(hour=20, minute=0, tzinfo=_uk_tz),
        days=(6,),
        name="weekly_push",
    )

    # Clinical Supervisor poll — read-only, inert unless there is at least
    # one user with cached kaizen_role=="assessor" AND credentials AND a
    # reachable CDP session. Trainee-only deployments stay silent.
    from supervisor_bot import connect_cdp_page, send_supervisor_notification
    from supervisor_scheduler import (
        SUPERVISOR_POLL_FIRST_RUN_SECONDS,
        SUPERVISOR_POLL_INTERVAL_SECONDS,
        supervisor_poll_tick,
    )

    async def _supervisor_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
        await supervisor_poll_tick(
            bot=context.bot,
            connect_cdp=connect_cdp_page,
            notify=send_supervisor_notification,
        )

    application.job_queue.run_repeating(
        _supervisor_tick,
        interval=SUPERVISOR_POLL_INTERVAL_SECONDS,
        first=SUPERVISOR_POLL_FIRST_RUN_SECONDS,
    )

    # Register commands so they appear in Telegram's "/" menu
    async def post_init(app):
        await app.bot.set_my_commands(BOT_COMMANDS)
        # Set bot description (shown on profile page before starting)
        try:
            await app.bot.set_my_description(render_message("bot_profile_description"))
            await app.bot.set_my_short_description(render_message("bot_profile_short_description"))
        except Exception:
            pass  # Non-critical — BotFather settings may not update on every restart
    application.post_init = post_init

    try:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        commit = _subprocess.check_output(
            ["git", "-C", repo_root, "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=_subprocess.DEVNULL,
        ).strip()
        branch = _subprocess.check_output(
            ["git", "-C", repo_root, "branch", "--show-current"],
            text=True,
            stderr=_subprocess.DEVNULL,
        ).strip() or "detached"
        logger.info("Portfolio Guru live commit: %s (%s)", commit, branch)
    except Exception:
        logger.info("Portfolio Guru live commit: unavailable")

    logger.info("Portfolio Guru v2 starting in POLLING mode...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
