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
from extractor import extract_cbd_data, extract_form_data, recommend_form_types, classify_intent, answer_question, extract_explicit_form_type, assess_case_sufficiency
from filer_router import route_filing
from kaizen_filer import FORM_UUIDS
from form_schemas import FORM_SCHEMAS
from models import FormDraft, CBDData
from whisper import transcribe_voice
from vision import extract_from_image
from profile_store import init_profile_db, store_training_level, get_training_level, get_voice_profile, store_voice_profile, clear_voice_profile

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




def _store_draft(context, draft):
    """Store draft as plain dict so PicklePersistence can serialise it."""
    if isinstance(draft, CBDData):
        context.user_data["draft_data"] = {"_type": "CBD", **draft.model_dump()}
    elif isinstance(draft, FormDraft):
        context.user_data["draft_data"] = {"_type": "FORM", "form_type": draft.form_type,
                                            "fields": draft.fields, "uuid": draft.uuid}


def _load_draft(context):
    """Reconstruct draft object from stored dict."""
    raw = context.user_data.get("draft_data")
    if not raw:
        return None
    if isinstance(raw, (CBDData, FormDraft)):
        return raw  # already an object (in-memory session, not restored)
    t = raw.get("_type")
    if t == "CBD":
        d = {k: v for k, v in raw.items() if k != "_type"}
        return CBDData(**d)
    elif t == "FORM":
        return FormDraft(form_type=raw["form_type"], fields=raw["fields"], uuid=raw.get("uuid"))
    return None

# ConversationHandler states
(AWAIT_USERNAME, AWAIT_PASSWORD,
 AWAIT_FORM_CHOICE, AWAIT_APPROVAL,
 AWAIT_EDIT_FIELD, AWAIT_EDIT_VALUE,
 AWAIT_CASE_INPUT, AWAIT_TRAINING_LEVEL,
 AWAIT_VOICE_EXAMPLES) = range(9)

# Common button patterns used across the bot
_BTN_RESET = InlineKeyboardButton("🔄 Start Fresh", callback_data="ACTION|reset")
_BTN_FILE = InlineKeyboardButton("📂 File a case", callback_data="ACTION|file")
_BTN_SETUP = InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup")
_BTN_CANCEL = InlineKeyboardButton("❌ Cancel", callback_data="ACTION|cancel")
_BTN_HELP = InlineKeyboardButton("ℹ️ Help", callback_data="INFO|what")
_BTN_VOICE = InlineKeyboardButton("✍️ Voice Profile", callback_data="ACTION|voice")
_BTN_ADD_DETAIL = InlineKeyboardButton("➕ I'll add more", callback_data="ACTION|add_detail")
_BTN_CONTINUE_THIN = InlineKeyboardButton("✅ Continue anyway", callback_data="ACTION|continue_thin")

_KB_RETRY_RESET = InlineKeyboardMarkup([[_BTN_RESET]])
_KB_FILE_RESET = InlineKeyboardMarkup([[_BTN_FILE], [_BTN_RESET]])

# Training level → form types available
TRAINING_LEVEL_FORMS = {
    "ST3": ["CBD", "DOPS", "MINI_CEX", "ACAT", "MSF", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH", "COMPLAINT", "SERIOUS_INC", "ESLE"],
    "ST4": ["CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "MSF", "QIAT", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC", "ESLE"],
    "ST5": ["CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "STAT", "MSF", "QIAT", "JCF", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC", "ESLE"],
    "ST6": ["CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "STAT", "MSF", "QIAT", "JCF", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC", "ESLE"],
    "SAS": ["CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "STAT", "MSF", "QIAT", "JCF", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC", "ESLE"],
}

WELCOME_MSG = """🩺 Portfolio Guru — your WPBA entries, filed in seconds.

Describe a case by text, voice note, or photo.
I'll pick the right form, draft the entry, and file it when you approve.

Your credentials are encrypted and never shared.

Tap 🔗 Connect to get started."""

WELCOME_MSG_CONNECTED = """🩺 Portfolio Guru — ready when you are.

Send me a clinical case (text, voice, or photo) and I'll handle the rest."""

WHAT_IS_THIS_MSG = """🩺 Portfolio Guru files your WPBA entries — in seconds.

📝 Describe → 🔍 I pick the form → ✅ You approve → 📤 Filed

Describe a clinical case by text, voice note, or photo. The bot works out which form fits best, extracts the right fields, and shows you the full draft to review. Nothing is saved until you approve.

All 19 RCEM forms supported:
CBD · DOPS · Mini-CEX · ACAT · LAT · ACAF · STAT · MSF · QIAT · JCF · Teaching · Procedural Log · SDL · Ultrasound Case · ESLE · Complaint · Serious Incident · Educational Activity · Formal Course

Works with Kaizen and other e-portfolio platforms."""

FILE_CASE_PROMPT = "Send me a case description — text, voice note, or photo."


def _build_welcome_keyboard(connected: bool = False):
    if connected:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("❓ What is this?", callback_data="INFO|what"),
                InlineKeyboardButton("📂 File a case", callback_data="ACTION|file"),
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
    # New forms
    "TEACH": "👨‍🏫", "PROC_LOG": "🔬", "SDL": "📖", "US_CASE": "🔊",
    "ESLE": "⚠️", "COMPLAINT": "📝", "SERIOUS_INC": "🚨",
    "EDU_ACT": "🎓", "FORMAL_COURSE": "📋",
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


def _build_form_choice_keyboard(recommendations):
    """Build inline keyboard for form type selection — AI suggestions + See all forms escape hatch."""
    buttons = []
    for rec in recommendations:
        emoji = FORM_EMOJIS.get(rec.form_type, "📄")
        label = _form_display_name(rec.form_type)
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


def _build_approval_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ File this draft", callback_data="APPROVE|draft"),
            InlineKeyboardButton("✏️ Edit", callback_data="EDIT|draft"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="CANCEL|draft")],
    ])


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


