"""
Structured knowledge extraction from video transcripts/summaries.

Given a transcript (or summary) and a collection's goal_type + extract_focus,
asks the LLM to pull out structured items (formulas, questions, tricks, concepts,
tools, code_patterns, project_ideas) and stores them in the DB.

Usage (CLI):
    python -m app.pipeline.extractor --collection "SSC CGL Maths" --transcript data/downloads/video.txt --url "https://..." --title "Video Title"
"""
import argparse
import json
import os
import re

from dotenv import load_dotenv

from app.db import (
    add_knowledge_items,
    add_collection_video,
    get_collection_by_name,
    ensure_collections_tables,
    mark_collection_video_extracted,
)
from app.pipeline.summarizer import _llm_chat, DEFAULT_MODEL

load_dotenv()

# ---------------------------------------------------------------------------
# Extraction prompt templates per goal_type
# ---------------------------------------------------------------------------

_EXAM_PREP_PROMPT = """\
You are an expert exam preparation assistant. Analyze the transcript below from an educational YouTube video and extract structured knowledge items for exam preparation.

Extract ALL of the following that appear in the transcript:

1. FORMULAS — Any mathematical formulas, equations, or rules stated (e.g. "SI = PRT/100")
2. QUESTIONS — Any practice questions, example problems, or MCQ-style questions asked in the video. Include the answer if given.
3. TRICKS — Any shortcuts, mnemonics, quick methods, or tips for solving problems faster
4. CONCEPTS — Key topics or concepts explained (topic name + brief explanation)

Return ONLY a valid JSON object. No explanation outside the JSON. Format:
{{
  "topics_covered": ["Topic 1", "Topic 2"],
  "items": [
    {{"type": "formula", "topic": "Percentage", "content": "Percentage = (Part/Whole) × 100", "answer": ""}},
    {{"type": "question", "topic": "Profit & Loss", "content": "If SP = Rs 120 and CP = Rs 100, find profit %", "answer": "20%"}},
    {{"type": "trick", "topic": "Time & Work", "content": "If A does work in X days and B in Y days, together they finish in XY/(X+Y) days", "answer": ""}},
    {{"type": "concept", "topic": "Simple Interest", "content": "Simple Interest is calculated only on the principal amount, not on accumulated interest", "answer": ""}}
  ]
}}

Transcript:
{text}
"""

_PROJECT_BUILD_PROMPT = """\
You are an expert technical learning assistant. Analyze the transcript below from an educational YouTube video about technology/AI/programming and extract structured knowledge items useful for building a project.

Extract ALL of the following that appear in the transcript:

1. CONCEPTS — Core ideas, algorithms, or techniques explained (name + clear explanation)
2. TOOLS — Libraries, frameworks, APIs, or services mentioned (name + what it does)
3. CODE_PATTERNS — Any code snippets, pseudocode, architecture patterns, or implementation approaches described
4. PROJECT_IDEAS — Any project suggestions, use cases, or application ideas mentioned

Return ONLY a valid JSON object. No explanation outside the JSON. Format:
{{
  "topics_covered": ["Topic 1", "Topic 2"],
  "items": [
    {{"type": "concept", "topic": "RAG", "content": "Retrieval-Augmented Generation combines a retrieval system with an LLM to ground responses in real documents", "answer": ""}},
    {{"type": "tool", "topic": "Vector DB", "content": "ChromaDB — open source vector database for storing and querying embeddings, easy local setup", "answer": ""}},
    {{"type": "code_pattern", "topic": "Embeddings", "content": "Use sentence-transformers to embed text: model.encode(['text here']) returns a numpy array", "answer": ""}},
    {{"type": "project_idea", "topic": "RAG", "content": "Build a personal document Q&A system using LangChain + ChromaDB + local Ollama", "answer": ""}}
  ]
}}

Transcript:
{text}
"""

_QUIZ_PRACTICE_PROMPT = """\
You are an expert medical/competitive exam coach. Analyze the transcript below from an educational YouTube video and extract structured knowledge items for intensive quiz practice.

Extract ALL of the following that appear in the transcript:

1. QUESTIONS — Every single question, MCQ, case study, or example problem mentioned. Include options if given, and the correct answer.
2. CONCEPTS — Key terms, definitions, classifications, or facts explained
3. FORMULAS — Any dosages, ratios, measurements, classification rules, or numerical facts

Return ONLY a valid JSON object. No explanation outside the JSON. Format:
{{
  "topics_covered": ["Topic 1", "Topic 2"],
  "items": [
    {{"type": "question", "topic": "Tridosha", "content": "Which dosha is responsible for movement and nervous system function?\\n(A) Vata\\n(B) Pitta\\n(C) Kapha\\n(D) None", "answer": "(A) Vata — governs all movement, nerve impulses, and circulation"}},
    {{"type": "concept", "topic": "Pancha Mahabhuta", "content": "The five great elements: Akasha (space), Vayu (air), Agni (fire), Jala (water), Prithvi (earth)", "answer": ""}},
    {{"type": "formula", "topic": "Dosage", "content": "Churna (powder) dose: 3-6 grams with honey or warm water", "answer": ""}}
  ]
}}

Transcript:
{text}
"""

