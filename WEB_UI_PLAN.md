# Web UI Implementation Plan

## Goal

Expose the YouTube summarizer pipeline as a web app (FastAPI), accessible securely from any device via Cloudflare Tunnel.

---

## Architecture Overview

```
Browser (any device)
    ↓ HTTPS
Cloudflare Edge
    ↓ Encrypted Tunnel
Your Mac — FastAPI (localhost:8000)
    ↓
Existing pipeline: download → transcribe → summarize
```

---

## File Structure

```
youtube-video-audio-data/
├── web_app.py                    # FastAPI application (single file)
├── templates/
│   ├── base.html                 # Layout: nav, HTMX scripts
│   ├── index.html                # Home: URL submit form
│   ├── summaries.html            # Past summaries list
│   ├── summary_detail.html       # Single summary + Q&A form
│   └── partials/
│       ├── progress_stream.html  # SSE progress fragment (streamed via HTMX)
│       └── qa_response.html      # Q&A answer fragment (HTMX swap)
├── static/
│   └── style.css                 # ~80 lines minimal CSS
├── CLOUDFLARE_SETUP.md           # Tunnel setup instructions
└── pyproject.toml                # Add new deps here
```

---

## Dependencies to Add (`pyproject.toml`)

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.9
jinja2>=3.1.4
itsdangerous>=2.2.0
```

Run `uv sync` after updating.

---

## API Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Home page — URL submit form |
| `GET` | `/login` | Login page |
| `POST` | `/login` | Accept token, set signed cookie, redirect to `/` |
| `POST` | `/logout` | Clear auth cookie, redirect to `/login` |
| `POST` | `/submit` | Create job, redirect to `/jobs/{job_id}` |
| `GET` | `/jobs/{job_id}` | Job status page with SSE progress |
| `GET` | `/jobs/{job_id}/stream` | SSE endpoint — streams pipeline progress |
| `GET` | `/summaries` | List all past summaries |
| `GET` | `/summaries/{slug}` | View summary + Q&A form |
| `POST` | `/summaries/{slug}/qa` | HTMX endpoint — Q&A over transcript |

All routes except `/login` require authentication.

---

## Key Design Decisions

### 1. Job Queue (in-memory)
Each submitted URL gets a UUID `job_id`. A module-level `JOBS: dict[str, JobState]` tracks state. `JobState` holds:
- `status`: `pending | running | done | error`
- `step`: current step description
- `result_path`: final summary file path
- `log_queue`: `asyncio.Queue[str]` — progress messages for SSE

No Redis, no Celery. Single-user tool on a single Mac.

### 2. SSE for Real-time Progress
Pipeline takes minutes. Use Server-Sent Events (SSE) to stream progress to the browser without polling.
- HTMX `hx-ext="sse"` handles the client side — no JS written by hand
- SSE sends `event: done` or `event: error` when pipeline finishes
- A 30-second keepalive comment (`: keepalive`) prevents Cloudflare from timing out idle connections

### 3. Concurrency Guard
The models (Whisper, Ollama) are single-instance. A module-level `asyncio.Semaphore(1)` prevents concurrent pipeline runs. A second submission while one is running returns HTTP 409 "A pipeline is already running."

### 4. Authentication
HTTP Bearer token stored in `.env` as `WEB_AUTH_TOKEN`. A `verify_auth` FastAPI dependency checks:
1. `Authorization: Bearer <token>` header
2. A signed `HttpOnly; SameSite=Strict` cookie (set after login form)

Cookie is signed with `itsdangerous.TimestampSigner` using `SECRET_KEY` from `.env`.

### 5. Frontend Stack
Jinja2 templates + HTMX CDN + plain CSS. No React, no build toolchain. HTMX handles:
- Async form submissions
- SSE streaming into a progress div
- Q&A answer swapping

### 6. Reuse Existing Modules
`web_app.py` imports directly from the existing modules:
```python
from script import download_youtube_audio_as_wav
from transcribe import transcribe_file, DEFAULT_WHISPER_MODEL
from summarize import summarize_file, DEFAULT_SUMMARIES_DIR, DEFAULT_MODEL, PROMPT_TEMPLATE
from get_latest_video import get_latest_video
```
The `_answer_question` logic from `telegram_bot.py` will be extracted into `web_app.py` directly (importing from `telegram_bot` pulls in the entire Telegram library unnecessarily).

---

## Implementation Tasks

### Phase 1: Foundation
- [ ] **Task 1** — Add 5 deps to `pyproject.toml`, run `uv sync`
- [ ] **Task 2** — Add `WEB_AUTH_TOKEN` and `SECRET_KEY` to `.env`
- [ ] **Task 3** — Scaffold `web_app.py`: app init, env loading, `JobState` dataclass, `JOBS` dict, semaphore, Jinja2 + static mount

### Phase 2: Backend
- [ ] **Task 4** — Implement auth: `verify_auth` dependency, `/login` (GET/POST), `/logout`
- [ ] **Task 5** — Implement `POST /submit`: validate URL, create job, launch background task
- [ ] **Task 6** — Implement `_run_pipeline` coroutine: acquire semaphore → download → transcribe → summarize via `asyncio.to_thread`, post progress to `log_queue`
- [ ] **Task 7** — Implement `GET /jobs/{job_id}/stream`: SSE endpoint draining `log_queue`, 30s keepalive, `event: done` / `event: error` signals
- [ ] **Task 8** — Implement `GET /jobs/{job_id}`: job status page
- [ ] **Task 9** — Implement `/summaries` list and `/summaries/{slug}` detail routes
- [ ] **Task 10** — Implement `POST /summaries/{slug}/qa`: read transcript, call Ollama, return HTMX partial

### Phase 3: Frontend
- [ ] **Task 11** — `base.html`: HTML5 skeleton, HTMX CDN, SSE extension, stylesheet link
- [ ] **Task 12** — `index.html`: form with video/channel mode radio + URL input
- [ ] **Task 13** — `partials/progress_stream.html`: SSE-connected scrolling log div
- [ ] **Task 14** — `summaries.html`: list of past summaries with links
- [ ] **Task 15** — `summary_detail.html`: summary display + conditional Q&A form
- [ ] **Task 16** — `partials/qa_response.html`: answer block for HTMX swap
- [ ] **Task 17** — `static/style.css`: system font, max-width container, form/progress/summary styling

### Phase 4: Tunnel & Docs
- [ ] **Task 18** — Write `CLOUDFLARE_SETUP.md` with step-by-step tunnel instructions
- [ ] **Task 19** — Update `PRODUCT_RESEARCH.md` to mark "Web UI" as in-progress

---

## Running the Web App

```bash
# Start the server (from project root)
source .venv/bin/activate
uvicorn web_app:app --host 0.0.0.0 --port 8000

