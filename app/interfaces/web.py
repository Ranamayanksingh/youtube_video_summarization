"""
FastAPI web UI for the YouTube Audio Summarizer.

Run from the project root:
    uvicorn web_app:app --host 0.0.0.0 --port 8000

Auth:
    Set WEB_AUTH_TOKEN and SECRET_KEY in .env.
    Login at /login with your token. A signed cookie persists the session.
"""
import asyncio
import datetime
import os
import urllib.parse
from dataclasses import dataclass, field
from typing import AsyncGenerator
from uuid import uuid4

import ollama
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from app.db import (
    add_subscription, get_secret, load_subscriptions, remove_subscription,
    get_allowed_users, add_allowed_user, remove_allowed_user,
    get_collections, get_collection_by_name, get_collection_by_id,
    create_collection, delete_collection,
    get_knowledge_items, get_collection_videos, ensure_collections_tables,
)
from app.pipeline.extractor import extract_and_store
from app.knowledge.builder import build_knowledge_file, read_knowledge_file, KNOWLEDGE_DIR
from app.knowledge.qa import answer_question as collection_answer_question, suggest_projects
from app.knowledge.quiz import run_scheduled_quizzes
from app.utils.youtube import get_latest_video
from app.pipeline.downloader import download_youtube_audio_as_wav
from app.utils.notifications import extract_highlights, send_telegram_message
from app.pipeline.summarizer import DEFAULT_MODEL as DEFAULT_LLM_MODEL
from app.pipeline.summarizer import DEFAULT_SUMMARIES_DIR, PROMPT_TEMPLATE, summarize_file
from app.pipeline.transcriber import DEFAULT_WHISPER_MODEL, transcribe_file

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WEB_AUTH_TOKEN: str = get_secret("WEB_AUTH_TOKEN")
SECRET_KEY: str = get_secret("SECRET_KEY", "change-me-please")
TELEGRAM_BOT_TOKEN: str = get_secret("TELEGRAM_BOT_TOKEN")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOWNLOADS_DIR = os.path.join(_PROJECT_ROOT, "data", "downloads")
SUMMARIES_DIR = DEFAULT_SUMMARIES_DIR
_STATIC_DIR    = os.path.join(_PROJECT_ROOT, "static")
_TEMPLATES_DIR = os.path.join(_PROJECT_ROOT, "templates")
_CATEGORIES_FILE = os.path.join(_PROJECT_ROOT, "categories.json")

# IST offset from UTC
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

if not WEB_AUTH_TOKEN:
    import warnings
    warnings.warn(
        "WEB_AUTH_TOKEN is not set — the web UI is unprotected. "
        "Set it in .env before exposing via Cloudflare Tunnel.",
        stacklevel=1,
    )

signer = TimestampSigner(SECRET_KEY)

# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------
@dataclass
class JobState:
    job_id: str
    mode: str          # "video" | "channel"
    url: str
    status: str = "pending"   # pending | running | done | error | cancelled
    step: str = ""
    result_slug: str = ""     # slug of the completed summary
    error: str = ""
    collection: str | None = None   # collection name to extract into
    batch_id: str | None = None     # groups jobs submitted together
    log_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


JOBS: dict[str, JobState] = {}

# Only one pipeline runs at a time (Whisper + Ollama are single-instance)
PIPELINE_SEMAPHORE = asyncio.Semaphore(1)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="YT Summarizer")

