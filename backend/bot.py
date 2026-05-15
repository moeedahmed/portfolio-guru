"""
Portfolio Guru Telegram Bot — v2
Multimodal input (text/voice/image) with approval flow before filing.
"""
import asyncio
import logging
import os
import tempfile
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler, PicklePersistence,
)
from store import store_credentials, get_credentials, has_credentials, init
from extractor import extract_cbd_data, extract_form_data, recommend_form_types, classify_intent, classify_menu_intent, answer_question, extract_explicit_form_type, review_draft, analyse_portfolio_health, summarise_recent_activity, generate_nudge_copy, extract_field_updates, compose_filing_recovery_copy, combine_case_inputs
from usage import record_case_filed, get_cases_this_month, check_can_file, get_user_tier, set_user_tier, get_case_history, TIER_LIMITS, get_all_active_users, get_cases_this_week
from filer_router import route_filing
from kaizen_form_filer import FORM_UUIDS
from form_schemas import FORM_SCHEMAS
from models import FormDraft, CBDData
from whisper import transcribe_voice
from vision import extract_from_image
from documents import extract_from_document, is_supported_document
from profile_store import init_profile_db, store_training_level, get_training_level, get_voice_profile, store_voice_profile, clear_voice_profile, store_curriculum, get_curriculum
from bulk_filer import bulk_file
from kaizen_unsigned_scraper import scrape_unsigned_tickets
import chase_guard

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_TELEGRAM_MSG = 4096


async def _safe_edit_text(target, text: str, **kwargs):
    """Edit message text, splitting if it exceeds Telegram's 4096 char limit.
    For the first chunk, passes kwargs (reply_markup, parse_mode) through.
    Subsequent chunks are sent as new messages with no markup."""
    if len(text) <= MAX_TELEGRAM_MSG:
        return await target.edit_text(text, **kwargs)

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
        return await target.edit_text(chunks[0], **kwargs)

    # First chunk edits the existing message (no buttons — they go on last chunk)
    markup = kwargs.pop("reply_markup", None)
    await target.edit_text(chunks[0], **kwargs)

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
    """Compute cases this week + longest form gap for a user."""
    from datetime import datetime, timezone
    import aiosqlite as _aiosqlite
    from usage import DB_PATH, _ensure_db

    await _ensure_db()
    cases = await get_cases_this_week(user_id)

    async with _aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT form_type, MAX(filed_at) as last_filed FROM portfolio_usage "
            "WHERE telegram_user_id = ? GROUP BY form_type",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()

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

    return {"cases": cases, "gap": gap}


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


async def weekly_push(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send weekly gap-detection nudge to all active users.

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
            text, keyboard = await _build_nudge_message(stats)
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=keyboard,
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
    context.user_data["last_bot_msg_id"] = msg.message_id
    context.user_data["last_bot_chat_id"] = msg.chat_id
    context.user_data["status_msg_id"] = msg.message_id
    context.user_data["status_msg_chat"] = msg.chat_id


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
    ):
        context.user_data.pop(key, None)
    if not keep_case:
        context.user_data.pop("case_text", None)

# ConversationHandler states
(AWAIT_USERNAME, AWAIT_PASSWORD,
 AWAIT_FORM_CHOICE, AWAIT_APPROVAL,
 AWAIT_EDIT_FIELD, AWAIT_EDIT_VALUE,
 AWAIT_CASE_INPUT, AWAIT_TRAINING_LEVEL,
 AWAIT_VOICE_EXAMPLES, AWAIT_TEMPLATE_REVIEW,
 AWAIT_CURRICULUM, AWAIT_FORM_SEARCH) = range(12)

# Common button patterns used across the bot
_BTN_RESET = InlineKeyboardButton("🆕 Start fresh", callback_data="ACTION|reset")
_BTN_SETUP = InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup")
_BTN_CANCEL = InlineKeyboardButton("❌ Cancel", callback_data="ACTION|cancel")
_BTN_HELP = InlineKeyboardButton("ℹ️ Help", callback_data="INFO|what")
_BTN_VOICE = InlineKeyboardButton("✍️ Voice Profile", callback_data="ACTION|voice")
_BTN_CONTINUE_THIN = InlineKeyboardButton("✅ Show me the draft", callback_data="ACTION|continue_thin")

_KB_RETRY_RESET = InlineKeyboardMarkup([[_BTN_RESET]])


def _setup_needs_finishing(user_id: int) -> bool:
    return not has_credentials(user_id)


def _build_next_step_keyboard(user_id: int, *, include_reset: bool = False) -> InlineKeyboardMarkup:
    if _setup_needs_finishing(user_id):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup")],
            [InlineKeyboardButton("ℹ️ How does this work?", callback_data="INFO|what")],
        ])
    # Connected user — the primary action is to send a case (just type it).
    # Surface secondary destinations only.
    rows = [
        [InlineKeyboardButton("⚙️ Settings", callback_data="ACTION|settings"),
         InlineKeyboardButton("ℹ️ Help", callback_data="INFO|what")],
    ]
    return InlineKeyboardMarkup(rows)


def _cancelled_next_step_text(user_id: int, scope: str = "Cancelled") -> str:
    if _setup_needs_finishing(user_id):
        return f"❌ {scope}. Finish setup when you're ready to file."
    return f"❌ {scope}. You can file another case whenever you're ready."


def _expired_prompt_text(user_id: int) -> str:
    if _setup_needs_finishing(user_id):
        return "⏳ That button has expired. Finish setup from the latest message and I'll pick it up from there."
    return "⏳ That button has expired. Start a new case from the latest message and I'll pick it up from there."

async def _resume_paused_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str) -> int:
    """Recover a paused case by sending a fresh latest message for the current step."""
    user_id = update.effective_user.id
    message = update.effective_message
    draft = _load_draft(context)
    pending_draft = _load_pending_draft(context)
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
            _format_draft_preview(draft),
            reply_markup=_build_approval_keyboard(),
            parse_mode="Markdown",
        )
        return AWAIT_APPROVAL

    if pending_draft and chosen_form:
        await _send_latest_message(
            message,
            context,
            f"{reason}\n\nYour case is still in progress — use the latest message below and I'll carry on from there.",
        )
        missing_required, missing_optional, _ = _missing_template_fields(pending_draft, chosen_form)
        if not missing_required:
            _store_draft(context, pending_draft)
            context.user_data.pop("awaiting_detail", None)
            context.user_data.pop("pending_draft_data", None)
            await _send_latest_message(
                message,
                context,
                _format_draft_preview(pending_draft, _chosen_form_reason(context, chosen_form)),
                reply_markup=_build_approval_keyboard(),
                parse_mode="Markdown",
            )
            return AWAIT_APPROVAL

        await _send_latest_message(
            message,
            context,
            _format_template_review(chosen_form, pending_draft),
            reply_markup=_build_template_review_keyboard(),
            parse_mode="Markdown",
        )
        return AWAIT_TEMPLATE_REVIEW

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
                reply_markup=_build_next_step_keyboard(user_id, include_reset=True),
            )
            return ConversationHandler.END
        except Exception as exc:
            logger.error("Paused flow rebuild failed for %s: %s", chosen_form, exc, exc_info=True)
            await _send_latest_message(
                message,
                context,
                "That case is still saved, but I couldn't rebuild the latest step just now. Start a new case below and I'll rebuild it with you.",
                reply_markup=_build_next_step_keyboard(user_id, include_reset=True),
            )
            return ConversationHandler.END

        missing_required, missing_optional, _ = _missing_template_fields(refreshed_draft, chosen_form)
        if not missing_required:
            _store_draft(context, refreshed_draft)
            await _send_latest_message(
                message,
                context,
                _format_draft_preview(refreshed_draft, _chosen_form_reason(context, chosen_form)),
                reply_markup=_build_approval_keyboard(),
                parse_mode="Markdown",
            )
            return AWAIT_APPROVAL

        await _send_latest_message(
            message,
            context,
            _format_template_review(chosen_form, refreshed_draft),
            reply_markup=_build_template_review_keyboard(),
            parse_mode="Markdown",
        )
        return AWAIT_TEMPLATE_REVIEW

    if case_text:
        recommendations = context.user_data.get("form_recommendations") or []
        if not recommendations:
            try:
                training_level = get_training_level(user_id)
                allowed_forms = TRAINING_LEVEL_FORMS.get(training_level, TRAINING_LEVEL_FORMS["ST5"]) if training_level else TRAINING_LEVEL_FORMS["ST5"]
                recommendations = await asyncio.wait_for(recommend_form_types(case_text), timeout=30)
                excluded_form = _normalise_form_type(context.user_data.get("excluded_form_type", ""))
                recommendations = [
                    r for r in recommendations
                    if r.form_type in allowed_forms and _normalise_form_type(r.form_type) != excluded_form
                ]
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
                reply_markup=_build_form_choice_keyboard(recommendations, curriculum=get_curriculum(user_id)),
                parse_mode="Markdown",
            )
            return AWAIT_FORM_CHOICE

    context.user_data.clear()
    await _send_latest_message(
        message,
        context,
        "That draft has expired, but your setup is still saved. Start a new case and I'll rebuild it with you.",
        reply_markup=_build_next_step_keyboard(user_id, include_reset=True),
    )
    return ConversationHandler.END