def _format_draft_preview(draft) -> str:
    """Format draft data as a preview message. Dispatches based on type."""
    if isinstance(draft, FormDraft):
        return _format_generic_draft(draft)
    return _format_cbd_draft(draft)


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


def _format_cbd_draft(cbd_data) -> str:
    """Format CBD data as a preview message."""
    date_str = cbd_data.date_of_encounter
    try:
        from datetime import datetime
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_display = dt.strftime("%-d %b %Y")
    except (ValueError, AttributeError):
        date_display = date_str

    curriculum = _format_curriculum_hierarchy(cbd_data.curriculum_links, cbd_data.key_capabilities)

    return (
        f"📋 *Draft CBD — Review before filing*\n\n"
        f"📅 *Date:* {date_display}\n"
        f"🏥 *Setting:* {cbd_data.clinical_setting}\n"
        f"🩺 *Presentation:* {cbd_data.patient_presentation}\n\n"
        f"🗒️ *Case narrative:*\n{cbd_data.clinical_reasoning}\n\n"
        f"💭 *Reflection:*\n{cbd_data.reflection}\n\n"
        f"📚 *Curriculum:*\n{curriculum}"
    )


def _format_generic_draft(draft: FormDraft) -> str:
    """Format a generic FormDraft as a preview message."""
    schema = FORM_SCHEMAS.get(draft.form_type, {})
    form_name = schema.get("name", draft.form_type)
    emoji = FORM_EMOJIS.get(draft.form_type, "📋")

    lines = [
        f"{emoji} *Draft {form_name} — Review before filing*",
        ""
    ]

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


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if has_credentials(user_id):
        training_level = get_training_level(user_id)
        grade_str = f"📊 Training level: {training_level}" if training_level else "📊 Training level: not set"
        # Count drafts filed
        import pathlib
        drafts_dir = pathlib.Path.home() / ".openclaw/data/portfolio-guru/drafts"
        draft_count = len(list(drafts_dir.glob(f"{user_id}_*"))) if drafts_dir.exists() else 0
        drafts_str = f"📂 Drafts filed: {draft_count}"
        vp = get_voice_profile(user_id)
        voice_str = "✍️ Voice profile: active" if vp else "✍️ Voice profile: not set"
        buttons = [
            [InlineKeyboardButton("📂 File a case", callback_data="ACTION|file")],
        ]
        if not vp:
            buttons.append([InlineKeyboardButton("✍️ Set up voice profile", callback_data="ACTION|voice")])
        if not training_level:
            buttons.append([InlineKeyboardButton("🔗 Update setup", callback_data="ACTION|setup")])
        await update.message.reply_text(
            f"✅ Portfolio connected and ready.\n\n{grade_str}\n{drafts_str}\n{voice_str}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await update.message.reply_text("🔗 No credentials stored.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup")]
        ]))
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

    # Can be triggered by command or callback
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("📧 What's your Kaizen username (email)?")
    else:
        await update.message.reply_text("📧 What's your Kaizen username (email)?")
    return AWAIT_USERNAME


async def setup_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if "@" not in text or "." not in text:
        await update.message.reply_text("⚠️ That doesn't look like an email. What's your Kaizen username?")
        return AWAIT_USERNAME
    context.user_data["setup_username"] = text
    await update.message.reply_text("🔒 What's your Kaizen password?")
    return AWAIT_PASSWORD


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

    store_credentials(user_id, username, password)
    context.user_data.pop("setup_username", None)

    # Ask training level before finishing setup
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("ST3", callback_data="LEVEL|ST3"),
         InlineKeyboardButton("ST4", callback_data="LEVEL|ST4")],
        [InlineKeyboardButton("ST5", callback_data="LEVEL|ST5"),
         InlineKeyboardButton("ST6", callback_data="LEVEL|ST6")],
        [InlineKeyboardButton("SAS / Fellow", callback_data="LEVEL|SAS")],
    ])
    await update.effective_chat.send_message(
        "Kaizen connected ✅\n\nOne more thing — what's your training level? This helps me show you the right form types.",
        reply_markup=keyboard
    )
    return AWAIT_TRAINING_LEVEL


async def setup_training_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle training level selection during setup."""
    query = update.callback_query
    await query.answer()
    level = query.data.split("|")[1]
    user_id = update.effective_user.id
    store_training_level(user_id, level)
    await query.edit_message_text(
        f"All set — training level saved as {level}.\n\nSend me a case whenever you're ready."
    )
    return ConversationHandler.END


async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("❌ Setup cancelled.")
    else:
        await update.message.reply_text("❌ Setup cancelled.")
    return ConversationHandler.END


# === VOICE PROFILE FLOW ===

async def voice_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start voice profile collection — /voice command."""
    user_id = update.effective_user.id
    existing = get_voice_profile(user_id)

    if existing:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Rebuild Profile", callback_data="VOICE|rebuild"),
             InlineKeyboardButton("🗑️ Remove Profile", callback_data="VOICE|remove")],
            [InlineKeyboardButton("❌ Cancel", callback_data="VOICE|cancel")],
        ])
        await update.message.reply_text(
            "✍️ You already have a voice profile active. Your drafts are styled to match your writing.\n\n"
            "What would you like to do?",
            reply_markup=keyboard
        )
        return AWAIT_VOICE_EXAMPLES

    await update.message.reply_text(
        "✍️ *Voice Profile Setup*\n\n"
        "Send me 3-5 examples of portfolio entries you've written before. "
        "These can be:\n"
        "• Text messages (paste or type)\n"
        "• Photos of handwritten/printed entries\n"
        "• Voice notes describing your style\n\n"
        "I'll analyse your writing style and use it to make all future drafts sound like you.\n\n"
        "Send your first example now.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="VOICE|cancel")],
        ])
    )
    context.user_data["voice_examples"] = []
    return AWAIT_VOICE_EXAMPLES


