"""Collections, knowledge items, and quiz schedules table access."""
import psycopg2.extras

from app.db.core import get_conn


def ensure_collections_tables() -> None:
    """Create all collections-related tables if they don't exist."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS collections (
                    id           SERIAL PRIMARY KEY,
                    name         TEXT NOT NULL UNIQUE,
                    goal_type    TEXT NOT NULL DEFAULT 'exam_prep',
                    description  TEXT DEFAULT '',
                    extract_focus TEXT[] DEFAULT ARRAY['formulas','questions','tricks','concepts'],
                    created_at   TIMESTAMPTZ DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS collection_videos (
                    id               SERIAL PRIMARY KEY,
                    collection_id    INT REFERENCES collections(id) ON DELETE CASCADE,
                    video_url        TEXT NOT NULL,
                    title            TEXT DEFAULT '',
                    summary_path     TEXT DEFAULT '',
                    transcript_path  TEXT DEFAULT '',
                    extraction_done  BOOLEAN DEFAULT FALSE,
                    created_at       TIMESTAMPTZ DEFAULT now(),
                    UNIQUE(collection_id, video_url)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id            SERIAL PRIMARY KEY,
                    collection_id INT REFERENCES collections(id) ON DELETE CASCADE,
                    video_url     TEXT DEFAULT '',
                    video_title   TEXT DEFAULT '',
                    item_type     TEXT NOT NULL,
                    content       TEXT NOT NULL,
                    answer        TEXT DEFAULT '',
                    topic         TEXT DEFAULT '',
                    created_at    TIMESTAMPTZ DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quiz_schedules (
                    id               SERIAL PRIMARY KEY,
                    telegram_chat_id TEXT NOT NULL,
                    collection_id    INT REFERENCES collections(id) ON DELETE CASCADE,
                    interval_minutes INT DEFAULT 60,
                    question_count   INT DEFAULT 10,
                    enabled          BOOLEAN DEFAULT TRUE,
                    last_sent_at     TIMESTAMPTZ,
                    UNIQUE(telegram_chat_id, collection_id)
                )
            """)
        conn.commit()


def get_collections() -> list[dict]:
    ensure_collections_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT c.id, c.name, c.goal_type, c.description, c.extract_focus, c.created_at,
                       COUNT(DISTINCT cv.id) AS video_count,
                       COUNT(ki.id) AS item_count
                FROM collections c
                LEFT JOIN collection_videos cv ON cv.collection_id = c.id
                LEFT JOIN knowledge_items ki ON ki.collection_id = c.id
                GROUP BY c.id ORDER BY c.created_at
            """)
            return [dict(r) for r in cur.fetchall()]


def get_collection_by_name(name: str) -> dict | None:
    ensure_collections_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM collections WHERE name = %s", (name,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_collection_by_id(collection_id: int) -> dict | None:
    ensure_collections_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM collections WHERE id = %s", (collection_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def create_collection(name: str, goal_type: str, description: str, extract_focus: list[str]) -> dict:
    ensure_collections_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO collections (name, goal_type, description, extract_focus)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE
                    SET goal_type = EXCLUDED.goal_type,
                        description = EXCLUDED.description,
                        extract_focus = EXCLUDED.extract_focus
                RETURNING *
            """, (name, goal_type, description, extract_focus))
            conn.commit()
            return dict(cur.fetchone())


def delete_collection(collection_id: int) -> bool:
    ensure_collections_tables()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM collections WHERE id = %s", (collection_id,))
            conn.commit()
            return cur.rowcount > 0