# Training level → form types available
TRAINING_LEVEL_FORMS = {
    "ST3": [
        "CBD", "DOPS", "MINI_CEX", "ACAT", "MSF", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH",
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
    "SAS": [
        "CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "STAT", "MSF", "QIAT", "JCF", "PROC_LOG", "SDL",
        "EDU_ACT", "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC",
        "REFLECT_LOG", "TEACH_OBS", "ESLE_ASSESS", "TEACH_CONFID", "APPRAISAL", "CLIN_GOV",
        "CRIT_INCIDENT", "AUDIT", "RESEARCH", "EDU_MEETING", "EDU_MEETING_SUPP", "PDP",
        "BUSINESS_CASE", "COST_IMPROVE", "EQUIP_SERVICE",
        "MGMT_ROTA", "MGMT_RISK", "MGMT_PROJECT",
        "MGMT_RECRUIT", "MGMT_RISK_PROC", "MGMT_TRAINING_EVT", "MGMT_GUIDELINE", "MGMT_INFO",
        "MGMT_INDUCTION", "MGMT_EXPERIENCE", "MGMT_REPORT", "MGMT_COMPLAINT",
    ],
}


TRAINING_LEVEL_FORMS["ACCS"] = TRAINING_LEVEL_FORMS["ST3"]
TRAINING_LEVEL_FORMS["INTERMEDIATE"] = TRAINING_LEVEL_FORMS["ST3"]
TRAINING_LEVEL_FORMS["HIGHER"] = TRAINING_LEVEL_FORMS["ST6"]

# Kaizen stage groups. Legacy ST3/ST4/ST5/ST6 values are still accepted for old profiles.
TRAINING_LEVEL_LABELS = {
    "ACCS": "ACCS (ST1–2)",
    "INTERMEDIATE": "Intermediate (ST3)",
    "HIGHER": "Higher (ST4–6)",
    "SAS": "SAS / Fellow",
    "ST3": "Intermediate (ST3)",
    "ST4": "Higher (ST4–6)",
    "ST5": "Higher (ST4–6)",
    "ST6": "Higher (ST4–6)",
}


def _training_level_label(level: str | None) -> str:
    return TRAINING_LEVEL_LABELS.get(level or "", "Unknown")


def _default_allowed_forms_for_unknown_training() -> list[str]:
    seen = set()
    forms = []
    for group_forms in TRAINING_LEVEL_FORMS.values():
        for form in group_forms:
            if form not in seen:
                seen.add(form)
                forms.append(form)
    return forms

# Category groupings for "See all forms" navigation
FORM_CATEGORIES = {
    "🩺 Clinical": ["CBD", "DOPS", "MINI_CEX", "ACAT", "LAT", "ACAF", "STAT", "MSF", "QIAT", "JCF", "ESLE_ASSESS", "AUDIT"],
    "📝 Reflective": ["REFLECT_LOG", "COMPLAINT", "SERIOUS_INC", "CRIT_INCIDENT", "PDP", "APPRAISAL"],
    "👨‍🏫 Teaching": ["TEACH", "TEACH_OBS", "TEACH_CONFID", "SDL", "EDU_ACT", "EDU_MEETING", "EDU_MEETING_SUPP", "FORMAL_COURSE"],
    "🔬 Procedural": ["PROC_LOG", "US_CASE"],
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

WELCOME_MSG = """🩺 Portfolio Guru — your WPBA entries, filed in seconds.

Send me what happened. Rough notes are fine — text, voice note, photo, or document.
I'll suggest the best WPBA types, then draft the one you pick.

Your credentials are encrypted and never shared.

Tap 🔗 Connect to get started."""

WELCOME_MSG_CONNECTED = """🩺 Portfolio Guru — ready when you are.

Send me what happened. Rough notes are fine — text, voice, photo, or document.
I'll suggest the best form and draft it once you choose.

Or use the menu below to check your portfolio status."""

_WHAT_IS_THIS_FORM_COUNT = max(len(v) for v in TRAINING_LEVEL_FORMS.values())

WHAT_IS_THIS_MSG = """🩺 Portfolio Guru files your WPBA entries — in seconds.

📝 Describe a case → 🔍 I pick the form → ✅ You approve → 📤 Filed to Kaizen

Send a case by text, voice note, photo, or document. I extract the fields, draft the entry, and save it to Kaizen as a draft when you approve. Nothing touches Kaizen without your OK.

All 45 RCEM forms supported — assessments, reflections, teaching, management, audit, and more."""

FILE_CASE_PROMPT = "Send me what happened. Rough notes are fine — text, voice note, photo, or document (PDF, PowerPoint, Word)."

FLOW_STATE_LABELS = {
    "captured": "Captured",
    "drafted": "Drafted",
    "needs_you": "Needs you",
    "filed_as_draft": "Filed as draft",
    "blocked": "Failed / blocked",
}

CAPTURED_ACK = (
    "📥 *Captured.* I’ll turn this into portfolio evidence and flag anything missing "
    "before filing. Nothing goes to Kaizen until you approve it."
)

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
        "*Portfolio Guru proof report*",
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



def _settings_view_components(
    user_id: int,
    *,
    tier: str | None = None,
    used: int | None = None,
    connected: bool | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Render the settings page text + keyboard.

    This is also the merged "status" view. When tier/used/connected are
    supplied, a plan + usage + connection block is rendered at the top.
    """
    curriculum = get_curriculum(user_id) or "2025"
    curriculum_label = "2021 Curriculum" if curriculum == "2021" else "2025 Update"
    training_level = _training_level_label(get_training_level(user_id))
    voice_profile = get_voice_profile(user_id)
    voice_status = "✅ Active" if voice_profile else "⭐ Recommended — not set"
    voice_cta = "⭐ Set up voice profile" if not voice_profile else "✅ Voice profile active / rebuild"
    voice_hint = "Set this once so drafts sound like you." if not voice_profile else "Drafts are already styled to your voice."

    plan_lines = []
    if connected is False:
        plan_lines.append("🔗 Kaizen: not connected")
    if tier is not None:
        tier_pretty = {"free": "Free", "pro": "Pro", "pro_plus": "Unlimited"}.get(tier, tier.title())
        plan_lines.append(f"⭐ Plan: {tier_pretty}")
        if used is not None:
            limit = TIER_LIMITS.get(tier, 5)
            if limit == -1:
                plan_lines.append(f"📋 Usage: {used} cases this month")
            else:
                plan_lines.append(f"📋 Usage: {used}/{limit} cases this month")
    plan_block = ("\n".join(plan_lines) + "\n\n") if plan_lines else ""

    setup_button_label = "🔗 Connect Kaizen" if connected is False else "🔗 Update Kaizen login"

    buttons = [
        [InlineKeyboardButton(voice_cta, callback_data="ACTION|voice")],
        [InlineKeyboardButton(f"🎓 Training stage: {training_level}", callback_data="ACTION|change_level")],
        [InlineKeyboardButton(f"📚 Curriculum: {curriculum_label}", callback_data="ACTION|change_curriculum")],
        [InlineKeyboardButton(setup_button_label, callback_data="ACTION|setup")],
        [InlineKeyboardButton("🔙 Back", callback_data="ACTION|back_to_menu"),
         InlineKeyboardButton("🗑️ Delete data", callback_data="ACTION|delete")],
    ]
    text = (
        f"⚙️ Your settings\n\n"
        f"{plan_block}"
        f"✍️ Voice profile: {voice_status}\n"
        f"   {voice_hint}\n\n"
        f"🎓 Training stage: {training_level}\n"
        f"📚 Curriculum: {curriculum_label}\n\n"
        f"Pick what you want to change."
    )
    return text, InlineKeyboardMarkup(buttons)


def _build_welcome_keyboard(connected: bool = False):
    if connected:
        # Filing is initiated by sending the case directly — no button needed.
        # Surface secondary destinations only.
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚙️ Settings", callback_data="ACTION|settings"),
                InlineKeyboardButton("📈 Portfolio health", callback_data="ACTION|health"),
            ],
            [
                InlineKeyboardButton("ℹ️ Help", callback_data="INFO|what"),
                InlineKeyboardButton("⚙️ Settings", callback_data="ACTION|settings"),
            ],
        ])
    else:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("❓ What is this?", callback_data="INFO|what"),
                InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup"),
            ],
        ])


FORM_EMOJIS = {
    "CBD": "🩺", "DOPS": "🔪", "MINI_CEX": "🏥", "ACAT": "📋",
    "MSF": "👥", "QIAT": "🎓", "LAT": "📖", "JCF": "💼",
    "ACAF": "✅", "STAT": "📊",
    "TEACH": "👨‍🏫", "PROC_LOG": "🔬", "SDL": "📖", "US_CASE": "🔊",
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
    "SDL": "SDL",

    # Procedures & Clinical
    "DOPS_PROC": "DOPS Procedure",
    "PROC_LOG": "Procedural Log",
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
    schema = FORM_SCHEMAS.get(form_type, {})
    return schema.get("name", form_type)


def _build_form_choice_keyboard(recommendations, curriculum="2025"):
    """Build inline keyboard for form type selection — AI suggestions + See all forms escape hatch.
    Filters recommendations by curriculum preference."""
    from extractor import FORM_UUIDS
    # Apply curriculum filter to recommendations
    filtered = []
    has_2021_variant = {k[:-5] for k in FORM_UUIDS if k.endswith("_2021")}
    for rec in recommendations:
        ft = rec.form_type
        if curriculum == "2021" and ft in has_2021_variant:
            # Swap to _2021 variant
            ft_2021 = ft + "_2021"
            from models import FormTypeRecommendation
            filtered.append(FormTypeRecommendation(
                form_type=ft_2021, rationale=rec.rationale, uuid=FORM_UUIDS.get(ft_2021)
            ))
        elif curriculum == "2025" and ft.endswith("_2021"):
            # Skip _2021 forms on 2025 curriculum
            continue
        else:
            filtered.append(rec)

    buttons = []
    for rec in filtered:
        base_ft = rec.form_type.replace("_2021", "") if rec.form_type.endswith("_2021") else rec.form_type
        emoji = FORM_EMOJIS.get(base_ft, "📄")
        label = FORM_BUTTON_LABELS.get(rec.form_type) or FORM_BUTTON_LABELS.get(base_ft) or _form_display_name(base_ft)[:24]
        if rec.uuid:
            buttons.append(InlineKeyboardButton(f"{emoji} {label}", callback_data=f"FORM|{rec.form_type}"))
        else:
            buttons.append(InlineKeyboardButton(f"{emoji} {label} (soon)", callback_data="FORM|disabled"))
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
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
    from extractor import FORM_UUIDS
    has_2021_variant = {k[:-5] for k in FORM_UUIDS if k.endswith("_2021")}

    if curriculum == "2021":
        result = []
        for ft in form_types:
            if ft.endswith("_2021"):
                result.append(ft)
            elif ft in has_2021_variant:
                result.append(ft + "_2021")
            else:
                result.append(ft)
        return result
    else:
        # 2025 (default) — base forms only
        return [ft for ft in form_types if not ft.endswith("_2021")]


def _get_allowed_forms(user_id):
    """Get the allowed form list for a user (training level + curriculum filtered)."""
    from extractor import FORM_UUIDS
    training_level = get_training_level(user_id)
    curriculum = get_curriculum(user_id)
    if training_level:
        allowed = TRAINING_LEVEL_FORMS.get(training_level, _default_allowed_forms_for_unknown_training())
    else:
        allowed = _default_allowed_forms_for_unknown_training()
    allowed = _filter_forms_by_curriculum(allowed, curriculum)
    # Only include forms that have UUIDs
    return [ft for ft in allowed if FORM_UUIDS.get(ft)]


def _build_category_picker_keyboard(user_id):
    """Build the level-1 category picker keyboard, hiding empty categories."""
    allowed = set(_get_allowed_forms(user_id))
    rows = []
    row = []
    for cat_name, cat_forms in FORM_CATEGORIES.items():
        # Check if any form in this category is available to this user
        # Need to check both base and _2021 variants
        has_forms = any(ft in allowed for ft in cat_forms)
        if not has_forms:
            # Also check _2021 variants
            has_forms = any(ft + "_2021" in allowed for ft in cat_forms)
        if not has_forms:
            continue
        slug = _CAT_SLUGS[cat_name]
        row.append(InlineKeyboardButton(cat_name, callback_data=f"FORM|cat_{slug}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔍 Search by name", callback_data="FORM|search")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="FORM|back")])
    return InlineKeyboardMarkup(rows)


def _build_category_forms_keyboard(user_id, cat_slug):
    """Build the level-2 keyboard showing forms within a category."""
    from extractor import FORM_UUIDS
    cat_name = _SLUG_TO_CAT[cat_slug]
    cat_forms = FORM_CATEGORIES[cat_name]
    allowed = set(_get_allowed_forms(user_id))
    buttons = []
    for ft in cat_forms:
        # Check if this form (or its _2021 variant) is in the allowed set
        if ft in allowed:
            actual_ft = ft
        elif ft + "_2021" in allowed:
            actual_ft = ft + "_2021"
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
        [_BTN_CANCEL],
    ])


def _build_approval_keyboard(improved_once: bool = False):
    improve_button = (
        InlineKeyboardButton("Improved once ✅", callback_data="IMPROVE|used")
        if improved_once
        else InlineKeyboardButton("✨ Quick improve", callback_data="IMPROVE|reflection")
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Save as draft", callback_data="APPROVE|draft"),
            improve_button,
        ],
        [
            InlineKeyboardButton("✏️ Edit", callback_data="EDIT|draft"),
            InlineKeyboardButton("❌ Cancel", callback_data="CANCEL|draft"),
        ],
    ])


def _build_post_review_keyboard(improved_once: bool = False):
    """Keyboard shown after lightweight draft improvement."""
    return _build_approval_keyboard(improved_once=improved_once)


def _build_post_filing_keyboard(
    form_type: str,
    status: str,
    *,
    uncertain: bool = False,
    same_case_available: bool = False,
) -> InlineKeyboardMarkup:
    """Compact keyboard shown after a filing attempt."""
    feedback_row = [
        InlineKeyboardButton("👍 It worked", callback_data=f"FEEDBACK|good|{form_type}|{status}"),
        InlineKeyboardButton("👎 Didn't work", callback_data=f"FEEDBACK|bad|{form_type}|{status}"),
    ]

    if uncertain and FORM_UUIDS.get(form_type):
        primary_row = [InlineKeyboardButton(
            "🔗 Open in Kaizen",
            url=f"https://kaizenep.com/events/new-section/{FORM_UUIDS[form_type]}",
        )]
    elif status == "failed":
        primary_row = [InlineKeyboardButton("🔄 Try again", callback_data="ACTION|retry_filing")]
    else:
        if same_case_available:
            primary_row = [InlineKeyboardButton("🔁 Same case, another WPBA", callback_data="ACTION|same_case_another")]
        else:
            primary_row = [InlineKeyboardButton("📋 File another case", callback_data="ACTION|file")]

    rows = [primary_row]
    if same_case_available:
        rows.append([InlineKeyboardButton("📋 File new case", callback_data="ACTION|file")])
    rows.extend([
        feedback_row,
        [InlineKeyboardButton("⋯ More options", callback_data=f"ACTION|post_file_more|{form_type}|{status}")],
    ])
    return InlineKeyboardMarkup(rows)


def _build_edit_field_keyboard(draft=None):
    """Build edit field keyboard. For FormDraft, generates buttons dynamically from schema."""
    if draft and isinstance(draft, FormDraft):
        schema = FORM_SCHEMAS.get(draft.form_type, {})
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


def _find_reflection_key(fields: dict) -> str | None:
    if "reflection" in fields:
        return "reflection"
    for key in fields:
        if "reflection" in key:
            return key
    return None


def _draft_reflection_text(draft) -> str:
    if isinstance(draft, CBDData):
        return draft.reflection or ""
    if isinstance(draft, FormDraft):
        key = _find_reflection_key(draft.fields)
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


def _draft_header(title: str, reason: str | None, draft) -> list[str]:
    lines = [f"🟢 *{FLOW_STATE_LABELS['drafted']} — {title} ready*"]
    if reason:
        lines.append(f"*Why this form:* {reason}")
    coach = _draft_coach_note(draft)
    if coach:
        lines.extend([coach, ""])
    else:
        lines.append("")
    return lines


def _format_draft_preview(draft, reason: str | None = None) -> str:
    """Format draft data as a preview message. Dispatches based on type."""
    if isinstance(draft, FormDraft):
        return _format_generic_draft(draft, reason=reason)
    return _format_cbd_draft(draft, reason=reason)


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


def _summarise_field_value(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if str(item).strip())
    return str(value).strip()


def _template_requirements(form_type: str):
    schema = FORM_SCHEMAS.get(form_type, {})
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




def _pre_file_missing_fields(form_type: str, fields: dict) -> list[str]:
    """Required-field guard before touching Kaizen.

    Template review may allow a user to continue with thin data; this final
    gate prevents a mostly blank Kaizen draft, especially for voice-led DOPS.
    """
    base_form = _normalise_form_type(form_type)
    if base_form != "DOPS":
        return []

    aliases = {
        "Date": ["date_of_encounter", "date_of_event", "end_date"],
        "Procedure / procedural skill": ["procedure_name", "procedural_skill"],
        "Clinical Setting": ["clinical_setting", "placement"],
        "Stage of Training": ["stage_of_training", "stage"],
        "Indication": ["indication", "case_observed"],
        "Trainee Performance": ["trainee_performance"],
    }
    missing = []
    for label, keys in aliases.items():
        values = [fields.get(k) for k in keys]
        if not any(not _is_missing_field_value(v) for v in values):
            missing.append(label)

    # Thin voice transcriptions often produce one-word/placeholder text; ask
    # before filing rather than creating an assessor-hostile draft.
    for label, keys in {
        "Indication": ["indication", "case_observed"],
        "Trainee Performance": ["trainee_performance"],
    }.items():
        value = next((str(fields.get(k) or "").strip() for k in keys if str(fields.get(k) or "").strip()), "")
        if value and len(value) < 20 and label not in missing:
            missing.append(label)

    seen = set()
    return [m for m in missing if not (m in seen or seen.add(m))]


def _format_pre_file_missing_message(form_type: str, missing: list[str]) -> str:
    form_name = _form_display_name(form_type)
    shown = missing[:3]
    questions = "\n".join(f"• {item}" for item in shown)
    extra = f"\n\nI found {len(missing) - 3} more gaps too, but start with these." if len(missing) > 3 else ""
    return (
        f"🟡 *{form_name} needs a bit more detail before I file it.*\n\n"
        "I’m not going to create a mostly blank Kaizen draft. Please send the missing detail for:\n"
        f"{questions}{extra}"
    )

def _format_template_review(form_type: str, draft) -> str:
    form_name = _form_display_name(form_type)
    required, optional = _template_requirements(form_type)
    missing_required, missing_optional, present_fields = _missing_template_fields(draft, form_type)
    fields = _draft_fields_for_review(draft)

    lines = [
        f"🧩 *{form_name} template*",
        f"🟡 *{FLOW_STATE_LABELS['needs_you']}* — review what I found, then add missing detail or tap Show me the draft.",
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

    lines.extend(["", "*Still missing*"])
    if missing_required:
        lines.extend(f"• {field['label']}" for field in missing_required)
    else:
        lines.append("• No required fields")

    if missing_optional:
        lines.extend(["", "*Optional not filled*"])
        lines.extend(f"• {field['label']}" for field in missing_optional[:3])

    lines.extend([
        "",
        "💬 Send anything to add more detail, or tap below.",
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
            "SLO10": "SLO10 — Quality improvement",
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


def _format_cbd_draft(cbd_data, reason: str | None = None) -> str:
    """Format CBD data as a preview message."""
    date_str = cbd_data.date_of_encounter
    try:
        from datetime import datetime
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_display = dt.strftime("%-d %b %Y")
    except (ValueError, AttributeError):
        date_display = date_str

    curriculum = _format_curriculum_hierarchy(cbd_data.curriculum_links, cbd_data.key_capabilities)
    lines = _draft_header("CBD draft", reason, cbd_data)

    lines.extend([
        f"📅 *Date:* {date_display}",
        f"🏥 *Setting:* {cbd_data.clinical_setting}",
        f"🩺 *Presentation:* {cbd_data.patient_presentation}",
        "",
        f"🗒️ *Case narrative:*\n{cbd_data.clinical_reasoning}",
        "",
        f"💭 *Reflection:*\n{cbd_data.reflection}",
        "",
        f"📚 *Curriculum:*\n{curriculum}",
    ])
    return "\n".join(lines)


def _format_generic_draft(draft: FormDraft, reason: str | None = None) -> str:
    """Format a generic FormDraft as a preview message."""
    schema = FORM_SCHEMAS.get(draft.form_type, {})
    form_name = schema.get("name", draft.form_type)
    emoji = FORM_EMOJIS.get(draft.form_type, "📋")

    lines = _draft_header(f"{form_name} draft", reason, draft)
    lines[0] = f"{emoji} *{form_name} draft ready*"

    fields = schema.get("fields", [])
    for field in fields:
        key = field["key"]
        field_type = field["type"]

        # These fields are rendered via the curriculum hierarchy — never render separately
        if key in ("key_capabilities", "curriculum_section", "section_of_curriculum"):
            continue

        value = draft.fields.get(key)
        if not value:
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
        elif len(str(value)) > 100:
            lines.append(f"{label_str}\n{value}\n")
        else:
            lines.append(f"{label_str} {value}\n")

    return "\n".join(lines)


# === COMMAND HANDLERS ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()

    # Handle deep links: /start setup, /start file
    if context.args:
        deep_link = context.args[0].lower()
        if deep_link == "setup":
            return await setup_start(update, context)
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
    context.user_data["setup_username"] = text
    context.user_data["_setup_state_hint"] = "password"
    await _flow_msg(
        update, context,
        "🔒 What's your Kaizen password?\n\n"
        "_I'll delete this message right after you send it._",
        parse_mode="Markdown",
        flow_key="setup",
    )
    return AWAIT_PASSWORD


async def _test_kaizen_login(username: str, password: str) -> bool:
    """Quick headless login test — returns True if credentials work."""
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto("https://eportfolio.rcem.ac.uk", wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            login_input = page.locator('input[name="login"]')
            if await login_input.count() > 0:
                await login_input.fill(username)
                await page.locator('button[type="submit"]').click()
                await asyncio.sleep(2)
            pwd_input = page.locator('input[name="password"]')
            if await pwd_input.count() > 0:
                await pwd_input.fill(password)
                await page.locator('button[type="submit"]').click()
            await page.wait_for_url("**/kaizenep.com/**", timeout=30000)
            return True
        finally:
            await browser.close()
            await pw.stop()
    except Exception as e:
        logger.warning(f"Credential test failed: {e}")
        return False


async def setup_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = context.user_data.get("setup_username", "")
    password = update.message.text.strip()
    user_id = update.effective_user.id

    # Delete password message for security
    try:
        await update.message.delete()
    except Exception:
        # Can't delete in groups without admin rights — warn user
        if update.effective_chat.type != "private":
            await update.effective_chat.send_message(
                "⚠️ I couldn't delete your password message — I need admin rights in groups. "
                "Please delete it manually for security."
            )

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
            "Try the setup again — or send the case anyway, you can connect later.",
            flow_key="setup",
        )
        context.user_data.pop("setup_username", None)
        _flow_done(context, "setup")
        return ConversationHandler.END
    except Exception as exc:
        progress_task.cancel()
        logger.warning("Credential test errored: %s", exc, exc_info=True)
        await _flow_edit(
            update, context,
            "⚠️ Couldn't reach Kaizen to verify the login. Try again in a moment.\n\n"
            "📧 What's your Kaizen username (email)?",
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
            "📧 What's your Kaizen username (email)?",
            flow_key="setup",
        )
        context.user_data.pop("setup_username", None)
        return AWAIT_USERNAME

    store_credentials(user_id, username, password)
    context.user_data.pop("setup_username", None)
    context.user_data.pop("_setup_state_hint", None)

    # Do not make onboarding ask for training level before the user gets value.
    # Keep it Unknown until the user explicitly chooses a Kaizen stage group.
    if not get_curriculum(user_id):
        store_curriculum(user_id, "2025")

    await _flow_edit(
        update, context,
        "Kaizen connected ✅\n\n"
        "Send your first case — text, voice, photo, or document — and I'll get started.\n\n"
        "Use the *Menu* (☰ bottom-left) any time for Settings, Status, or Voice profile.",
        parse_mode="Markdown",
        flow_key="setup",
    )
    _flow_done(context, "setup")
    return ConversationHandler.END


async def setup_training_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle training level selection during setup — then ask curriculum."""
    query = update.callback_query
    await query.answer()
    level = query.data.split("|")[1]
    user_id = update.effective_user.id
    store_training_level(user_id, level)
    await query.edit_message_text(
        f"Training level saved as {level}.\n\nWhich curriculum are you working under? Most trainees starting now are on the 2025 Update. If your deanery still uses the 2021 forms, pick that.",
        reply_markup=_build_curriculum_keyboard("SETUP_CURRICULUM")
    )
    return AWAIT_CURRICULUM


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
        f"Send your first case when ready — text, voice note, photo, or document.\n\n"
        f"Use the *Menu* (☰ bottom-left) any time for Settings, Status, or Voice profile.",
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

async def voice_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start voice profile collection — /voice command."""
    user_id = update.effective_user.id
    existing = get_voice_profile(user_id)
    _flow_done(context, "voice")  # fresh start — drop any stale anchor

    if existing:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Rebuild Profile", callback_data="VOICE|rebuild"),
             InlineKeyboardButton("🗑️ Remove Profile", callback_data="VOICE|remove")],
            [InlineKeyboardButton("❌ Cancel", callback_data="VOICE|cancel")],
        ])
        await _flow_msg(
            update, context,
            "✍️ You already have a voice profile active. Your drafts are styled to match your writing.\n\n"
            "What would you like to do?",
            reply_markup=keyboard,
            flow_key="voice",
        )
        return AWAIT_VOICE_EXAMPLES

    await _flow_msg(
        update, context,
        "⭐ *Voice Profile Setup*\n\n"
        "This is the personalisation step that makes Portfolio Guru sound like you, not a generic bot.\n\n"
        "Send 3-5 examples of real portfolio writing. Best examples are reflections or WPBA text you would actually submit.\n\n"
        "You can send:\n"
        "• pasted text — best quality\n"
        "• screenshots/photos — I’ll extract the text\n"
        "• voice notes — useful, but pasted examples are cleaner\n\n"
        "I’ll learn your tone, structure, reflection depth and phrases. Send the first example now.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="VOICE|cancel")],
        ]),
        flow_key="voice",
    )
    context.user_data["voice_examples"] = []
    return AWAIT_VOICE_EXAMPLES


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
            await _flow_edit(
                update, context,
                _cancelled_next_step_text(update.effective_user.id, "Voice profile setup cancelled"),
                reply_markup=_build_next_step_keyboard(update.effective_user.id),
                flow_key="voice",
            )
            _flow_done(context, "voice")
            return ConversationHandler.END

        if data == "VOICE|preview_accept":
            profile_json = context.user_data.get("pending_voice_profile")
            if not profile_json:
                await _flow_edit(
                    update, context,
                    "⚠️ That preview has expired. Please rebuild your voice profile.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Rebuild Profile", callback_data="ACTION|voice")],
                    ]),
                    flow_key="voice",
                )
                context.user_data.pop("voice_examples", None)
                _flow_done(context, "voice")
                return ConversationHandler.END

            store_voice_profile(update.effective_user.id, profile_json, len(examples))
            context.user_data.pop("pending_voice_profile", None)
            context.user_data.pop("voice_examples", None)
            await _flow_edit(
                update, context,
                "✅ Voice profile activated. All future drafts will match your writing voice.\n\n"
                "Send a case any time to try it.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Rebuild profile", callback_data="ACTION|voice")],
                ]),
                flow_key="voice",
            )
            _flow_done(context, "voice")
            return ConversationHandler.END

        if data == "VOICE|preview_reject":
            context.user_data.pop("pending_voice_profile", None)
            context.user_data.pop("voice_examples", None)
            await _flow_edit(
                update, context,
                "No problem — let's try again with different examples.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|voice")],
                ]),
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
            context.user_data["voice_examples"] = []
            context.user_data.pop("pending_voice_profile", None)
            await _flow_edit(
                update, context,
                "🔄 Starting fresh. Send 3-5 examples of real portfolio writing. Pasted text is best; screenshots and voice notes also work.\n\n"
                "Send your first example now.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="VOICE|cancel")],
                ]),
                flow_key="voice",
            )
            return AWAIT_VOICE_EXAMPLES

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
        await _flow_msg(update, context, "🎙️ Transcribing…", flow_key="voice")
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
                await _flow_edit(update, context, "⚠️ Couldn't transcribe. Try pasting text instead.", flow_key="voice")
                return AWAIT_VOICE_EXAMPLES
        except Exception:
            await _flow_edit(update, context, "⚠️ Transcription failed. Try pasting text instead.", flow_key="voice")
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
                [InlineKeyboardButton("❌ Cancel", callback_data="VOICE|cancel")],
            ]),
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


async def _build_voice_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Build the voice profile from collected examples."""
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
        context.user_data["pending_voice_profile"] = profile_json
        sample_draft = await _generate_voice_preview(profile_json)

        # No user input between "Analysing…" and the preview — always edit.
        await _flow_edit(
            update, context,
            f"🔍 Here's a sample draft using your voice profile:\n\n"
            f"---\n{sample_draft}\n---\n\n"
            f"Does this sound like you?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Looks like me — Activate", callback_data="VOICE|preview_accept")],
                [InlineKeyboardButton("❌ Not quite — try again", callback_data="VOICE|preview_reject")],
            ]),
            flow_key="voice",
        )
        return AWAIT_VOICE_EXAMPLES
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
    """Handle INFO|what button from any message, regardless of conversation state."""
    query = update.callback_query
    await query.answer()
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
    await query.answer()

    if action == "setup":
        if has_credentials(user_id):
            await query.message.reply_text(
                "Your Kaizen account is already connected. Just send your next case to file it."
            )
        else:
            await setup_start(update, context)

    elif action == "reset":
        context.user_data.clear()
        await query.message.reply_text(
            "✅ Cleared — back to the main menu.",
            reply_markup=_build_welcome_keyboard(connected=has_credentials(user_id))
        )

    elif action == "cancel":
        context.user_data.clear()
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

    elif action == "voice":
        # Trigger voice profile flow — simulate /voice command
        existing = get_voice_profile(user_id)
        if existing:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Rebuild Profile", callback_data="VOICE|rebuild"),
                 InlineKeyboardButton("🗑️ Remove Profile", callback_data="VOICE|remove")],
                [InlineKeyboardButton("❌ Cancel", callback_data="VOICE|cancel")],
            ])
            await query.message.reply_text(
                "✍️ You already have a voice profile. What would you like to do next?",
                reply_markup=keyboard
            )
        else:
            await query.message.reply_text(
                "⭐ *Voice Profile Setup*\n\n"
                "Send 3-5 examples of real portfolio writing. Pasted text is best; screenshots and voice notes also work.\n\n"
                "I’ll learn your tone, structure, reflection depth and phrases. Send your first example now.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="VOICE|cancel")],
                ])
            )
            context.user_data["voice_examples"] = []

    elif action == "file":
        if not has_credentials(user_id):
            await query.message.reply_text(
                "🔗 Connect your Kaizen account first.",
                reply_markup=InlineKeyboardMarkup([[_BTN_SETUP]])
            )
        else:
            await query.message.reply_text(FILE_CASE_PROMPT)

    elif action == "same_case_another":
        case_text = context.user_data.get("last_filed_case_text", "")
        filed_form = context.user_data.get("last_filed_form_type", "")
        if not case_text:
            await query.message.reply_text(
                "That filed case is no longer available here. Send the case again and I’ll draft another WPBA.",
                reply_markup=_build_next_step_keyboard(user_id),
            )
            return ConversationHandler.END
        context.user_data.clear()
        context.user_data["case_text"] = case_text
        context.user_data["excluded_form_type"] = filed_form
        await query.message.reply_text(
            "🔁 Reusing the same case. I’ll suggest a different WPBA type — not the one you already filed."
        )
        return await _process_case_text(query.message, context, user_id, case_text, "same case")

    elif action.startswith("post_file_more|"):
        parts = action.split("|")
        form_type = parts[1] if len(parts) > 1 else "unknown"
        status = parts[2] if len(parts) > 2 else "unknown"
        rows = [
            [InlineKeyboardButton("📋 File another case", callback_data="ACTION|file")],
            [InlineKeyboardButton("💬 Something missing?", callback_data=f"FILING|feedback|{form_type}")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="ACTION|settings")],
        ]
        if context.user_data.get("last_filed_case_text") and status == "success":
            rows.insert(0, [InlineKeyboardButton("🔁 Same case, another WPBA", callback_data="ACTION|same_case_another")])
        if status in {"failed", "partial"} and _load_draft(context):
            rows.insert(0, [InlineKeyboardButton("🔄 Try again", callback_data="ACTION|retry_filing")])
        rows.append([InlineKeyboardButton("🏠 Main menu", callback_data="ACTION|back_to_menu")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))

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
        # Inline ARCP health check — same as /health command
        if not has_credentials(user_id):
            await query.message.reply_text("🔗 Connect your Kaizen account first.", reply_markup=InlineKeyboardMarkup([[_BTN_SETUP]]))
            return ConversationHandler.END
        tier = await get_user_tier(user_id)
        if tier != "pro_plus":
            await query.message.reply_text(
                "📊 ARCP Health is included in Portfolio Guru Unlimited.\n\nUpgrade to get gap analysis and readiness scoring.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⭐⭐ Upgrade to Unlimited", callback_data="UPGRADE|pro_plus")]]),
            )
            return ConversationHandler.END
        await query.message.chat.send_action(constants.ChatAction.TYPING)
        training_level = get_training_level(user_id) or "ST4"
        history = await get_case_history(user_id, months=6)
        if not history:
            await query.message.reply_text("📊 No cases filed yet — start filing and come back to check your ARCP readiness.")
            return ConversationHandler.END
        try:
            analysis = await analyse_portfolio_health(history, training_level)
        except Exception:
            await query.message.reply_text("Could not run health check — try again later.")
            return ConversationHandler.END
        from datetime import datetime as _dt
        month_label = _dt.now().strftime("%B %Y")
        gaps = analysis.get("gaps", [])
        gaps_str = "\n".join(f"• {g}" for g in gaps) if gaps else "• No major gaps"
        suggestions = analysis.get("suggestions", [])
        suggestions_str = "\n".join(f"• {s}" for s in suggestions) if suggestions else "• Keep filing"
        readiness = analysis.get("arcp_readiness", "needs_attention")
        readiness_str = {"on_track": "🟢 On track", "needs_attention": "🟡 Needs attention", "at_risk": "🔴 At risk"}.get(readiness, readiness)
        await query.message.reply_text(
            f"📊 *Portfolio Health — {month_label}*\n\n"
            f"⚠️ *Gaps:*\n{gaps_str}\n\n"
            f"💡 *Suggestions:*\n{suggestions_str}\n\n"
            f"ARCP readiness: {readiness_str}",
            parse_mode="Markdown"
        )

    elif action == "settings":
        tier = await get_user_tier(user_id)
        try:
            used = await get_cases_this_month(user_id)
        except Exception:
            used = 0
        text, keyboard = _settings_view_components(
            user_id, tier=tier, used=used, connected=has_credentials(user_id)
        )
        await query.message.edit_text(text, reply_markup=keyboard)

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
            [InlineKeyboardButton("ACCS (ST1–2)", callback_data="SETLEVEL|ACCS")],
            [InlineKeyboardButton("Intermediate (ST3)", callback_data="SETLEVEL|INTERMEDIATE")],
            [InlineKeyboardButton("Higher (ST4–6)", callback_data="SETLEVEL|HIGHER")],
            [InlineKeyboardButton("SAS / Fellow", callback_data="SETLEVEL|SAS")],
            [InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")],
        ])
        await query.message.edit_text(
            "🎓 Which Kaizen training stage applies to you?",
            reply_markup=keyboard,
        )

    elif action == "back_to_menu":
        await query.message.edit_text(
            "🩺 Portfolio Guru — ready when you are.\n\n"
            "Send me what happened. Rough notes are fine — text, voice, photo, or document.\n\n"
            "Or use the menu below to check your portfolio status.",
            reply_markup=_build_welcome_keyboard(connected=has_credentials(user_id)),
        )

    elif action == "delete":
        # Confirm before deleting
        await query.message.edit_text(
            "⚠️ This wipes your saved Kaizen login, training level, curriculum choice, and voice profile. It does not affect cases already saved in Kaizen. Are you sure?",
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
    """Top-level /cancel — clears state and returns to main menu."""
    user_id = update.effective_user.id
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Cancelled — back to the main menu.",
        reply_markup=_build_welcome_keyboard(connected=has_credentials(user_id)),
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

    deleted_items = []
    with Session(cred_engine) as session:
        cred = session.exec(select(UserCredential).where(UserCredential.telegram_user_id == user_id)).first()
        if cred:
            session.delete(cred)
            session.commit()
            deleted_items.append("Kaizen credentials")

    with Session(prof_engine) as session:
        profile = session.exec(select(UserProfile).where(UserProfile.telegram_user_id == user_id)).first()
        if profile:
            session.delete(profile)
            session.commit()
            deleted_items.append("training preferences and voice profile")

    if deleted_items:
        await update.message.reply_text(
            f"🗑️ Deleted: {', '.join(deleted_items)}.\n\nYour data has been erased.",
            reply_markup=InlineKeyboardMarkup([[_BTN_SETUP]])
        )
    else:
        await update.message.reply_text("ℹ️ No stored data found for your account.")
    return ConversationHandler.END


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Reset conversation state and clear user data."""
    user_id = update.effective_user.id
    context.user_data.clear()
    context.user_data["post_reset"] = True
    await update.message.reply_text(
        "✅ Cleared — back to the main menu.",
        reply_markup=_build_welcome_keyboard(connected=has_credentials(user_id))
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


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        HELP_MSG,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [_BTN_SETUP, _BTN_VOICE],
            [InlineKeyboardButton("⚙️ Settings", callback_data="ACTION|settings"),
             _BTN_RESET],
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
        user_id, tier=tier, used=used, connected=has_credentials(user_id)
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
            "You're on the top plan! 🎉\n\nPortfolio Guru Unlimited — unlimited Kaizen WPBA filing, AI extraction, draft review, auto-filing, ARCP health, and unsigned-ticket scanning.",
            flow_key="upgrade",
        )
        _flow_done(context, "upgrade")
        return ConversationHandler.END

    text = f"📊 Your plan: {TIER_LABELS.get(tier, tier)} ({used}/{limit_str} cases used this month)\n\n"
    text += (
        "⭐⭐ *Portfolio Guru Unlimited* — £9.99/month\n"
        "• Unlimited Kaizen WPBA filing\n"
        "• Draft Review (AI critique before filing)\n"
        "• ARCP Health — gap analysis & readiness scoring\n"
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


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /health — analyse portfolio health against ARCP requirements."""
    user_id = update.effective_user.id

    # Gate: Unlimited only
    tier = await get_user_tier(user_id)
    if tier != "pro_plus":
        await update.message.reply_text(
            "📊 Portfolio Health is included in Portfolio Guru Unlimited.\n\n"
            "Upgrade to get monthly ARCP readiness analysis.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐⭐ Upgrade to Unlimited", callback_data="UPGRADE|pro_plus")],
            ]),
        )
        return ConversationHandler.END

    await update.effective_chat.send_action(constants.ChatAction.TYPING)

    training_level = get_training_level(user_id)
    if not training_level:
        training_level = "ST4"
        level_note = "\n\n_Note: No training level set — analysing as ST4. Use /setup to update._"
    else:
        level_note = ""

    history = await get_case_history(user_id, months=6)

    if not history:
        await update.message.reply_text(
            "📊 *Portfolio Health*\n\n"
            "No cases filed yet. Start filing cases and come back to check your ARCP readiness.\n\n"
            "Tip: Send a clinical case to get started.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    progress_msg = await update.message.reply_text("📊 Analysing your portfolio...")

    try:
        analysis = await asyncio.wait_for(
            analyse_portfolio_health(history, training_level), timeout=45
        )
    except asyncio.TimeoutError:
        logger.warning("Portfolio health analysis timed out (45s)")
        await progress_msg.edit_text("⚠️ Analysis took too long — please try again.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Portfolio health analysis failed: {e}", exc_info=True)
        await progress_msg.edit_text("❌ Could not analyse portfolio health. Try again later.")
        return ConversationHandler.END

    from datetime import datetime as _dt
    month_label = _dt.now().strftime("%B %Y")

    # Form distribution line
    form_dist = analysis.get("form_distribution", {})
    from form_schemas import FORM_SCHEMAS as _FS
    dist_parts = []
    for ft, count in form_dist.items():
        name = _FS.get(ft, {}).get("name", ft)
        dist_parts.append(f"{name} ({count})")
    dist_str = " · ".join(dist_parts) if dist_parts else "None"

    # Strengths
    strengths = analysis.get("strengths", [])
    strengths_str = "\n".join(f"• {s}" for s in strengths) if strengths else "• None identified yet"

    # Gaps
    gaps = analysis.get("gaps", [])
    gaps_str = "\n".join(f"• {g}" for g in gaps) if gaps else "• No major gaps"

    # Suggestions
    suggestions = analysis.get("suggestions", [])
    suggestions_str = "\n".join(f"• {s}" for s in suggestions) if suggestions else "• Keep filing cases"

    # ARCP readiness
    readiness = analysis.get("arcp_readiness", "needs_attention")
    readiness_map = {
        "on_track": "🟢 On track",
        "needs_attention": "🟡 Needs attention",
        "at_risk": "🔴 At risk",
    }
    readiness_str = readiness_map.get(readiness, readiness)

    total = analysis.get("total_cases", len(history))

    msg = (
        f"📊 *Portfolio Health — {month_label}*\n\n"
        f"Cases filed (last 6 months): {total}\n"
        f"Form types: {dist_str}\n\n"
        f"✅ *Strengths:*\n{strengths_str}\n\n"
        f"⚠️ *Gaps:*\n{gaps_str}\n\n"
        f"💡 *Suggestions:*\n{suggestions_str}\n\n"
        f"ARCP readiness: {readiness_str}"
        f"{level_note}"
    )

    await progress_msg.edit_text(msg, parse_mode="Markdown")
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
    """Handle SETLEVEL| callback from settings → change training level."""
    query = update.callback_query
    await query.answer()
    level = query.data.split("|")[1]
    user_id = update.effective_user.id
    store_training_level(user_id, level)
    await query.edit_message_text(
        f"✅ Training level set to {_training_level_label(level)}.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to settings", callback_data="ACTION|settings")],
        ]),
    )


