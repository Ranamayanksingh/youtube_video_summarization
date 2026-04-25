"""
Quiz scheduler — picks random questions from a collection and sends them via Telegram.

Can be:
  - Called on-demand (bot /quiz command)
  - Driven by the background scheduler in web.py (interval-based)

Usage:
    python -m app.knowledge.quiz --collection "SSC CGL Maths" --chat-id 123456789 --count 10
"""
import argparse
import asyncio
import datetime

from dotenv import load_dotenv

from app.db import (
    ensure_collections_tables,
    get_collection_by_name,
    get_quiz_schedules,
    get_random_questions,
    update_quiz_last_sent,
)
from app.utils.notifications import send_telegram_message

load_dotenv()


def format_quiz_message(collection_name: str, questions: list[dict], show_answers: bool = False) -> str:
    """Format a list of questions into a Telegram quiz message."""
    if not questions:
        return (
            f"📭 No questions found in *{collection_name}* yet.\n"
            "Add more videos to build up the question bank!"
        )

    count = len(questions)
    now = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")

    lines = [
        f"📝 *Quiz — {collection_name}*",
        f"_{count} question{'s' if count != 1 else ''} • {now}_",
        "",
    ]

    for i, q in enumerate(questions, 1):
        content = q.get("content", "").strip()
        answer = q.get("answer", "").strip()
        topic = q.get("topic", "").strip()

        topic_tag = f"\\[{topic}\\]" if topic else ""
        lines.append(f"*Q{i}.* {topic_tag}")
        lines.append(content)

        if show_answers and answer:
            lines.append(f"✅ _{answer}_")

        lines.append("")

    if not show_answers:
        lines.append("_Reply with your answers or use /answers to reveal them._")

    return "\n".join(lines)


def format_answers_message(collection_name: str, questions: list[dict]) -> str:
    """Format answers for a previously sent quiz."""
    if not questions:
        return "No answers to show."

    lines = [f"✅ *Answers — {collection_name}*", ""]
    for i, q in enumerate(questions, 1):
        answer = q.get("answer", "").strip()
        topic = q.get("topic", "").strip()
        content = q.get("content", "").strip()
        q_short = content.split("\n")[0][:80]
        lines.append(f"*Q{i}.* _{q_short}_")
        if answer:
            lines.append(f"→ {answer}")
        else:
            lines.append("→ _(no answer recorded)_")
        lines.append("")

    return "\n".join(lines)


async def send_quiz(
    bot_token: str,
    chat_id: str,
    collection_name: str,
    count: int = 10,
    show_answers: bool = False,
) -> list[dict]:
    """
    Pull `count` random questions from collection and send to Telegram chat.
    Returns the list of questions sent (caller can store for answer reveal).
    """
    ensure_collections_tables()

    collection = get_collection_by_name(collection_name)
    if not collection:
        await send_telegram_message(
            bot_token, chat_id,
            f"❌ Collection '{collection_name}' not found."
        )
        return []

    questions = get_random_questions(collection["id"], count)
    message = format_quiz_message(collection_name, questions, show_answers=show_answers)

    await send_telegram_message(bot_token, chat_id, message)
    return questions


async def run_scheduled_quizzes(bot_token: str) -> None:
    """
    Check all quiz_schedules and fire any that are due based on interval_minutes.
    Called by the background scheduler in web.py every minute.
    """
    ensure_collections_tables()
    schedules = get_quiz_schedules()
    now = datetime.datetime.now(datetime.timezone.utc)

    for sched in schedules:
        if not sched.get("enabled"):
            continue

        interval = sched.get("interval_minutes", 60)
        last_sent = sched.get("last_sent_at")

        if last_sent is None:
            due = True
        else:
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=datetime.timezone.utc)
            elapsed = (now - last_sent).total_seconds() / 60
            due = elapsed >= interval

        if not due:
            continue

        collection_name = sched.get("collection_name", "")
        chat_id = sched.get("telegram_chat_id", "")
        count = sched.get("question_count", 10)

        try:
            await send_quiz(bot_token, chat_id, collection_name, count=count)
            update_quiz_last_sent(sched["id"])
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                "Quiz schedule failed for chat_id=%s collection=%s: %s",
                chat_id, collection_name, e
            )


if __name__ == "__main__":
    import os
    from app.db import get_secret

    parser = argparse.ArgumentParser(description="Send a quiz to a Telegram chat from a collection.")
    parser.add_argument("--collection", required=True, help="Collection name")
    parser.add_argument("--chat-id", required=True, help="Telegram chat ID to send to")
    parser.add_argument("--count", type=int, default=10, help="Number of questions (default: 10)")
    parser.add_argument("--with-answers", action="store_true", help="Include answers in the message")
    args = parser.parse_args()

    token = get_secret("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN not set.")
        exit(1)

    async def _main():
        questions = await send_quiz(
            bot_token=token,
            chat_id=args.chat_id,
            collection_name=args.collection,
            count=args.count,
            show_answers=args.with_answers,
        )
        print(f"✅ Sent {len(questions)} questions to chat {args.chat_id}")

    asyncio.run(_main())
