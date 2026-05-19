"""
Pipeline job tracking table.

Tracks each video processing request through its stages so that:
- Failures can be retried from the last successful step
- If the summary is done but Telegram send failed, we just resend
- On bot restart, stuck jobs are resumed automatically

Steps (in order):
  pending → downloading → transcribing → summarizing → sending → done
                                                                ↓
                                                             failed  (on unrecoverable error)
"""
import psycopg2.extras
from app.db.core import get_conn


# Valid step values — ordered for progress tracking
STEPS = ["pending", "downloading", "transcribing", "summarizing", "sending", "done", "failed"]

# Steps where the output file already exists — retry just resumes from here
RESUMABLE_STEPS = {"transcribing", "summarizing", "sending", "done"}


def ensure_pipeline_jobs_table() -> None:
    """Create pipeline_jobs table and add any missing columns."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_jobs (
                    id               SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    telegram_chat_id BIGINT NOT NULL,
                    video_url        TEXT NOT NULL,
                    title            TEXT DEFAULT '',
                    step             TEXT NOT NULL DEFAULT 'pending',
                    wav_path         TEXT DEFAULT '',
                    txt_path         TEXT DEFAULT '',
                    summary_path     TEXT DEFAULT '',
                    error_msg        TEXT DEFAULT '',
                    retry_count      INT DEFAULT 0,
                    created_at       TIMESTAMPTZ DEFAULT NOW(),
                    updated_at       TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Index for fast lookup of stuck/pending jobs on restart
            cur.execute("""
                CREATE INDEX IF NOT EXISTS pipeline_jobs_user_step_idx
                ON pipeline_jobs (telegram_user_id, step)
            """)
        conn.commit()


def create_job(telegram_user_id: int, telegram_chat_id: int, video_url: str) -> int:
    """Insert a new job in 'pending' state. Returns the job id."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pipeline_jobs
                    (telegram_user_id, telegram_chat_id, video_url, step)
                VALUES (%s, %s, %s, 'pending')
                RETURNING id
                """,
                (telegram_user_id, telegram_chat_id, video_url),
            )
            job_id = cur.fetchone()[0]
        conn.commit()
    return job_id


def advance_job(
    job_id: int,
    step: str,
    *,
    title: str = "",
    wav_path: str = "",
    txt_path: str = "",
    summary_path: str = "",
    error_msg: str = "",
) -> None:
    """
    Move a job to the given step, updating any paths/metadata provided.
    Non-empty string values overwrite existing columns; empty strings are ignored.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline_jobs SET
                    step         = %s,
                    updated_at   = NOW(),
                    title        = CASE WHEN %s <> '' THEN %s ELSE title END,
                    wav_path     = CASE WHEN %s <> '' THEN %s ELSE wav_path END,
                    txt_path     = CASE WHEN %s <> '' THEN %s ELSE txt_path END,
                    summary_path = CASE WHEN %s <> '' THEN %s ELSE summary_path END,
                    error_msg    = CASE WHEN %s <> '' THEN %s ELSE error_msg END
                WHERE id = %s
                """,
                (
                    step,
                    title, title,
                    wav_path, wav_path,
                    txt_path, txt_path,
                    summary_path, summary_path,
                    error_msg, error_msg,
                    job_id,
                ),
            )
        conn.commit()


def increment_retry(job_id: int) -> int:
    """Bump retry_count by 1. Returns the new count."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline_jobs
                SET retry_count = retry_count + 1, updated_at = NOW()
                WHERE id = %s
                RETURNING retry_count
                """,
                (job_id,),
            )
            count = cur.fetchone()[0]
        conn.commit()
    return count


def get_job(job_id: int) -> dict | None:
    """Fetch a single job by id."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM pipeline_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_stuck_jobs(telegram_user_id: int) -> list[dict]:
    """
    Return jobs for this user that started but never reached 'done' or 'failed'.
    These are candidates for retry on bot restart or /retry command.
    Ordered oldest first so we retry in submission order.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM pipeline_jobs
                WHERE telegram_user_id = %s
                  AND step NOT IN ('done', 'failed')
                ORDER BY created_at ASC
                """,
                (telegram_user_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_all_stuck_jobs() -> list[dict]:
    """
    Return ALL jobs across all users that are not done/failed.
    Used on bot startup to resume any jobs interrupted by a crash/restart.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM pipeline_jobs
                WHERE step NOT IN ('done', 'failed')
                ORDER BY created_at ASC
                """
            )
            return [dict(r) for r in cur.fetchall()]


def get_recent_jobs(telegram_user_id: int, limit: int = 5) -> list[dict]:
    """Return the most recent jobs for a user (any status), newest first."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM pipeline_jobs
                WHERE telegram_user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (telegram_user_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]