async def voice_collect_example(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Collect a voice profile example from the user."""
    msg = update.message
    examples = context.user_data.get("voice_examples", [])

    # Handle callback queries (rebuild/remove/cancel buttons)
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "VOICE|cancel":
            context.user_data.pop("voice_examples", None)
            await query.edit_message_text("❌ Voice profile setup cancelled.")
            return ConversationHandler.END

        if data == "VOICE|remove":
            clear_voice_profile(update.effective_user.id)
            context.user_data.pop("voice_examples", None)
            await query.edit_message_text("🗑️ Voice profile removed. Drafts will use standard clinical style.")
            return ConversationHandler.END

        if data == "VOICE|rebuild":
            context.user_data["voice_examples"] = []
            await query.edit_message_text(
                "🔄 Starting fresh. Send me 3-5 examples of your portfolio writing.\n\n"
                "Send your first example now."
            )
            return AWAIT_VOICE_EXAMPLES

        if data == "VOICE|done":
            return await _build_voice_profile(update, context)

        return AWAIT_VOICE_EXAMPLES

    # Text example
    if msg and msg.text:
        text = msg.text.strip()
        if text.lower() in ("/cancel", "/done"):
            if text.lower() == "/done" and len(examples) >= 2:
                return await _build_voice_profile(update, context)
            context.user_data.pop("voice_examples", None)
            await msg.reply_text("❌ Voice profile setup cancelled.")
            return ConversationHandler.END
        examples.append(text)

    # Photo example — extract text from image
    elif msg and msg.photo:
        from vision import extract_from_image
        ack = await msg.reply_text("📷 Reading image…")
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
                await ack.edit_text(f"📷 Got it — example {len(examples)} captured.")
            else:
                await ack.edit_text("⚠️ Couldn't extract text from that image. Try another.")
                return AWAIT_VOICE_EXAMPLES
        except Exception:
            await ack.edit_text("⚠️ Couldn't read image. Try pasting text instead.")
            return AWAIT_VOICE_EXAMPLES

    # Voice note
    elif msg and msg.voice:
        ack = await msg.reply_text("🎙️ Transcribing…")
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
                await ack.edit_text(f"🎙️ Transcribed — example {len(examples)} captured.")
            else:
                await ack.edit_text("⚠️ Couldn't transcribe. Try pasting text instead.")
                return AWAIT_VOICE_EXAMPLES
        except Exception:
            await ack.edit_text("⚠️ Transcription failed. Try pasting text instead.")
            return AWAIT_VOICE_EXAMPLES

    context.user_data["voice_examples"] = examples

    if len(examples) >= 5:
        return await _build_voice_profile(update, context)

    if len(examples) >= 3:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Build Profile ({len(examples)} examples)", callback_data="VOICE|done")],
            [InlineKeyboardButton("➕ Add More", callback_data="VOICE|more")],
        ])
        await (msg or update.callback_query.message).reply_text(
            f"Got {len(examples)} examples. You can send more (up to 5) or build your profile now.",
            reply_markup=keyboard,
        )
    else:
        remaining = 3 - len(examples)
        await (msg or update.callback_query.message).reply_text(
            f"Got it — example {len(examples)} captured. Send {remaining} more (minimum 3 needed)."
        )

    return AWAIT_VOICE_EXAMPLES


async def _build_voice_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Build the voice profile from collected examples."""
    examples = context.user_data.get("voice_examples", [])
    target = update.callback_query.message if update.callback_query else update.message

    ack = await target.reply_text("🔍 Analysing your writing style…")

    try:
        from voice_profile import generate_voice_profile
        profile_json = await asyncio.wait_for(
            generate_voice_profile(examples), timeout=30
        )
        store_voice_profile(update.effective_user.id, profile_json, len(examples))

        import json
        profile = json.loads(profile_json)
        summary = profile.get("voice_summary", "Profile generated successfully.")

        await ack.edit_text(
            f"✅ Voice profile created from {len(examples)} examples.\n\n"
            f"Your style: {summary}\n\n"
            "All future drafts will match your writing voice.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 File a case", callback_data="ACTION|file"),
                 InlineKeyboardButton("🔄 Rebuild", callback_data="ACTION|voice")],
            ])
        )
    except Exception as e:
        logger.error(f"Voice profile generation failed: {e}", exc_info=True)
        await ack.edit_text(
            "⚠️ Couldn't analyse your writing style.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Try Again", callback_data="ACTION|voice")],
            ])
        )

    context.user_data.pop("voice_examples", None)
    return ConversationHandler.END


async def handle_info_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle INFO|what button from any message, regardless of conversation state."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(WHAT_IS_THIS_MSG)


