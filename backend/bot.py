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
    filters, ContextTypes, ConversationHandler,
)
from store import store_credentials, get_credentials, has_credentials, init
from extractor import extract_cbd_data, recommend_form_types, classify_intent, answer_question
from filer import file_cbd_to_kaizen
from whisper import transcribe_voice
from vision import extract_from_image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ConversationHandler states
(AWAIT_USERNAME, AWAIT_PASSWORD,
 AWAIT_FORM_CHOICE, AWAIT_APPROVAL,
 AWAIT_EDIT_FIELD, AWAIT_EDIT_VALUE,
 AWAIT_CASE_INPUT) = range(7)

WELCOME_MSG = """Portfolio Guru helps you file clinical cases to your RCEM Kaizen e-portfolio - in seconds.

Share a case by text, voice note, or photo. I'll draft the entry, show you exactly what will be filed, and only submit when you approve.

Your Kaizen credentials are encrypted and never shared."""

WHAT_IS_THIS_MSG = """A CBD (Case-Based Discussion) is a workplace-based assessment where you discuss a clinical case you managed with a supervisor.

Portfolio Guru extracts the key information from your case description and creates a draft CBD in Kaizen - ready for you to review and submit to your supervisor.

You stay in control: nothing is submitted until you approve it."""

FILE_CASE_PROMPT = "Send me a case description - text, voice note, or photo."


def _build_welcome_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❓ What is this?", callback_data="INFO|what"),
            InlineKeyboardButton("🔗 Connect Kaizen", callback_data="ACTION|setup"),
        ],
        [InlineKeyboardButton("📂 File a case", callback_data="ACTION|file")],
    ])


