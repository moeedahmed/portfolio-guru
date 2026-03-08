"""
Portfolio Guru Telegram Bot — v2
Multimodal input (text/voice/image) with approval flow before filing.
"""
import asyncio
import logging
import os
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler, PicklePersistence,
)
from store import store_credentials, get_credentials, has_credentials, init
from extractor import extract_cbd_data, extract_form_data, recommend_form_types, classify_intent, answer_question, extract_explicit_form_type
from filer import file_cbd_to_kaizen
from form_schemas import FORM_SCHEMAS
from models import FormDraft, CBDData
from whisper import transcribe_voice
from vision import extract_from_image
from profile_store import init_profile_db, store_training_level, get_training_level

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
 AWAIT_CASE_INPUT, AWAIT_TRAINING_LEVEL) = range(8)

# Training level → form types available
TRAINING_LEVEL_FORMS = {
    "ST3": ["CBD", "DOPS", "MINI_CEX", "ACAT", "MSF"],
    "ST4": ["CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "MSF", "QIAT"],
    "ST5": ["CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "STAT", "MSF", "QIAT", "JCF"],
    "ST6": ["CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "STAT", "MSF", "QIAT", "JCF"],
    "SAS": ["CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "STAT", "MSF", "QIAT", "JCF"],
}

WELCOME_MSG = """Portfolio Guru helps you file clinical cases to your RCEM Kaizen e-portfolio - in seconds.

Share a case by text, voice note, or photo. I'll draft the entry, show you exactly what will be filed, and only submit when you approve.

Your Kaizen credentials are encrypted and never shared."""

WELCOME_MSG_CONNECTED = """Portfolio Guru — ready to go.

Send a case by text, voice note, or photo and I'll handle the rest."""

WHAT_IS_THIS_MSG = """Portfolio Guru files your WPBA entries to Kaizen — in seconds.

Describe a clinical case by text, voice note, or photo. The bot works out which form fits (CBD, DOPS, LAT, Mini-CEX, ACAT, and more), extracts the right fields, and shows you a draft to review before anything is saved.

You approve every draft before it goes to Kaizen. Nothing is submitted to an assessor without your sign-off.

Supported forms: CBD · DOPS · Mini-CEX · ACAT · LAT · ACAF · STAT · MSF · QIAT · JCF"""

FILE_CASE_PROMPT = "Send me a case description - text, voice note, or photo."


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
}

FIELD_EMOJIS = {
    "date_of_encounter":    "📅",
    "date":                 "📅",
    "clinical_setting":     "🏥",
    "setting":              "🏥",
    "patient_presentation": "🩺",
    "presentation":         "🩺",
    "procedure":            "🔪",
    "procedure_performed":  "🔪",
    "clinical_reasoning":   "🗒️",
    "case_discussion":      "🗒️",
    "reflection":           "💭",
    "supervisor_name":      "👤",
    "assessor":             "👤",
    "level_of_supervision": "🎚️",
    "stage_of_training":    "📈",
    "trainee_role":         "👨‍⚕️",
    "leadership_context":   "🧭",
    "journal":              "📰",
    "article_title":        "📰",
    "qi_project":           "📊",
}

def _build_form_choice_keyboard(recommendations):
    """Build inline keyboard for form type selection."""
    buttons = []
    for rec in recommendations:
        emoji = FORM_EMOJIS.get(rec.form_type, "📄")
        if rec.uuid:
            label = f"{emoji} {rec.form_type}"
            buttons.append(InlineKeyboardButton(label, callback_data=f"FORM|{rec.form_type}"))
        else:
            buttons.append(InlineKeyboardButton(f"{emoji} {rec.form_type} (coming soon)", callback_data="FORM|disabled"))
    # Add cancel button
    buttons.append(InlineKeyboardButton("❌ Cancel", callback_data="CANCEL|form"))
    # Arrange in rows of 2
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
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

        # key_capabilities is merged into curriculum_links hierarchy — never render separately
        if key == "key_capabilities":
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

        # curriculum_links: render as unified SLO→KC hierarchy (pull key_capabilities too)
        if key == "curriculum_links" and isinstance(value, list):
            import re as _re
            key_caps = draft.fields.get("key_capabilities") or []
            # Derive parent SLO list from curriculum_links values
            slos_seen = []
            all_kcs = list(value) + list(key_caps)
            for item in value:
                m = _re.match(r'^(SLO\w+)', item, _re.IGNORECASE)
                slo = m.group(1).upper() if m else item.upper()
                if slo not in slos_seen:
                    slos_seen.append(slo)
            formatted = _format_curriculum_hierarchy(slos_seen, all_kcs)
            lines.append(f"📚 *Curriculum:*\n{formatted}\n")
            continue

        # Prefix label with emoji if available
        fe = FIELD_EMOJIS.get(key, "")
        label_str = f"{fe} *{label}:*" if fe else f"*{label}:*"

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
            lines.append(f"{label_str} {value}")

    return "\n".join(lines)


# === COMMAND HANDLERS ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    connected = has_credentials(update.effective_user.id)
    msg = WELCOME_MSG_CONNECTED if connected else WELCOME_MSG
    await update.message.reply_text(msg, reply_markup=_build_welcome_keyboard(connected=connected))


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if has_credentials(user_id):
        await update.message.reply_text("Credentials stored. Ready to file cases.")
    else:
        await update.message.reply_text("No credentials stored.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup")]
        ]))