def add_collection_video(collection_id: int, video_url: str, title: str,
                         summary_path: str, transcript_path: str) -> dict:
    ensure_collections_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO collection_videos
                    (collection_id, video_url, title, summary_path, transcript_path)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (collection_id, video_url) DO UPDATE
                    SET title = EXCLUDED.title,
                        summary_path = EXCLUDED.summary_path,
                        transcript_path = EXCLUDED.transcript_path
                RETURNING *
            """, (collection_id, video_url, title, summary_path, transcript_path))
            conn.commit()
            return dict(cur.fetchone())


def mark_collection_video_extracted(collection_id: int, video_url: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE collection_videos SET extraction_done = TRUE
                WHERE collection_id = %s AND video_url = %s
            """, (collection_id, video_url))
        conn.commit()


def get_collection_videos(collection_id: int) -> list[dict]:
    ensure_collections_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM collection_videos WHERE collection_id = %s ORDER BY created_at
            """, (collection_id,))
            return [dict(r) for r in cur.fetchall()]


def add_knowledge_items(collection_id: int, video_url: str, video_title: str,
                        items: list[dict]) -> int:
    """Bulk-insert extracted knowledge items. Returns count inserted."""
    ensure_collections_tables()
    if not items:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO knowledge_items
                    (collection_id, video_url, video_title, item_type, content, answer, topic)
                VALUES %s
            """, [
                (collection_id, video_url, video_title,
                 item.get("type", "concept"),
                 item.get("content", ""),
                 item.get("answer", ""),
                 item.get("topic", ""))
                for item in items
            ])
        conn.commit()
        return len(items)


def get_knowledge_items(collection_id: int, item_type: str | None = None) -> list[dict]:
    ensure_collections_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if item_type:
                cur.execute("""
                    SELECT * FROM knowledge_items
                    WHERE collection_id = %s AND item_type = %s
                    ORDER BY topic, created_at
                """, (collection_id, item_type))
            else:
                cur.execute("""
                    SELECT * FROM knowledge_items
                    WHERE collection_id = %s
                    ORDER BY item_type, topic, created_at
                """, (collection_id,))
            return [dict(r) for r in cur.fetchall()]


def get_random_questions(collection_id: int, count: int = 10) -> list[dict]:
    """Return `count` random question-type items from the collection."""
    ensure_collections_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM knowledge_items
                WHERE collection_id = %s AND item_type = 'question'
                ORDER BY RANDOM()
                LIMIT %s
            """, (collection_id, count))
            return [dict(r) for r in cur.fetchall()]


def delete_collection_video_items(collection_id: int, video_url: str) -> int:
    """Delete all knowledge items for a specific video in a collection."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM knowledge_items
                WHERE collection_id = %s AND video_url = %s
            """, (collection_id, video_url))
            conn.commit()
            return cur.rowcount


def get_quiz_schedules() -> list[dict]:
    ensure_collections_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT qs.*, c.name AS collection_name
                FROM quiz_schedules qs
                JOIN collections c ON c.id = qs.collection_id
                WHERE qs.enabled = TRUE
                ORDER BY qs.id
            """)
            return [dict(r) for r in cur.fetchall()]


def upsert_quiz_schedule(telegram_chat_id: str, collection_id: int,
                         interval_minutes: int, question_count: int) -> dict:
    ensure_collections_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO quiz_schedules
                    (telegram_chat_id, collection_id, interval_minutes, question_count)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (telegram_chat_id, collection_id) DO UPDATE
                    SET interval_minutes = EXCLUDED.interval_minutes,
                        question_count = EXCLUDED.question_count,
                        enabled = TRUE
                RETURNING *
            """, (telegram_chat_id, collection_id, interval_minutes, question_count))
            conn.commit()
            return dict(cur.fetchone())


def update_quiz_last_sent(schedule_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE quiz_schedules SET last_sent_at = now() WHERE id = %s",
                (schedule_id,),
            )
        conn.commit()


def disable_quiz_schedule(telegram_chat_id: str, collection_id: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE quiz_schedules SET enabled = FALSE
                WHERE telegram_chat_id = %s AND collection_id = %s
            """, (telegram_chat_id, collection_id))
            conn.commit()
            return cur.rowcount > 0