# === CALLBACK QUERY HANDLERS ===

async def _analyse_selected_form(context: ContextTypes.DEFAULT_TYPE, user_id: int, case_text: str, form_type: str):
    """Create an explicit-only draft snapshot for the selected form."""
    # Set tier for provider chain gating
    os.environ["CURRENT_USER_TIER"] = context.user_data.get("user_tier", "free")
    vp = get_voice_profile(user_id) or ""
    # Normalise _2021 suffix — extractor uses base form type for schema lookup
    base_form_type = form_type[:-5] if form_type.endswith("_2021") else form_type
    if base_form_type == "CBD":
        draft = await asyncio.wait_for(
            extract_cbd_data(
                case_text,
                voice_profile_json=vp,
                leave_missing_blank=True,
                preserve_original_content=True,
            ),
            timeout=45,
        )
    else:
        draft = await asyncio.wait_for(
            extract_form_data(
                case_text,
                base_form_type,
                voice_profile_json=vp,
                leave_missing_blank=True,
                preserve_original_content=True,
            ),
            timeout=45,
        )
    _store_pending_draft(context, draft)
    context.user_data["chosen_form"] = form_type
    context.user_data["awaiting_detail"] = True
    return draft


async def _process_case_text(message, context: ContextTypes.DEFAULT_TYPE, user_id: int, case_text: str, input_source: str) -> int:
    """Store case text, suggest form types, or move directly to the chosen template review."""
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

    training_level = get_training_level(user_id)
    allowed_forms = TRAINING_LEVEL_FORMS.get(training_level, TRAINING_LEVEL_FORMS["ST5"]) if training_level else TRAINING_LEVEL_FORMS["ST5"]

    await message.chat.send_action(constants.ChatAction.TYPING)
    try:
        recommendations = await asyncio.wait_for(recommend_form_types(case_text), timeout=30)
        excluded_form = _normalise_form_type(context.user_data.get("excluded_form_type", ""))
        recommendations = [
            r for r in recommendations
            if r.form_type in allowed_forms and _normalise_form_type(r.form_type) != excluded_form
        ]
        if not recommendations:
            await _send_latest_message(
                message, context,
                "Couldn't determine the best form — browse all types below.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 See all forms", callback_data="FORM|show_all")],
                    [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|retry_recommend")],
                ]),
            )
            return AWAIT_FORM_CHOICE
        context.user_data["form_recommendations"] = recommendations
    except Exception as exc:
        logger.error("Form recommendation failed across all providers: %s", exc)
        await _send_latest_message(
            message, context,
            "⚠️ AI is temporarily unavailable. Tap below to try again or pick a form manually.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|retry_recommend")],
                [InlineKeyboardButton("📋 Pick form manually", callback_data="FORM|show_all")],
            ]),
        )
        return AWAIT_FORM_CHOICE

    def _short_rationale(text: str, max_words: int = 12) -> str:
        clean = _safe_markdown_text(text or "").strip()
        words = clean.split()
        if len(words) <= max_words:
            return clean
        return " ".join(words[:max_words]).rstrip(",;:") + "…"

    rationale_lines = [
        f"• *{_form_display_name(r.form_type)}* — {_short_rationale(r.rationale)}"
        for r in recommendations if r.uuid
    ]
    rationale_text = "\n".join(rationale_lines) if rationale_lines else "• Case-Based Discussion — Clinical case discussion."

    status_msg = context.user_data.pop("status_msg_id", None)
    status_chat = context.user_data.pop("status_msg_chat", None)

    prompt_text = (
        "I think these forms fit your case:\n\n"
        f"{rationale_text}\n\n"
        "Pick one and I'll draft it. If a required Kaizen field is missing, I'll ask for just that detail."
    )
    context.user_data["form_recommendations_text"] = prompt_text

    if status_msg and status_chat:
        try:
            await context.bot.edit_message_text(
                chat_id=status_chat,
                message_id=status_msg,
                text=prompt_text,
                reply_markup=_build_form_choice_keyboard(recommendations, curriculum=get_curriculum(user_id)),
            )
        except Exception:
            await message.reply_text(
                prompt_text,
                reply_markup=_build_form_choice_keyboard(recommendations, curriculum=get_curriculum(user_id)),
            )
    else:
        await message.reply_text(
            prompt_text,
            reply_markup=_build_form_choice_keyboard(recommendations, curriculum=get_curriculum(user_id)),
        )
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
        # Disarm button immediately — prevents multiple taps sending multiple prompts
        await query.edit_message_reply_markup(reply_markup=None)
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
            await query.message.reply_text(FILE_CASE_PROMPT)
            return AWAIT_CASE_INPUT

    elif data == "ACTION|add_detail":
        # Legacy button — just acknowledge; users now send detail directly
        await query.answer("Just send your detail — no button needed!")
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
        context.user_data.pop("pending_draft_data", None)
        preview = _format_draft_preview(draft, _chosen_form_reason(context, chosen_form))
        await _safe_edit_text(
            query.message,
            preview,
            reply_markup=_build_approval_keyboard(),
            parse_mode="Markdown",
        )
        return AWAIT_APPROVAL

    elif data == "ACTION|retry_recommend":
        await query.answer("Retrying…")
        case_text = context.user_data.get("case_text", "")
        user_id = update.effective_user.id
        if case_text:
            return await _process_case_text(query.message, context, user_id, case_text, "retry")
        await query.edit_message_text("No case text found. Please send a new case.")
        return AWAIT_CASE_INPUT

    elif data == "ACTION|retry_filing":
        return await handle_approval_approve(update, context)

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
            await query.edit_message_text(f"🧩 Updating {_form_display_name(chosen_form)} template…")
            try:
                draft = await _analyse_selected_form(context, user_id, merged, chosen_form)
            except Exception as exc:
                logger.error("Template review failed for improve: %s", exc, exc_info=True)
                await query.edit_message_text("⚠️ Could not refresh that template.", reply_markup=_KB_RETRY_RESET)
                return AWAIT_TEMPLATE_REVIEW
            missing_required, missing_optional, _ = _missing_template_fields(draft, chosen_form)
            if not missing_required:
                _store_draft(context, draft)
                preview = _format_draft_preview(draft, _chosen_form_reason(context, chosen_form))
                await _safe_edit_text(
                    query.message,
                    preview,
                    reply_markup=_build_approval_keyboard(),
                    parse_mode="Markdown",
                )
                return AWAIT_APPROVAL

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
        else:
            context.user_data.clear()
            return await _process_case_text(query.message, context, user_id, merged, "text")

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
        context.user_data.clear()
        await query.message.reply_text(
            "❌ Cancelled — back to the main menu.",
            reply_markup=_build_welcome_keyboard(connected=has_credentials(user_id)),
        )
        return ConversationHandler.END


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
        f"🧩 Updating {form_name} template…",
    )

    try:
        draft = await _analyse_selected_form(context, user_id, combined, chosen_form)
    except asyncio.TimeoutError:
        await ack.edit_text("⏳ Template review timed out. Please try again.")
        return AWAIT_TEMPLATE_REVIEW
    except Exception as exc:
        logger.error("Accumulation refresh failed for %s: %s", chosen_form, exc, exc_info=True)
        await ack.edit_text("⚠️ Could not refresh that template.", reply_markup=_KB_RETRY_RESET)
        return AWAIT_TEMPLATE_REVIEW

    missing_required, missing_optional, _ = _missing_template_fields(draft, chosen_form)
    if not missing_required:
        _store_draft(context, draft)
        context.user_data.pop("accumulating_case", None)
        context.user_data.pop("accumulation_additions", None)
        preview = _format_draft_preview(draft, _chosen_form_reason(context, chosen_form))
        await _safe_edit_text(
            ack,
            preview,
            reply_markup=_build_approval_keyboard(),
            parse_mode="Markdown",
        )
        return AWAIT_APPROVAL

    review_text = _format_template_review(chosen_form, draft)
    await _safe_edit_text(
        ack,
        review_text,
        reply_markup=_build_template_review_keyboard(),
        parse_mode="Markdown",
    )
    context.user_data["last_bot_msg_id"] = ack.message_id
    return AWAIT_TEMPLATE_REVIEW


