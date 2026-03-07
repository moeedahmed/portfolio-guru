"""
Portfolio Guru Telegram Bot
Run: python bot.py  (or as part of main FastAPI app via lifespan)
"""
import asyncio
import logging
import os
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler,
)
from credentials import init_db, store_credentials, get_credentials, has_credentials
from extractor import extract_cbd_data
from filer import file_cbd_to_kaizen

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ConversationHandler states
AWAIT_USERNAME, AWAIT_PASSWORD = range(2)

WELCOME_MSG = """Welcome to Portfolio Guru.

Run /setup to connect your Kaizen account.
Then send me a case description — I'll file it as a CBD draft.

/setup — connect Kaizen
/status — check connection"""

SETUP_START_MSG = "What's your Kaizen username?"

SETUP_PASSWORD_MSG = "What's your Kaizen password?"

SETUP_DONE_MSG = "Done. Send me a case description and I'll file it to Kaizen."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_MSG)


async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(SETUP_START_MSG)
    return AWAIT_USERNAME


async def setup_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if "@" not in text or "." not in text:
        await update.message.reply_text("That doesn't look like an email. What's your Kaizen username?")
        return AWAIT_USERNAME
    context.user_data["setup_username"] = text
    await update.message.reply_text(SETUP_PASSWORD_MSG)
    return AWAIT_PASSWORD


async def setup_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = context.user_data.get("setup_username", "")
    password = update.message.text.strip()
    user_id = update.effective_user.id
    # Delete the password message immediately for security
    try:
        await update.message.delete()
    except Exception:
        pass

    # Verify credentials before saving
    checking_msg = await update.effective_chat.send_message("Checking your credentials...")

    from credentials import verify_kaizen_credentials
    valid = await asyncio.to_thread(verify_kaizen_credentials, username, password)

    if not valid:
        await checking_msg.edit_text(
            "Those credentials didn't work. Check your Kaizen username and password and run /setup again."
        )
        context.user_data.clear()
        return ConversationHandler.END

    # Save only if valid
    store_credentials(user_id, username, password)
    context.user_data.clear()
    await checking_msg.edit_text("Connected. Send me a case description and I'll file it to Kaizen.")
    return ConversationHandler.END


async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Setup cancelled.")
    return ConversationHandler.END


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if has_credentials(user_id):
        await update.message.reply_text("Credentials are stored. Ready to file cases.")
    else:
        await update.message.reply_text("No credentials stored. Run /setup first.")


async def handle_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    # Check credentials
    creds = get_credentials(user_id)
    if not creds:
        await update.message.reply_text(
            "No credentials stored. Run /setup first."
        )
        return

    username, password = creds
    case_text = update.message.text.strip()

    # Acknowledge immediately
    ack = await update.message.reply_text("Filing your case to Kaizen...")

    try:
        # Step 1: Extract
        cbd_data = extract_cbd_data(case_text)
    except Exception as e:
        await ack.edit_text(
            f"Could not extract case data from your description.\n\n"
            f"Try rephrasing with more detail.\n\nError: {str(e)[:200]}"
        )
        return

    try:
        # Step 2: File
        status_result, action_log, screenshot_b64, assessor_warning = await file_cbd_to_kaizen(
            cbd_data, username, password
        )
    except Exception as e:
        await ack.edit_text(
            f"Filing failed: {str(e)[:300]}\n\n"
            f"Your case description has been received. Reply /retry to try again."
        )
        # Store last CBD data for retry
        context.user_data["last_cbd"] = cbd_data
        return

    # Build reply
    if status_result == "success":
        msg = (
            f"CBD draft saved to Kaizen!\n\n"
            f"Date: {cbd_data.date_of_encounter}\n"
            f"Case: {cbd_data.patient_presentation[:80]}...\n"
            f"SLOs: {', '.join(cbd_data.curriculum_links) or 'None selected'}\n\n"
            f"Review your draft in Kaizen before submitting."
        )
    elif status_result == "partial":
        msg = (
            f"Draft saved but some fields may be incomplete. "
            f"Please review in Kaizen before submitting.\n\n"
            f"Date: {cbd_data.date_of_encounter}"
        )
    else:
        msg = (
            f"Filing failed at the save step. "
            f"Screenshot attached for debugging.\n\n"
            f"Try again or check Kaizen manually."
        )

    if assessor_warning:
        msg += f"\n\n{assessor_warning}"

    await ack.edit_text(msg)


def build_application() -> Application:
    """Build and return the Telegram bot Application with all handlers registered."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN env var not set")

    application = Application.builder().token(token).build()

    # /setup conversation
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            AWAIT_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_username)],
            AWAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_password)],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(setup_conv)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_case))

    return application
