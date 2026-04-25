"""
Q&A engine that answers questions using a collection's knowledge base as context.

Instead of answering from a single video's transcript, this answers from the
entire accumulated knowledge of a collection — all extracted formulas, concepts,
questions, and tricks from every video ever added.

Usage:
    python -m app.knowledge.qa --collection "SSC CGL Maths" --question "What is the formula for compound interest?"
"""
import argparse

from dotenv import load_dotenv

from app.db import get_collection_by_name, get_knowledge_items, ensure_collections_tables
from app.knowledge.builder import read_knowledge_file
from app.pipeline.summarizer import _llm_chat, DEFAULT_MODEL

load_dotenv()

_MAX_CONTEXT_CHARS = 14000

_QA_PROMPT = """\
You are a knowledgeable assistant helping a student/learner.
Below is their personal knowledge base — compiled from educational videos they have watched.

Use ONLY the knowledge base below to answer the question.
If the answer is not present in the knowledge base, say: "I don't have information on this in your collection yet. Try adding more videos on this topic."

Be direct and precise. For exam questions, give the answer and brief explanation.
For conceptual questions, explain clearly. For formulas, state the formula and a usage example.

---
KNOWLEDGE BASE ({collection_name}):
{context}
---

Question: {question}
"""

_PROJECT_QA_PROMPT = """\
You are a technical mentor helping a developer learn and build projects.
Below is their personal knowledge base — compiled from educational videos they have watched.

Use the knowledge base below to answer the question.
Connect concepts where relevant. If the answer isn't in the knowledge base, say so and suggest what topics to study next.

---
KNOWLEDGE BASE ({collection_name}):
{context}
---

Question: {question}
"""

_SUGGEST_PROJECT_PROMPT = """\
You are a technical mentor. Based on the knowledge base below (compiled from educational videos the learner has watched),
suggest 3 concrete project ideas they could build RIGHT NOW with what they've learned.

For each project:
- Project name
- What they'll build
- Which concepts/tools from their knowledge base it uses
- Difficulty: Beginner / Intermediate / Advanced

---
KNOWLEDGE BASE ({collection_name}):
{context}
---

Suggest 3 projects:
"""


def _build_context(collection_name: str, max_chars: int = _MAX_CONTEXT_CHARS) -> str:
    """
    Build a context string from the collection's knowledge file.
    Falls back to DB items if the file doesn't exist.
    """
    content = read_knowledge_file(collection_name)
    if content:
        return content[:max_chars]

    collection = get_collection_by_name(collection_name)
    if not collection:
        return ""

    items = get_knowledge_items(collection["id"])
    lines = [f"Collection: {collection_name}", ""]
    for item in items:
        item_type = item.get("item_type", "")
        topic = item.get("topic", "")
        content_text = item.get("content", "")
        answer = item.get("answer", "")
        line = f"[{item_type.upper()}] ({topic}) {content_text}"
        if answer:
            line += f" | Answer: {answer}"
        lines.append(line)

    return "\n".join(lines)[:max_chars]


def answer_question(
    collection_name: str,
    question: str,
    model: str = DEFAULT_MODEL,
    lang: str = "english",
) -> str:
    """
    Answer a question using the collection's knowledge base as context.
    Returns the LLM response string.
    """
    ensure_collections_tables()

    collection = get_collection_by_name(collection_name)
    if not collection:
        return f"Collection '{collection_name}' not found."

    context = _build_context(collection_name)
    if not context.strip():
        return (
            f"Your '{collection_name}' collection is empty. "
            "Add some videos to it first so I can build your knowledge base."
        )

    goal_type = collection.get("goal_type", "exam_prep")
    prompt_template = _PROJECT_QA_PROMPT if goal_type == "project_build" else _QA_PROMPT

    lang_note = " Answer in Hindi." if lang == "hindi" else ""
    prompt = prompt_template.format(
        collection_name=collection_name,
        context=context,
        question=question,
    ) + lang_note

    return _llm_chat(model, prompt)


def suggest_projects(
    collection_name: str,
    model: str = DEFAULT_MODEL,
) -> str:
    """
    Suggest project ideas based on what the user has learned in the collection.
    Only meaningful for project_build goal type.
    """
    ensure_collections_tables()

    collection = get_collection_by_name(collection_name)
    if not collection:
        return f"Collection '{collection_name}' not found."

    context = _build_context(collection_name)
    if not context.strip():
        return "Your collection is empty. Add some videos first."

    prompt = _SUGGEST_PROJECT_PROMPT.format(
        collection_name=collection_name,
        context=context,
    )
    return _llm_chat(model, prompt)


def get_collection_stats(collection_name: str) -> dict:
    """Return item counts by type for a collection."""
    ensure_collections_tables()
    collection = get_collection_by_name(collection_name)
    if not collection:
        return {}

    items = get_knowledge_items(collection["id"])
    from collections import Counter
    counts = Counter(item.get("item_type") for item in items)
    return {
        "total": len(items),
        "by_type": dict(counts),
        "collection": collection_name,
        "goal_type": collection.get("goal_type"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Answer questions using a collection's knowledge base.")
    parser.add_argument("--collection", required=True, help="Collection name")
    parser.add_argument("--question", help="Question to answer")
    parser.add_argument("--suggest-projects", action="store_true", help="Suggest project ideas")
    parser.add_argument("--stats", action="store_true", help="Show collection item counts")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    if args.stats:
        stats = get_collection_stats(args.collection)
        print(f"\nCollection: {stats.get('collection')} ({stats.get('goal_type')})")
        print(f"Total items: {stats.get('total', 0)}")
        for t, c in sorted(stats.get("by_type", {}).items()):
            print(f"  {t}: {c}")
    elif args.suggest_projects:
        print(suggest_projects(args.collection, model=args.model))
    elif args.question:
        answer = answer_question(args.collection, args.question, model=args.model)
        print(f"\n{answer}")
    else:
        parser.print_help()
