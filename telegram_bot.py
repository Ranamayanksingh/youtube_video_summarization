"""
Telegram bot for YouTube video summarization.

Flow:
  1. User sends a YouTube video URL
  2. Bot asks for a summarization prompt (or /default to use the standard one)
  3. Bot runs: download → transcribe → summarize
  4. Bot returns the summary text

Setup:
  - Create a bot via @BotFather on Telegram, copy the token into .env
  - Find your Telegram user ID via @userinfobot and add to .env
  - Run: python telegram_bot.py
"""
import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from script import download_youtube_audio_as_wav
from transcribe import transcribe_file, DEFAULT_WHISPER_MODEL
from summarize import summarize_file, DEFAULT_SUMMARIES_DIR, DEFAULT_MODEL as DEFAULT_LLM_MODEL, PROMPT_TEMPLATE

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DOWNLOADS_DIR = "downloads"
SUMMARIES_DIR = DEFAULT_SUMMARIES_DIR

# Conversation states
WAITING_FOR_URL, WAITING_FOR_PROMPT = range(2)

# Allowed user IDs (empty = allow all)
_raw_ids = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "").strip()
ALLOWED_USER_IDS: set[int] = (
    {int(uid.strip()) for uid in _raw_ids.split(",") if uid.strip()}
    if _raw_ids else set()
)


def _is_allowed(user_id: int) -> bool:
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


def _is_youtube_url(text: str) -> bool:
    text = text.strip()
    return "youtube.com/watch" in text or "youtu.be/" in text


def _split_message(text: str, limit: int = 4096) -> list[str]:
    """Split long text into Telegram-safe chunks."""
    return [text[i : i + limit] for i in range(0, len(text), limit)]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_allowed(update.effective_user.id):
        await update.message.reply_text("Unauthorized.")
        return ConversationHandler.END

    await update.message.reply_text(
        "Send me a YouTube video URL and I'll summarize it for you."
    )
    return WAITING_FOR_URL


async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_allowed(update.effective_user.id):
        await update.message.reply_text("Unauthorized.")
        return ConversationHandler.END

    url = update.message.text.strip()
    if not _is_youtube_url(url):
        await update.message.reply_text(
            "That doesn't look like a YouTube URL. Please send a valid YouTube video link."
        )
        return WAITING_FOR_URL

    context.user_data["url"] = url
    await update.message.reply_text(
        "Got it!\n\n"
        "Now send me your summarization prompt, or send /default to use the standard prompt.\n\n"
        "Your prompt should include `{text}` as a placeholder for the transcript. Example:\n"
        "`Summarize this video transcript in 5 bullet points:\n{text}`"
    )
    return WAITING_FOR_PROMPT


async def receive_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_allowed(update.effective_user.id):
        await update.message.reply_text("Unauthorized.")
        return ConversationHandler.END

    user_text = update.message.text.strip()
    url = context.user_data.get("url")

    if not url:
        await update.message.reply_text("Something went wrong. Please start over with /start.")
        return ConversationHandler.END

    # Use default prompt if user sends /default or doesn't include {text}
    if user_text == "/default" or "{text}" not in user_text:
        if user_text != "/default":
            # User sent a plain prompt without {text} placeholder — append it
            custom_prompt = user_text + "\n\n{text}"
        else:
            custom_prompt = None  # use PROMPT_TEMPLATE
    else:
        custom_prompt = user_text

    await update.message.reply_text("Starting pipeline... this may take a few minutes.")

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    os.makedirs(SUMMARIES_DIR, exist_ok=True)

    try:
        # Step 1: Download
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        await update.message.reply_text("Step 1/3 — Downloading audio...")
        wav_path = await asyncio.to_thread(
            download_youtube_audio_as_wav, url, DOWNLOADS_DIR
        )
        if not wav_path:
            await update.message.reply_text("Download failed. Please check the URL and try again.")
            return ConversationHandler.END

        # Step 2: Transcribe
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        await update.message.reply_text("Step 2/3 — Transcribing audio to English...")
        txt_path = await asyncio.to_thread(
            transcribe_file, wav_path, DEFAULT_WHISPER_MODEL
        )
        if not txt_path:
            await update.message.reply_text("Transcription failed.")
            return ConversationHandler.END

        # Step 3: Summarize
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        await update.message.reply_text("Step 3/3 — Summarizing transcript...")
        summary_path = await asyncio.to_thread(
            summarize_file,
            txt_path,
            SUMMARIES_DIR,
            DEFAULT_LLM_MODEL,
            True,  # overwrite
            custom_prompt,
        )
        if not summary_path:
            await update.message.reply_text("Summarization failed.")
            return ConversationHandler.END

        # Send summary
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = f.read().strip()

        await update.message.reply_text("Done! Here's your summary:")
        for chunk in _split_message(summary):
            await update.message.reply_text(chunk)

    except Exception as e:
        logger.exception("Pipeline error")
        await update.message.reply_text(f"Something went wrong: {e}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("An unexpected error occurred.")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token or token == "your_bot_token_here":
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN not set. Add it to .env:\n"
            "  TELEGRAM_BOT_TOKEN=<your token from @BotFather>"
        )

    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url),
        ],
        states={
            WAITING_FOR_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url),
            ],
            WAITING_FOR_PROMPT: [
                CommandHandler("default", receive_prompt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_error_handler(error_handler)

    logger.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
