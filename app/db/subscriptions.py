"""yt_subscriptions table access."""
import psycopg2.extras

from app.db.core import get_conn


def ensure_subscriptions_table() -> None:
    """Add last_sent_video_id / last_sent_at columns if they don't exist yet."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE yt_subscriptions
                ADD COLUMN IF NOT EXISTS last_sent_video_id TEXT,
                ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS custom_prompt TEXT
            """)
        conn.commit()


def load_subscriptions() -> list[dict]:
    """Return all subscriptions as a list of dicts."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT telegram_chat_id, channel_url, run_time, enabled, "
                "last_sent_video_id, last_sent_at, custom_prompt "
                "FROM yt_subscriptions ORDER BY id"
            )
            return [dict(r) for r in cur.fetchall()]


def add_subscription(telegram_chat_id: str, channel_url: str, run_time: str,
                     custom_prompt: str | None = None) -> dict:
    """Upsert a subscription. Returns the resulting row."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO yt_subscriptions (telegram_chat_id, channel_url, run_time, custom_prompt)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (telegram_chat_id, channel_url)
                DO UPDATE SET run_time = EXCLUDED.run_time,
                              enabled = TRUE,
                              custom_prompt = COALESCE(EXCLUDED.custom_prompt, yt_subscriptions.custom_prompt)
                RETURNING telegram_chat_id, channel_url, run_time, enabled,
                          last_sent_video_id, last_sent_at, custom_prompt
                """,
                (telegram_chat_id, channel_url, run_time, custom_prompt),
            )
            conn.commit()
            return dict(cur.fetchone())


def update_subscription_last_sent(telegram_chat_id: str, channel_url: str,
                                  video_id: str) -> None:
    """Record the last successfully sent video ID and timestamp."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE yt_subscriptions
                SET last_sent_video_id = %s, last_sent_at = NOW()
                WHERE telegram_chat_id = %s AND channel_url = %s
                """,
                (video_id, telegram_chat_id, channel_url),
            )
        conn.commit()


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