async def handle_template_review_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle voice, photo, video, or document during AWAIT_TEMPLATE_REVIEW — implicit accumulation."""
    msg = update.message
    extracted_text = None

    if msg.voice:
        ack = await msg.reply_text("🎙️ Transcribing voice note…")
        tmp_path = None
        try:
            voice_file = await msg.voice.get_file()
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
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
            await ack.edit_text("⚠️ Couldn't read image. Try a clearer photo or text.")
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
            await ack.edit_text("⚠️ Couldn't extract audio from video. Try a voice note or text.")
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
            await ack.edit_text("⚠️ Couldn't read that file. Try text instead.")
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
            answer = await answer_question(raw_text)
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
            allowed_forms = TRAINING_LEVEL_FORMS.get(training_level, TRAINING_LEVEL_FORMS["ST5"]) if training_level else TRAINING_LEVEL_FORMS["ST5"]
            recommendations = await asyncio.wait_for(
                recommend_form_types(case_text), timeout=30
            )
            excluded_form = _normalise_form_type(context.user_data.get("excluded_form_type", ""))
            recommendations = [
                r for r in recommendations
                if r.form_type in allowed_forms and _normalise_form_type(r.form_type) != excluded_form
            ]

            if recommendations:
                rationale_lines = [
                    f"• *{_form_display_name(r.form_type)}* — {r.rationale}"
                    for r in recommendations if r.uuid
                ]
                rationale_text = "\n".join(rationale_lines)
                prompt_text = (
                    f"Sure — here's what fits your case:\n\n"
                    f"{rationale_text}\n\n"
                    f"Pick one to switch, or keep going with {form_name}."
                )
                context.user_data["form_recommendations"] = recommendations
                await update.message.reply_text(
                    prompt_text,
                    reply_markup=_build_form_choice_keyboard(recommendations, curriculum=get_curriculum(update.effective_user.id)),
                    parse_mode="Markdown",
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
            answer = await answer_question(raw_text, case_context=case_text)
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

    # Clear post_reset flag if set (belt and braces)
    context.user_data.pop("post_reset", None)

    # Clear any stale status message state from previous sessions
    context.user_data.pop("status_msg_id", None)
    context.user_data.pop("status_msg_chat", None)
    context.user_data.pop("last_bot_msg_id", None)
    context.user_data.pop("last_bot_chat_id", None)

    # Check credentials
    if not has_credentials(user_id):
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
        await update.message.reply_text(
            f"📊 You've used all {limit} free cases this month.\n\n"
            "Upgrade to Portfolio Guru Unlimited (£9.99/mo) for unlimited filing and premium features — or wait until next month.",
            reply_markup=InlineKeyboardMarkup(_upgrade_buttons(tier)),
        )
        return ConversationHandler.END
    context.user_data["user_tier"] = tier

    # Determine input type and extract text
    case_text = None

    if update.message.text:
        raw_text = update.message.text.strip()
        if context.user_data.get("awaiting_detail") and context.user_data.get("chosen_form"):
            case_text = raw_text
        else:
            words_lower = raw_text.lower()
            word_count = len(raw_text.split())

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
                "voice profile", "curriculum", "training level",
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
                    user_id, tier=tier, used=used, connected=has_credentials(user_id)
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
                    user_id, tier=tier, used=used, connected=has_credentials(user_id)
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
                    answer = await answer_question(raw_text)
                    await update.message.reply_text(answer)
                except Exception:
                    await update.message.reply_text(
                        "I help you file clinical cases to your Kaizen e-portfolio. "
                        "Send me a case description by text, voice note, photo, or document."
                    )
                return ConversationHandler.END

            case_text = raw_text

    elif update.message.voice:
        ack = await update.message.reply_text("🎙️ Transcribing voice note…")
        tmp_path = None
        try:
            voice_file = await update.message.voice.get_file()
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp_path = tmp.name
                await voice_file.download_to_drive(tmp_path)
                case_text = await transcribe_voice(tmp_path)
            await ack.edit_text("🎙️ Voice note read. Finding matching forms…")
            _track_latest_message(context, ack)
        except Exception as e:
            await ack.edit_text(
                "⚠️ Couldn't transcribe that voice note — send it again or type the case as text.",
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
        ack = await update.message.reply_text("📷 Reading image…")
        tmp_path = None
        try:
            photo = update.message.photo[-1]
            photo_file = await photo.get_file()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
                await photo_file.download_to_drive(tmp_path)
                case_text = await extract_from_image(tmp_path)
            if case_text.strip() == "NOT_CLINICAL":
                await ack.edit_text("This image doesn't look like a clinical case. Send a text description or a photo of clinical notes/findings.")
                return ConversationHandler.END
            caption = (update.message.caption or "").strip()
            if caption:
                case_text = f"{caption}\n\n{case_text}".strip()
            await ack.edit_text("📷 Image read. Finding matching forms…")
            _track_latest_message(context, ack)
        except Exception as e:
            context.user_data.clear()
            await ack.edit_text("⚠️ Couldn't read image. Try a clearer photo or describe the case in text.")
            return ConversationHandler.END
        finally:
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
        
        ack = await update.message.reply_text(f"📄 Reading *{file_name}*…", parse_mode="Markdown")
        tmp_path = None
        try:
            doc_file = await doc.get_file()
            # Determine file extension from original filename
            suffix = os.path.splitext(file_name)[1] or ".tmp"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name
                await doc_file.download_to_drive(tmp_path)
                case_text = await extract_from_document(tmp_path)
            
            if not case_text or not case_text.strip():
                await ack.edit_text(
                    f"⚠️ Couldn't extract text from *{file_name}*. "
                    "The file might be scanned images (no text layer) or password-protected.\n\n"
                    "Try:\n"
                    "• Sending a clearer/updated version\n"
                    "• Describing the case in text instead",
                    parse_mode="Markdown"
                )
                return ConversationHandler.END
            
            # Truncate very long documents
            max_chars = 15000
            if len(case_text) > max_chars:
                case_text = case_text[:max_chars] + "\n\n[Document truncated — using first 15,000 characters]"
            
            await ack.edit_text(f"📄 *{file_name}* read. Finding matching forms…", parse_mode="Markdown")
            _track_latest_message(context, ack)
            context.user_data["document_name"] = file_name
        except Exception as e:
            logger.error(f"Document processing failed: {e}", exc_info=True)
            context.user_data.clear()
            await ack.edit_text(
                f"⚠️ Couldn't read *{file_name}*. Try a different file format or describe the case in text.",
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

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
    elif update.message.voice:
        input_source = "voice"
    elif update.message.document:
        input_source = "document"
    else:
        input_source = "text"

    chosen_form = context.user_data.get("chosen_form")
    if chosen_form and context.user_data.get("awaiting_detail"):
        context.user_data["case_text"] = case_text
        context.user_data["case_input_source"] = input_source
        await update.effective_chat.send_action(constants.ChatAction.TYPING)
        ack = await update.message.reply_text(f"🧩 Updating {_form_display_name(chosen_form)} template…")
        context.user_data["last_bot_msg_id"] = ack.message_id
        context.user_data["last_bot_chat_id"] = ack.chat_id
        try:
            draft = await _analyse_selected_form(context, user_id, case_text, chosen_form)
        except asyncio.TimeoutError:
            await ack.edit_text("⏳ Template review timed out. Please try again.")
            return AWAIT_TEMPLATE_REVIEW
        except Exception as exc:
            logger.error("Template review refresh failed for %s: %s", chosen_form, exc, exc_info=True)
            await ack.edit_text("⚠️ Could not refresh that template.", reply_markup=_KB_RETRY_RESET)
            return AWAIT_TEMPLATE_REVIEW

        missing_required, missing_optional, _ = _missing_template_fields(draft, chosen_form)
        if not missing_required:
            _store_draft(context, draft)
            preview = _format_draft_preview(draft, _chosen_form_reason(context, chosen_form))
            await _safe_edit_text(
                ack,
                preview,
                reply_markup=_build_approval_keyboard(),
                parse_mode="Markdown",
            )
            return AWAIT_APPROVAL

        review_text = _format_template_review(chosen_form, draft)
        await _safe_edit_text(
            ack,
            review_text,
            reply_markup=_build_template_review_keyboard(),
            parse_mode="Markdown",
        )
        context.user_data["last_bot_msg_id"] = ack.message_id
        return AWAIT_TEMPLATE_REVIEW

    _clear_case_review_state(context, keep_case=False)
    if not context.user_data.get("last_bot_msg_id"):
        ack = await update.message.reply_text(CAPTURED_ACK, parse_mode="Markdown")
        _track_latest_message(context, ack)
    return await _process_case_text(update.message, context, user_id, case_text, input_source)


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

    if data == "FORM|show_all":
        # Level 1 — category picker
        user_id = update.effective_user.id
        curriculum = get_curriculum(user_id)
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
        cur_label = "2025 curriculum" if new_cur == "2025" else "2021 curriculum"
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
            reply_markup=_build_form_choice_keyboard(recommendations, curriculum=get_curriculum(update.effective_user.id))
        )
        return AWAIT_FORM_CHOICE

    form_type = data.split("|")[1]
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
        return await _resume_paused_flow(
            update,
            context,
            "That earlier button is no longer active.",
        )

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
        await query.edit_message_text("⏳ Template review timed out.", reply_markup=_KB_RETRY_RESET)
        return ConversationHandler.END
    except Exception as e:
        logger.error("Template review failed in form_choice: %s", e, exc_info=True)
        await query.edit_message_text("⚠️ Could not review that template.", reply_markup=_KB_RETRY_RESET)
        return ConversationHandler.END

    missing_required, missing_optional, _ = _missing_template_fields(draft, form_type)
    if not missing_required:
        # All fields filled — skip template review, go to draft preview
        _store_draft(context, draft)
        preview = _format_draft_preview(draft, _chosen_form_reason(context, form_type))
        await _safe_edit_text(
            query.message,
            preview,
            reply_markup=_build_approval_keyboard(),
            parse_mode="Markdown",
        )
        return AWAIT_APPROVAL

    review_text = _format_template_review(form_type, draft)
    await _safe_edit_text(
        query.message,
        review_text,
        reply_markup=_build_template_review_keyboard(),
        parse_mode="Markdown",
    )
    context.user_data["last_bot_msg_id"] = query.message.message_id
    context.user_data["last_bot_chat_id"] = query.message.chat_id
    return AWAIT_TEMPLATE_REVIEW


async def handle_approval_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 'File this draft' approval."""
    query = update.callback_query
    await query.answer()

    # Disarm buttons immediately — prevents double-tap filing
    await query.edit_message_reply_markup(reply_markup=None)

    user_id = update.effective_user.id
    creds = get_credentials(user_id)
    if not creds:
        context.user_data.clear()
        await query.message.reply_text(
            "⚠️ Credentials not found.",
            reply_markup=InlineKeyboardMarkup([[_BTN_SETUP]])
        )
        return ConversationHandler.END

    username, password = creds
    draft = _load_draft(context)
    if not draft:
        return await _resume_paused_flow(
            update,
            context,
            "That earlier draft is no longer active.",
        )

    # Handle FormDraft (non-CBD forms)
    # Unified filing for ALL forms (CBD and non-CBD)
    if isinstance(draft, FormDraft):
        form_type = draft.form_type
        fields = draft.fields
        curriculum_links = draft.fields.get("curriculum_links", [])
    else:
        # CBDData
        form_type = "CBD"
        fields = {
            "date_of_encounter": draft.date_of_encounter,
            "end_date": draft.date_of_encounter,
            "date_of_event": draft.date_of_encounter,
            "stage_of_training": draft.stage_of_training,
            "clinical_reasoning": draft.clinical_reasoning,
            "reflection": draft.reflection,
        }
        curriculum_links = draft.curriculum_links or []

    schema = FORM_SCHEMAS.get(form_type, {})
    form_name = schema.get("name", form_type)
    form_emoji = FORM_EMOJIS.get(form_type, "📋")

    missing_before_file = _pre_file_missing_fields(form_type, fields)
    if missing_before_file:
        await _safe_edit_text(
            query.message,
            _format_pre_file_missing_message(form_type, missing_before_file),
            reply_markup=_build_approval_keyboard(improved_once=context.user_data.get("quick_improve_used", False)),
            parse_mode="Markdown",
        )
        return AWAIT_APPROVAL

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

    # Determine platform (default: kaizen; future: from user profile)
    platform = "kaizen"
    await update.effective_chat.send_action(constants.ChatAction.TYPING)
    ack = query.message
    await _safe_edit_text(
        ack,
        f"📤 Saving {form_name} as a Kaizen draft…",
        parse_mode=None,
    )

    # Progress edits during the long filing wait so the user doesn't see a
    # static message for up to 5 minutes.
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
            ),
            timeout=300,  # 5 min — browser-use path may take longer
        )
    except asyncio.TimeoutError:
        progress_task.cancel()
        kaizen_url = f"https://kaizenep.com/events/new-section/{FORM_UUIDS.get(form_type, '')}" if FORM_UUIDS.get(form_type) else "https://kaizenep.com/activities"
        timeout_msg = (
            f"⏱ Filing took too long. The draft might be in your activities list already — "
            f"[open Kaizen]({kaizen_url}) to check before retrying."
        )
        try:
            await ack.edit_text(timeout_msg, parse_mode="Markdown")
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=timeout_msg,
                parse_mode="Markdown",
            )
        # Keep draft so user can retry without re-typing the case
        return AWAIT_APPROVAL
    except Exception as e:
        progress_task.cancel()
        logger.error(f"Filer error for {form_type}: {e}", exc_info=True)
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
        progress_task.cancel()

    status = result["status"]
    filled = result.get("filled", [])
    skipped = result.get("skipped", [])
    error = result.get("error")
    required_labels = {field["label"] for field in _template_requirements(form_type)[0]}
    required_keys = {field["key"] for field in _template_requirements(form_type)[0]}
    skipped_required = [s for s in skipped if s in required_keys or s in required_labels]
    if status == "success" and skipped_required:
        status = "partial"
        error = error or "Required fields were skipped during filing: " + ", ".join(str(s) for s in skipped_required[:4])
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

    end_keyboard = _build_post_filing_keyboard(
        form_type,
        status,
        uncertain=uncertain_save,
        same_case_available=bool(filed_case_text and status == "success" and not uncertain_save),
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
        date_val = fields.get("date_of_encounter", fields.get("date_of_event", ""))
        slo_str = ", ".join(curriculum_links) if curriculum_links else ""
        summary = f"\n\n📅 {date_val}" if date_val else ""
        if slo_str:
            summary += f"  ·  📚 {slo_str}"
        msg = f"✅ *{FLOW_STATE_LABELS['filed_as_draft']}: {form_name} draft saved.*\n\nFiled to your portfolio as a draft — open Kaizen to assign an assessor when ready.{summary}{usage_line}{observation_line}{proof_report}"
        status_line = "✅ Filing finished."
    elif status == "partial":
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
        }
        skipped_names = [_FIELD_FRIENDLY.get(s, s.replace("_", " ").capitalize()) for s in skipped]
        if len(skipped_names) > 3:
            skipped_display = ", ".join(skipped_names[:3]) + f" + {len(skipped_names) - 3} more"
        else:
            skipped_display = ", ".join(skipped_names)
        if error:
            # Partial with error — save may not have worked
            kaizen_url = f"https://kaizenep.com/events/new-section/{FORM_UUIDS.get(form_type, '')}" if FORM_UUIDS.get(form_type) else ""
            link_text = f"\n\n[Open {form_name} manually in Kaizen]({kaizen_url})" if kaizen_url else ""
            recovery = ""
            try:
                recovery = await compose_filing_recovery_copy("partial", error)
            except Exception:
                logger.warning("Recovery copy generation failed", exc_info=True)
            recovery_block = recovery or f"Check your portfolio — the draft may not have saved.\n\nDetails: {error}"
            msg = (
                f"⚠️ *{form_name} — filing had issues.*\n\n"
                f"{len(filled)} fields filled.\n\n"
                f"{recovery_block}{link_text}{usage_line}{proof_report}"
            )
            status_line = "⚠️ Filing needs attention."
        else:
            msg = (
                f"✅ *{form_name} draft saved to Kaizen.*\n\n"
                f"{len(filled)} fields filled from your case.\n"
                f"{len(skipped)} left blank — not enough info to fill without guessing: {skipped_display}.\n\n"
                f"Open your portfolio, complete those fields, then assign an assessor.{usage_line}{proof_report}"
            )
            status_line = "✅ Filing finished."
    else:
        # Show manual link for Kaizen; generic message for other platforms
        recovery = ""
        try:
            recovery = await compose_filing_recovery_copy("failed", error or "")
        except Exception:
            logger.warning("Recovery copy generation failed", exc_info=True)
        if platform == "kaizen" and FORM_UUIDS.get(form_type):
            kaizen_url = f"https://kaizenep.com/events/new-section/{FORM_UUIDS[form_type]}"
            recovery_block = recovery or "Try again, or open the form in Kaizen and fill it manually."
            msg = f"❌ *Filing didn't complete — {FLOW_STATE_LABELS['blocked']}.*\n\n{recovery_block}\n\n[Open {form_name} manually in Kaizen]({kaizen_url}){proof_report}"
            if not recovery and error:
                msg += f"\n\n_Details: {error}_"
            status_line = "❌ Filing stopped."
        else:
            recovery_block = recovery or "Try again, or fill the form manually in your portfolio."
            msg = f"❌ *Filing didn't complete — {FLOW_STATE_LABELS['blocked']}.*\n\n{recovery_block}{proof_report}"
            if not recovery and error:
                msg += f"\n\n_Details: {error}_"
            status_line = "❌ Filing stopped."

    try:
        await _safe_edit_text(
            ack,
            msg,
            reply_markup=end_keyboard,
            parse_mode="Markdown",
        )
    except Exception:
        logger.warning("Could not update filing result line")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=msg,
            reply_markup=end_keyboard,
            parse_mode="Markdown",
        )
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
    schema = FORM_SCHEMAS.get(form_type, {})
    form_name = schema.get("name", form_type)

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
    reflection_key = "reflection" if isinstance(draft, CBDData) else _find_reflection_key(draft.fields)
    if not reflection_key:
        await query.message.reply_text(
            "This form does not have a reflection field to improve. You can still save it as a draft or tap Edit.",
            reply_markup=_build_approval_keyboard(improved_once=context.user_data.get("quick_improve_used", False)),
        )
        return AWAIT_APPROVAL

    ack = await query.message.reply_text("✨ Tightening the reflection only…")
    case_text = context.user_data.get("case_text", "")
    current_draft_text = _format_draft_preview(draft, _chosen_form_reason(context, form_type))
    feedback = (
        "Improve the reflection only. Keep the clinical facts, date, setting, curriculum links, "
        "and every non-reflection field unchanged. Make the reflection concise, first-person, "
        "specific, and useful for a UK EM WPBA assessor."
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
                ),
                timeout=45,
            )
            improved_reflection = str(regenerated.fields.get(reflection_key) or "").strip()
            if not improved_reflection:
                raise ValueError("Improved reflection was empty")
            fields = dict(draft.fields)
            fields[reflection_key] = improved_reflection
            updated = FormDraft(form_type=draft.form_type, fields=fields, uuid=draft.uuid)
        _store_draft(context, updated)
    except asyncio.TimeoutError:
        await ack.edit_text("⏳ Quick improve timed out. Your original draft is still ready.", reply_markup=_build_approval_keyboard())
        return AWAIT_APPROVAL
    except Exception as exc:
        logger.error("Quick improve failed for %s: %s", form_type, exc, exc_info=True)
        await ack.edit_text("⚠️ Quick improve failed. Your original draft is still ready.", reply_markup=_build_approval_keyboard())
        return AWAIT_APPROVAL

    preview = _format_draft_preview(updated, _chosen_form_reason(context, form_type))
    header = "✨ *Reflection polished.* Here's the updated draft:\n\n"
    await _safe_edit_text(ack, header + preview, reply_markup=_build_approval_keyboard(), parse_mode="Markdown")
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
    """Unused — kept for state compatibility."""
    return AWAIT_EDIT_VALUE


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
    voice = msg.voice or (msg.audio if msg.audio else None)
    photo = msg.photo

    if voice:
        ack = await msg.reply_text("🎙️ Transcribing…")
        tmp_path = None
        try:
            voice_file = await voice.get_file()
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
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
            ), timeout=45)
        else:
            updated = await asyncio.wait_for(extract_form_data(
                case_text,
                form_type,
                edit_feedback=feedback,
                current_draft=current_draft_text,
                voice_profile_json=vp,
            ), timeout=45)
        _store_draft(context, updated)
    except asyncio.TimeoutError:
        await ack.edit_text("⏳ Regeneration timed out.", reply_markup=_KB_RETRY_RESET)
        return AWAIT_APPROVAL
    except Exception as e:
        await ack.edit_text("⚠️ Couldn't regenerate.", reply_markup=_KB_RETRY_RESET)
        return AWAIT_APPROVAL

    preview = _format_draft_preview(updated)
    await _safe_edit_text(ack, preview, reply_markup=_build_approval_keyboard(), parse_mode="Markdown")
    return AWAIT_APPROVAL