async def handle_action_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all ACTION| buttons — universal dispatcher for button-first UX."""
    query = update.callback_query
    await query.answer()
    action = query.data.split("|", 1)[1] if "|" in query.data else ""
    user_id = update.effective_user.id

    if action == "setup":
        if has_credentials(user_id):
            await query.message.reply_text(
                "Your Kaizen account is already connected.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📂 File a case", callback_data="ACTION|file")]
                ])
            )
        else:
            await setup_start(update, context)

    elif action == "reset":
        context.user_data.clear()
        await query.message.reply_text(
            "🔄 Session cleared. Ready for a new case.",
            reply_markup=InlineKeyboardMarkup([
                [_BTN_FILE],
            ])
        )

    elif action == "cancel":
        context.user_data.clear()
        await query.message.reply_text("❌ Cancelled.")

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
                "✍️ You already have a voice profile. What would you like to do?",
                reply_markup=keyboard
            )
        else:
            await query.message.reply_text(
                "✍️ *Voice Profile Setup*\n\n"
                "Send me 3-5 examples of portfolio entries you've written before.\n"
                "• Text messages (paste or type)\n"
                "• Photos of handwritten/printed entries\n"
                "• Voice notes describing your style\n\n"
                "Send your first example now.",
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
            await query.message.reply_text("📋 Send me a case — text, voice note, or photo.")

    elif action == "help":
        await query.message.reply_text(WHAT_IS_THIS_MSG)

    elif action == "status":
        # Inline status — same as /status command
        if has_credentials(user_id):
            training_level = get_training_level(user_id)
            grade_str = f"📊 Training level: {training_level}" if training_level else "📊 Training level: not set"
            import pathlib
            drafts_dir = pathlib.Path.home() / ".openclaw/data/portfolio-guru/drafts"
            draft_count = len(list(drafts_dir.glob(f"{user_id}_*"))) if drafts_dir.exists() else 0
            vp = get_voice_profile(user_id)
            voice_str = "✍️ Voice profile: active" if vp else "✍️ Voice profile: not set"
            await query.message.reply_text(
                f"✅ Portfolio connected.\n\n{grade_str}\n📂 Drafts filed: {draft_count}\n{voice_str}",
                reply_markup=InlineKeyboardMarkup([[_BTN_FILE]])
            )
        else:
            await query.message.reply_text("🔗 Not connected yet.", reply_markup=InlineKeyboardMarkup([[_BTN_SETUP]]))

    elif action == "delete":
        # Confirm before deleting
        await query.message.reply_text(
            "⚠️ This will delete all your stored data (credentials, profile, voice profile). Are you sure?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ Yes, delete", callback_data="CONFIRM|delete"),
                 InlineKeyboardButton("❌ No, keep", callback_data="ACTION|cancel")],
            ])
        )


async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 👍/👎 feedback after filing."""
    query = update.callback_query
    await query.answer("Thanks for the feedback!")
    feedback = query.data.split("|")[1]  # "good" or "bad"
    user_id = update.effective_user.id
    # Log feedback
    import json as _json
    from pathlib import Path
    feedback_dir = Path.home() / ".openclaw/data/portfolio-guru/feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    entry = {"user_id": user_id, "feedback": feedback, "timestamp": datetime.now().isoformat()}
    feedback_path = feedback_dir / f"{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(feedback_path, "w") as f:
        _json.dump(entry, f)
    # Disarm feedback buttons, keep "File another" button
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 File another case", callback_data="ACTION|file")],
    ]))


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Top-level /cancel — clears state and returns to idle."""
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Send a case whenever you're ready.")
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
            deleted_items.append("training level")

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
    context.user_data.clear()
    await update.message.reply_text(
        "✅ Reset done — all clear.\n\nSend a case by text, voice, or photo whenever you're ready."
    )
    return ConversationHandler.END


HELP_MSG = """📖 *Portfolio Guru — Help*

*How it works:*
📝 Describe a case → 🔍 I pick the form → ✅ You approve → 📤 Filed

Send a case by text, voice note, or photo. I'll suggest the best WPBA form, generate a full draft, and file it to your e-portfolio when you approve.

*All 19 RCEM forms supported:*
CBD · DOPS · Mini-CEX · ACAT · LAT · ACAF · STAT · MSF · QIAT · JCF · Teaching · Procedural Log · SDL · Ultrasound Case · ESLE · Complaint · Serious Incident · Educational Activity · Formal Course"""


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        HELP_MSG,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [_BTN_FILE],
            [_BTN_SETUP, _BTN_VOICE],
            [InlineKeyboardButton("📊 Status", callback_data="ACTION|status"),
             _BTN_RESET],
        ])
    )
    return ConversationHandler.END


# === CALLBACK QUERY HANDLERS ===

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route callback queries based on prefix."""
    query = update.callback_query
    data = query.data

    if data.startswith("INFO|"):
        await query.answer()
        await query.message.reply_text(WHAT_IS_THIS_MSG)
        return ConversationHandler.END

    elif data == "ACTION|setup":
        await query.answer()
        user_id = update.effective_user.id
        if has_credentials(user_id):
            await query.message.reply_text(
                "Your Kaizen account is already connected. Send me a case to get started.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📂 File a case", callback_data="ACTION|file")]
                ])
            )
            return ConversationHandler.END
        return await setup_start(update, context)

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
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        context.user_data["awaiting_detail"] = True
        await query.message.reply_text("Send me the extra detail and I'll fold it into the same case.")
        return AWAIT_CASE_INPUT

    elif data == "ACTION|continue_thin":
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        context.user_data["continue_thin"] = True
        await query.message.reply_text("Okay — continuing with the detail you already gave me.")
        case_text = context.user_data.get("case_text", "")
        if case_text:
            class _SyntheticMessage:
                def __init__(self, original_message, text):
                    self._original = original_message
                    self.text = text
                    self.voice = None
                    self.photo = None
                    self.chat_id = original_message.chat_id
                    self.message_id = original_message.message_id
                async def reply_text(self, *args, **kwargs):
                    return await self._original.reply_text(*args, **kwargs)
            original_message = update.effective_message
            update.message = _SyntheticMessage(original_message, case_text)
            return await handle_case_input(update, context)
        await query.message.reply_text(FILE_CASE_PROMPT)
        return AWAIT_CASE_INPUT

    elif data.startswith("FORM|"):
        return await handle_form_choice(update, context)

    elif data.startswith("APPROVE|"):
        return await handle_approval_approve(update, context)

    elif data.startswith("EDIT|"):
        return await handle_approval_edit(update, context)

    elif data.startswith("FIELD|"):
        return await handle_edit_field(update, context)

    elif data.startswith("CANCEL|") or data == "ACTION|reset":
        await query.answer()
        # Disarm buttons immediately — prevents double-tap
        await query.edit_message_reply_markup(reply_markup=None)
        context.user_data.clear()
        await query.message.reply_text("❌ Cancelled. Send me a case whenever you're ready.")
        return ConversationHandler.END


