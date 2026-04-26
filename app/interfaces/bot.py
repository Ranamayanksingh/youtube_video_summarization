"""
Telegram bot for YouTube video summarization.

Flow:
  1. User sends /start
  2. Bot asks for the access key (BOT_ACCESS_KEY in .env) — skipped if key not set
  3. User picks a language (English / Hindi) via inline buttons
  4. User sends a YouTube video URL
  5. Bot asks for a summarization prompt (or /default for standard prompt)
  6. Bot runs: download → transcribe → summarize (in chosen language)
  7. Bot returns structured summary with inline action buttons
  8. User can ask follow-up questions (answered in chosen language)
  9. /new begins a fresh video  |  /language switches language  |  /history shows past videos

Setup:
  - Create a bot via @BotFather, copy token into .env as TELEGRAM_BOT_TOKEN
  - Set BOT_ACCESS_KEY in .env — share with people you want to allow (leave empty for open access)
  - Run: python telegram_bot.py
"""
import asyncio
import datetime
import logging
import os

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    BotCommand,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from app.db import (
    add_video_history, get_user_history, delete_user_history, ensure_history_table,
    get_collections, get_collection_by_name, ensure_collections_tables,
)
from app.pipeline.downloader import download_youtube_audio_as_wav
from app.pipeline.transcriber import transcribe_file, DEFAULT_WHISPER_MODEL
from app.pipeline.summarizer import summarize_file, DEFAULT_SUMMARIES_DIR, DEFAULT_MODEL as DEFAULT_LLM_MODEL, _llm_chat
from app.pipeline.extractor import extract_and_store
from app.knowledge.builder import build_knowledge_file
from app.knowledge.qa import answer_question as collection_answer_question
from app.knowledge.quiz import send_quiz

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOWNLOADS_DIR = os.path.join(_PROJECT_ROOT, "data", "downloads")
SUMMARIES_DIR = DEFAULT_SUMMARIES_DIR

# Conversation states
WAITING_FOR_KEY, WAITING_FOR_LANG, WAITING_FOR_URL, WAITING_FOR_PROMPT, FOLLOW_UP = range(5)
SELECTING_VIDEO = 5
SELECTING_COLLECTION = 6
COLLECTION_ASK = 7

# Callback data constants
CB_LANG_EN     = "lang:english"
CB_LANG_HI     = "lang:hindi"
CB_NEW_VIDEO     = "action:new_video"
CB_SWITCH_LANG   = "action:switch_lang"
CB_HISTORY       = "action:history"
CB_CLEAR_HISTORY = "action:clear_history"
CB_CLEAR_CONFIRM = "action:clear_confirm"
CB_CLEAR_CANCEL  = "action:clear_cancel"
CB_SKIP_COLLECTION = "action:skip_collection"
CB_COLLECTION_PREFIX = "collection:"  # + collection name

# Global semaphore — Whisper + Ollama are single-instance
PIPELINE_SEMAPHORE = asyncio.Semaphore(1)
PIPELINE_QUEUE_COUNT = 0

# Per-user pipeline tasks: user_id -> asyncio.Task
PIPELINE_TASKS: dict[int, asyncio.Task] = {}

# Per-user batch state for status reporting
# user_id -> {"total": N, "done": N, "success": N, "current": str, "results": list}
BATCH_STATE: dict[int, dict] = {}

