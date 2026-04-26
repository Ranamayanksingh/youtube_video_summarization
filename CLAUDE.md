# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Python pipeline that downloads audio from YouTube videos, transcribes it to English (supports Hindi and English audio), and summarizes the content. Has three interfaces: CLI, Telegram bot, and FastAPI web UI. Designed for Apple Silicon (M-series) Macs.

## Setup

```bash
bash setup.sh        # one-time: installs all system deps, creates .venv, verifies
source .venv/bin/activate
```

Copy `example_env` to `.env` and fill in values. Secrets (bot token, web auth key) are stored in Postgres `app_secrets` table — seed them via `psql` or `db_seed.py`. The `.env` only needs Postgres credentials and `BOT_ACCESS_KEY`/`GROQ_API_KEY`.

System requirements: FFmpeg, Node.js (yt-dlp JS challenges), Google Chrome (cookie extraction), Postgres running locally.

## Running the Services

```bash
# Telegram bot
python telegram_bot.py

# Web UI
uvicorn web_app:app --host 0.0.0.0 --port 8000

# CLI pipeline
python main.py video "<youtube_video_url>"
python main.py channel "<youtube_channel_url>"
```

## Individual Pipeline Modules

```bash
python -m app.pipeline.downloader "<url>"                        # download audio as WAV
python -m app.utils.youtube "<channel_url>"                      # print latest video URL
python -m app.pipeline.transcriber [--overwrite] [--dir data/downloads]
python -m app.pipeline.summarizer [--overwrite] [--model MODEL] [--summaries-dir data/summaries]
```

## Architecture

All code lives under the `app/` package. Three thin root shims (`main.py`, `telegram_bot.py`, `web_app.py`) exist only for launchd/uvicorn compatibility — don't add logic to them.

```
app/
  pipeline/
    downloader.py   →  yt-dlp + FFmpeg to download audio as WAV (was script.py)
    transcriber.py  →  Groq Whisper API (primary) or mlx-whisper fallback (was transcribe.py)
    summarizer.py   →  Groq LLM or Ollama; writes .summary.txt (was summarize.py)
    extractor.py    →  LLM extracts knowledge items from transcripts (was extract.py)

  knowledge/
    builder.py      →  Compiles DB items into data/knowledge/<Name>.md (was knowledge_builder.py)
    qa.py           →  Answers questions from collection knowledge base (was qa_engine.py)
    quiz.py         →  Sends random quiz questions via Telegram (was quiz_scheduler.py)

  db/
    core.py         →  get_conn(), get_secret(), set_secret()
    history.py      →  user_video_history table
    subscriptions.py→  yt_subscriptions table
    users.py        →  allowed_telegram_users table
    collections.py  →  collections/knowledge_items/quiz_schedules tables
    __init__.py     →  re-exports all public symbols (import from app.db directly)

  interfaces/
    cli.py          →  CLI entry point composing the four stages (was main.py)
    bot.py          →  Conversational Telegram bot (was telegram_bot.py)
    web.py          →  FastAPI UI with SSE job streaming, Q&A, subscriptions (was web_app.py)

  utils/
    youtube.py      →  get_latest_video() + get_all_videos_with_dates() (merged two files)
    notifications.py→  send_telegram_message() + extract_highlights() (was subscriptions.py)
    services.py     →  (unused — services.py kept at root for launchd)

data/               →  runtime data (gitignored)
  downloads/        →  transcripts (.txt); WAVs deleted after transcription
  summaries/        →  .summary.txt files
  knowledge/        →  compiled .md files per collection
  cookies.txt       →  YouTube auth cookies (auto-refreshed every 3 days)

tests/
  test_pipeline.py  →  integration tests: imports, cookies, download, transcribe, summarize, bot_features
```

**Key design decisions:**

- **LLM backend**: `app/pipeline/summarizer.py._llm_chat()` uses Groq (`llama-3.3-70b-versatile`) when `GROQ_API_KEY` is set, otherwise falls back to local Ollama. `DEFAULT_MODEL` in `summarizer.py` is the Groq model name.
- **Secrets storage**: All runtime secrets (`TELEGRAM_BOT_TOKEN`, `WEB_AUTH_TOKEN`, `SECRET_KEY`) live in Postgres `app_secrets` table, fetched via `app.db.get_secret()`. Env vars are a fallback only.
- **Concurrency**: Both `app/interfaces/bot.py` and `app/interfaces/web.py` use a single `asyncio.Semaphore(1)` named `PIPELINE_SEMAPHORE` — Whisper and LLM are single-instance; all pipeline calls use `asyncio.to_thread()`.
- **Subscriptions**: `app/interfaces/web.py` runs `_subscription_scheduler()` as a background task on startup. It wakes every minute, checks `yt_subscriptions` table for matching `HH:MM` IST run times, and dispatches the pipeline + Telegram notification. Data is in Postgres, not `subscriptions.json`.
- **Web auth**: Cookie-based with `itsdangerous.TimestampSigner`; 30-day expiry. Bearer token supported for API/curl access. `WEB_AUTH_TOKEN` from `app_secrets`.
- **Bot access control**: Two layers — `BOT_ACCESS_KEY` (shared password in `.env`) and `allowed_telegram_users` Postgres table (per-user allowlist managed via `/allowed-users` web route).
- **Transcription**: Always `task="translate"` so both Hindi and English audio produces English transcripts.
- **Templates**: Jinja2 templates in `templates/`, partials in `templates/partials/`. HTMX is used for dynamic updates (job rows, Q&A, suggestions).

## Database Schema (Postgres)