# === CASE INPUT HANDLER ===

async def handle_case_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text, voice, or photo input for case description."""
    user_id = update.effective_user.id

    # Clear any stale status message state from previous sessions
    context.user_data.pop("status_msg_id", None)
    context.user_data.pop("status_msg_chat", None)

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

    # Determine input type and extract text
    case_text = None

    if update.message.text:
        raw_text = update.message.text.strip()

        # Fast heuristic: long clinical-sounding messages skip classify entirely
        # Saves ~3-5s of Gemini latency for obvious cases
        _CLINICAL_KEYWORDS = {"patient", "presented", "diagnosed", "examined", "management",
                              "symptoms", "clinical", "assessment", "treatment", "referred",
                              "history", "examination", "investigation", "procedure", "resuscitation",
                              "chest pain", "shortness of breath", "abdominal", "fracture", "suture",
                              "intubation", "cannulation", "triage", "observations", "bloods"}
        words_lower = raw_text.lower()
        word_count = len(raw_text.split())
        clinical_hits = sum(1 for kw in _CLINICAL_KEYWORDS if kw in words_lower)

        if word_count > 30 and clinical_hits >= 2:
            # Obviously a clinical case — skip AI classify
            intent = "case"
        elif word_count < 8 and clinical_hits == 0:
            # Very short, no clinical language — likely chitchat
            intent = "chitchat" if word_count < 4 else "case"
        else:
            # Ambiguous — use AI classify
            await update.effective_chat.send_action(constants.ChatAction.TYPING)
            try:
                intent = await classify_intent(raw_text)
            except Exception:
                intent = "case"  # Default to case on error

        if intent == "chitchat":
            context.user_data.clear()
            await update.message.reply_text(
                "Hey! Ready when you are. Send me a clinical case and I'll draft it for your portfolio."
            )
            return ConversationHandler.END

        if intent == "question":
            context.user_data.clear()
            try:
                answer = await answer_question(raw_text)
                await update.message.reply_text(answer)
            except Exception:
                await update.message.reply_text(
                    "I help you file clinical cases to your Kaizen e-portfolio. "
                    "Send me a case description by text, voice note, or photo."
                )
            return ConversationHandler.END

        # Intent is 'case' - proceed with filing flow
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
            context.user_data["status_msg_id"] = ack.message_id
            context.user_data["status_msg_chat"] = ack.chat_id
        except Exception as e:
            context.user_data.clear()
            await ack.edit_text("⚠️ Couldn't transcribe voice note. Try again.")
            return ConversationHandler.END
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
            await ack.edit_text("📷 Image read. Finding matching forms…")
            context.user_data["status_msg_id"] = ack.message_id
            context.user_data["status_msg_chat"] = ack.chat_id
        except Exception as e:
            context.user_data.clear()
            await ack.edit_text("⚠️ Couldn't read image. Try a clearer photo or describe the case in text.")
            return ConversationHandler.END
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    if not case_text:
        context.user_data.clear()
        await update.message.reply_text("💬 Send a text message, voice note, or photo.")
        return ConversationHandler.END

    # If user is adding detail after a thin-case prompt, merge it once and continue
    if context.user_data.get("awaiting_detail") and update.message.text:
        previous_case = context.user_data.get("case_text", "")
        case_text = f"{previous_case}\n\nAdditional detail:\n{case_text}".strip()
        context.user_data.pop("awaiting_detail", None)
        context.user_data["thin_case_rechecked"] = True

    # Store case text and input source
    context.user_data["case_text"] = case_text
    input_source = "photo" if update.message.photo else ("voice" if update.message.voice else "text")

    # Thin-case gate — ask specific questions before drafting if the case is too sparse
    if not context.user_data.get("thin_case_rechecked") and not context.user_data.get("continue_thin"):
        try:
            sufficiency = await asyncio.wait_for(assess_case_sufficiency(case_text), timeout=15)
        except Exception:
            sufficiency = {"sufficient": True, "questions": []}
        if not sufficiency.get("sufficient", True):
            questions = sufficiency.get("questions", [])[:3]
            q_text = "\n".join(f"• {q}" for q in questions) if questions else "• What happened clinically?\n• What were you thinking at the time?\n• What did you learn from it?"
            context.user_data["case_text"] = case_text
            context.user_data["thin_case_rechecked"] = True
            await update.message.reply_text(
                f"Your case is a bit brief for a strong portfolio entry. A few questions that would help:\n\n{q_text}\n\nSend me the extra detail and I'll add it to your case, or tap below to continue with what you have.",
                reply_markup=InlineKeyboardMarkup([[_BTN_ADD_DETAIL, _BTN_CONTINUE_THIN]])
            )
            context.user_data["awaiting_detail"] = True
            return AWAIT_CASE_INPUT

    context.user_data.pop("continue_thin", None)

    # Only check for explicit form type in text/voice input — never for photos
    # (photo descriptions should always go to user-selected form, never auto-routed)
    explicit_form = extract_explicit_form_type(case_text) if input_source != "photo" else None
    if explicit_form:
        context.user_data["chosen_form"] = explicit_form
        emoji = FORM_EMOJIS.get(explicit_form, "📋")
        await update.effective_chat.send_action(constants.ChatAction.TYPING)
        ack = await update.message.reply_text(f"{emoji} Generating {_form_display_name(explicit_form)} draft…")
        try:
            vp = get_voice_profile(update.effective_user.id) or ""
            if explicit_form == "CBD":
                draft = await asyncio.wait_for(extract_cbd_data(case_text, voice_profile_json=vp), timeout=45)
            else:
                draft = await asyncio.wait_for(extract_form_data(case_text, explicit_form, voice_profile_json=vp), timeout=45)
            _store_draft(context, draft)
        except asyncio.TimeoutError:
            logger.error(f"Draft generation timed out for explicit {explicit_form}")
            await ack.edit_text("⏳ Draft generation timed out. Please try again.")
            return ConversationHandler.END
        except Exception as e:
            logger.error(f"Draft generation failed: {e}", exc_info=True)
            await ack.edit_text("⚠️ Could not generate draft.", reply_markup=_KB_RETRY_RESET)
            return ConversationHandler.END
        preview = _format_draft_preview(draft)
        await _safe_edit_text(ack, preview, reply_markup=_build_approval_keyboard(), parse_mode="Markdown")
        return AWAIT_APPROVAL

    # No explicit form — get AI recommendations filtered by training level (if set)
    user_id = update.effective_user.id
    training_level = get_training_level(user_id)
    allowed_forms = TRAINING_LEVEL_FORMS.get(training_level, TRAINING_LEVEL_FORMS["ST5"]) if training_level else TRAINING_LEVEL_FORMS["ST5"]

    await update.effective_chat.send_action(constants.ChatAction.TYPING)
    try:
        recommendations = await asyncio.wait_for(recommend_form_types(case_text), timeout=30)
        # Filter to forms appropriate for this training level
        recommendations = [r for r in recommendations if r.form_type in allowed_forms]
        context.user_data["form_recommendations"] = recommendations
    except Exception as e:
        logger.error(f"Form recommendation failed: {e}")
        from models import FormTypeRecommendation
        from extractor import FORM_UUIDS
        recommendations = [FormTypeRecommendation(
            form_type="CBD",
            rationale="Clinical case",
            uuid=FORM_UUIDS["CBD"]
        )]
        context.user_data["form_recommendations"] = recommendations

    rationale_lines = [f"- {_form_display_name(r.form_type)}: {r.rationale}" for r in recommendations if r.uuid]
    rationale_text = "\n".join(rationale_lines) if rationale_lines else "- Case-Based Discussion: Clinical case"

    status_msg = context.user_data.pop("status_msg_id", None)
    status_chat = context.user_data.pop("status_msg_chat", None)

    # Persist recommendations so back button can restore this screen
    context.user_data["form_recommendations_text"] = f"Which form would you like to create?\n\n{rationale_text}"

    if status_msg and status_chat:
        try:
            await context.bot.edit_message_text(
                chat_id=status_chat,
                message_id=status_msg,
                text=f"Which form would you like to create?\n\n{rationale_text}",
                reply_markup=_build_form_choice_keyboard(recommendations)
            )
        except Exception:
            await update.message.reply_text(
                f"Which form would you like to create?\n\n{rationale_text}",
                reply_markup=_build_form_choice_keyboard(recommendations)
            )
    else:
        await update.message.reply_text(
            f"Which form would you like to create?\n\n{rationale_text}",
            reply_markup=_build_form_choice_keyboard(recommendations)
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
        from extractor import FORM_UUIDS
        from models import FormTypeRecommendation
        user_id = update.effective_user.id
        training_level = get_training_level(user_id)
        # If no training level set, show all forms and nudge user to set grade
        if training_level:
            allowed = TRAINING_LEVEL_FORMS.get(training_level, TRAINING_LEVEL_FORMS["ST5"])
            header = f"All forms available for {training_level} — pick one:"
        else:
            allowed = TRAINING_LEVEL_FORMS["ST5"]  # show full set, no filtering
            header = "All forms - pick one:"
        all_recs = [
            FormTypeRecommendation(form_type=ft, rationale="", uuid=FORM_UUIDS.get(ft))
            for ft in allowed if FORM_UUIDS.get(ft)
        ]
        buttons = []
        for rec in all_recs:
            emoji = FORM_EMOJIS.get(rec.form_type, "📄")
            label = _form_display_name(rec.form_type)
            buttons.append(InlineKeyboardButton(f"{emoji} {label}", callback_data=f"FORM|{rec.form_type}"))
        rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        rows.append([
            InlineKeyboardButton("⬅️ Back", callback_data="FORM|back"),
            InlineKeyboardButton("❌ Cancel", callback_data="CANCEL|form"),
        ])
        await query.edit_message_text(header, reply_markup=InlineKeyboardMarkup(rows))
        return AWAIT_FORM_CHOICE

    if data == "FORM|back":
        # Restore the AI recommendations screen
        recommendations = context.user_data.get("form_recommendations", [])
        saved_text = context.user_data.get("form_recommendations_text", "Which form would you like to create?")
        await query.edit_message_text(
            saved_text,
            reply_markup=_build_form_choice_keyboard(recommendations)
        )
        return AWAIT_FORM_CHOICE

    form_type = data.split("|")[1]

    # Stale button guard — if case_text is gone, this button belongs to an old flow
    case_text = context.user_data.get("case_text", "")
    if not case_text:
        try:
            await query.edit_message_text("⏳ This draft has expired. Start a new case whenever you're ready.", reply_markup=None)
        except Exception:
            pass  # message may already be edited
        return ConversationHandler.END

    context.user_data["chosen_form"] = form_type

    # Disarm buttons, show single working status — updated in-place throughout
    emoji = FORM_EMOJIS.get(form_type, "📋")
    await query.edit_message_text(
        f"{emoji} Generating {_form_display_name(form_type)} draft…",
        reply_markup=None
    )

    try:
        vp = get_voice_profile(update.effective_user.id) or ""
        if form_type == "CBD":
            draft = await asyncio.wait_for(extract_cbd_data(case_text, voice_profile_json=vp), timeout=45)
        else:
            draft = await asyncio.wait_for(extract_form_data(case_text, form_type, voice_profile_json=vp), timeout=45)
        _store_draft(context, draft)
    except asyncio.TimeoutError:
        logger.error(f"Draft generation timed out after 45s for {form_type}")
        await query.edit_message_text("⏳ Draft generation timed out.", reply_markup=_KB_RETRY_RESET)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Draft generation failed in form_choice: {e}", exc_info=True)
        await query.edit_message_text("⚠️ Could not generate draft.", reply_markup=_KB_RETRY_RESET)
        # Do NOT clear user_data — a newer flow may be active
        return ConversationHandler.END

    # Replace status with draft preview + approval buttons — same message, no new bubble
    preview = _format_draft_preview(draft)
    await _safe_edit_text(query.message, preview, reply_markup=_build_approval_keyboard(), parse_mode="Markdown")
    return AWAIT_APPROVAL


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
        context.user_data.clear()
        await query.message.reply_text("⚠️ No draft data found. Start over.")
        return ConversationHandler.END

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

    # Save local JSON backup
    import json as _json
    import pathlib
    from datetime import date
    drafts_dir = pathlib.Path.home() / ".openclaw/data/portfolio-guru/drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{user_id}_{form_type}_{date.today()}.json"
    with open(drafts_dir / filename, "w") as f:
        _json.dump({"form_type": form_type, "fields": fields}, f, indent=2)

    # Determine platform (default: kaizen; future: from user profile)
    platform = "kaizen"
    await update.effective_chat.send_action(constants.ChatAction.TYPING)
    ack = await query.message.reply_text(f"📤 Filing {form_name}…")

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
        context.user_data.clear()
        await ack.edit_text("⏱ Filing timed out. The draft may have saved — check your portfolio directly.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Filer error for {form_type}: {e}", exc_info=True)
        context.user_data.clear()
        await ack.edit_text("❌ Filing failed. Try again or check Kaizen directly.")
        return ConversationHandler.END

    context.user_data.clear()
    end_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👍", callback_data="FEEDBACK|good"),
            InlineKeyboardButton("👎", callback_data="FEEDBACK|bad"),
            InlineKeyboardButton("📂 File another", callback_data="ACTION|file"),
        ],
    ])

    status = result["status"]
    filled = result.get("filled", [])
    skipped = result.get("skipped", [])
    error = result.get("error")

    method = result.get("method", "deterministic")

    if status == "success":
        date_val = fields.get("date_of_encounter", fields.get("date_of_event", ""))
        slo_str = ", ".join(curriculum_links) if curriculum_links else ""
        summary = f"\n\n📅 {date_val}" if date_val else ""
        if slo_str:
            summary += f"  ·  📚 {slo_str}"
        msg = f"✅ *{form_name} draft saved.*\n\nNot submitted to assessor — open your portfolio to assign one when ready.{summary}"
    elif status == "partial":
        msg = (
            f"⚠️ *{form_name} draft saved but some fields may be incomplete.*\n\n"
            f"Filled: {len(filled)} · Skipped: {len(skipped)}\n"
            f"Review in your portfolio before sending to assessor."
        )
    else:
        # Show manual link for Kaizen; generic message for other platforms
        if platform == "kaizen" and FORM_UUIDS.get(form_type):
            kaizen_url = f"https://kaizenep.com/events/new-section/{FORM_UUIDS[form_type]}"
            msg = f"❌ *Filing failed.* {error or ''}\n\n[Open {form_name} manually in Kaizen]({kaizen_url})"
        else:
            msg = f"❌ *Filing failed.* {error or ''}\n\nTry again or fill the form manually in your portfolio."

    await _safe_edit_text(ack, msg, reply_markup=end_keyboard, parse_mode="Markdown")
    return ConversationHandler.END


async def handle_approval_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 'Edit' button — ask for free-text feedback to improve the draft."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_reply_markup(reply_markup=None)

    draft = _load_draft(context)
    if not draft:
        await query.message.reply_text(
            "This draft has expired.",
            reply_markup=_KB_FILE_RESET
        )
        return ConversationHandler.END

    await query.message.reply_text(
        "What would you like to change? Describe it in plain English — e.g. \"the reflection needs more learning points\" or \"add the SLO for shift leadership\".\n\nI'll regenerate the draft with your feedback."
    )
    return AWAIT_EDIT_VALUE


async def handle_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Unused — kept for state compatibility."""
    return AWAIT_EDIT_VALUE