# Localized UI messages
MESSAGES = {
    "english": {
        "welcome_open":     "👋 Welcome to *YT Summarizer*!\n\nI can download, transcribe, and summarize any YouTube video — and answer your questions about it.",
        "ask_key":          "🔐 Please send the *access key* to get started.",
        "bad_key":          "❌ Incorrect key. Please try again or contact the bot owner.",
        "ask_lang":         "🌐 Choose your preferred language:",
        "lang_set_en":      "🇬🇧 English selected! Send me a YouTube video URL.",
        "lang_set_hi":      "🇮🇳 हिंदी चुनी गई! अब एक YouTube वीडियो URL भेजें।",
        "ask_url":          "🎬 Send me a YouTube video URL — or paste *multiple links* (one per line) to process them all in the background at once.",
        "bad_url":          "⚠️ That doesn't look like a YouTube URL. Please send a valid link.",
        "ask_prompt":       (
            "📝 Send your summarization prompt, or use /default for the standard structured summary.\n\n"
            "Your prompt must include `{text}` as a placeholder. Example:\n"
            "`Summarize this in 5 bullet points:\n{text}`"
        ),
        "queued":           "⏳ Pipeline is busy. You're #{pos} in the queue — I'll start as soon as the current one finishes.",
        "starting":         "🚀 Starting pipeline… this may take a few minutes.",
        "step1":            "📥 Step 1/3 — Downloading audio…",
        "step2":            "📝 Step 2/3 — Transcribing audio to text…",
        "step3":            "🧠 Step 3/3 — Generating structured summary…",
        "dl_fail":          "❌ Download failed. Please check the URL and try again.",
        "tx_fail":          "❌ Transcription failed.",
        "sum_fail":         "❌ Summarization failed.",
        "done":             "✅ Done! Here's your summary:",
        "post_summary":     "💬 Ask me anything about this video, or use the buttons below.",
        "thinking":         "🤔 Thinking… I'll respond once done.",
        "no_transcript":    "⚠️ No transcript in memory. Use /new to start with a video.",
        "cancelled":        "Cancelled.",
        "error":            "⚠️ Something went wrong: {err}",
        "lang_switched":    "🌐 Language updated! Replies will now be in English.",
        "new_video":        "🎬 Send me a YouTube URL — or paste *multiple links* (one per line) to process them all in the background.",
        "history_empty":    "📭 You haven't summarized any videos yet. Send me a YouTube URL to get started!",
        "history_list":     "📚 *Your recent videos* — send the number to revisit one:\n\n{items}",
        "history_loaded":   "✅ Loaded: *{title}*\n\n💬 Ask me anything about this video, or use the buttons below.",
        "history_invalid":  "⚠️ Please send a number from the list, or /cancel to go back.",
        "history_missing":  "⚠️ That video's files are no longer on disk. Try another number.",
        "history_bad_num":  "⚠️ Invalid number. Please pick from 1 to {max}.",
        "clear_confirm":    "🗑 Are you sure you want to delete all your videos and their files? This cannot be undone.",
        "clear_done":       "✅ All your videos have been deleted ({count} removed).",
        "clear_cancelled":  "↩️ Cancelled. Your videos are safe.",
        "ask_collection":   "📚 Add this video to a collection? Choose one or skip:",
        "collection_added": "✅ Added to *{name}*\n📊 Collection now has {items} items ({formulas} formulas, {questions} questions, {tricks} tricks)",
        "collection_skip":  "Skipped. Summary saved without collection.",
        "collection_empty": "📭 No collections found. Create one at the web UI first.",
        "quiz_sent":        "🎯 Here's your quiz from *{name}*:",
        "quiz_no_questions": "📭 No questions in *{name}* yet. Add more videos first!",
        "collection_ask_question": "Ask your question about the *{name}* collection:",
        "collection_answer_done": "From your *{name}* knowledge base:",
        "no_collections_for_quiz": "You have no collections yet. Add videos to a collection first.",
        "batch_received":   "📋 Got *{count}* video link{s}. Processing them in the background — I'll ping you when all are done.\n\nYou can keep chatting or use /status to check progress.",
        "batch_ask_collection": "📚 Which collection should these go into? (or skip)",
        "batch_status_idle":    "✅ No batch currently running.",
        "batch_status_running": "⚙️ Batch in progress: *{done}/{total}* done\n\nCurrent: _{current}_",
        "batch_done":       "✅ *Batch complete!* Processed *{success}/{total}* videos successfully.\n\n{results}\n\nYou can now:\n• /ask — query your knowledge base\n• /quiz — get practice questions\n• /history — see processed videos",
        "batch_done_item_ok":  "✅ {title}",
        "batch_done_item_fail": "❌ {url} — failed",
        "batch_cancelled":  "🛑 Batch cancelled. {done} of {total} videos had already been processed.",
    },
    "hindi": {
        "welcome_open":     "👋 *YT Summarizer* में आपका स्वागत है!\n\nमैं किसी भी YouTube वीडियो को डाउनलोड, ट्रांसक्राइब और सारांशित कर सकता हूं — और आपके सवालों का जवाब दे सकता हूं।",
        "ask_key":          "🔐 शुरू करने के लिए कृपया *एक्सेस की* भेजें।",
        "bad_key":          "❌ गलत की। कृपया दोबारा कोशिश करें या बॉट के मालिक से संपर्क करें।",
        "ask_lang":         "🌐 अपनी पसंदीदा भाषा चुनें:",
        "lang_set_en":      "🇬🇧 English selected! Send me a YouTube video URL.",
        "lang_set_hi":      "🇮🇳 हिंदी चुनी गई! अब एक YouTube वीडियो URL भेजें।",
        "ask_url":          "🎬 एक YouTube वीडियो URL भेजें — या *एक साथ कई लिंक* भेजें (हर लिंक अलग लाइन पर) और सभी बैकग्राउंड में प्रोसेस होंगे।",
        "bad_url":          "⚠️ यह YouTube URL नहीं लगता। कृपया एक सही लिंक भेजें।",
        "ask_prompt":       (
            "📝 अपना सारांश प्रॉम्प्ट भेजें, या मानक संरचित सारांश के लिए /default उपयोग करें।\n\n"
            "प्रॉम्प्ट में `{text}` ज़रूर शामिल करें। उदाहरण:\n"
            "`इसे 5 बुलेट पॉइंट में सारांशित करें:\n{text}`"
        ),
        "queued":           "⏳ पाइपलाइन व्यस्त है। आप #{pos} नंबर पर हैं — मैं जल्द ही शुरू करूंगा।",
        "starting":         "🚀 पाइपलाइन शुरू हो रही है… इसमें कुछ मिनट लग सकते हैं।",
        "step1":            "📥 चरण 1/3 — ऑडियो डाउनलोड हो रहा है…",
        "step2":            "📝 चरण 2/3 — ऑडियो को टेक्स्ट में बदला जा रहा है…",
        "step3":            "🧠 चरण 3/3 — संरचित सारांश तैयार हो रहा है…",
        "dl_fail":          "❌ डाउनलोड विफल। कृपया URL जांचें और दोबारा कोशिश करें।",
        "tx_fail":          "❌ ट्रांसक्रिप्शन विफल।",
        "sum_fail":         "❌ सारांश विफल।",
        "done":             "✅ हो गया! यहाँ आपका सारांश है:",
        "post_summary":     "💬 इस वीडियो के बारे में कुछ भी पूछें, या नीचे दिए बटन का उपयोग करें।",
        "thinking":         "🤔 सोच रहा हूं… उत्तर तैयार होते ही भेजूंगा।",
        "no_transcript":    "⚠️ मेमोरी में कोई ट्रांसक्रिप्ट नहीं है। /new से शुरू करें।",
        "cancelled":        "रद्द किया गया।",
        "error":            "⚠️ कुछ गड़बड़ हुई: {err}",
        "lang_switched":    "🌐 भाषा बदली गई! अब जवाब हिंदी में मिलेंगे।",
        "new_video":        "🎬 नई शुरुआत। एक URL भेजें — या *कई लिंक* एक साथ (हर लिंक अलग लाइन पर) बैकग्राउंड में प्रोसेस होंगे।",
        "history_empty":    "📭 आपने अभी तक कोई वीडियो सारांशित नहीं किया है। शुरू करने के लिए एक YouTube URL भेजें!",
        "history_list":     "📚 *आपके हालिया वीडियो* — किसी को फिर से देखने के लिए नंबर भेजें:\n\n{items}",
        "history_loaded":   "✅ लोड हुआ: *{title}*\n\n💬 इस वीडियो के बारे में कुछ भी पूछें, या नीचे दिए बटन का उपयोग करें।",
        "history_invalid":  "⚠️ सूची से एक नंबर भेजें, या वापस जाने के लिए /cancel करें।",
        "history_missing":  "⚠️ उस वीडियो की फ़ाइलें अब डिस्क पर नहीं हैं। कोई अन्य नंबर चुनें।",
        "history_bad_num":  "⚠️ अमान्य नंबर। 1 से {max} के बीच चुनें।",
        "clear_confirm":    "🗑 क्या आप वाकई अपने सभी वीडियो और उनकी फ़ाइलें हटाना चाहते हैं? यह क्रिया पूर्ववत नहीं की जा सकती।",
        "clear_done":       "✅ आपके सभी वीडियो हटा दिए गए हैं ({count} हटाए गए)।",
        "clear_cancelled":  "↩️ रद्द किया गया। आपके वीडियो सुरक्षित हैं।",
        "ask_collection":   "📚 इस वीडियो को किस संग्रह में जोड़ें? चुनें या छोड़ें:",
        "collection_added": "✅ *{name}* में जोड़ा गया\n📊 संग्रह में अब {items} आइटम हैं ({formulas} सूत्र, {questions} प्रश्न, {tricks} ट्रिक्स)",
        "collection_skip":  "छोड़ दिया। सारांश बिना संग्रह के सहेजा गया।",
        "collection_empty": "📭 कोई संग्रह नहीं मिला। पहले वेब UI पर एक बनाएं।",
        "quiz_sent":        "🎯 *{name}* से आपका क्विज़:",
        "quiz_no_questions": "📭 *{name}* में अभी कोई प्रश्न नहीं है। पहले और वीडियो जोड़ें!",
        "collection_ask_question": "*{name}* संग्रह के बारे में अपना प्रश्न पूछें:",
        "collection_answer_done": "आपके *{name}* ज्ञान आधार से:",
        "no_collections_for_quiz": "अभी तक कोई संग्रह नहीं है। पहले वीडियो को किसी संग्रह में जोड़ें।",
        "batch_received":   "📋 *{count}* वीडियो लिंक मिले{s}। बैकग्राउंड में प्रोसेस हो रहे हैं — पूरा होने पर सूचित करूंगा।\n\nआप /status से प्रगति देख सकते हैं।",
        "batch_ask_collection": "📚 इन्हें किस संग्रह में जोड़ें? (या छोड़ें)",
        "batch_status_idle":    "✅ अभी कोई बैच नहीं चल रहा।",
        "batch_status_running": "⚙️ बैच चल रहा है: *{done}/{total}* पूरे\n\nअभी: _{current}_",
        "batch_done":       "✅ *बैच पूरा!* *{success}/{total}* वीडियो सफलतापूर्वक प्रोसेस हुए।\n\n{results}\n\nअब आप:\n• /ask — अपने ज्ञान आधार से पूछें\n• /quiz — अभ्यास प्रश्न पाएं\n• /history — प्रोसेस किए गए वीडियो देखें",
        "batch_done_item_ok":  "✅ {title}",
        "batch_done_item_fail": "❌ {url} — विफल",
        "batch_cancelled":  "🛑 बैच रद्द। {done} / {total} वीडियो पहले ही प्रोसेस हो चुके थे।",
    },
}

