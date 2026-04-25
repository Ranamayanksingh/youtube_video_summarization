"""User video history table access."""
import psycopg2.extras

from app.db.core import get_conn


def ensure_history_table() -> None:
    """Create user_video_history table if it doesn't exist."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_video_history (
                    id               SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    title            TEXT NOT NULL,
                    video_url        TEXT NOT NULL,
                    transcript_path  TEXT NOT NULL,
                    summary_path     TEXT NOT NULL,
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        conn.commit()


def add_video_history(
    telegram_user_id: int,
    title: str,
    video_url: str,
    transcript_path: str,
    summary_path: str,
) -> None:
    """Insert a new video history entry for a user."""
    ensure_history_table()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_video_history
                    (telegram_user_id, title, video_url, transcript_path, summary_path)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (telegram_user_id, title, video_url, transcript_path, summary_path),
            )
        conn.commit()


def delete_user_history(telegram_user_id: int) -> list[dict]:
    """Delete all history entries for a user. Returns the deleted rows (for file cleanup)."""
    ensure_history_table()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                DELETE FROM user_video_history
                WHERE telegram_user_id = %s
                RETURNING transcript_path, summary_path, title
                """,
                (telegram_user_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows


def get_user_history(telegram_user_id: int, limit: int = 10) -> list[dict]:
    """Return the user's most recent video history entries, newest first."""
    ensure_history_table()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, telegram_user_id, title, video_url,
                       transcript_path, summary_path, created_at
                FROM user_video_history
                WHERE telegram_user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (telegram_user_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]
