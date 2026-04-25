"""
Postgres connection and secrets helpers.

Connection details read from environment variables (set in .env):
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
"""
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

_DSN = {
    "host":     os.environ.get("POSTGRES_HOST", "localhost"),
    "port":     int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname":   os.environ.get("POSTGRES_DB", "billbot_db"),
    "user":     os.environ.get("POSTGRES_USER", "billbot_user"),
    "password": os.environ.get("POSTGRES_PASSWORD", "changeme"),
}


def get_conn():
    """Return a new psycopg2 connection. Caller is responsible for closing."""
    return psycopg2.connect(**_DSN)


def get_secret(key: str, fallback: str = "") -> str:
    """Fetch a secret from app_secrets table. Falls back to env var, then fallback."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM app_secrets WHERE key = %s", (key,))
                row = cur.fetchone()
                if row:
                    return row[0]
    except Exception:
        pass
    return os.environ.get(key, fallback)


def set_secret(key: str, value: str, note: str = "") -> None:
    """Upsert a secret into app_secrets."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_secrets (key, value, note)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,
                                                note = COALESCE(NULLIF(EXCLUDED.note,''), app_secrets.note),
                                                updated_at = now()
                """,
                (key, value, note),
            )
        conn.commit()