HELP_TEXT = (
    "🎬 *YT Summarizer — What I can do*\n\n"

    "📥 *Summarize a video*\n"
    "Send any YouTube link\\. I'll download, transcribe and give you a structured summary with topic overview, detailed sections, key takeaways and conclusion\\.\n\n"

    "📋 *Batch processing — multiple videos at once*\n"
    "Paste several YouTube links in one message \\(one per line\\)\\. All of them will be processed in the background while you do other things\\. You'll get a single notification when everything is done\\.\n\n"
    "_Example:_\n"
    "`https://youtu\\.be/abc123`\n"
    "`https://youtu\\.be/def456`\n"
    "`https://youtu\\.be/ghi789`\n\n"
    "Use /status to check progress while a batch is running\\.\n\n"

    "📚 *Collections & knowledge base*\n"
    "After processing, assign videos to a collection \\(e\\.g\\. SSC CGL Maths, AI Learning\\)\\. The bot extracts formulas, questions, and tricks from each video and builds a growing knowledge file\\.\n\n"

    "🎯 *Quiz yourself*\n"
    "Use /quiz to get random practice questions from any of your collections\\.\n\n"

    "🧠 *Ask your knowledge base*\n"
    "Use /ask to ask a question that is answered from everything in a collection — not just one video\\.\n\n"

    "❓ *Ask about a specific video*\n"
    "After a summary, type any question and I'll answer based on that video's transcript\\.\n\n"

    "📂 *Your video history*\n"
    "Use /history to see past videos and reload any of them\\.\n\n"

    "🌐 *Change language*\n"
    "Use /language to switch between English 🇬🇧 and Hindi 🇮🇳\\.\n\n"

    "/status — Check batch progress\n"
    "/new — Start fresh with a new video\n"
    "/cancel — Cancel current operation"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_access_key() -> str:
    return os.environ.get("BOT_ACCESS_KEY", "").strip()


def _lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang", "english")


def _msg(context: ContextTypes.DEFAULT_TYPE, key: str, **kwargs) -> str:
    text = MESSAGES[_lang(context)][key]
    return text.format(**kwargs) if kwargs else text


def _is_youtube_url(text: str) -> bool:
    text = text.strip()
    return (
        "youtube.com/watch" in text
        or "youtu.be/" in text
        or "youtube.com/live/" in text
        or "youtube.com/shorts/" in text
    )


def _extract_youtube_urls(text: str) -> list[str]:
    """Extract all YouTube URLs from a block of text (newline or space separated)."""
    import re
    # Match full URLs including query strings — covers /watch, /live, /shorts, youtu.be
    pattern = r'https?://(?:www\.)?(?:youtube\.com/(?:watch|live|shorts)\S+|youtu\.be/\S+)'
    found = re.findall(pattern, text)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for url in found:
        url = url.rstrip(".,;)>\"'")  # strip trailing punctuation
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


async def _transcribe_with_keepalive(
    wav_path: str,
    chat_id: int,
    bot,
    lang: str = "english",
) -> str | None:
    """
    Run transcribe_file in a thread while sending periodic keepalive messages
    to the user so they know the bot is still working.

    - Sends a TYPING action every 4 seconds so Telegram shows the bot as active.
    - Sends a text ping every 2 minutes with elapsed time.
    - Enforces a hard 45-minute timeout via asyncio.wait_for; on timeout the
      thread is abandoned (mlx-whisper cannot be cancelled mid-run) and None
      is returned so the pipeline reports failure gracefully.
    """
    from app.pipeline.transcriber import TRANSCRIBE_TIMEOUT_SECS

    keepalive_interval_secs = 4
    ping_interval_secs = 120  # text message every 2 minutes
    elapsed = 0

    ping_texts = {
        "english": "⏳ Still transcribing… ({min} min elapsed)",
        "hindi":   "⏳ अभी भी ट्रांसक्राइब हो रहा है… ({min} मिनट हो गए)",
    }
    ping_tmpl = ping_texts.get(lang, ping_texts["english"])

    transcribe_task = asyncio.create_task(
        asyncio.to_thread(transcribe_file, wav_path, DEFAULT_WHISPER_MODEL, False, True)
    )

    try:
        while not transcribe_task.done():
            if elapsed >= TRANSCRIBE_TIMEOUT_SECS:
                transcribe_task.cancel()
                logger.error("Transcription timed out after %d min: %s", elapsed // 60, wav_path)
                return None

            try:
                await asyncio.wait_for(asyncio.shield(transcribe_task), timeout=keepalive_interval_secs)
            except asyncio.TimeoutError:
                elapsed += keepalive_interval_secs
                try:
                    await bot.send_chat_action(chat_id, ChatAction.TYPING)
                except Exception:
                    pass
                if elapsed > 0 and elapsed % ping_interval_secs == 0:
                    try:
                        await bot.send_message(
                            chat_id,
                            ping_tmpl.format(min=elapsed // 60),
                        )
                    except Exception:
                        pass
            except asyncio.CancelledError:
                return None

        return transcribe_task.result()
    except Exception as e:
        logger.error("Transcription task raised: %s", e)
        return None


def _split_message(text: str, limit: int = 4096) -> list[str]:
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _escape_mdv2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r'\_*[]()~`>#+-=|{}.!'
    return "".join(f"\\{c}" if c in special else c for c in text)


def _format_relative_time(dt: datetime.datetime) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        m = seconds // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    elif seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    elif seconds < 7 * 86400:
        d = seconds // 86400
        return f"{d} day{'s' if d != 1 else ''} ago"
    else:
        return dt.strftime("%b %d, %Y")


def _lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🇬🇧 English", callback_data=CB_LANG_EN),
        InlineKeyboardButton("🇮🇳 Hindi", callback_data=CB_LANG_HI),
    ]])


