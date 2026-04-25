"""allowed_telegram_users table access."""
import psycopg2.extras

from app.db.core import get_conn


def ensure_allowed_users_table() -> None:
    """Create allowed_telegram_users table if it doesn't exist."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS allowed_telegram_users (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT UNIQUE NOT NULL,
                    label TEXT DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        conn.commit()


def get_allowed_users() -> list[dict]:
    """Return all allowed Telegram user IDs."""
    ensure_allowed_users_table()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, telegram_user_id, label, created_at "
                "FROM allowed_telegram_users ORDER BY created_at"
            )
            return [dict(r) for r in cur.fetchall()]


def add_allowed_user(telegram_user_id: int, label: str = "") -> None:
    """Add a Telegram user ID to the allowed list."""
    ensure_allowed_users_table()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO allowed_telegram_users (telegram_user_id, label)
                VALUES (%s, %s)
                ON CONFLICT (telegram_user_id) DO UPDATE SET label = EXCLUDED.label
                """,
                (telegram_user_id, label),
            )
        conn.commit()


def remove_allowed_user(telegram_user_id: int) -> bool:
    """Remove a Telegram user ID from the allowed list. Returns True if removed."""
    ensure_allowed_users_table()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM allowed_telegram_users WHERE telegram_user_id = %s",
                (telegram_user_id,),
            )
            conn.commit()
            return cur.rowcount > 0


def is_telegram_user_allowed(telegram_user_id: int) -> bool:
    """Return True if user is in the allowed list (or the list is empty = allow all)."""
    ensure_allowed_users_table()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM allowed_telegram_users")
            total = cur.fetchone()[0]
            if total == 0:
                return True  # empty list = open access
            cur.execute(
                "SELECT 1 FROM allowed_telegram_users WHERE telegram_user_id = %s",
                (telegram_user_id,),
            )
            return cur.fetchone() is not None
