"""
Telegram notification helpers and subscription briefing utilities.
"""
import logging

logger = logging.getLogger(__name__)


def extract_highlights(summary_text: str, model: str) -> str:
    """
    Extract concise morning-newspaper-style highlights from the summary.
    Uses Groq (via _llm_chat) when GROQ_API_KEY is set, otherwise falls back to Ollama.
    Returns a formatted string ready to send via Telegram.
    """
    from app.pipeline.summarizer import _llm_chat

    prompt = (
        "You are a news editor creating a morning briefing. "
        "Extract the 5 most important highlights from the video summary below. "
        "Format each highlight as a short, punchy bullet point (1-2 sentences max). "
        "Write like a newspaper — factual, no fluff, no calls to action. "
        "Start directly with the bullets, no intro text.\n\n"
        f"Summary:\n{summary_text[:4000]}"
    )
    return _llm_chat(model, prompt)


async def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    """Send a message via the Telegram Bot API (async, chunked for long messages)."""
    import httpx
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    chunks = [text[i:i + 4096] for i in range(0, len(text), 4096)]
    async with httpx.AsyncClient(timeout=30) as client:
        for chunk in chunks:
            await client.post(url, json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
            })