def _history_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑 Clear All", callback_data=CB_CLEAR_HISTORY),
    ]])


def _clear_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, delete all", callback_data=CB_CLEAR_CONFIRM),
        InlineKeyboardButton("❌ Cancel", callback_data=CB_CLEAR_CANCEL),
    ]])


def _post_summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 New video", callback_data=CB_NEW_VIDEO),
            InlineKeyboardButton("🌐 Switch language", callback_data=CB_SWITCH_LANG),
        ],
        [
            InlineKeyboardButton("📚 My Videos", callback_data=CB_HISTORY),
        ],
    ])


def _collection_keyboard(collections: list[dict]) -> InlineKeyboardMarkup:
    """Build inline keyboard for collection selection after summary."""
    buttons = []
    for col in collections[:8]:  # max 8 collections to avoid huge keyboard
        goal_emoji = {"exam_prep": "📝", "project_build": "🚀", "quiz_practice": "🎯"}.get(
            col.get("goal_type", ""), "📚"
        )
        buttons.append([InlineKeyboardButton(
            f"{goal_emoji} {col['name']}",
            callback_data=f"{CB_COLLECTION_PREFIX}{col['name']}"
        )])
    buttons.append([InlineKeyboardButton("⏭ Skip", callback_data=CB_SKIP_COLLECTION)])
    return InlineKeyboardMarkup(buttons)


def _collections_list_keyboard(collections: list[dict]) -> InlineKeyboardMarkup:
    """Build inline keyboard for /quiz or /ask collection selection."""
    buttons = []
    for col in collections[:8]:
        goal_emoji = {"exam_prep": "📝", "project_build": "🚀", "quiz_practice": "🎯"}.get(
            col.get("goal_type", ""), "📚"
        )
        buttons.append([InlineKeyboardButton(
            f"{goal_emoji} {col['name']} ({col.get('item_count', 0)} items)",
            callback_data=f"{CB_COLLECTION_PREFIX}{col['name']}"
        )])
    return InlineKeyboardMarkup(buttons)


def _main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["🎬 New Video", "📚 My Videos", "❓ Help"]],
        resize_keyboard=True,
        input_field_placeholder="Send a YouTube URL or ask a question…",
    )


def _answer_question(transcript: str, question: str, model: str, lang: str) -> str:
    lang_instruction = "Answer in Hindi." if lang == "hindi" else "Answer in English."
    prompt = (
        "You are a helpful assistant. The user has watched a YouTube video and you have its transcript below.\n"
        "Answer the user's question based solely on the transcript. If the answer is not in the transcript, say so.\n"
        f"{lang_instruction}\n\n"
        f"Transcript:\n{transcript}\n\n"
        f"Question: {question}"
    )
    return _llm_chat(model, prompt)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Preserve lang across restarts so the lang picker doesn't re-appear
    prev_lang = context.user_data.get("lang")
    prev_auth = context.user_data.get("authenticated")
    context.user_data.clear()
    if prev_lang:
        context.user_data["lang"] = prev_lang

    access_key = _get_access_key()

    # If already authenticated (or no key required) and lang known, go straight to URL
    if prev_lang and (prev_auth or not access_key):
        await update.message.reply_text(_msg(context, "ask_url"), reply_markup=_main_reply_keyboard())
        return WAITING_FOR_URL

    welcome = _msg(context, "welcome_open")
    if not access_key:
        await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text(
            _msg(context, "ask_lang"), reply_markup=_lang_keyboard()
        )
        return WAITING_FOR_LANG

    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text(_msg(context, "ask_key"), parse_mode=ParseMode.MARKDOWN)
    return WAITING_FOR_KEY


async def verify_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    provided = update.message.text.strip()
    if provided == _get_access_key():
        context.user_data["authenticated"] = True
        await update.message.reply_text(
            _msg(context, "ask_lang"), reply_markup=_lang_keyboard()
        )
        return WAITING_FOR_LANG
    else:
        await update.message.reply_text(_msg(context, "bad_key"))
        return WAITING_FOR_KEY


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang = "hindi" if query.data == CB_LANG_HI else "english"
    context.user_data["lang"] = lang
    confirm_key = "lang_set_hi" if lang == "hindi" else "lang_set_en"
    await query.edit_message_text(MESSAGES[lang][confirm_key])
    await query.message.reply_text(
        _msg(context, "ask_url"), reply_markup=_main_reply_keyboard()
    )
    return WAITING_FOR_URL


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        _msg(context, "ask_lang"), reply_markup=_lang_keyboard()
    )
    return WAITING_FOR_LANG


async def switch_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        _msg(context, "ask_lang"), reply_markup=_lang_keyboard()
    )
    return WAITING_FOR_LANG


async def new_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        reply_fn = update.callback_query.message.reply_text
    else:
        reply_fn = update.message.reply_text

    if not context.user_data.get("authenticated") and _get_access_key():
        await reply_fn(_msg(context, "ask_key"), parse_mode=ParseMode.MARKDOWN)
        return WAITING_FOR_KEY

    context.user_data.pop("url", None)
    context.user_data.pop("transcript", None)

    if not context.user_data.get("lang"):
        await reply_fn(_msg(context, "ask_lang"), reply_markup=_lang_keyboard())
        return WAITING_FOR_LANG

    await reply_fn(_msg(context, "new_video"), reply_markup=_main_reply_keyboard())
    return WAITING_FOR_URL


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2)
    return FOLLOW_UP if context.user_data.get("transcript") else WAITING_FOR_URL


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show the user's video history as a numbered list."""
    if update.callback_query:
        await update.callback_query.answer()
        reply_fn = update.callback_query.message.reply_text
        user_id = update.callback_query.from_user.id
    else:
        reply_fn = update.message.reply_text
        user_id = update.effective_user.id

    try:
        entries = await asyncio.to_thread(get_user_history, user_id, 10)
    except Exception as e:
        logger.exception("Failed to fetch history for user %s", user_id)
        await reply_fn("⚠️ Database unavailable — cannot load history. Please make sure Postgres is running.")
        return FOLLOW_UP if context.user_data.get("transcript") else WAITING_FOR_URL

    # Filter out entries whose files have been deleted from disk
    valid_entries = [
        e for e in entries
        if os.path.exists(e["transcript_path"]) and os.path.exists(e["summary_path"])
    ]

    if not valid_entries:
        await reply_fn(_msg(context, "history_empty"))
        return FOLLOW_UP if context.user_data.get("transcript") else WAITING_FOR_URL

    lines = []
    for i, entry in enumerate(valid_entries, start=1):
        rel_time = _format_relative_time(entry["created_at"])
        title = _escape_mdv2(entry["title"])
        time_escaped = _escape_mdv2(rel_time)
        lines.append(f"{i}\\. {title} — _{time_escaped}_")

    context.user_data["history_entries"] = valid_entries

    await reply_fn(
        _msg(context, "history_list", items="\n".join(lines)),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=_history_keyboard(),
    )
    return SELECTING_VIDEO