# === SETUP FLOW ===

async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Can be triggered by command or callback
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("What's your Kaizen username (email)?")
    else:
        await update.message.reply_text("What's your Kaizen username (email)?")
    return AWAIT_USERNAME


async def setup_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if "@" not in text or "." not in text:
        await update.message.reply_text("That doesn't look like an email. What's your Kaizen username?")
        return AWAIT_USERNAME
    context.user_data["setup_username"] = text
    await update.message.reply_text("What's your Kaizen password?")
    return AWAIT_PASSWORD


async def setup_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = context.user_data.get("setup_username", "")
    password = update.message.text.strip()
    user_id = update.effective_user.id

    # Delete password message for security
    try:
        await update.message.delete()
    except Exception:
        pass

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
        await update.callback_query.message.reply_text("Setup cancelled.")
    else:
        await update.message.reply_text("Setup cancelled.")
    return ConversationHandler.END


async def handle_info_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle INFO|what button from any message, regardless of conversation state."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(WHAT_IS_THIS_MSG)


async def handle_setup_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ACTION|setup button from any message, regardless of conversation state."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if has_credentials(user_id):
        await query.message.reply_text(
            "Your Kaizen account is already connected. Send me a case to get started.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 File a case", callback_data="ACTION|file")]
            ])
        )
    else:
        await setup_start(update, context)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Top-level /cancel — clears state and returns to idle."""
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled. Send a case whenever you're ready.")
    return ConversationHandler.END


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Reset conversation state and clear user data."""
    context.user_data.clear()
    await update.message.reply_text(
        "✅ Reset done — all clear.\n\nSend a case by text, voice, or photo whenever you're ready."
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
        await query.message.reply_text("Cancelled. Send me a case whenever you're ready.")
        return ConversationHandler.END


# === CASE INPUT HANDLER ===

async def handle_case_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text, voice, or photo input for case description."""
    user_id = update.effective_user.id

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

        # Classify intent for text messages
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
        ack = await update.message.reply_text("Transcribing voice note...")
        try:
            voice_file = await update.message.voice.get_file()
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                await voice_file.download_to_drive(tmp.name)
                case_text = await transcribe_voice(tmp.name)
                os.unlink(tmp.name)
            await ack.edit_text(f"Transcribed:\n\n{case_text[:500]}...")
        except Exception as e:
            context.user_data.clear()
            await ack.edit_text(f"Could not transcribe voice note: {str(e)[:200]}")
            return ConversationHandler.END

    elif update.message.photo:
        ack = await update.message.reply_text("Reading image...")
        try:
            # Get largest photo
            photo = update.message.photo[-1]
            photo_file = await photo.get_file()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                await photo_file.download_to_drive(tmp.name)
                case_text = await extract_from_image(tmp.name)
                os.unlink(tmp.name)
            if case_text.strip() == "NOT_CLINICAL":
                await ack.edit_text("This image doesn't look like a clinical case. Send a text description or a photo of clinical notes/findings.")
                return ConversationHandler.END
            await ack.edit_text(f"📷 Image read. Generating form options…")
        except Exception as e:
            context.user_data.clear()
            await ack.edit_text("⚠️ Couldn't read image. Try a clearer photo or describe the case in text.")
            return ConversationHandler.END

    if not case_text:
        context.user_data.clear()
        await update.message.reply_text("Send a text message, voice note, or photo.")
        return ConversationHandler.END

    # Store case text
    context.user_data["case_text"] = case_text

    # Check if user explicitly named a form type — skip selection if so
    explicit_form = extract_explicit_form_type(case_text)
    if explicit_form:
        context.user_data["chosen_form"] = explicit_form
        emoji = FORM_EMOJIS.get(explicit_form, "📋")
        ack = await update.message.reply_text(f"{emoji} Generating {explicit_form} draft…")
        try:
            if explicit_form == "CBD":
                draft = await extract_cbd_data(case_text)
            else:
                draft = await extract_form_data(case_text, explicit_form)
            _store_draft(context, draft)
        except Exception as e:
            context.user_data.clear()
            await ack.edit_text("⚠️ Could not generate draft. Try again or /reset.")
            return ConversationHandler.END
        preview = _format_draft_preview(draft)
        await ack.edit_text(preview, reply_markup=_build_approval_keyboard(), parse_mode="Markdown")
        return AWAIT_APPROVAL

    # No explicit form — get AI recommendations filtered by training level
    user_id = update.effective_user.id
    training_level = get_training_level(user_id) or "ST5"
    allowed_forms = TRAINING_LEVEL_FORMS.get(training_level, TRAINING_LEVEL_FORMS["ST5"])

    try:
        recommendations = await recommend_form_types(case_text)
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

    rationale_lines = [f"- {r.form_type}: {r.rationale}" for r in recommendations if r.uuid]
    rationale_text = "\n".join(rationale_lines) if rationale_lines else "- CBD: Clinical case"

    await update.message.reply_text(
        f"From this case I can create:\n\n{rationale_text}",
        reply_markup=_build_form_choice_keyboard(recommendations)
    )
    return AWAIT_FORM_CHOICE


async def handle_form_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle form type selection."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "FORM|disabled":
        await query.message.reply_text("This form type is coming soon. Choose another or cancel.")
        return AWAIT_FORM_CHOICE

    form_type = data.split("|")[1]
    context.user_data["chosen_form"] = form_type

    # Disarm buttons, show single working status — updated in-place throughout
    emoji = FORM_EMOJIS.get(form_type, "📋")
    await query.edit_message_text(
        f"{emoji} Generating {form_type} draft…",
        reply_markup=None
    )

    case_text = context.user_data.get("case_text", "")

    try:
        if form_type == "CBD":
            draft = await extract_cbd_data(case_text)
        else:
            draft = await extract_form_data(case_text, form_type)
        _store_draft(context, draft)
    except Exception as e:
        context.user_data.clear()
        await query.edit_message_text(f"⚠️ Could not generate draft. Try again or /reset.")
        return ConversationHandler.END

    # Replace status with draft preview + approval buttons — same message, no new bubble
    preview = _format_draft_preview(draft)
    await query.edit_message_text(preview, reply_markup=_build_approval_keyboard(), parse_mode="Markdown")
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
        await query.message.reply_text("Credentials not found. Run /setup again.")
        return ConversationHandler.END

    username, password = creds
    draft = _load_draft(context)
    if not draft:
        context.user_data.clear()
        await query.message.reply_text("No draft data found. Start over.")
        return ConversationHandler.END

    # Handle FormDraft (non-CBD forms)
    if isinstance(draft, FormDraft):
        schema = FORM_SCHEMAS.get(draft.form_type, {})
        form_name = schema.get("name", draft.form_type)
        form_emoji = FORM_EMOJIS.get(draft.form_type, "📋")

        # Save draft to JSON file
        import json
        import pathlib
        from datetime import date
        drafts_dir = pathlib.Path.home() / ".openclaw/data/portfolio-guru/drafts"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{user_id}_{draft.form_type}_{date.today()}.json"
        draft_path = drafts_dir / filename
        with open(draft_path, "w") as f:
            json.dump({"form_type": draft.form_type, "uuid": draft.uuid, "fields": draft.fields}, f, indent=2)

        context.user_data.clear()
        end_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 File another case", callback_data="ACTION|file")],
        ])
        kaizen_url = f"https://kaizenep.com/events/new-section/{draft.uuid}"
        await query.message.reply_text(
            f"{form_emoji} *{form_name} draft saved.*\n\n"
            f"Open Kaizen to review and assign an assessor — auto-filing for this form type is coming soon.\n\n"
            f"[Open in Kaizen]({kaizen_url})",
            reply_markup=end_keyboard,
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # CBD form — use filer
    cbd_data = draft
    ack = await query.message.reply_text("Filing to Kaizen...")

    try:
        status_result, action_log, screenshot_b64, assessor_warning = await file_cbd_to_kaizen(
            cbd_data, username, password
        )
    except Exception as e:
        context.user_data.clear()
        await ack.edit_text(f"Filing failed. Try again or check Kaizen directly.")
        return ConversationHandler.END

    context.user_data.clear()
    end_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 File another case", callback_data="ACTION|file")],
    ])

    if status_result == "success":
        slos = ", ".join(cbd_data.curriculum_links) if cbd_data.curriculum_links else "None"
        msg = f"✅ *CBD draft saved in Kaizen.*\n\nNot submitted to assessor — open Kaizen to assign one when ready.\n\n📅 {cbd_data.date_of_encounter}  ·  📚 {slos}"
    elif status_result == "partial":
        msg = f"⚠️ *Draft saved but some fields may be incomplete.*\n\nReview in Kaizen before sending to assessor.\n\n📅 {cbd_data.date_of_encounter}"
    else:
        msg = "❌ Filing failed. Check Kaizen manually or try again."

    if assessor_warning:
        msg += f"\n\n{assessor_warning}"

    await ack.edit_text(msg, reply_markup=end_keyboard, parse_mode="Markdown")
    return ConversationHandler.END


async def handle_approval_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 'Edit' button — ask for free-text feedback to improve the draft."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_reply_markup(reply_markup=None)

    draft = _load_draft(context)
    if not draft:
        await query.message.reply_text(
            "This draft has expired. Send /reset and file a new case.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Reset", callback_data="ACTION|reset")]])
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
    """Handle free-text feedback — regenerate draft using original case + feedback."""
    feedback = update.message.text.strip()
    draft = _load_draft(context)
    case_text = context.user_data.get("case_text", "")

    if not draft:
        context.user_data.clear()
        await update.message.reply_text("Edit failed — draft expired. Send /reset.")
        return ConversationHandler.END

    ack = await update.message.reply_text("✏️ Regenerating draft with your feedback…")

    try:
        form_type = draft.form_type if isinstance(draft, FormDraft) else "CBD"
        current_draft_text = _format_draft_preview(draft)

        if form_type == "CBD":
            updated = await extract_cbd_data(
                case_text,
                edit_feedback=feedback,
                current_draft=current_draft_text
            )
        else:
            updated = await extract_form_data(
                case_text,
                form_type,
                edit_feedback=feedback,
                current_draft=current_draft_text
            )
        _store_draft(context, updated)
    except Exception as e:
        await ack.edit_text("⚠️ Couldn't regenerate. Try again or /reset.")
        return AWAIT_APPROVAL

    preview = _format_draft_preview(updated)
    await ack.edit_text(preview, reply_markup=_build_approval_keyboard(), parse_mode="Markdown")
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
            "It looks like you want to file a new case. Send /reset to start fresh, or /cancel to exit."
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_value),
                CallbackQueryHandler(handle_callback, pattern=r"^CANCEL\|"),
            ],
        },
        fallbacks=[
            CommandHandler("reset", reset),
            CommandHandler("cancel", setup_cancel),
            CallbackQueryHandler(handle_callback),  # Handle all callbacks in fallback
        ],
        per_message=False,
        allow_reentry=True,
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
    # Top-level handlers that must work regardless of conversation state
    application.add_handler(CallbackQueryHandler(handle_info_button, pattern=r"^INFO\|"))
    application.add_handler(CallbackQueryHandler(handle_setup_button, pattern=r"^ACTION\|setup$"))
    application.add_handler(setup_conv)
    application.add_handler(case_conv)

    # NOTE: CallbackQueryHandler already registered in case_conv fallbacks.
    # Do NOT add a second one here — causes duplicate message delivery.

    return application


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and notify user if possible."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if update and hasattr(update, 'effective_message') and update.effective_message:
        await update.effective_message.reply_text(
            "Something went wrong. Please send /reset and try again."
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
            ("setup", "Connect your Kaizen account"),
            ("status", "Check if Kaizen is connected"),
            ("reset", "Clear current session and start fresh"),
            ("cancel", "Cancel whatever is happening"),
        ])
    application.post_init = post_init

    logger.info("Portfolio Guru v2 starting in POLLING mode...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