FORM_EMOJIS = {
    "CBD": "🩺", "DOPS": "🔪", "MINI_CEX": "🏥", "ACAT": "📋",
    "MSF": "👥", "QIAT": "🎓", "LAT": "📖", "JCF": "💼",
    "ACAF": "✅", "STAT": "📊",
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


def _build_edit_field_keyboard():
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


def _format_draft_preview(cbd_data) -> str:
    """Format CBD data as a preview message."""
    date_str = cbd_data.date_of_encounter
    try:
        from datetime import datetime
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_display = dt.strftime("%-d %b %Y")
    except (ValueError, AttributeError):
        date_display = date_str

    slos = "\n".join(f"  • {s}" for s in cbd_data.curriculum_links) if cbd_data.curriculum_links else "  • None"
    kcs = "\n".join(f"  • {k}" for k in cbd_data.key_capabilities) if cbd_data.key_capabilities else "  • None"

    return (
        f"📋 *Draft CBD — Review before filing*\n"
        f"{'─' * 30}\n\n"
        f"📅 *Date:* {date_display}\n"
        f"🏥 *Setting:* {cbd_data.clinical_setting}\n"
        f"🩺 *Presentation:* {cbd_data.patient_presentation}\n\n"
        f"*Case narrative:*\n{cbd_data.clinical_reasoning}\n\n"
        f"*Reflection:*\n{cbd_data.reflection}\n\n"
        f"{'─' * 30}\n"
        f"📚 *Curriculum links:*\n{slos}\n\n"
        f"⚡ *Key capabilities:*\n{kcs}"
    )


# === COMMAND HANDLERS ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_MSG, reply_markup=_build_welcome_keyboard())


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
    await update.effective_chat.send_message(
        "Connected. Send me a case description and I'll draft it for review."
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
            await ack.edit_text(f"Extracted:\n\n{case_text[:500]}...")
        except Exception as e:
            context.user_data.clear()
            await ack.edit_text(f"Could not extract text from image: {str(e)[:200]}")
            return ConversationHandler.END

    if not case_text:
        context.user_data.clear()
        await update.message.reply_text("Send a text message, voice note, or photo.")
        return ConversationHandler.END

    # Store case text
    context.user_data["case_text"] = case_text

    # Get form recommendations
    try:
        recommendations = await recommend_form_types(case_text)
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

    # Build rationale text
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

    # Disarm buttons immediately — show selection, prevent double-tap
    await query.edit_message_text(
        f"✅ {form_type} selected\n\nGenerating draft...",
        reply_markup=None
    )

    # Extract CBD data
    case_text = context.user_data.get("case_text", "")
    ack = await query.message.reply_text("Extracting case data...")

    try:
        cbd_data = await extract_cbd_data(case_text)
        context.user_data["draft_data"] = cbd_data
    except Exception as e:
        context.user_data.clear()
        await ack.edit_text(f"Could not extract case data: {str(e)[:200]}")
        return ConversationHandler.END

    # Show draft preview
    preview = _format_draft_preview(cbd_data)
    await ack.edit_text(preview)

    # Send approval buttons in separate message
    await query.message.reply_text(
        "Review the draft above. File it, edit fields, or cancel.",
        reply_markup=_build_approval_keyboard()
    )
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
    cbd_data = context.user_data.get("draft_data")
    if not cbd_data:
        context.user_data.clear()
        await query.message.reply_text("No draft data found. Start over.")
        return ConversationHandler.END

    ack = await query.message.reply_text("Filing to Kaizen...")

    try:
        status_result, action_log, screenshot_b64, assessor_warning = await file_cbd_to_kaizen(
            cbd_data, username, password
        )
    except Exception as e:
        context.user_data.clear()
        await ack.edit_text(f"Filing failed: {str(e)[:300]}")
        return ConversationHandler.END

    # Build confirmation
    slos = ", ".join(cbd_data.curriculum_links) if cbd_data.curriculum_links else "None"

    if status_result == "success":
        msg = f"Saved as draft in Kaizen. Not submitted to supervisor.\n\nDate: {cbd_data.date_of_encounter}\nForm: CBD\nSLOs: {slos}"
    elif status_result == "partial":
        msg = f"Draft saved but some fields may be incomplete. Review in Kaizen.\n\nDate: {cbd_data.date_of_encounter}"
    else:
        msg = "Filing failed. Check Kaizen manually or try again."

    if assessor_warning:
        msg += f"\n\n{assessor_warning}"

    await ack.edit_text(msg)
    context.user_data.clear()
    return ConversationHandler.END


async def handle_approval_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 'Edit' button - show field selection."""
    query = update.callback_query
    await query.answer()

    # Disarm approval buttons immediately — prevents double-tap
    await query.edit_message_reply_markup(reply_markup=None)

    if not context.user_data.get("draft_data"):
        await query.message.reply_text(
            "This draft has expired (bot was restarted). Send /reset and file a new case.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Reset", callback_data="ACTION|reset")]])
        )
        return ConversationHandler.END

    await query.message.reply_text(
        "Which field do you want to edit?",
        reply_markup=_build_edit_field_keyboard()
    )
    return AWAIT_EDIT_FIELD


async def handle_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle field selection for editing."""
    query = update.callback_query
    await query.answer()

    field = query.data.split("|")[1]
    context.user_data["edit_field"] = field

    field_labels = {
        "date_of_encounter": "date (YYYY-MM-DD)",
        "clinical_setting": "clinical setting",
        "patient_presentation": "presentation",
        "clinical_reasoning": "case discussion",
        "reflection": "reflection",
        "curriculum_links": "SLOs (comma-separated, e.g. SLO1, SLO3)",
    }
    label = field_labels.get(field, field)
    await query.message.reply_text(f"Enter the new {label}:")
    return AWAIT_EDIT_VALUE


async def handle_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle new value for edited field."""
    new_value = update.message.text.strip()
    field = context.user_data.get("edit_field")
    cbd_data = context.user_data.get("draft_data")

    if not field or not cbd_data:
        context.user_data.clear()
        await update.message.reply_text("Edit failed. Start over.")
        return ConversationHandler.END

    # Update the field
    if field == "curriculum_links":
        # Parse comma-separated SLOs
        slos = [s.strip().upper() for s in new_value.split(",") if s.strip()]
        cbd_data.curriculum_links = slos
    else:
        setattr(cbd_data, field, new_value)

    context.user_data["draft_data"] = cbd_data
    context.user_data.pop("edit_field", None)

    # Show updated preview
    preview = _format_draft_preview(cbd_data)
    await update.message.reply_text(preview)

    # Re-send approval buttons
    await update.message.reply_text(
        "Review the updated draft. File it, edit more, or cancel.",
        reply_markup=_build_approval_keyboard()
    )
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

    application = (
        Application.builder()
        .token(token)
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mid_conversation_text),
            ],
            AWAIT_APPROVAL: [
                CallbackQueryHandler(handle_approval_approve, pattern=r"^APPROVE\|"),
                CallbackQueryHandler(handle_approval_edit, pattern=r"^EDIT\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mid_conversation_text),
            ],
            AWAIT_EDIT_FIELD: [
                CallbackQueryHandler(handle_edit_field, pattern=r"^FIELD\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mid_conversation_text),
            ],
            AWAIT_EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_value),
            ],
        },
        fallbacks=[
            CommandHandler("reset", reset),
            CommandHandler("cancel", setup_cancel),
            CallbackQueryHandler(handle_callback),  # Handle all callbacks in fallback
        ],
        per_message=False,
        allow_reentry=True,
    )

    # Setup conversation handler
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            AWAIT_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_username)],
            AWAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_password)],
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
