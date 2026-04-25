"""
Telegram notification helpers and subscription briefing utilities.
"""
import logging

import ollama

logger = logging.getLogger(__name__)


def extract_highlights(summary_text: str, model: str) -> str:
    """
    Use Ollama to extract concise morning-newspaper-style highlights from the summary.
    Returns a formatted string ready to send via Telegram.
    """
    prompt = (
        "You are a news editor creating a morning briefing. "
        "Extract the 5 most important highlights from the video summary below. "
        "Format each highlight as a short, punchy bullet point (1-2 sentences max). "
        "Write like a newspaper — factual, no fluff, no calls to action. "
        "Start directly with the bullets, no intro text.\n\n"
        f"Summary:\n{summary_text[:4000]}"
    )
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response["message"]["content"].strip()


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
