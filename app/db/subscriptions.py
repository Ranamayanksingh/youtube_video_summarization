"""yt_subscriptions table access."""
import psycopg2.extras

from app.db.core import get_conn


def load_subscriptions() -> list[dict]:
    """Return all subscriptions as a list of dicts."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT telegram_chat_id, channel_url, run_time, enabled "
                "FROM yt_subscriptions ORDER BY id"
            )
            return [dict(r) for r in cur.fetchall()]


def add_subscription(telegram_chat_id: str, channel_url: str, run_time: str) -> dict:
    """Upsert a subscription. Returns the resulting row."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO yt_subscriptions (telegram_chat_id, channel_url, run_time)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_chat_id, channel_url)
                DO UPDATE SET run_time = EXCLUDED.run_time, enabled = TRUE
                RETURNING telegram_chat_id, channel_url, run_time, enabled
                """,
                (telegram_chat_id, channel_url, run_time),
            )
            conn.commit()
            return dict(cur.fetchone())


def remove_subscription(telegram_chat_id: str, channel_url: str) -> bool:
    """Delete a subscription. Returns True if a row was deleted."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM yt_subscriptions WHERE telegram_chat_id = %s AND channel_url = %s",
                (telegram_chat_id, channel_url),
            )
            conn.commit()
            return cur.rowcount > 0