_PROMPT_BY_GOAL = {
    "exam_prep":     _EXAM_PREP_PROMPT,
    "project_build": _PROJECT_BUILD_PROMPT,
    "quiz_practice": _QUIZ_PRACTICE_PROMPT,
}

_MAX_TRANSCRIPT_CHARS = 12000


def _parse_llm_json(raw: str) -> dict:
    """Extract and parse JSON from LLM response, stripping markdown fences."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def extract_knowledge(
    transcript_text: str,
    goal_type: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Call the LLM to extract structured knowledge from a transcript.

    Returns a dict with keys: topics_covered (list), items (list of dicts).
    Each item has: type, topic, content, answer.
    """
    prompt_template = _PROMPT_BY_GOAL.get(goal_type, _EXAM_PREP_PROMPT)

    text = transcript_text[:_MAX_TRANSCRIPT_CHARS]
    if len(transcript_text) > _MAX_TRANSCRIPT_CHARS:
        text += "\n\n[Transcript truncated for length]"

    prompt = prompt_template.format(text=text)
    raw = _llm_chat(model, prompt)

    try:
        data = _parse_llm_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[EXTRACT] Warning: could not parse LLM JSON response: {e}")
        print(f"[EXTRACT] Raw response was:\n{raw[:500]}")
        return {"topics_covered": [], "items": []}

    if "items" not in data:
        data["items"] = []
    if "topics_covered" not in data:
        data["topics_covered"] = []

    valid_types = {"formula", "question", "trick", "concept", "tool", "code_pattern", "project_idea"}
    data["items"] = [
        item for item in data["items"]
        if isinstance(item, dict) and item.get("content", "").strip()
        and item.get("type", "") in valid_types
    ]

    return data


def extract_and_store(
    collection_name: str,
    transcript_path: str,
    summary_path: str,
    video_url: str,
    video_title: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Full extraction pipeline:
      1. Load transcript
      2. Call LLM for structured extraction
      3. Store items in DB
      4. Return extraction result

    Returns dict with keys: items_count, topics_covered, items
    """
    ensure_collections_tables()

    collection = get_collection_by_name(collection_name)
    if not collection:
        raise ValueError(f"Collection '{collection_name}' not found. Create it first.")

    collection_id = collection["id"]
    goal_type = collection["goal_type"]

    add_collection_video(
        collection_id=collection_id,
        video_url=video_url,
        title=video_title,
        summary_path=summary_path,
        transcript_path=transcript_path,
    )

    if not os.path.exists(transcript_path):
        raise FileNotFoundError(f"Transcript not found: {transcript_path}")

    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript_text = f.read().strip()

    if not transcript_text:
        print(f"[EXTRACT] Empty transcript: {transcript_path}")
        return {"items_count": 0, "topics_covered": [], "items": []}

    print(f"[EXTRACT] Extracting from: {video_title}")
    print(f"[EXTRACT] Collection: {collection_name} (goal: {goal_type})")

    result = extract_knowledge(transcript_text, goal_type, model)

    items_count = add_knowledge_items(
        collection_id=collection_id,
        video_url=video_url,
        video_title=video_title,
        items=result["items"],
    )

    mark_collection_video_extracted(collection_id, video_url)

    print(f"[EXTRACT] Stored {items_count} items | Topics: {', '.join(result['topics_covered'])}")
    _print_item_summary(result["items"])

    result["items_count"] = items_count
    return result


def _print_item_summary(items: list[dict]) -> None:
    from collections import Counter
    counts = Counter(item.get("type") for item in items)
    for item_type, count in sorted(counts.items()):
        print(f"  {item_type}: {count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract structured knowledge from a transcript into a collection.")
    parser.add_argument("--collection", required=True, help="Collection name (must exist in DB)")
    parser.add_argument("--transcript", required=True, help="Path to the .txt transcript file")
    parser.add_argument("--summary", default="", help="Path to the .summary.txt file (optional)")
    parser.add_argument("--url", default="", help="YouTube video URL")
    parser.add_argument("--title", default="", help="Video title")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    result = extract_and_store(
        collection_name=args.collection,
        transcript_path=args.transcript,
        summary_path=args.summary,
        video_url=args.url,
        video_title=args.title or os.path.basename(args.transcript),
        model=args.model,
    )
    print(f"\n✅ Extraction complete. {result['items_count']} items stored.")