async def handle_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text, voice, or photo feedback — regenerate draft using original case + feedback."""
    draft = _load_draft(context)
    case_text = context.user_data.get("case_text", "")

    if not draft:
        context.user_data.clear()
        await update.message.reply_text("⚠️ Edit failed — draft expired.", reply_markup=_KB_RETRY_RESET)
        return ConversationHandler.END

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
    """Handle unexpected text messages mid-conversation."""
    raw_text = update.message.text.strip()

    try:
        intent = await classify_intent(raw_text)
    except Exception:
        intent = "case"

    if intent == "chitchat":
        await update.message.reply_text(
            "Hey! Ready when you are. Send me a clinical case and I'll draft it for your portfolio."
        )
        return AWAIT_CASE_INPUT

    elif intent == "question":
        try:
            answer = await answer_question(raw_text)
            await update.message.reply_text(answer)
        except Exception:
            await update.message.reply_text(
                "I help you file clinical cases to your Kaizen e-portfolio. "
                "Send me a case description by text, voice note, or photo."
            )
        return AWAIT_CASE_INPUT

    else:
        # Intent is 'case' - looks like a new case
        await update.message.reply_text(
            "It looks like you want to file a new case.",
            reply_markup=InlineKeyboardMarkup([
                [_BTN_RESET, _BTN_CANCEL],
            ])
        )
        return AWAIT_CASE_INPUT


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
            CallbackQueryHandler(handle_callback, pattern=r"^ACTION\|file$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_case_input),
            MessageHandler(filters.VOICE, handle_case_input),
            MessageHandler(filters.PHOTO, handle_case_input),
        ],
        states={
            AWAIT_CASE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_case_input),
                MessageHandler(filters.VOICE, handle_case_input),
                MessageHandler(filters.PHOTO, handle_case_input),
            ],
            AWAIT_FORM_CHOICE: [
                CallbackQueryHandler(handle_form_choice, pattern=r"^FORM\|"),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mid_conversation_text),
            ],
            AWAIT_APPROVAL: [
                CallbackQueryHandler(handle_approval_approve, pattern=r"^APPROVE\|"),
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
                # Catch ALL message types — text, voice, photo, forwarded voice/photo
                # Never let unmatched messages escape to entry points while in edit mode
                MessageHandler(~filters.COMMAND, handle_edit_value),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("help", help_command),
            CommandHandler("status", status),
            CommandHandler("reset", reset),
            CommandHandler("cancel", setup_cancel),
            CallbackQueryHandler(handle_callback),  # Handle all callbacks in fallback
        ],
        per_message=False,
        allow_reentry=False,
        persistent=True,
        name="case_conv",
    )

    # Setup conversation handler
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            AWAIT_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_username)],
            AWAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_password)],
            AWAIT_TRAINING_LEVEL: [CallbackQueryHandler(setup_training_level, pattern=r"^LEVEL\|")],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
        allow_reentry=True,
    )

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("delete", delete_data))
    application.add_handler(CommandHandler("help", help_command))
    # Top-level handlers that must work regardless of conversation state
    application.add_handler(CallbackQueryHandler(handle_info_button, pattern=r"^INFO\|"))
    application.add_handler(CallbackQueryHandler(handle_action_button, pattern=r"^ACTION\|"))
    application.add_handler(CallbackQueryHandler(handle_feedback, pattern=r"^FEEDBACK\|"))

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
            await update.effective_message.reply_text(
                "⏳ That button expired. Please tap the latest buttons or send your case again."
            )
        return

    # Conflict error from dual bot instances — silent, self-resolving
    if "conflict" in error_msg and "terminated by other" in error_msg:
        logger.warning("409 Conflict — another bot instance running, will self-resolve")
        return

    # Generic fallback
    if update and hasattr(update, 'effective_message') and update.effective_message:
        await update.effective_message.reply_text(
            "Something went wrong.",
            reply_markup=_KB_RETRY_RESET
        )


def main():
    """Entry point for local development - runs in polling mode."""
    import requests as _req

    init()
    init_profile_db()

    # Clear any existing webhook so polling works
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    _req.post(f"https://api.telegram.org/bot{token}/deleteWebhook", json={"drop_pending_updates": True})
    logger.info("Webhook cleared - polling mode active")

    application = build_application()
    application.add_error_handler(error_handler)

    # Register commands so they appear in Telegram's "/" menu
    async def post_init(app):
        await app.bot.set_my_commands([
            ("start", "Open Portfolio Guru and get started"),
            ("setup", "Connect your portfolio account"),
            ("voice", "Set up your personal writing voice"),
            ("status", "Check connection and stats"),
            ("reset", "Clear current session and start fresh"),
            ("cancel", "Cancel whatever is happening"),
            ("delete", "Delete all your stored data"),
            ("help", "How to use Portfolio Guru"),
        ])
        # Set bot description (shown on profile page before starting)
        try:
            await app.bot.set_my_description(
                "Portfolio Guru files your medical WPBA entries in seconds.\n\n"
                "Describe a case by text, voice, or photo — the bot picks the right form, "
                "drafts the entry, and files it when you approve.\n\n"
                "All 19 RCEM forms supported. Works with Kaizen and other e-portfolio platforms."
            )
            await app.bot.set_my_short_description(
                "File WPBA entries to your e-portfolio in seconds. Text, voice, or photo → draft → approve → filed."
            )
        except Exception:
            pass  # Non-critical — BotFather settings may not update on every restart
    application.post_init = post_init

    logger.info("Portfolio Guru v2 starting in POLLING mode...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
