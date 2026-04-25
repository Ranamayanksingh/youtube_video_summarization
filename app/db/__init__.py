"""
Public re-export of all DB helpers.

Import from here: `from app.db import get_conn, get_secret, ...`
All symbols from the sub-modules are available at this level.
"""
from app.db.core import get_conn, get_secret, set_secret
from app.db.history import (
    ensure_history_table,
    add_video_history,
    delete_user_history,
    get_user_history,
)
from app.db.subscriptions import (
    load_subscriptions,
    add_subscription,
    remove_subscription,
)
from app.db.users import (
    ensure_allowed_users_table,
    get_allowed_users,
    add_allowed_user,
    remove_allowed_user,
    is_telegram_user_allowed,
)
from app.db.collections import (
    ensure_collections_tables,
    get_collections,
    get_collection_by_name,
    get_collection_by_id,
    create_collection,
    delete_collection,
    add_collection_video,
    mark_collection_video_extracted,
    get_collection_videos,
    add_knowledge_items,
    get_knowledge_items,
    get_random_questions,
    delete_collection_video_items,
    get_quiz_schedules,
    upsert_quiz_schedule,
    update_quiz_last_sent,
    disable_quiz_schedule,
)

__all__ = [
    "get_conn", "get_secret", "set_secret",
    "ensure_history_table", "add_video_history",
    "delete_user_history", "get_user_history",
    "load_subscriptions", "add_subscription", "remove_subscription",
    "ensure_allowed_users_table", "get_allowed_users",
    "add_allowed_user", "remove_allowed_user", "is_telegram_user_allowed",
    "ensure_collections_tables",
    "get_collections", "get_collection_by_name", "get_collection_by_id",
    "create_collection", "delete_collection",
    "add_collection_video", "mark_collection_video_extracted",
    "get_collection_videos",
    "add_knowledge_items", "get_knowledge_items", "get_random_questions",
    "delete_collection_video_items",
    "get_quiz_schedules", "upsert_quiz_schedule",
    "update_quiz_last_sent", "disable_quiz_schedule",
]
