# Architecture & Module Guide

A deep-dive into the folder structure, how each module works, and how they connect.

---

## Table of Contents

1. [Folder Structure](#1-folder-structure)
2. [How the Modules Connect](#2-how-the-modules-connect)
3. [Core Pipeline: Audio → Transcript → Summary](#3-core-pipeline-audio--transcript--summary)
   - [Downloader](#31-downloader)
   - [Transcriber](#32-transcriber)
   - [Summarizer](#33-summarizer)
   - [Extractor](#34-extractor)
4. [Interfaces: Three Ways to Use the System](#4-interfaces-three-ways-to-use-the-system)
   - [CLI](#41-cli)
   - [Telegram Bot](#42-telegram-bot)
   - [Web UI](#43-web-ui)
5. [Knowledge System](#5-knowledge-system)
6. [Database Layer](#6-database-layer)
7. [Utilities](#7-utilities)
8. [Data Flow Walkthroughs](#8-data-flow-walkthroughs)
9. [Key Architectural Patterns](#9-key-architectural-patterns)

---

## 1. Folder Structure

```
youtube-video-audio-data/
│
├── app/                        ← All application logic lives here
│   ├── pipeline/               ← The 4-stage processing pipeline
│   │   ├── downloader.py       ← Stage 1: YouTube → WAV audio
│   │   ├── transcriber.py      ← Stage 2: WAV → text transcript
│   │   ├── summarizer.py       ← Stage 3: transcript → summary
│   │   └── extractor.py        ← Stage 4 (optional): transcript → structured knowledge
│   │
│   ├── interfaces/             ← User-facing entry points
│   │   ├── cli.py              ← Command-line interface
│   │   ├── bot.py              ← Telegram conversational bot
│   │   └── web.py              ← FastAPI web UI + REST API
│   │
│   ├── db/                     ← All PostgreSQL access, split by domain
│   │   ├── core.py             ← get_conn(), get_secret(), set_secret()
│   │   ├── history.py          ← user_video_history table
│   │   ├── subscriptions.py    ← yt_subscriptions table
│   │   ├── users.py            ← allowed_telegram_users table
│   │   ├── collections.py      ← collections, knowledge_items, quiz_schedules tables
│   │   └── __init__.py         ← Re-exports everything; always import from app.db
│   │
│   ├── knowledge/              ← Knowledge base features (built on top of pipeline)
│   │   ├── builder.py          ← Compile DB items → markdown knowledge file
│   │   ├── qa.py               ← Answer questions using knowledge base as context
│   │   └── quiz.py             ← Format + send quiz questions via Telegram
│   │
│   └── utils/
│       ├── youtube.py          ← yt-dlp metadata (get_latest_video, get_all_videos_with_dates)
│       └── notifications.py    ← send_telegram_message() + extract_highlights()
│
├── templates/                  ← Jinja2 HTML templates for the web UI
│   ├── base.html, login.html, index.html, ...
│   └── partials/               ← HTMX partial templates (job rows, Q&A responses)
│
├── data/                       ← Runtime data (gitignored)
│   ├── downloads/              ← WAV files (deleted after transcription) + .txt transcripts
│   ├── summaries/              ← .summary.txt files
│   ├── knowledge/              ← <CollectionName>.md files compiled by builder.py
│   └── cookies.txt             ← YouTube auth cookies (auto-refreshed every 3 days)
│
├── main.py                     ← Thin shim → app/interfaces/cli.py
├── telegram_bot.py             ← Thin shim → app/interfaces/bot.py
└── web_app.py                  ← Thin shim → app/interfaces/web.py (for uvicorn)
```

**Why thin root shims?** macOS launchd service definitions and `uvicorn web_app:app` both need a stable file at a known root path. All real logic is inside `app/` — the root files just re-export.

---

## 2. How the Modules Connect

```
                    ┌─────────────────────────────────────────┐
                    │            User Entry Points             │
                    │   CLI (main.py)  │  Bot  │  Web UI       │
                    └────────┬─────────┴───┬───┴────┬──────────┘
                             │             │        │
                    ┌────────▼─────────────▼────────▼──────────┐
                    │           app/interfaces/                 │
                    │   cli.py     bot.py      web.py           │
                    └────────┬─────────────────────┬───────────┘
                             │                     │
              ┌──────────────▼──────────────┐      │
              │       app/pipeline/          │      │
              │  downloader → transcriber    │      │
              │  → summarizer → extractor   │      │
              └──────────────┬──────────────┘      │
                             │                     │
         ┌───────────────────▼─────────────────────▼──────────┐
         │                   app/db/                           │
         │  core · history · subscriptions · users · collections │
         └───────────────────┬─────────────────────────────────┘
                             │
              ┌──────────────▼──────────────┐
              │      app/knowledge/          │
              │  builder · qa · quiz         │
              └─────────────────────────────┘
```

**Data connections:**
- **Interfaces** call pipeline stages directly and write results to DB
- **Pipeline** stages are stateless functions — they read/write files, not DB
- **DB** is the single source of truth for all persistent state
- **Knowledge** modules read from DB (builder) and from files (qa reads the compiled .md)
- **Utils** (`youtube.py`, `notifications.py`) are used by both interfaces and pipeline

---

## 3. Core Pipeline: Audio → Transcript → Summary

Every video goes through four stages. Stages 1–3 are always run; stage 4 is optional (only when adding to a Collection).

```
YouTube URL
     │
     ▼  Stage 1
downloader.py  →  data/downloads/<title>.wav
     │
     ▼  Stage 2
transcriber.py →  data/downloads/<title>.txt    (WAV deleted)
     │
     ▼  Stage 3
summarizer.py  →  data/summaries/<title>.summary.txt
     │
     ▼  Stage 4 (optional — only for Collections)
extractor.py   →  PostgreSQL knowledge_items table
                  + builder.py rebuilds data/knowledge/<Collection>.md
```

### 3.1 Downloader

**File:** `app/pipeline/downloader.py`
**Entry function:** `download_youtube_audio_as_wav(url, output_dir)`

**What it does:**

Uses **yt-dlp** to download the best available audio stream from YouTube, then **FFmpeg** to convert it to a 192kbps WAV file.

**Cookie management** is automatic:
- On first run, it extracts cookies from your Chrome browser (needed to pass YouTube's bot checks)
- Cookies are saved to `data/cookies.txt` and reused for 3 days
- After 3 days (or if you delete cookies.txt), it re-extracts from Chrome
- If downloads fail with 403 errors, deleting `data/cookies.txt` forces a refresh

**Output:** WAV file at `data/downloads/<title>.wav`
(The WAV is deleted automatically after transcription to save disk space)

---

### 3.2 Transcriber

**File:** `app/pipeline/transcriber.py`
**Entry function:** `transcribe_file(wav_path, ...)`

**What it does:**

Converts audio to text using Whisper. Always transcribes with `task="translate"` — this means **both Hindi and English audio produce English transcripts**.

**Four backends, automatic fallback chain:**

| Priority | Backend | Requires | Notes |
|---|---|---|---|
| 1 | **faster-whisper** (local) | Nothing (auto-downloads model) | `large-v3-turbo`, never hangs, best local quality |
| 2 | **AssemblyAI** (cloud) | `ASSEMBLYAI_API_KEY` | Best multilingual accuracy, 100 hrs/month free |
| 3 | **Groq Whisper API** (cloud) | `GROQ_API_KEY` | Fast cloud, 25MB chunk limit |
| 4 | **mlx-whisper** (local) | Nothing | Last resort — may hang on some audio |

**faster-whisper details:**
- Uses CTranslate2 engine — synchronous, never hangs
- Model: `large-v3-turbo` (~1.5 GB, downloaded once to `~/.cache/huggingface/`)
- `vad_filter=True` skips silent segments, improving accuracy
- Auto-detects source language; always outputs English (`task="translate"`)

**Chunking for large files (Groq only):**

Groq has a 25MB per-request limit. For long audio:
1. Calculate chunk duration so each chunk ≤ 20MB
2. Split with FFmpeg into segments
3. Transcribe each chunk sequentially
4. Join transcripts together

**Output:** Text file at `data/downloads/<title>.txt`

---

### 3.3 Summarizer

**File:** `app/pipeline/summarizer.py`
**Entry function:** `summarize_file(txt_path, summaries_dir, model, overwrite, prompt_template)`

**What it does:**

Reads the transcript and sends it to an LLM with a structured prompt, producing a formatted summary.

**LLM Backend Priority (Groq → NVIDIA → Ollama):**

```python
# app/pipeline/summarizer.py: _llm_chat()
if GROQ_API_KEY:   → use Groq (llama-3.3-70b-versatile)
elif NVIDIA_API_KEY: → use NVIDIA NIM (meta/llama-3.3-70b-instruct)
else:              → use local Ollama (llama3)
```

**Groq free-tier truncation:** If using Groq and the transcript is over 32,000 characters, it's truncated before sending (to stay within token limits).

**The prompt produces a structured output:**

```
📌 Topic
One-paragraph overview

📋 Summary
Detailed breakdown with bold sub-headings

💡 Key Takeaways
• Bullet 1
• Bullet 2

🏁 Conclusion
Final wrap-up
```

**Custom prompt:** Any interface can pass a `prompt_template` (a string with `{text}` placeholder) to override the default. This is how per-subscription custom prompts work.

**Output:** Text file at `data/summaries/<title>.summary.txt`

---

### 3.4 Extractor

**File:** `app/pipeline/extractor.py`
**Entry function:** `extract_and_store(collection_name, transcript_path, ...)`

**What it does:**

This is stage 4 — only runs when a video is being added to a **Collection**. It sends the transcript to an LLM and asks it to extract structured knowledge items (formulas, practice questions, tricks, code patterns, etc.).

**What gets extracted depends on the Collection's goal type:**

| Goal Type | Items Extracted |
|---|---|
| `exam_prep` | Formulas, practice questions, tricks, concepts |
| `project_build` | Concepts, tools, code patterns, project ideas |
| `quiz_practice` | Questions (heavy), concepts, formulas |

The LLM returns JSON like:
```json
[
  {"type": "formula", "topic": "Compound Interest", "content": "A = P(1 + r/n)^nt", "answer": ""},
  {"type": "question", "topic": "Percentages", "content": "If 30% of X = 90, find X", "answer": "300"}
]
```

Items are validated and bulk-inserted into the `knowledge_items` DB table.

After extraction, `builder.py` is called to rebuild the collection's markdown file from the updated DB.

---

## 4. Interfaces: Three Ways to Use the System

All three interfaces share the same pipeline functions. They differ in how they accept input and deliver output.

### 4.1 CLI

**File:** `app/interfaces/cli.py`
**Entry:** `python main.py video <url>` or `python main.py channel <url>`

**What it does:**

The simplest interface — runs the full pipeline sequentially and prints progress to the terminal.

- `video` mode: Process a single video URL directly
- `channel` mode: First calls `get_latest_video()` from `app/utils/youtube.py` to find the newest video, then processes it

Optional `--collection` flag runs the extractor (stage 4) after summarization.

**Flow:**
```
parse args → download → transcribe → summarize → [extract → build knowledge]
```

---

### 4.2 Telegram Bot

**File:** `app/interfaces/bot.py`

**What it does:**

A full conversational bot using **python-telegram-bot** with a state machine (`ConversationHandler`). Users send URLs and receive summaries, then can ask follow-up questions or add videos to collections.

**Conversation States:**

```
WAITING_FOR_KEY     ← User must enter access password (if BOT_ACCESS_KEY set)
      ↓
WAITING_FOR_LANG    ← Pick English or Hindi
      ↓
WAITING_FOR_URL     ← Send one or more YouTube URLs
      ↓
WAITING_FOR_PROMPT  ← Ask a follow-up question about the video
      ↓
FOLLOW_UP           ← Continue chatting or start a new video
```

**Key features:**

- **Access control:** Two layers — a shared password (`BOT_ACCESS_KEY`) required on `/start`, plus a per-user allowlist in the database
- **Batch processing:** Send multiple URLs (one per line) and they're queued and processed one at a time
- **PIPELINE_SEMAPHORE:** Only one pipeline run at a time (Whisper and LLM are single-instance). Other requests wait
- **Keepalive:** During transcription, the bot sends `TYPING` action every 4 seconds so Telegram doesn't show the bot as offline
- **Q&A:** After summarization, the user can ask questions — answered using the transcript as context
- **Collections:** After summarization, the bot offers to add the video to a collection and run extraction
- **Subscription commands:** `/subscribe <url> [HH:MM] [-- custom prompt]`, `/unsubscribe`, `/latest`, `/sendnow`
- **Quiz:** `/quiz [collection]` sends random practice questions from a collection's knowledge base

---

### 4.3 Web UI

**File:** `app/interfaces/web.py`

**What it does:**

A **FastAPI** web application with a browser UI (Jinja2 templates + HTMX). Provides all pipeline features plus job management, collection browsing, subscription management, and admin.

**Authentication:**
- Cookie-based, signed with `itsdangerous.TimestampSigner`, 30-day expiry
- `WEB_AUTH_TOKEN` fetched from `app_secrets` DB table (falls back to `.env`)
- Bearer token also accepted for API/curl access

**Job System:**

Each submitted URL becomes a **Job**:
```python
@dataclass
class JobState:
    job_id: str
    status: str           # pending | running | done | error | cancelled
    log_queue: asyncio.Queue  # real-time log messages
    cancel_event: asyncio.Event  # user can cancel mid-run
```

Jobs run as asyncio background tasks. The frontend polls for updates via **Server-Sent Events (SSE)** — you see each pipeline step as it happens.

**Key routes:**

| Route | Purpose |
|---|---|
| `POST /api/jobs` | Submit a new video/channel URL |
| `GET /api/jobs/{id}/log` | Stream real-time job logs (SSE) |
| `POST /api/jobs/{id}/cancel` | Cancel a running job |
| `GET /summary/{slug}` | View summary + Q&A interface |
| `GET /collections` | Browse and manage collections |
| `GET /subscriptions` | Manage channel subscriptions |
| `GET /allowed-users` | Manage who can use the bot |

**Two background schedulers run on startup:**

1. **Subscription Scheduler** — wakes every 60 seconds, checks `yt_subscriptions` for any subscription whose `run_time` (HH:MM IST) matches now, and dispatches the pipeline + Telegram notification. Uses `last_sent_video_id` to skip if the latest video hasn't changed.

2. **Quiz Scheduler** — wakes every 60 seconds, checks `quiz_schedules` for any schedule where `elapsed >= interval_minutes`, and sends a batch of random questions to the configured Telegram chat.

---

## 5. Knowledge System

The knowledge system is a second layer built on top of the pipeline. It lets you group videos into **Collections**, extract structured items from each video's transcript, and then query or quiz yourself on that knowledge.

```
Videos in a Collection
        │
        ▼
extractor.py (LLM extracts structured items)
        │
        ▼
knowledge_items table (PostgreSQL)
        │
        ▼
builder.py → data/knowledge/<Name>.md (compiled markdown)
        │
      ┌─┴─────────────┐
      ▼               ▼
   qa.py           quiz.py
(answer questions) (send quiz via Telegram)
```

### `app/knowledge/builder.py`

Reads all knowledge items for a collection from the DB and compiles them into a single markdown file, organized by item type and topic:

```markdown
# SSC CGL Maths — Knowledge Base

## 📐 Formulas & Rules
### Compound Interest
**1.** A = P(1 + r/n)^nt
*Source: CI Video 1*

## ❓ Practice Questions
### Percentages
**Q1.** If 30% of X = 90, find X
> **Answer:** 300
```

### `app/knowledge/qa.py`

Answers questions by loading the compiled markdown file as LLM context (up to 14KB), then asking the LLM to answer using only that material. Different prompt styles for `exam_prep` vs. `project_build`.

### `app/knowledge/quiz.py`

Fetches N random `question`-type items from the DB, formats them as a numbered quiz, and sends via Telegram. Also handles scheduled quiz delivery via `run_scheduled_quizzes()`.

---

## 6. Database Layer

**All imports should come from `app.db`** — the `__init__.py` re-exports everything.

```python
from app.db import get_conn, get_secret, add_video_history, load_subscriptions, ...
```

**Tables and their owners:**

| Table | Module | Purpose |
|---|---|---|
| `app_secrets` | `db/core.py` | Key-value store for secrets (bot token, auth key) |
| `user_video_history` | `db/history.py` | Track which videos each Telegram user processed |
| `yt_subscriptions` | `db/subscriptions.py` | Daily briefing subscriptions (channel + schedule) |
| `allowed_telegram_users` | `db/users.py` | Bot access allowlist |
| `collections` | `db/collections.py` | Named topic groups with goal_type |
| `collection_videos` | `db/collections.py` | Videos belonging to a collection |
| `knowledge_items` | `db/collections.py` | Extracted items (formulas, questions, etc.) |
| `quiz_schedules` | `db/collections.py` | Interval-based quiz delivery config |

**Each table module has an `ensure_*_tables()` function** that creates the table if it doesn't exist and runs any needed `ALTER TABLE` migrations. Called at startup.

**Secrets priority:** `get_secret(key)` checks:
1. `app_secrets` PostgreSQL table
2. Environment variable (same key name)
3. Provided fallback value

---

## 7. Utilities

### `app/utils/youtube.py`

Wraps yt-dlp for metadata-only operations (no download):
- `get_latest_video(channel_url)` → `{id, title, url}` — used by CLI channel mode and subscription scheduler
- `get_all_videos_with_dates(channel_url)` → list of videos with upload dates — used by bot `/latest` and web channel mode

### `app/utils/notifications.py`

Two functions used by the subscription dispatcher:
- `extract_highlights(summary_text, model)` — calls `_llm_chat()` (same Groq/NVIDIA/Ollama chain) to condense a summary into 5 punchy morning-newspaper bullets
- `send_telegram_message(bot_token, chat_id, text)` — async HTTP POST to Telegram, auto-chunks messages over 4096 characters

---

## 8. Data Flow Walkthroughs

### Single Video via CLI

```
$ python main.py video "https://youtube.com/watch?v=abc123"

1. cli.py: parse args, call process_video(url)
2. downloader.py: yt-dlp fetches audio → FFmpeg converts → data/downloads/Title.wav
3. transcriber.py: Groq Whisper API translates+transcribes → data/downloads/Title.txt
                   (WAV is deleted here)
4. summarizer.py: LLM reads transcript → data/summaries/Title.summary.txt
5. cli.py: print path to summary file
```

### Video via Telegram Bot

```
User: /start
  bot.py: check BOT_ACCESS_KEY → ask for language → WAITING_FOR_LANG

User: 🇬🇧 English
  bot.py: save lang → WAITING_FOR_URL

User: https://youtube.com/watch?v=abc123
  bot.py: validate URL → acquire PIPELINE_SEMAPHORE → run in asyncio.to_thread():
    downloader → transcriber → summarizer
  bot.py: send summary text to user as Telegram message
  bot.py: offer buttons: [Ask a question] [Add to Collection] [New Video]

User: Ask a question: "What was the main formula?"
  bot.py: load transcript → LLM Q&A call → reply with answer
```

### Daily Subscription Briefing

```
Every 60 seconds, web.py's _subscription_scheduler() runs:

  1. Load all enabled subscriptions from DB
  2. For each subscription where run_time == current HH:MM IST:
     a. get_latest_video(channel_url) → {id, title, url}
     b. Compare id to last_sent_video_id → skip if same (no new video)
     c. Acquire PIPELINE_SEMAPHORE
     d. download → transcribe → summarize (with custom_prompt if set)
     e. extract_highlights(summary) → 5 bullet briefing
     f. send_telegram_message(chat_id, formatted_briefing)
     g. update_subscription_last_sent(chat_id, channel_url, video_id)
```

### Web UI Job with Real-Time Log

```
User: submit URL in browser form
  → POST /api/jobs {url, mode}
  → create JobState(job_id, log_queue, cancel_event)
  → asyncio.create_task(_run_pipeline(job_id))
  → return job_id

Browser: open SSE stream  GET /api/jobs/{job_id}/log
  → server yields from job.log_queue as "data: message\n\n"
  → browser shows each step live

_run_pipeline():
  → acquire PIPELINE_SEMAPHORE
  → download → transcribe → summarize
  → push log messages to job.log_queue at each step
  → set job.status = "done"
  → SSE stream closes
```

---

## 9. Key Architectural Patterns

### Single Semaphore for All Pipeline Runs

Both `bot.py` and `web.py` declare `PIPELINE_SEMAPHORE = asyncio.Semaphore(1)`. This ensures that:
- Whisper (either Groq or mlx-whisper) processes one audio file at a time
- The LLM is not called concurrently for summaries
- Multiple simultaneous requests queue up and wait their turn

### Files as Intermediate State

The pipeline uses the filesystem as its "message bus" between stages:
- Each stage outputs to a known file path
- If that file already exists, the stage skips (idempotent — re-run safe)
- `overwrite=True` flag forces re-processing

### DB as Long-Term State

Files are transient (WAVs are deleted; summaries could be lost). The DB is permanent:
- Video history, subscriptions, collections, quiz schedules → all Postgres
- Knowledge items extracted from transcripts live in DB and are rebuilt into files on demand

### All Pipeline Calls Go Through `asyncio.to_thread()`

Pipeline functions (download, transcribe, summarize) are synchronous (CPU/IO bound). Both bot and web call them via `asyncio.to_thread()` to avoid blocking the event loop:
```python
wav_path = await asyncio.to_thread(download_youtube_audio_as_wav, url, DOWNLOADS_DIR)
txt_path = await asyncio.to_thread(transcribe_file, wav_path)
```

### HTMX for Partial Page Updates

The web UI uses **HTMX** instead of a full JavaScript framework. Key interactions (job status rows, Q&A responses, project suggestions) return HTML partials that HTMX swaps into the page — no page reload needed, no separate API client layer.
