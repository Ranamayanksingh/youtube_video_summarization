"""
Builds and maintains data/knowledge/<CollectionName>.md files from extracted DB items.

Each collection gets one master .md file that grows with every new video.
The file is structured with sections per item type so it can be used directly
as LLM context or read by humans.

Usage:
    python -m app.knowledge.builder --collection "SSC CGL Maths"
    python -m app.knowledge.builder --all
"""
import argparse
import datetime
import os

from dotenv import load_dotenv

from app.db import (
    ensure_collections_tables,
    get_collections,
    get_collection_by_name,
    get_knowledge_items,
    get_collection_videos,
)

load_dotenv()

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KNOWLEDGE_DIR = os.path.join(_PROJECT_ROOT, "data", "knowledge")

_TYPE_META = {
    "formula":      ("📐 Formulas & Rules",     "formula"),
    "question":     ("❓ Practice Questions",    "question"),
    "trick":        ("⚡ Shortcuts & Tricks",     "trick"),
    "concept":      ("📖 Concepts & Theory",     "concept"),
    "tool":         ("🛠️ Tools & Libraries",     "tool"),
    "code_pattern": ("💻 Code Patterns",         "code_pattern"),
    "project_idea": ("💡 Project Ideas",         "project_idea"),
}

_TYPE_ORDER = ["formula", "question", "trick", "concept", "tool", "code_pattern", "project_idea"]


def _format_item(item: dict, index: int) -> str:
    """Format a single knowledge item as markdown."""
    lines = []
    item_type = item.get("item_type", "")
    content = item.get("content", "").strip()
    answer = item.get("answer", "").strip()
    topic = item.get("topic", "").strip()
    video_title = item.get("video_title", "").strip()

    if item_type == "question":
        lines.append(f"**Q{index}.** {content}")
        if answer:
            lines.append(f"> **Answer:** {answer}")
    elif item_type in ("formula", "trick"):
        lines.append(f"**{index}.** {content}")
    elif item_type == "code_pattern":
        lines.append(f"**{index}. {topic or 'Pattern'}**")
        if any(c in content for c in ["()", "import", "def ", "=", "->", "::"]):
            lines.append(f"```\n{content}\n```")
        else:
            lines.append(content)
    else:
        lines.append(f"**{index}. {topic or 'Item'}**")
        lines.append(content)

    if video_title and item_type != "question":
        lines.append(f"*Source: {video_title}*")

    return "\n".join(lines)


def build_knowledge_file(collection_name: str) -> str:
    """
    Build (or rebuild) the knowledge file for a collection.
    Returns the path to the written file.
    """
    ensure_collections_tables()

    collection = get_collection_by_name(collection_name)
    if not collection:
        raise ValueError(f"Collection '{collection_name}' not found.")

    collection_id = collection["id"]
    all_items = get_knowledge_items(collection_id)
    videos = get_collection_videos(collection_id)

    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

    safe_name = collection_name.replace("/", "-").replace("\\", "-")
    file_path = os.path.join(KNOWLEDGE_DIR, f"{safe_name}.md")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    total_items = len(all_items)
    video_count = len(videos)

    lines = []

    lines.append(f"# {collection_name} — Knowledge Base")
    lines.append(f"")
    lines.append(f"**Goal:** {collection.get('goal_type', '').replace('_', ' ').title()}  ")
    lines.append(f"**Description:** {collection.get('description', '')}  ")
    lines.append(f"**Last updated:** {now}  ")
    lines.append(f"**Videos processed:** {video_count}  ")
    lines.append(f"**Total items:** {total_items}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if videos:
        lines.append("## 📺 Videos in this Collection")
        lines.append("")
        for i, v in enumerate(videos, 1):
            title = v.get("title") or "Untitled"
            url = v.get("video_url", "")
            date = v.get("created_at")
            date_str = date.strftime("%Y-%m-%d") if date else ""
            status = "✅" if v.get("extraction_done") else "⏳"
            if url:
                lines.append(f"{i}. {status} [{title}]({url}) — {date_str}")
            else:
                lines.append(f"{i}. {status} {title} — {date_str}")
        lines.append("")
        lines.append("---")
        lines.append("")

    items_by_type: dict[str, list[dict]] = {t: [] for t in _TYPE_ORDER}
    for item in all_items:
        t = item.get("item_type", "concept")
        if t in items_by_type:
            items_by_type[t].append(item)

    for item_type in _TYPE_ORDER:
        items = items_by_type[item_type]
        if not items:
            continue

        section_title, _ = _TYPE_META.get(item_type, (item_type.title(), item_type))
        lines.append(f"## {section_title}")
        lines.append("")

        topics: dict[str, list[dict]] = {}
        for item in items:
            topic = item.get("topic", "").strip() or "General"
            topics.setdefault(topic, []).append(item)

        global_index = 1
        for topic, topic_items in sorted(topics.items()):
            if len(topics) > 1:
                lines.append(f"### {topic}")
                lines.append("")
            for item in topic_items:
                lines.append(_format_item(item, global_index))
                lines.append("")
                global_index += 1

        lines.append("---")
        lines.append("")

    content = "\n".join(lines)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[KNOWLEDGE] Built: {file_path}")
    print(f"[KNOWLEDGE] {video_count} videos | {total_items} items")
    return file_path


def build_all() -> list[str]:
    """Rebuild knowledge files for all collections."""
    ensure_collections_tables()
    collections = get_collections()
    if not collections:
        print("[KNOWLEDGE] No collections found.")
        return []

    paths = []
    for c in collections:
        try:
            path = build_knowledge_file(c["name"])
            paths.append(path)
        except Exception as e:
            print(f"[KNOWLEDGE] Error building '{c['name']}': {e}")
    return paths


def get_knowledge_file_path(collection_name: str) -> str:
    """Return expected path to a collection's knowledge file (may not exist yet)."""
    safe_name = collection_name.replace("/", "-").replace("\\", "-")
    return os.path.join(KNOWLEDGE_DIR, f"{safe_name}.md")


def read_knowledge_file(collection_name: str) -> str | None:
    """Read and return the content of a collection's knowledge file, or None if missing."""
    path = get_knowledge_file_path(collection_name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build knowledge .md files from extracted collection items.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--collection", help="Collection name to build")
    group.add_argument("--all", action="store_true", help="Rebuild all collections")
    args = parser.parse_args()

    if args.all:
        paths = build_all()
        print(f"\n✅ Rebuilt {len(paths)} knowledge file(s).")
    else:
        path = build_knowledge_file(args.collection)
        print(f"\n✅ Knowledge file: {path}")