# Access locally
open http://localhost:8000

# Access from other devices (after Cloudflare Tunnel setup)
# https://summarizer.yourdomain.com
```

---

## `.env` additions required

```
WEB_AUTH_TOKEN=your_long_random_secret_here
SECRET_KEY=another_long_random_string_for_cookie_signing
```

---

## Cloudflare Tunnel Setup (Summary)

Full instructions in `CLOUDFLARE_SETUP.md`. The short version:

```bash
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create yt-summarizer
# Edit ~/.cloudflared/config.yml to point to localhost:8000
cloudflared tunnel route dns yt-summarizer summarizer.yourdomain.com
cloudflared tunnel run yt-summarizer
```

HTTPS is handled by Cloudflare. No cert management needed on your Mac.

> **Security note**: The app's own `WEB_AUTH_TOKEN` auth must be active before exposing the tunnel. Cloudflare Tunnel encrypts transit but does not restrict who can hit the endpoint.

---

## Notes & Gotchas

- Run `uvicorn` from the project root — `script.py` resolves `cookies.txt` relative to `__file__`
- Summary slugs use URL-encoded filenames: `urllib.parse.quote(stem, safe='')` in templates, `unquote` in route handlers
- Q&A form only shown when `downloads/{slug}.txt` exists — set `has_transcript` flag in the route handler
- `asyncio.to_thread` is the correct pattern for all blocking pipeline calls (same as `telegram_bot.py`)