| Table | Purpose |
|---|---|
| `app_secrets` | Key-value store for bot token, web auth token, secret key |
| `yt_subscriptions` | Per-user channel subscriptions with scheduled run time |
| `allowed_telegram_users` | Allowlist for Telegram bot access |
| `user_video_history` | Per-user history of summarized videos (paths + metadata) |

## Output Locations

| File | Location |
|---|---|
| Audio (WAV) | `data/downloads/<title>.wav` — deleted after transcription |
| Transcript | `data/downloads/<title>.txt` |
| Summary | `data/summaries/<title>.summary.txt` |
| Knowledge | `data/knowledge/<CollectionName>.md` |
| Cookies | `data/cookies.txt` |

## Models

- **Transcription**: `mlx-community/whisper-large-v3-mlx` — cached at `~/.cache/huggingface/` after first run
- **Summarization**: Groq `llama-3.3-70b-versatile` (preferred) or local Ollama `llama3`

## Service Management (macOS launchd)

`services.py` replaces `restart_bot.sh` and `scheduler.py` for persistent service management. Services survive terminal close, lid close, and reboots.

```bash
python services.py start all          # start bot + web, register with launchd
python services.py stop all
python services.py restart bot
python services.py status             # check running state + PID
python services.py logs bot           # tail bot log
python services.py logs web           # tail web log
python services.py uninstall all      # remove launchd plists

# Legacy (still works):
python scheduler.py install --channel "<channel_url>"  # launchd job for 7 AM IST daily pipeline
./restart_bot.sh                                       # simple background restart
```

## Collections & Knowledge System

A second major feature layered on top of the pipeline: group videos into named **Collections**, extract structured knowledge items, and run scheduled quizzes via Telegram.

```
app/pipeline/extractor.py   →  LLM extracts formulas/questions/tricks/concepts → knowledge_items table
app/knowledge/builder.py    →  Compiles all DB items into data/knowledge/<Name>.md (used as LLM context)
app/knowledge/qa.py         →  Answers questions from a collection's knowledge base (collection-scoped RAG)
app/knowledge/quiz.py       →  Sends random questions to Telegram, driven by quiz_schedules table
```

**Collection goal types** determine what gets extracted and how Q&A prompts are framed:
- `exam_prep` — formulas, questions, tricks, concepts (SSC/competitive exams)
- `project_build` — concepts, tools, code_patterns, project_ideas (tech learning)
- `quiz_practice` — heavy on questions, used for medical/intensive practice

```bash
# Manual extraction after pipeline runs
python -m app.pipeline.extractor --collection "SSC CGL Maths" --transcript data/downloads/video.txt --url "<url>" --title "Title"

# Rebuild knowledge file from DB
python -m app.knowledge.builder --collection "SSC CGL Maths"
python -m app.knowledge.builder --all

# Q&A from knowledge base
python -m app.knowledge.qa --collection "SSC CGL Maths" --question "What is compound interest formula?"
python -m app.knowledge.qa --collection "My Tech Collection" --suggest-projects
python -m app.knowledge.qa --collection "SSC CGL Maths" --stats

# Send quiz to Telegram
python -m app.knowledge.quiz --collection "SSC CGL Maths" --chat-id 123456789 --count 10
```

**Additional DB tables** (auto-created by `app.db.ensure_collections_tables()`):

| Table | Purpose |
|---|---|
| `collections` | Named collections with `goal_type`, `description`, `extract_focus` |
| `collection_videos` | Videos belonging to a collection + extraction status |
| `knowledge_items` | Extracted items: `formula`, `question`, `trick`, `concept`, `tool`, `code_pattern`, `project_idea` |
| `quiz_schedules` | Per-chat interval-based quiz delivery config |

**Knowledge files**: `data/knowledge/<CollectionName>.md` — compiled markdown used as context for Q&A. Rebuilt by `app/knowledge/builder.py`; read by `app/knowledge/qa._build_context()`.

**Quiz scheduling**: `app/interfaces/web.py`'s background scheduler also calls `quiz.run_scheduled_quizzes()` every minute, firing quizzes when `elapsed >= interval_minutes`.

## Utility Modules

- **`app/db/`**: All Postgres access split by domain. `app/db/core.py` has `get_conn()` and `get_secret()`; `app/db/__init__.py` re-exports everything. All tables have `ensure_*_tables()` auto-create guards.
- **`app/utils/notifications.py`**: `extract_highlights()` (Ollama morning briefing) + `send_telegram_message()` (chunked async Telegram HTTP).
- **`app/utils/youtube.py`**: `get_latest_video()` + `get_all_videos_with_dates()` — yt-dlp metadata utilities.

## Key Implementation Notes

- **Cookie management**: `app/pipeline/downloader.py` caches cookies to `data/cookies.txt` for 3 days before re-extracting from Chrome Keychain. If downloads fail with 403, delete `data/cookies.txt` to force refresh.
- **Subscription scheduler**: Runs as a background asyncio task inside `app/interfaces/web.py` (not a separate process). Checks `yt_subscriptions` table every minute against current IST time.
- **Job cancellation**: Web UI jobs can be cancelled mid-pipeline via `cancel_event` on the `JobState` dataclass; Telegram bot uses `PIPELINE_TASKS` dict to track per-user tasks.
- **History storage**: Paths in `user_video_history` are relative paths prefixed with `data/` (e.g. `data/downloads/Title.txt`).
- **`subscriptions.json`**: Legacy file; current data lives in Postgres `yt_subscriptions` table.
- **`allowed_telegram_users` open-access rule**: If the table is empty, all users are allowed (see `db.is_telegram_user_allowed()`). Add at least one user to enforce the allowlist.