async def select_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle number selection from the history list."""
    text = update.message.text.strip()

    # Allow reply keyboard shortcuts even in SELECTING_VIDEO state
    if text == "🎬 New Video":
        return await new_video(update, context)
    if text == "❓ Help":
        await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2)
        return SELECTING_VIDEO

    entries = context.user_data.get("history_entries", [])
    if not entries:
        await update.message.reply_text(_msg(context, "history_invalid"))
        return WAITING_FOR_URL

    try:
        choice = int(text)
    except ValueError:
        await update.message.reply_text(
            _msg(context, "history_bad_num", max=len(entries))
        )
        return SELECTING_VIDEO

    if choice < 1 or choice > len(entries):
        await update.message.reply_text(
            _msg(context, "history_bad_num", max=len(entries))
        )
        return SELECTING_VIDEO

    entry = entries[choice - 1]

    # Re-check files exist (could have been deleted since list was shown)
    if not os.path.exists(entry["transcript_path"]) or not os.path.exists(entry["summary_path"]):
        await update.message.reply_text(_msg(context, "history_missing"))
        return SELECTING_VIDEO

    with open(entry["transcript_path"], "r", encoding="utf-8") as f:
        context.user_data["transcript"] = f.read().strip()

    context.user_data["url"] = entry["video_url"]
    context.user_data.pop("history_entries", None)

    await update.message.reply_text(
        _msg(context, "history_loaded", title=entry["title"]),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_post_summary_keyboard(),
    )
    return FOLLOW_UP


async def clear_history_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask for confirmation before clearing all history."""
    if update.callback_query:
        await update.callback_query.answer()
        reply_fn = update.callback_query.message.reply_text
        user_id = update.callback_query.from_user.id
    else:
        reply_fn = update.message.reply_text
        user_id = update.effective_user.id

    # Check if there's anything to delete
    try:
        entries = await asyncio.to_thread(get_user_history, user_id, 1)
    except Exception:
        entries = []

    if not entries:
        await reply_fn(_msg(context, "history_empty"))
        return FOLLOW_UP if context.user_data.get("transcript") else WAITING_FOR_URL

    await reply_fn(
        _msg(context, "clear_confirm"),
        reply_markup=_clear_confirm_keyboard(),
    )
    return SELECTING_VIDEO


async def clear_history_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Delete all files and DB entries for the user after confirmation."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    try:
        deleted_rows = await asyncio.to_thread(delete_user_history, user_id)
    except Exception:
        logger.exception("Failed to delete history for user %s", user_id)
        deleted_rows = []

    # Delete files from disk
    for row in deleted_rows:
        for path in (row.get("transcript_path"), row.get("summary_path")):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    logger.warning("Could not delete file: %s", path)

        # Also delete the WAV if it exists (same stem as transcript)
        if row.get("transcript_path"):
            wav_path = os.path.splitext(row["transcript_path"])[0] + ".wav"
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    logger.warning("Could not delete file: %s", wav_path)

    context.user_data.pop("history_entries", None)
    context.user_data.pop("transcript", None)
    context.user_data.pop("url", None)

    await query.message.reply_text(_msg(context, "clear_done", count=len(deleted_rows)))
    await query.message.reply_text(_msg(context, "ask_url"), reply_markup=_main_reply_keyboard())
    return WAITING_FOR_URL


async def clear_history_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User cancelled the clear confirmation."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(_msg(context, "clear_cancelled"))
    if context.user_data.get("transcript"):
        await query.message.reply_text(_msg(context, "post_summary"), reply_markup=_post_summary_keyboard())
        return FOLLOW_UP
    await query.message.reply_text(_msg(context, "ask_url"), reply_markup=_main_reply_keyboard())
    return WAITING_FOR_URL


async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    if text == "🎬 New Video":
        return await new_video(update, context)
    if text == "📚 My Videos":
        return await history_command(update, context)
    if text == "❓ Help":
        return await help_command(update, context)

    urls = _extract_youtube_urls(text)

    if not urls:
        await update.message.reply_text(_msg(context, "bad_url"))
        return WAITING_FOR_URL

    # --- BATCH MODE: 2+ URLs ---
    if len(urls) > 1:
        context.user_data["batch_urls"] = urls
        context.user_data.pop("url", None)

        # Ask which collection to assign (or skip)
        try:
            collections = await asyncio.to_thread(get_collections)
        except Exception:
            collections = []

        s = "s" if len(urls) != 1 else ""
        await update.message.reply_text(
            _msg(context, "batch_received", count=len(urls), s=s),
            parse_mode=ParseMode.MARKDOWN,
        )

        if collections:
            await update.message.reply_text(
                _msg(context, "batch_ask_collection"),
                reply_markup=_collection_keyboard(collections),
            )
            # Collection callback will start the batch
            return SELECTING_COLLECTION

        # No collections — start batch immediately with no collection
        _launch_batch(update, context, urls, collection_name=None)
        return FOLLOW_UP

    # --- SINGLE URL: original flow ---
    context.user_data["url"] = urls[0]
    await update.message.reply_text(_msg(context, "ask_prompt"))
    return WAITING_FOR_PROMPT