async def handle_mid_conversation_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle unexpected text messages mid-conversation (AWAIT_APPROVAL, AWAIT_EDIT_FIELD, AWAIT_FORM_CHOICE)."""
    # After a reset, treat ANY incoming message as a fresh case
    if context.user_data.pop("post_reset", False):
        context.user_data.clear()
        return await handle_case_input(update, context)

    raw_text = update.message.text.strip()
    case_text = context.user_data.get("case_text", "")

    try:
        intent = await classify_intent(raw_text, case_context=case_text)
    except Exception:
        intent = "new_case"

    # Check if we're in a state with an active draft
    has_draft = bool(_load_draft(context))
    has_pending = bool(context.user_data.get("pending_draft_data"))
    in_flow = has_draft or has_pending or bool(case_text)

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
            answer = await answer_question(raw_text, case_context=case_text)
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
                    ack_line + preview,
                    reply_markup=_build_approval_keyboard(),
                    parse_mode="Markdown",
                )
                return AWAIT_APPROVAL

            # Edit instruction but nothing matched — don't pretend it was a new case
            await update.message.reply_text(
                "I couldn't tell which field to change from that. Tap *Edit* below to pick a field, or rephrase ("
                "e.g. \"change the date to 12 May 2026\").",
                reply_markup=_build_approval_keyboard(),
                parse_mode="Markdown",
            )
            return AWAIT_APPROVAL

        # new_case — looks like a new case
        if in_flow:
            await update.message.reply_text(
                "It looks like you want to file a new case.",
                reply_markup=InlineKeyboardMarkup([
                    [_BTN_RESET, _BTN_CANCEL],
                ])
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
    entry = chase_guard.log_chase(email=email, name=email, method="manual")
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
            # Let thin-case buttons re-enter the case conversation even if the
            # user taps them after the active state has been lost.
            CallbackQueryHandler(handle_callback, pattern=r"^ACTION\|(?:file|reset|cancel|continue_thin|unsigned|status|health|help|voice)$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_case_input),
            MessageHandler(filters.VOICE, handle_case_input),
            MessageHandler(filters.PHOTO, handle_case_input),
            MessageHandler(filters.Document.ALL, handle_case_input),
        ],
        states={
            AWAIT_CASE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_case_input),
                MessageHandler(filters.VOICE, handle_case_input),
                MessageHandler(filters.PHOTO, handle_case_input),
                MessageHandler(filters.Document.ALL, handle_case_input),
                CallbackQueryHandler(handle_callback, pattern=r"^ACTION\|continue_thin$"),
            ],
            AWAIT_FORM_CHOICE: [
                CallbackQueryHandler(handle_form_choice, pattern=r"^FORM\|"),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
                CallbackQueryHandler(handle_callback, pattern=r"^ACTION\|retry_recommend$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mid_conversation_text),
            ],
            AWAIT_FORM_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_form_search_text),
                CallbackQueryHandler(handle_form_choice, pattern=r"^FORM\|"),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
            ],
            AWAIT_TEMPLATE_REVIEW: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_template_review_text),
                MessageHandler(filters.VOICE, handle_template_review_media),
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
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mid_conversation_text),
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
            CommandHandler("reset", reset),
            CommandHandler("cancel", setup_cancel),
            CallbackQueryHandler(
                handle_callback,
                pattern=r"^(?:INFO\|.*|CANCEL\|.*|ACTION\|(?:file|reset|cancel|continue_thin|retry_filing|retry_recommend))$",
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
            AWAIT_TRAINING_LEVEL: [CallbackQueryHandler(setup_training_level, pattern=r"^LEVEL\|")],
            AWAIT_CURRICULUM: [CallbackQueryHandler(setup_curriculum, pattern=r"^SETUP_CURRICULUM\|")],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
        allow_reentry=True,
    )

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("delete", delete_data))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("bulk", bulk_command))
    application.add_handler(CommandHandler("unsigned", unsigned_command))
    application.add_handler(CommandHandler("chase", chase_command))
    application.add_handler(CommandHandler("curriculum", curriculum_command))
    application.add_handler(CommandHandler("health", health_command))
    application.add_handler(CommandHandler("upgrade", upgrade_command))
    application.add_handler(CommandHandler("plan", upgrade_command))
    application.add_handler(CommandHandler("settier", settier_command))
    application.add_handler(CallbackQueryHandler(handle_upgrade_button, pattern=r"^UPGRADE\|"))
    application.add_handler(CallbackQueryHandler(handle_unsigned_range_pick, pattern=r"^UNSIGNED\|"))
    application.add_handler(CallbackQueryHandler(handle_set_curriculum, pattern=r"^SET_CURRICULUM\|"))
    application.add_handler(CallbackQueryHandler(handle_set_level, pattern=r"^SETLEVEL\|"))
    application.add_handler(CallbackQueryHandler(handle_chase_log, pattern=r"^CHASE_LOG\|"))
    # Top-level handlers that must work regardless of conversation state
    application.add_handler(CallbackQueryHandler(handle_info_button, pattern=r"^INFO\|"))
    application.add_handler(
        CallbackQueryHandler(
            handle_action_button,
            pattern=r"^ACTION\|(?!file$|reset$|cancel$|continue_thin$|retry_filing$|setup$).+",
        )
    )
    application.add_handler(CallbackQueryHandler(handle_feedback, pattern=r"^FEEDBACK\|"))
    application.add_handler(CallbackQueryHandler(handle_filing_feedback, pattern=r"^FILING\|feedback\|"))
    application.add_handler(CallbackQueryHandler(handle_pushback, pattern=r"^PUSHBACK\|"))

    voice_conv = ConversationHandler(
        entry_points=[CommandHandler("voice", voice_start)],
        states={
            AWAIT_VOICE_EXAMPLES: [
                CallbackQueryHandler(voice_collect_example, pattern=r"^VOICE\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, voice_collect_example),
                MessageHandler(filters.PHOTO, voice_collect_example),
                MessageHandler(filters.VOICE, voice_collect_example),
            ],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
        allow_reentry=True,
    )

    application.add_handler(setup_conv)
    application.add_handler(voice_conv)
    application.add_handler(case_conv)

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
                reply_markup=_build_next_step_keyboard(update.effective_user.id, include_reset=True),
            )


def main():
    """Entry point for local development - runs in polling mode."""
    import requests as _req
    import subprocess as _subprocess

    init()
    init_profile_db()

    # Clear any existing webhook so polling works
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    _req.post(f"https://api.telegram.org/bot{token}/deleteWebhook", json={"drop_pending_updates": True})
    logger.info("Webhook cleared - polling mode active")

    application = build_application()
    application.add_error_handler(error_handler)

    # Weekly nudge — every 7 days. first=86400 means don't fire on startup.
    application.job_queue.run_repeating(weekly_push, interval=604800, first=86400)

    # Register commands so they appear in Telegram's "/" menu
    async def post_init(app):
        await app.bot.set_my_commands([
            ("start", "Open Portfolio Guru and get started"),
            ("setup", "Connect your portfolio account"),
            ("voice", "Set up your personal writing voice"),
            ("settings", "View status, usage, and preferences"),
            ("reset", "Clear current session and start fresh"),
            ("cancel", "Cancel whatever is happening"),
            ("delete", "Delete all your stored data"),
            ("help", "How to use Portfolio Guru"),
        ])
        # Set bot description (shown on profile page before starting)
        try:
            await app.bot.set_my_description(
                "Portfolio Guru files your medical WPBA entries in seconds.\n\n"
                "Describe a case by text, voice, photo, or document — the bot picks the right form, "
                "drafts the entry, and files it when you approve.\n\n"
                "45 RCEM forms supported. Files directly to Kaizen ePortfolio."
            )
            await app.bot.set_my_short_description(
                "File WPBA entries to Kaizen in seconds. Text, voice, photo, or document → draft → approve → filed."
            )
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