os.makedirs(_TEMPLATES_DIR, exist_ok=True)
os.makedirs(_STATIC_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(SUMMARIES_DIR, exist_ok=True)


@app.on_event("startup")
async def _start_scheduler():
    ensure_collections_tables()
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    asyncio.create_task(_subscription_scheduler())
    asyncio.create_task(_quiz_scheduler())

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
COOKIE_NAME = "yt_auth"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _sign_token(token: str) -> str:
    return signer.sign(token).decode()


def _verify_signed(signed: str) -> bool:
    try:
        signer.unsign(signed, max_age=COOKIE_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def _is_authenticated(request: Request) -> bool:
    if not WEB_AUTH_TOKEN:
        return True  # no auth configured — open access

    # 1. Bearer token header (API / curl usage)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:] == WEB_AUTH_TOKEN:
        return True

    # 2. Signed cookie (browser session)
    cookie = request.cookies.get(COOKIE_NAME, "")
    if cookie and _verify_signed(cookie):
        # Verify the payload matches the configured token
        try:
            payload = signer.unsign(cookie, max_age=COOKIE_MAX_AGE).decode()
            return payload == WEB_AUTH_TOKEN
        except Exception:
            return False

    return False


def require_auth(request: Request):
    """FastAPI dependency — redirects to /login if not authenticated."""
    if not _is_authenticated(request):
        from fastapi.responses import RedirectResponse as _RR
        # Raising an HTTPException with a redirect response attached
        next_url = urllib.parse.quote(str(request.url), safe="")
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/login?next={next_url}"},
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def _slug_from_path(path: str) -> str:
    """summaries/My Video.summary.txt  →  My%20Video"""
    stem = os.path.basename(path).replace(".summary.txt", "")
    return urllib.parse.quote(stem, safe="")


def _path_from_slug(slug: str) -> str:
    """My%20Video  →  summaries/My Video.summary.txt"""
    return os.path.join(SUMMARIES_DIR, urllib.parse.unquote(slug) + ".summary.txt")


def _transcript_path(slug: str) -> str:
    return os.path.join(DOWNLOADS_DIR, urllib.parse.unquote(slug) + ".txt")


def _generate_suggestions(transcript: str, model: str) -> list[str]:
    """Generate 4 follow-up questions based on the transcript."""
    prompt = (
        "You are given a transcript of a YouTube video. "
        "Generate exactly 4 short, specific follow-up questions a curious viewer would want to ask about this video. "
        "Each question must be answerable from the transcript. "
        "Output only the 4 questions, one per line, no numbering, no bullets, no extra text.\n\n"
        f"Transcript:\n{transcript[:6000]}"  # cap to avoid context overflow
    )
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response["message"]["content"].strip()
    questions = [
        line.strip().lstrip("-•*123456789. ").strip()
        for line in raw.splitlines()
        if line.strip()
    ]
    return questions[:4]


def _answer_question(transcript: str, question: str, model: str) -> str:
    prompt = (
        "You are a helpful assistant. The user has watched a YouTube video "
        "and you have its full transcript below.\n"
        "Answer the user's question based solely on the transcript. "
        "If the answer is not in the transcript, say so.\n\n"
        f"Transcript:\n{transcript}\n\n"
        f"Question: {question}"
    )
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response["message"]["content"].strip()


def _list_summaries() -> list[dict]:
    """Return summary metadata sorted by modification time (newest first)."""
    items = []
    if not os.path.isdir(SUMMARIES_DIR):
        return items
    for entry in os.scandir(SUMMARIES_DIR):
        if entry.name.endswith(".summary.txt"):
            stem = entry.name.replace(".summary.txt", "")
            items.append({
                "slug": urllib.parse.quote(stem, safe=""),
                "title": stem,
                "mtime": entry.stat().st_mtime,
            })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


# ---------------------------------------------------------------------------
# Background pipeline
# ---------------------------------------------------------------------------
async def _run_pipeline(job_id: str):
    job = JOBS[job_id]

    async def log(msg: str):
        job.step = msg
        await job.log_queue.put(msg)

    async with PIPELINE_SEMAPHORE:
        if job.cancel_event.is_set():
            job.status = "cancelled"
            return

        job.status = "running"
        try:
            url = job.url

            # Channel mode: resolve to video URL first
            if job.mode == "channel":
                await log("Fetching latest video from channel...")
                video = await asyncio.to_thread(get_latest_video, url)
                if not video:
                    raise RuntimeError("Could not retrieve latest video from channel.")
                url = video["url"]
                await log(f"Found: {video['title']}")

            if job.cancel_event.is_set():
                raise RuntimeError("Cancelled by user.")

            # Step 1: Download
            await log("Step 1/3 — Downloading audio...")
            wav_path = await asyncio.to_thread(
                download_youtube_audio_as_wav, url, DOWNLOADS_DIR
            )
            if not wav_path:
                raise RuntimeError("Download failed. Check the URL and try again.")

            if job.cancel_event.is_set():
                raise RuntimeError("Cancelled by user.")

            # Step 2: Transcribe
            await log("Step 2/3 — Transcribing audio to English...")
            txt_path = await asyncio.to_thread(
                transcribe_file, wav_path, DEFAULT_WHISPER_MODEL
            )
            if not txt_path:
                raise RuntimeError("Transcription failed.")

            if job.cancel_event.is_set():
                raise RuntimeError("Cancelled by user.")

            # Step 3: Summarize
            steps_total = "4" if job.collection else "3"
            await log(f"Step 3/{steps_total} — Summarizing transcript...")
            summary_path = await asyncio.to_thread(
                summarize_file, txt_path, SUMMARIES_DIR, DEFAULT_LLM_MODEL, True, None
            )
            if not summary_path:
                raise RuntimeError("Summarization failed.")

            # Step 4: Extract into collection (optional)
            if job.collection:
                if job.cancel_event.is_set():
                    raise RuntimeError("Cancelled by user.")
                await log(f"Step 4/4 — Extracting knowledge → {job.collection}...")
                video_title = os.path.splitext(os.path.basename(txt_path))[0]
                try:
                    await asyncio.to_thread(
                        extract_and_store,
                        job.collection, txt_path, summary_path, url, video_title,
                        DEFAULT_LLM_MODEL,
                    )
                    await asyncio.to_thread(build_knowledge_file, job.collection)
                except Exception as exc:
                    await log(f"⚠️ Extraction failed: {exc} (summary still saved)")

            job.result_slug = _slug_from_path(summary_path)
            job.status = "done"
            await log(f"__done__{job.result_slug}")

        except Exception as exc:
            if job.cancel_event.is_set():
                job.status = "cancelled"
                await log("__error__Cancelled by user.")
            else:
                job.status = "error"
                job.error = str(exc)
                await log(f"__error__{exc}")


# ---------------------------------------------------------------------------
# Scheduled subscription runner
# ---------------------------------------------------------------------------
async def _run_subscription_pipeline(sub: dict) -> str | None:
    """
    Run the full pipeline for a subscription's channel URL.
    Acquires PIPELINE_SEMAPHORE so it never runs concurrently with another
    pipeline (web-submitted or another subscription).
    Returns the summary text if successful, None otherwise.
    """
    channel_url = sub["channel_url"]
    chat_id = sub["telegram_chat_id"]

    try:
        async with PIPELINE_SEMAPHORE:
            video = await asyncio.to_thread(get_latest_video, channel_url)
            if not video:
                return None
            url = video["url"]

            wav_path = await asyncio.to_thread(download_youtube_audio_as_wav, url, DOWNLOADS_DIR)
            if not wav_path:
                return None

            txt_path = await asyncio.to_thread(transcribe_file, wav_path, DEFAULT_WHISPER_MODEL)
            if not txt_path:
                return None

            summary_path = await asyncio.to_thread(
                summarize_file, txt_path, SUMMARIES_DIR, DEFAULT_LLM_MODEL, True, None
            )
            if not summary_path:
                return None

            with open(summary_path, "r", encoding="utf-8") as f:
                return f.read().strip()

    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Subscription pipeline failed for chat_id=%s channel=%s", chat_id, channel_url
        )
        return None


async def _dispatch_subscription(sub: dict) -> None:
    """Run pipeline for one subscription and send highlights via Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return

    chat_id = sub["telegram_chat_id"]
    channel_url = sub["channel_url"]

    summary_text = await _run_subscription_pipeline(sub)
    if not summary_text:
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN, chat_id,
            f"⚠️ Could not fetch/summarize the latest video from:\n{channel_url}"
        )
        return

    highlights = await asyncio.to_thread(extract_highlights, summary_text, DEFAULT_LLM_MODEL)

    # Format like a morning newspaper briefing
    video_title_line = summary_text.splitlines()[0] if summary_text else ""
    header = f"☀️ *Morning Briefing*\n\n📺 {video_title_line}\n\n*Key Highlights:*\n"
    message = header + highlights

    await send_telegram_message(TELEGRAM_BOT_TOKEN, chat_id, message)


async def _subscription_scheduler() -> None:
    """
    Long-running background task. Wakes every minute, checks if any
    subscriptions are due (their HH:MM IST matches current time), and fires them.
    Uses a set of (chat_id, channel_url, date) already-fired today to avoid double-sends.
    """
    fired_today: set[tuple[str, str, str]] = set()

    while True:
        now_ist = datetime.datetime.now(IST)
        today_str = now_ist.strftime("%Y-%m-%d")
        current_hhmm = now_ist.strftime("%H:%M")

        # Reset fired set at midnight
        fired_today = {k for k in fired_today if k[2] == today_str}

        subs = load_subscriptions()
        for sub in subs:
            if not sub.get("enabled", True):
                continue
            run_time = sub.get("run_time", "07:00")
            key = (sub["telegram_chat_id"], sub["channel_url"], today_str)
            if run_time == current_hhmm and key not in fired_today:
                fired_today.add(key)
                asyncio.create_task(_dispatch_subscription(sub))

        # Sleep until the next minute boundary
        await asyncio.sleep(60 - now_ist.second)


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": ""})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    token: str = Form(...),
    next: str = Form("/"),
):
    if token != WEB_AUTH_TOKEN and WEB_AUTH_TOKEN:
        return templates.TemplateResponse(
            request, "login.html", {"next": next, "error": "Invalid token."}, status_code=401
        )
    signed = _sign_token(token if WEB_AUTH_TOKEN else "open")
    response = RedirectResponse(url=next or "/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        signed,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="strict",
    )
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# Routes — Processing page + active jobs API
# ---------------------------------------------------------------------------
@app.get("/processing", response_class=HTMLResponse)
async def processing_page(request: Request, _=Depends(require_auth)):
    all_jobs = list(JOBS.values())
    # Sort: running/pending first, then done/error/cancelled by recency
    all_jobs.sort(key=lambda j: (j.status not in ("pending", "running"), j.job_id))
    return templates.TemplateResponse(request, "processing.html", {"jobs": all_jobs})


@app.get("/api/job-rows", response_class=HTMLResponse)
async def job_rows(request: Request, _=Depends(require_auth)):
    all_jobs = list(JOBS.values())
    all_jobs.sort(key=lambda j: (j.status not in ("pending", "running"), j.job_id))
    if not all_jobs:
        return HTMLResponse('<p class="muted">No jobs yet. <a href="/">Summarize a video</a> to get started.</p>')
    parts = []
    tmpl = templates.env.get_template("partials/job_row.html")
    for job in all_jobs:
        parts.append(tmpl.render(job=job))
    return HTMLResponse("".join(parts))


@app.get("/api/active-jobs", response_class=HTMLResponse)
async def active_jobs(request: Request, _=Depends(require_auth)):
    active = [j for j in JOBS.values() if j.status in ("pending", "running")]
    return templates.TemplateResponse(request, "partials/active_jobs.html", {"jobs": active})


@app.delete("/api/jobs/{job_id}", response_class=HTMLResponse)
async def cancel_job(job_id: str, request: Request, _=Depends(require_auth)):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    job.cancel_event.set()
    job.status = "cancelled"
    job.step = "Cancelled by user."
    # Return updated row HTML for HTMX swap
    return templates.TemplateResponse(request, "partials/job_row.html", {"job": job})


# ---------------------------------------------------------------------------
# Routes — Home
# ---------------------------------------------------------------------------
@app.get("/about", response_class=HTMLResponse)
async def about(request: Request, _=Depends(require_auth)):
    return templates.TemplateResponse(request, "about.html", {})


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, _=Depends(require_auth)):
    recent = _list_summaries()[:5]
    cols = get_collections()
    return templates.TemplateResponse(request, "index.html", {"summaries": recent, "collections": cols})


# ---------------------------------------------------------------------------
# Routes — Submit (single or batch)
# ---------------------------------------------------------------------------
import re as _re

def _extract_yt_urls(text: str) -> list[str]:
    """Extract all YouTube URLs from a block of text."""
    pattern = r'https?://(?:www\.)?(?:youtube\.com/watch\S+|youtu\.be/\S+)'
    found = _re.findall(pattern, text)
    seen, result = set(), []
    for url in found:
        url = url.rstrip(".,;)>\"'")
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


@app.post("/submit")
async def submit(
    request: Request,
    url: str = Form(...),
    mode: str = Form("video"),
    collection: str = Form(""),
    _=Depends(require_auth),
):
    raw = url.strip()
    if not raw:
        return templates.TemplateResponse(
            request, "index.html", {"error": "Please enter a URL."}, status_code=400
        )

    collection = collection.strip() or None

    # Extract all YouTube URLs from the input (handles single or multi-URL paste)
    urls = _extract_yt_urls(raw)

    # Fallback: treat the whole input as one URL if extraction found nothing
    if not urls:
        if "youtube.com" not in raw and "youtu.be" not in raw:
            return templates.TemplateResponse(
                request, "index.html",
                {"error": "That doesn't look like a YouTube URL."}, status_code=400
            )
        urls = [raw]

    # Single URL — original flow, redirect to the job page
    if len(urls) == 1:
        job_id = str(uuid4())
        JOBS[job_id] = JobState(job_id=job_id, mode=mode, url=urls[0],
                                collection=collection)
        asyncio.create_task(_run_pipeline(job_id))
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

    # Multiple URLs — create one job per URL, redirect to batch overview
    batch_id = str(uuid4())
    job_ids = []
    for video_url in urls:
        job_id = str(uuid4())
        JOBS[job_id] = JobState(job_id=job_id, mode="video", url=video_url,
                                collection=collection, batch_id=batch_id)
        asyncio.create_task(_run_pipeline(job_id))
        job_ids.append(job_id)

    return RedirectResponse(url=f"/processing?batch={batch_id}", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Job status page
# ---------------------------------------------------------------------------
@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(job_id: str, request: Request, _=Depends(require_auth)):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return templates.TemplateResponse(request, "job.html", {"job": job})


# ---------------------------------------------------------------------------
# Routes — SSE stream
# ---------------------------------------------------------------------------
@app.get("/jobs/{job_id}/stream")
async def job_stream(job_id: str, request: Request, _=Depends(require_auth)):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    async def event_generator() -> AsyncGenerator[str, None]:
        keepalive_interval = 25  # seconds
        while True:
            try:
                msg = await asyncio.wait_for(job.log_queue.get(), timeout=keepalive_interval)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue

            if msg.startswith("__done__"):
                slug = msg[len("__done__"):]
                yield f"event: done\ndata: {slug}\n\n"
                break
            elif msg.startswith("__error__"):
                err = msg[len("__error__"):]
                yield f"event: error\ndata: {err}\n\n"
                break
            else:
                # Escape newlines in SSE data field
                safe = msg.replace("\n", " ")
                yield f"data: {safe}\n\n"

            if await request.is_disconnected():
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Routes — Summaries
# ---------------------------------------------------------------------------
@app.get("/summaries", response_class=HTMLResponse)
async def summaries_list(request: Request, _=Depends(require_auth)):
    items = _list_summaries()
    return templates.TemplateResponse(request, "summaries.html", {"summaries": items})


@app.get("/summaries/{slug}", response_class=HTMLResponse)
async def summary_detail(slug: str, request: Request, _=Depends(require_auth)):
    summary_path = _path_from_slug(slug)
    if not os.path.exists(summary_path):
        raise HTTPException(status_code=404, detail="Summary not found.")

    with open(summary_path, "r", encoding="utf-8") as f:
        content = f.read()

    transcript_path = _transcript_path(slug)
    has_transcript = os.path.exists(transcript_path)
    title = urllib.parse.unquote(slug)

    return templates.TemplateResponse(
        request,
        "summary_detail.html",
        {"title": title, "slug": slug, "content": content, "has_transcript": has_transcript},
    )


# ---------------------------------------------------------------------------
# Routes — Delete summary
# ---------------------------------------------------------------------------
def _delete_summary_files(slug: str) -> dict:
    """Synchronous worker — runs in a thread. Returns sizes of deleted files."""
    title = urllib.parse.unquote(slug)
    paths = [
        _path_from_slug(slug),                              # .summary.txt
        _transcript_path(slug),                             # .txt
        os.path.join(DOWNLOADS_DIR, title + ".wav"),        # .wav
    ]
    deleted = {}
    for path in paths:
        if os.path.exists(path):
            size = os.path.getsize(path)
            os.remove(path)
            deleted[os.path.basename(path)] = size
    return deleted


@app.delete("/summaries/{slug}", response_class=HTMLResponse)
async def delete_summary(slug: str, _=Depends(require_auth)):
    if not os.path.exists(_path_from_slug(slug)):
        raise HTTPException(status_code=404, detail="Summary not found.")
    # Run all file I/O in a thread — never blocks the event loop
    await asyncio.to_thread(_delete_summary_files, slug)
    return HTMLResponse("")  # HTMX swaps the <li> out


# POST version for the detail page (HTML forms can't send DELETE)
@app.post("/summaries/{slug}/delete")
async def delete_summary_post(slug: str, _=Depends(require_auth)):
    if not os.path.exists(_path_from_slug(slug)):
        raise HTTPException(status_code=404, detail="Summary not found.")
    await asyncio.to_thread(_delete_summary_files, slug)
    return RedirectResponse(url="/summaries", status_code=303)


# ---------------------------------------------------------------------------
# Routes — Q&A
# ---------------------------------------------------------------------------
@app.post("/summaries/{slug}/qa", response_class=HTMLResponse)
async def qa(
    slug: str,
    request: Request,
    question: str = Form(...),
    _=Depends(require_auth),
):
    transcript_path = _transcript_path(slug)
    if not os.path.exists(transcript_path):
        raise HTTPException(status_code=404, detail="Transcript not found.")

    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript = f.read().strip()

    if not transcript:
        raise HTTPException(status_code=422, detail="Transcript is empty.")

    answer = await asyncio.to_thread(_answer_question, transcript, question, DEFAULT_LLM_MODEL)

    return templates.TemplateResponse(
        request, "partials/qa_response.html", {"question": question, "answer": answer}
    )


@app.get("/summaries/{slug}/suggestions", response_class=HTMLResponse)
async def suggestions(slug: str, request: Request, _=Depends(require_auth)):
    transcript_path = _transcript_path(slug)
    if not os.path.exists(transcript_path):
        return HTMLResponse("")

    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript = f.read().strip()

    if not transcript:
        return HTMLResponse("")

    questions = await asyncio.to_thread(_generate_suggestions, transcript, DEFAULT_LLM_MODEL)
    return templates.TemplateResponse(
        request, "partials/suggestions.html", {"questions": questions, "slug": slug}
    )


# ---------------------------------------------------------------------------
# Routes — Subscriptions
# ---------------------------------------------------------------------------
@app.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request, _=Depends(require_auth)):
    subs = load_subscriptions()
    bot_configured = bool(TELEGRAM_BOT_TOKEN)
    return templates.TemplateResponse(
        request, "subscriptions.html", {"subs": subs, "bot_configured": bot_configured}
    )


@app.post("/subscriptions/add")
async def add_subscription_route(
    request: Request,
    telegram_chat_id: str = Form(...),
    channel_url: str = Form(...),
    run_time: str = Form("07:00"),
    _=Depends(require_auth),
):
    telegram_chat_id = telegram_chat_id.strip()
    channel_url = channel_url.strip()
    run_time = run_time.strip()

    if not telegram_chat_id or not channel_url:
        subs = load_subscriptions()
        return templates.TemplateResponse(
            request, "subscriptions.html",
            {"subs": subs, "bot_configured": bool(TELEGRAM_BOT_TOKEN),
             "error": "Telegram Chat ID and Channel URL are required."},
            status_code=400,
        )

    if "youtube.com" not in channel_url and "youtu.be" not in channel_url:
        subs = load_subscriptions()
        return templates.TemplateResponse(
            request, "subscriptions.html",
            {"subs": subs, "bot_configured": bool(TELEGRAM_BOT_TOKEN),
             "error": "That doesn't look like a YouTube URL."},
            status_code=400,
        )

    add_subscription(telegram_chat_id, channel_url, run_time)
    return RedirectResponse(url="/subscriptions", status_code=303)


@app.post("/subscriptions/delete")
async def delete_subscription_route(
    telegram_chat_id: str = Form(...),
    channel_url: str = Form(...),
    _=Depends(require_auth),
):
    remove_subscription(telegram_chat_id.strip(), channel_url.strip())
    return RedirectResponse(url="/subscriptions", status_code=303)


@app.post("/subscriptions/test")
async def test_subscription_route(
    request: Request,
    telegram_chat_id: str = Form(...),
    channel_url: str = Form(...),
    _=Depends(require_auth),
):
    """Send a test 'ping' message to verify the chat ID and bot token."""
    if not TELEGRAM_BOT_TOKEN:
        subs = load_subscriptions()
        return templates.TemplateResponse(
            request, "subscriptions.html",
            {"subs": subs, "bot_configured": False,
             "error": "TELEGRAM_BOT_TOKEN is not set in .env."},
            status_code=400,
        )
    try:
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            telegram_chat_id.strip(),
            "✅ YT Summarizer subscription is active! You'll receive morning briefings here.",
        )
        subs = load_subscriptions()
        return templates.TemplateResponse(
            request, "subscriptions.html",
            {"subs": subs, "bot_configured": True,
             "success": f"Test message sent to {telegram_chat_id}!"},
        )
    except Exception as exc:
        subs = load_subscriptions()
        return templates.TemplateResponse(
            request, "subscriptions.html",
            {"subs": subs, "bot_configured": bool(TELEGRAM_BOT_TOKEN),
             "error": f"Failed to send: {exc}"},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# Routes — Allowed Telegram Users
# ---------------------------------------------------------------------------
@app.get("/allowed-users", response_class=HTMLResponse)
async def allowed_users_page(request: Request, _=Depends(require_auth)):
    users = get_allowed_users()
    return templates.TemplateResponse(request, "allowed_users.html", {"users": users})


@app.post("/allowed-users/add")
async def add_allowed_user_route(
    request: Request,
    telegram_user_id: str = Form(...),
    label: str = Form(""),
    _=Depends(require_auth),
):
    telegram_user_id = telegram_user_id.strip()
    label = label.strip()
    if not telegram_user_id.lstrip("-").isdigit():
        users = get_allowed_users()
        return templates.TemplateResponse(
            request, "allowed_users.html",
            {"users": users, "error": "Telegram User ID must be a number."},
            status_code=400,
        )
    add_allowed_user(int(telegram_user_id), label)
    return RedirectResponse(url="/allowed-users", status_code=303)


@app.post("/allowed-users/delete")
async def remove_allowed_user_route(
    telegram_user_id: str = Form(...),
    _=Depends(require_auth),
):
    remove_allowed_user(int(telegram_user_id.strip()))
    return RedirectResponse(url="/allowed-users", status_code=303)


# ---------------------------------------------------------------------------
# Background — Quiz scheduler
# ---------------------------------------------------------------------------
async def _quiz_scheduler() -> None:
    """Run every minute and fire any due quiz schedules."""
    while True:
        await asyncio.sleep(60)
        if TELEGRAM_BOT_TOKEN:
            try:
                await run_scheduled_quizzes(TELEGRAM_BOT_TOKEN)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Quiz scheduler error")


# ---------------------------------------------------------------------------
# Routes — Collections
# ---------------------------------------------------------------------------
import json as _json

@app.get("/collections", response_class=HTMLResponse)
async def collections_page(request: Request, _=Depends(require_auth)):
    cols = get_collections()
    # Load category presets from categories.json for the create form
    presets = []
    if os.path.exists(_CATEGORIES_FILE):
        with open(_CATEGORIES_FILE, "r") as f:
            data = _json.load(f)
            presets = data.get("collections", [])
            goal_types = data.get("goal_types", {})
    else:
        goal_types = {}
    return templates.TemplateResponse(
        request, "collections.html",
        {"collections": cols, "presets": presets, "goal_types": goal_types}
    )


@app.post("/collections/create")
async def create_collection_route(
    request: Request,
    name: str = Form(...),
    goal_type: str = Form("exam_prep"),
    description: str = Form(""),
    extract_focus: str = Form("formulas,questions,tricks,concepts"),
    _=Depends(require_auth),
):
    name = name.strip()
    if not name:
        return RedirectResponse(url="/collections", status_code=303)
    focus_list = [f.strip() for f in extract_focus.split(",") if f.strip()]
    create_collection(name=name, goal_type=goal_type, description=description, extract_focus=focus_list)
    return RedirectResponse(url="/collections", status_code=303)


@app.post("/collections/{collection_id}/delete")
async def delete_collection_route(collection_id: int, _=Depends(require_auth)):
    delete_collection(collection_id)
    return RedirectResponse(url="/collections", status_code=303)


@app.get("/collections/{collection_id}", response_class=HTMLResponse)
async def collection_detail(request: Request, collection_id: int, tab: str = "questions", _=Depends(require_auth)):
    collection = get_collection_by_id(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found.")

    items = get_knowledge_items(collection_id, item_type=tab if tab != "all" else None)
    videos = get_collection_videos(collection_id)

    # Item type counts for tab badges
    all_items = get_knowledge_items(collection_id)
    from collections import Counter
    type_counts = Counter(i["item_type"] for i in all_items)

    return templates.TemplateResponse(
        request, "collection_detail.html",
        {
            "collection": collection,
            "items": items,
            "videos": videos,
            "tab": tab,
            "type_counts": dict(type_counts),
            "total_items": len(all_items),
        }
    )


@app.post("/collections/{collection_id}/rebuild")
async def rebuild_knowledge(collection_id: int, _=Depends(require_auth)):
    """Rebuild the knowledge .md file for a collection from DB items."""
    collection = get_collection_by_id(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found.")
    await asyncio.to_thread(build_knowledge_file, collection["name"])
    return RedirectResponse(url=f"/collections/{collection_id}", status_code=303)


@app.post("/collections/{collection_id}/ask", response_class=HTMLResponse)
async def collection_ask(
    request: Request,
    collection_id: int,
    question: str = Form(...),
    _=Depends(require_auth),
):
    collection = get_collection_by_id(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found.")
    answer = await asyncio.to_thread(
        collection_answer_question, collection["name"], question, DEFAULT_LLM_MODEL
    )
    return templates.TemplateResponse(
        request, "partials/qa_response.html", {"question": question, "answer": answer}
    )


@app.post("/collections/{collection_id}/suggest-projects", response_class=HTMLResponse)
async def collection_suggest_projects(
    request: Request,
    collection_id: int,
    _=Depends(require_auth),
):
    collection = get_collection_by_id(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found.")
    result = await asyncio.to_thread(suggest_projects, collection["name"], DEFAULT_LLM_MODEL)
    return templates.TemplateResponse(
        request, "partials/qa_response.html",
        {"question": "Suggest projects based on my knowledge base", "answer": result}
    )


@app.get("/collections/{collection_id}/knowledge-file")
async def download_knowledge_file(collection_id: int, _=Depends(require_auth)):
    """Download the compiled knowledge .md file."""
    from fastapi.responses import FileResponse
    collection = get_collection_by_id(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found.")
    safe_name = collection["name"].replace("/", "-").replace("\\", "-")
    path = os.path.join(KNOWLEDGE_DIR, f"{safe_name}.md")
    if not os.path.exists(path):
        # Build it on the fly
        await asyncio.to_thread(build_knowledge_file, collection["name"])
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Knowledge file could not be generated.")
    return FileResponse(path, media_type="text/markdown", filename=f"{safe_name}.md")