def _launch_batch(update: Update, context: ContextTypes.DEFAULT_TYPE,
                  urls: list[str], collection_name: str | None) -> None:
    """Create and register the background batch task."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    lang = _lang(context)

    # Cancel any existing batch for this user
    old_task = PIPELINE_TASKS.pop(user_id, None)
    if old_task and not old_task.done():
        old_task.cancel()

    BATCH_STATE[user_id] = {
        "total": len(urls),
        "done": 0,
        "success": 0,
        "current": "",
        "results": [],
        "collection": collection_name,
    }

    task = asyncio.create_task(
        _run_batch_pipeline(context.bot, chat_id, user_id, urls, collection_name, lang)
    )
    PIPELINE_TASKS[user_id] = task


async def _run_batch_pipeline(
    bot,
    chat_id: int,
    user_id: int,
    urls: list[str],
    collection_name: str | None,
    lang: str,
) -> None:
    """
    Process a list of YouTube URLs one by one in the background.
    Sends a single completion ping when all are done.
    """
    global PIPELINE_QUEUE_COUNT
    state = BATCH_STATE.get(user_id, {})

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    os.makedirs(SUMMARIES_DIR, exist_ok=True)

    PIPELINE_QUEUE_COUNT += 1

    try:
        for i, url in enumerate(urls):
            if user_id in PIPELINE_TASKS and PIPELINE_TASKS[user_id].cancelled():
                break

            state["current"] = url

            async with PIPELINE_SEMAPHORE:
                try:
                    # Step 1: Download
                    wav_path = await asyncio.to_thread(
                        download_youtube_audio_as_wav, url, DOWNLOADS_DIR
                    )
                    if not wav_path:
                        state["results"].append({"url": url, "ok": False, "title": ""})
                        state["done"] += 1
                        continue

                    # Step 2: Transcribe (with periodic keepalive)
                    txt_path = await _transcribe_with_keepalive(
                        wav_path, chat_id, bot, lang=lang
                    )
                    if not txt_path:
                        state["results"].append({"url": url, "ok": False, "title": ""})
                        state["done"] += 1
                        continue

                    # Step 3: Summarize
                    summary_path = await asyncio.to_thread(
                        summarize_file, txt_path, SUMMARIES_DIR, DEFAULT_LLM_MODEL,
                        True, None, lang,
                    )
                    if not summary_path:
                        state["results"].append({"url": url, "ok": False, "title": ""})
                        state["done"] += 1
                        continue

                    title = os.path.splitext(os.path.basename(txt_path))[0]

                    # Step 4: Save to history
                    try:
                        await asyncio.to_thread(
                            add_video_history, user_id, title, url, txt_path, summary_path
                        )
                    except Exception:
                        logger.exception("History save failed for %s", url)

                    # Step 5: Extract into collection (if assigned)
                    if collection_name:
                        try:
                            await asyncio.to_thread(
                                extract_and_store,
                                collection_name, txt_path, summary_path,
                                url, title, DEFAULT_LLM_MODEL,
                            )
                        except Exception:
                            logger.exception("Extraction failed for %s", url)

                    state["results"].append({"url": url, "ok": True, "title": title})
                    state["success"] += 1

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.exception("Batch item failed: %s", url)
                    state["results"].append({"url": url, "ok": False, "title": str(e)})

                finally:
                    state["done"] += 1

        # Rebuild knowledge file once after all videos are done
        if collection_name and state["success"] > 0:
            try:
                await asyncio.to_thread(build_knowledge_file, collection_name)
            except Exception:
                logger.exception("Knowledge rebuild failed for %s", collection_name)

        # Build completion message
        msgs = MESSAGES[lang]
        result_lines = []
        for r in state["results"]:
            if r["ok"]:
                result_lines.append(msgs["batch_done_item_ok"].format(title=r["title"]))
            else:
                result_lines.append(msgs["batch_done_item_fail"].format(url=r["url"]))

        completion_msg = msgs["batch_done"].format(
            success=state["success"],
            total=state["total"],
            results="\n".join(result_lines),
        )

        if collection_name:
            completion_msg += f"\n\n📚 Added to collection: *{collection_name}*"

        await bot.send_message(chat_id, completion_msg, parse_mode=ParseMode.MARKDOWN)

    except asyncio.CancelledError:
        done = state.get("done", 0)
        total = state.get("total", len(urls))
        await bot.send_message(
            chat_id,
            MESSAGES[lang]["batch_cancelled"].format(done=done, total=total),
        )
    finally:
        PIPELINE_QUEUE_COUNT = max(0, PIPELINE_QUEUE_COUNT - 1)
        PIPELINE_TASKS.pop(user_id, None)
        BATCH_STATE.pop(user_id, None)


async def receive_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_text = update.message.text.strip()
    url = context.user_data.get("url")
    lang = _lang(context)

    if not url:
        await update.message.reply_text("Something went wrong. Please use /start.")
        return ConversationHandler.END

    if user_text == "/default" or "{text}" not in user_text:
        custom_prompt = None if user_text == "/default" else user_text + "\n\n{text}"
    else:
        custom_prompt = user_text

    global PIPELINE_QUEUE_COUNT
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    os.makedirs(SUMMARIES_DIR, exist_ok=True)

    user_id = update.effective_user.id

    PIPELINE_QUEUE_COUNT += 1
    if PIPELINE_SEMAPHORE.locked():
        await update.message.reply_text(_msg(context, "queued", pos=PIPELINE_QUEUE_COUNT))
    else:
        await update.message.reply_text(_msg(context, "starting"))

    async def _run_pipeline():
        try:
            async with PIPELINE_SEMAPHORE:
                # Step 1: Download
                await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
                await update.message.reply_text(_msg(context, "step1"))
                wav_path = await asyncio.to_thread(
                    download_youtube_audio_as_wav, url, DOWNLOADS_DIR
                )
                if not wav_path:
                    await update.message.reply_text(_msg(context, "dl_fail"))
                    return

                # Step 2: Transcribe (with periodic keepalive so Telegram shows typing)
                await update.message.reply_text(_msg(context, "step2"))
                txt_path = await _transcribe_with_keepalive(
                    wav_path,
                    update.effective_chat.id,
                    context.bot,
                    lang=lang,
                )
                if not txt_path:
                    await update.message.reply_text(_msg(context, "tx_fail"))
                    return

                # Step 3: Summarize
                await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
                await update.message.reply_text(_msg(context, "step3"))
                summary_path = await asyncio.to_thread(
                    summarize_file,
                    txt_path,
                    SUMMARIES_DIR,
                    DEFAULT_LLM_MODEL,
                    True,
                    custom_prompt,
                    lang,
                )
                if not summary_path:
                    await update.message.reply_text(_msg(context, "sum_fail"))
                    return

                with open(txt_path, "r", encoding="utf-8") as f:
                    context.user_data["transcript"] = f.read().strip()

                with open(summary_path, "r", encoding="utf-8") as f:
                    summary = f.read().strip()

                # Store paths for collection assignment
                title = os.path.splitext(os.path.basename(txt_path))[0]
                context.user_data["transcript_path"] = txt_path
                context.user_data["summary_path"] = summary_path
                context.user_data["video_url"] = url
                context.user_data["video_title"] = title

                await update.message.reply_text(_msg(context, "done"))
                for chunk in _split_message(summary):
                    await update.message.reply_text(chunk)

                # Save to history
                try:
                    await asyncio.to_thread(
                        add_video_history, user_id, title, url, txt_path, summary_path,
                    )
                except Exception:
                    logger.exception("Failed to save video history for user %s", user_id)

                # Prompt for collection assignment if any collections exist
                try:
                    collections = await asyncio.to_thread(get_collections)
                except Exception:
                    collections = []

                if collections:
                    await update.message.reply_text(
                        _msg(context, "ask_collection"),
                        reply_markup=_collection_keyboard(collections),
                    )
                else:
                    await update.message.reply_text(
                        _msg(context, "post_summary"),
                        reply_markup=_post_summary_keyboard(),
                    )

        except asyncio.CancelledError:
            logger.info("Pipeline cancelled for user %s", user_id)
        except Exception as e:
            logger.exception("Pipeline error")
            await update.message.reply_text(_msg(context, "error", err=e))
        finally:
            global PIPELINE_QUEUE_COUNT
            PIPELINE_QUEUE_COUNT = max(0, PIPELINE_QUEUE_COUNT - 1)
            PIPELINE_TASKS.pop(user_id, None)

    task = asyncio.create_task(_run_pipeline())
    PIPELINE_TASKS[user_id] = task
    return FOLLOW_UP


async def follow_up(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    if text == "🎬 New Video":
        return await new_video(update, context)
    if text == "📚 My Videos":
        return await history_command(update, context)
    if text == "❓ Help":
        await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2)
        return FOLLOW_UP

    transcript = context.user_data.get("transcript")
    if not transcript:
        await update.message.reply_text(_msg(context, "no_transcript"))
        return ConversationHandler.END

    thinking_msg = await update.message.reply_text(_msg(context, "thinking"))
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    try:
        answer = await asyncio.to_thread(
            _answer_question, transcript, text, DEFAULT_LLM_MODEL, _lang(context)
        )
        await thinking_msg.delete()
        for chunk in _split_message(answer):
            await update.message.reply_text(chunk)
    except Exception as e:
        logger.exception("Follow-up error")
        await thinking_msg.delete()
        await update.message.reply_text(_msg(context, "error", err=e))

    return FOLLOW_UP


async def assign_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle collection selection after a summary is done (single or batch)."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # --- BATCH flow: collection chosen for a pending batch ---
    batch_urls = context.user_data.pop("batch_urls", None)
    if batch_urls:
        if data == CB_SKIP_COLLECTION:
            await query.edit_message_text(_msg(context, "collection_skip"))
            _launch_batch(update, context, batch_urls, collection_name=None)
        else:
            collection_name = data[len(CB_COLLECTION_PREFIX):]
            await query.edit_message_text(
                f"🚀 Starting batch of {len(batch_urls)} videos → *{collection_name}*",
                parse_mode=ParseMode.MARKDOWN,
            )
            _launch_batch(update, context, batch_urls, collection_name=collection_name)
        return FOLLOW_UP

    # --- SINGLE video flow: post-summary collection assignment ---
    if data == CB_SKIP_COLLECTION:
        await query.edit_message_text(_msg(context, "collection_skip"))
        return FOLLOW_UP

    collection_name = data[len(CB_COLLECTION_PREFIX):]
    context.user_data["pending_collection"] = collection_name

    # Run extraction in background
    txt_path = context.user_data.get("transcript_path", "")
    summary_path = context.user_data.get("summary_path", "")
    video_url = context.user_data.get("video_url", "")
    video_title = context.user_data.get("video_title", "")

    if not txt_path or not os.path.exists(txt_path):
        await query.edit_message_text(f"⚠️ Transcript file not found, cannot extract.")
        return FOLLOW_UP

    await query.edit_message_text(f"⚙️ Extracting knowledge into *{collection_name}*…", parse_mode=ParseMode.MARKDOWN)

    try:
        result = await asyncio.to_thread(
            extract_and_store,
            collection_name, txt_path, summary_path, video_url, video_title, DEFAULT_LLM_MODEL
        )
        await asyncio.to_thread(build_knowledge_file, collection_name)

        from collections import Counter
        type_counts = Counter(i.get("type") for i in result.get("items", []))
        await query.edit_message_text(
            _msg(context, "collection_added",
                 name=collection_name,
                 items=result.get("items_count", 0),
                 formulas=type_counts.get("formula", 0),
                 questions=type_counts.get("question", 0),
                 tricks=type_counts.get("trick", 0)),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.exception("Collection extraction failed")
        await query.edit_message_text(f"⚠️ Extraction failed: {e}")

    return FOLLOW_UP


async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/quiz — pick a collection and send questions."""
    try:
        collections = await asyncio.to_thread(get_collections)
    except Exception:
        await update.message.reply_text("⚠️ Database unavailable — cannot load quiz. Please make sure Postgres is running.")
        return FOLLOW_UP
    if not collections:
        await update.message.reply_text(_msg(context, "no_collections_for_quiz"))
        return FOLLOW_UP

    # Check args: /quiz <collection_name> <count>
    args = context.args or []
    if args:
        collection_name = " ".join(args[:-1]) if len(args) > 1 and args[-1].isdigit() else " ".join(args)
        count = int(args[-1]) if args and args[-1].isdigit() else 10
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = str(update.effective_chat.id)
        questions = await send_quiz(bot_token, chat_id, collection_name, count=count)
        if not questions:
            await update.message.reply_text(_msg(context, "quiz_no_questions", name=collection_name))
        return FOLLOW_UP

    # No args — show collection picker
    context.user_data["quiz_pending"] = True
    keyboard = _collections_list_keyboard(collections)
    await update.message.reply_text("🎯 Pick a collection to quiz from:", reply_markup=keyboard)
    return SELECTING_COLLECTION


async def ask_collection_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/ask — ask a question against a collection knowledge base."""
    try:
        collections = await asyncio.to_thread(get_collections)
    except Exception:
        await update.message.reply_text("⚠️ Database unavailable — cannot load collections. Please make sure Postgres is running.")
        return FOLLOW_UP
    if not collections:
        await update.message.reply_text(_msg(context, "no_collections_for_quiz"))
        return FOLLOW_UP

    keyboard = _collections_list_keyboard(collections)
    await update.message.reply_text("🧠 Pick a collection to ask from:", reply_markup=keyboard)
    context.user_data["ask_collection_pending"] = True
    return SELECTING_COLLECTION


async def select_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle collection selection for /quiz or /ask flows."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith(CB_COLLECTION_PREFIX):
        return SELECTING_COLLECTION

    collection_name = data[len(CB_COLLECTION_PREFIX):]
    chat_id = str(update.effective_chat.id)
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    if context.user_data.pop("quiz_pending", False):
        # Quiz flow
        questions = await send_quiz(bot_token, chat_id, collection_name, count=10)
        if not questions:
            await query.edit_message_text(_msg(context, "quiz_no_questions", name=collection_name))
        else:
            await query.edit_message_text(f"✅ Quiz sent from *{collection_name}*!", parse_mode=ParseMode.MARKDOWN)
        return FOLLOW_UP

    if context.user_data.pop("ask_collection_pending", False):
        # Store collection for next message
        context.user_data["asking_collection"] = collection_name
        await query.edit_message_text(
            _msg(context, "collection_ask_question", name=collection_name),
            parse_mode=ParseMode.MARKDOWN,
        )
        return COLLECTION_ASK

    return FOLLOW_UP


async def collection_question_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the user's typed question against a collection."""
    collection_name = context.user_data.pop("asking_collection", None)
    if not collection_name:
        return FOLLOW_UP

    question = update.message.text.strip()
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    thinking_msg = await update.message.reply_text(_msg(context, "thinking"))

    try:
        answer = await asyncio.to_thread(
            collection_answer_question, collection_name, question, DEFAULT_LLM_MODEL, _lang(context)
        )
        await thinking_msg.delete()
        header = _msg(context, "collection_answer_done", name=collection_name)
        for chunk in _split_message(f"{header}\n\n{answer}"):
            await update.message.reply_text(chunk, reply_markup=_post_summary_keyboard())
    except Exception as e:
        logger.exception("Collection Q&A error")
        await thinking_msg.delete()
        await update.message.reply_text(_msg(context, "error", err=e))

    return FOLLOW_UP


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/status — show current batch progress."""
    user_id = update.effective_user.id
    state = BATCH_STATE.get(user_id)
    task = PIPELINE_TASKS.get(user_id)

    if not state or not task or task.done():
        await update.message.reply_text(
            _msg(context, "batch_status_idle"), parse_mode=ParseMode.MARKDOWN
        )
        return FOLLOW_UP if context.user_data.get("transcript") else WAITING_FOR_URL

    current_url = state.get("current", "")
    # Shorten URL for display
    current_display = current_url[:60] + "…" if len(current_url) > 60 else current_url

    await update.message.reply_text(
        _msg(context, "batch_status_running",
             done=state["done"],
             total=state["total"],
             current=current_display),
        parse_mode=ParseMode.MARKDOWN,
    )
    return FOLLOW_UP if context.user_data.get("transcript") else WAITING_FOR_URL


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = _lang(context)
    user_id = update.effective_user.id
    task = PIPELINE_TASKS.pop(user_id, None)
    if task and not task.done():
        task.cancel()
    context.user_data.clear()
    cancelled_text = MESSAGES[lang]["cancelled"]
    await update.message.reply_text(cancelled_text, reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text("An unexpected error occurred.")


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start",    "Start or restart the bot"),
        BotCommand("new",      "Summarize a new video"),
        BotCommand("history",  "Browse your past videos"),
        BotCommand("clear",    "Delete all your videos and files"),
        BotCommand("language", "Change response language"),
        BotCommand("quiz",     "Get quiz questions from a collection"),
        BotCommand("ask",      "Ask a question from your knowledge base"),
        BotCommand("status",   "Check background processing progress"),
        BotCommand("help",     "What can this bot do?"),
        BotCommand("cancel",   "Cancel current operation"),
    ])


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token or token == "your_bot_token_here":
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN not set. Add it to .env:\n"
            "  TELEGRAM_BOT_TOKEN=<your token from @BotFather>"
        )

    # Ensure DB tables exist on startup
    try:
        ensure_history_table()
        ensure_collections_tables()
    except Exception:
        logger.warning("Could not create DB tables — DB may be unavailable.")

    app = Application.builder().token(token).post_init(post_init).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start",   start),
            CommandHandler("new",     new_video),
            CommandHandler("help",    help_command),
            CommandHandler("history", history_command),
            CommandHandler("clear",   clear_history_prompt),
            CommandHandler("quiz",    quiz_command),
            CommandHandler("ask",     ask_collection_command),
            CommandHandler("status",  status_command),
            CommandHandler("cancel",  cancel),
        ],
        states={
            WAITING_FOR_KEY: [
                CommandHandler("start",   start),
                CommandHandler("cancel",  cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, verify_key),
                CommandHandler("history", history_command),
            ],
            WAITING_FOR_LANG: [
                CommandHandler("start",   start),
                CommandHandler("cancel",  cancel),
                CallbackQueryHandler(set_language, pattern=r"^lang:"),
                CommandHandler("language", language_command),
                CommandHandler("history", history_command),
            ],
            WAITING_FOR_URL: [
                CommandHandler("start",   start),
                CommandHandler("cancel",  cancel),
                CommandHandler("status",  status_command),
                CallbackQueryHandler(set_language, pattern=r"^lang:"),
                CommandHandler("language", language_command),
                CommandHandler("history", history_command),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url),
            ],
            WAITING_FOR_PROMPT: [
                CommandHandler("start",   start),
                CommandHandler("cancel",  cancel),
                CommandHandler("default", receive_prompt),
                CommandHandler("history", history_command),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt),
            ],
            SELECTING_VIDEO: [
                CommandHandler("start",   start),
                CommandHandler("cancel",  cancel),
                CommandHandler("history", history_command),
                CommandHandler("new",     new_video),
                CallbackQueryHandler(clear_history_prompt,   pattern=r"^action:clear_history$"),
                CallbackQueryHandler(clear_history_confirm,  pattern=r"^action:clear_confirm$"),
                CallbackQueryHandler(clear_history_cancel,   pattern=r"^action:clear_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_video),
            ],
            SELECTING_COLLECTION: [
                CommandHandler("start",   start),
                CommandHandler("cancel",  cancel),
                CommandHandler("new",     new_video),
                CallbackQueryHandler(assign_collection_callback, pattern=r"^collection:"),
                CallbackQueryHandler(assign_collection_callback, pattern=r"^action:skip_collection$"),
                CallbackQueryHandler(select_collection_callback, pattern=r"^collection:"),
            ],
            COLLECTION_ASK: [
                CommandHandler("start",   start),
                CommandHandler("cancel",  cancel),
                CommandHandler("new",     new_video),
                MessageHandler(filters.TEXT & ~filters.COMMAND, collection_question_handler),
            ],
            FOLLOW_UP: [
                CommandHandler("start",    start),
                CommandHandler("cancel",   cancel),
                CommandHandler("new",      new_video),
                CommandHandler("language", language_command),
                CommandHandler("history",  history_command),
                CommandHandler("clear",    clear_history_prompt),
                CommandHandler("quiz",     quiz_command),
                CommandHandler("ask",      ask_collection_command),
                CommandHandler("status",   status_command),
                CallbackQueryHandler(set_language,               pattern=r"^lang:"),
                CallbackQueryHandler(new_video,                  pattern=r"^action:new_video$"),
                CallbackQueryHandler(switch_language_callback,   pattern=r"^action:switch_lang$"),
                CallbackQueryHandler(history_command,            pattern=r"^action:history$"),
                CallbackQueryHandler(assign_collection_callback, pattern=r"^collection:"),
                CallbackQueryHandler(assign_collection_callback, pattern=r"^action:skip_collection$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, follow_up),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(conv_handler)
    app.add_error_handler(error_handler)

    logger.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
