"""
Pipeline integration test.

Tests each stage independently with a real (short) YouTube video so the
entire flow — download → transcribe → summarize — is validated end-to-end.

Usage:
    python test_pipeline.py              # run all tests
    python test_pipeline.py --stage download
    python test_pipeline.py --stage transcribe
    python test_pipeline.py --stage summarize
    python test_pipeline.py --stage cookies
    python test_pipeline.py --stage imports

Each stage prints PASS or FAIL with a reason. Exit code is 0 if all pass.
"""
import argparse
import os
import sys
import tempfile
import time

# Short public-domain video (~30 seconds) used for download/transcribe tests.
# Big Buck Bunny trailer — always available, no login needed.
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, reason: str = ""):
    tag = PASS if ok else FAIL
    msg = f"  [{tag}] {name}"
    if reason:
        msg += f" — {reason}"
    print(msg)
    results.append((name, ok, reason))


# ── Stage: imports ────────────────────────────────────────────────────────────

def test_imports():
    print("\n=== Stage: imports ===")

    for module in ["mlx_whisper", "yt_dlp", "psycopg2", "groq", "httpx",
                   "fastapi", "telegram", "dotenv"]:
        try:
            __import__(module)
            check(f"import {module}", True)
        except ImportError as e:
            check(f"import {module}", False, str(e))

    # Project modules
    for module in ["app.pipeline.downloader", "app.pipeline.transcriber",
                   "app.pipeline.summarizer", "app.db"]:
        try:
            __import__(module)
            check(f"import {module} (project)", True)
        except Exception as e:
            check(f"import {module} (project)", False, str(e))


# ── Stage: cookies ────────────────────────────────────────────────────────────

def test_cookies():
    print("\n=== Stage: cookies ===")
    from app.pipeline.downloader import COOKIES_FILE, _cookie_file_age, _cookies_are_valid

    exists = os.path.exists(COOKIES_FILE)
    check("cookies.txt exists", exists, "" if exists else f"not found at {COOKIES_FILE}")

    if exists:
        age_h = int(_cookie_file_age(COOKIES_FILE) // 3600)
        valid = _cookies_are_valid(COOKIES_FILE)
        check(
            "cookies.txt freshness",
            valid or age_h < 24 * 7,  # warn if >7 days, but don't hard-fail
            f"age={age_h}h, valid={valid}",
        )


# ── Stage: download ───────────────────────────────────────────────────────────

def test_download(tmp_dir: str):
    print("\n=== Stage: download ===")
    from app.pipeline.downloader import download_youtube_audio_as_wav

    t0 = time.time()
    wav_path = download_youtube_audio_as_wav(TEST_VIDEO_URL, output_dir=tmp_dir)
    elapsed = time.time() - t0

    ok = wav_path is not None and os.path.exists(wav_path)
    size_mb = os.path.getsize(wav_path) / (1024 * 1024) if ok else 0
    check(
        "download_youtube_audio_as_wav",
        ok,
        f"{size_mb:.1f} MB in {elapsed:.0f}s" if ok else "returned None or file missing",
    )
    return wav_path if ok else None


# ── Stage: transcribe ─────────────────────────────────────────────────────────

def test_transcribe(wav_path: str):
    print("\n=== Stage: transcribe ===")
    from app.pipeline.transcriber import transcribe_file, TRANSCRIBE_TIMEOUT_SECS

    check("TRANSCRIBE_TIMEOUT_SECS set", TRANSCRIBE_TIMEOUT_SECS > 0, str(TRANSCRIBE_TIMEOUT_SECS))

    # Confirm signal.signal is not used (that was the bug — crashes in threads)
    import inspect, transcribe as _t
    src = inspect.getsource(_t)
    has_signal = "signal.signal" in src
    check("no signal.signal in transcribe.py", not has_signal,
          "FIXED" if not has_signal else "signal.signal found — will crash in threads")

    t0 = time.time()
    txt_path = transcribe_file(wav_path, delete_wav=False)
    elapsed = time.time() - t0

    ok = txt_path is not None and os.path.exists(txt_path)
    if ok:
        with open(txt_path) as f:
            preview = f.read(200).strip()
        check("transcribe_file", True, f"{elapsed:.0f}s, preview: {preview[:80]!r}")
    else:
        check("transcribe_file", False, "returned None or .txt not created")
    return txt_path if ok else None


# ── Stage: summarize ──────────────────────────────────────────────────────────

def test_summarize(txt_path: str, tmp_dir: str):
    print("\n=== Stage: summarize ===")
    from app.pipeline.summarizer import summarize_file, DEFAULT_MODEL

    t0 = time.time()
    summary_path = summarize_file(txt_path, tmp_dir, DEFAULT_MODEL, overwrite=True)
    elapsed = time.time() - t0

    ok = summary_path is not None and os.path.exists(summary_path)
    if ok:
        with open(summary_path) as f:
            preview = f.read(200).strip()
        check("summarize_file", True, f"{elapsed:.0f}s, preview: {preview[:80]!r}")
    else:
        check("summarize_file", False, "returned None or .summary.txt not created")
    return summary_path if ok else None


# ── Stage: bot_features ───────────────────────────────────────────────────────

# A real user_id that has history in the DB (adjust if needed)
_TEST_USER_ID = 1817863826


def test_history():
    print("\n=== Stage: history ===")
    try:
        from app.db import get_user_history
    except Exception as e:
        check("import db", False, str(e))
        return

    try:
        entries = get_user_history(_TEST_USER_ID, 10)
    except Exception as e:
        check("DB connection", False, str(e))
        return

    check("DB connection", True, f"{len(entries)} history entries")

    if not entries:
        check("history entries exist", False, f"no entries for user {_TEST_USER_ID}")
        return

    check("history entries exist", True, f"{len(entries)} entries found")

    # Validate files on disk
    ok_count = 0
    for entry in entries:
        txt_ok = os.path.exists(entry["transcript_path"])
        sum_ok = os.path.exists(entry["summary_path"])
        if txt_ok and sum_ok:
            ok_count += 1
        else:
            print(f"    [WARN] '{entry['title']}': txt={txt_ok} sum={sum_ok}")

    check(
        "history files on disk",
        ok_count > 0,
        f"{ok_count}/{len(entries)} entries have both transcript+summary on disk",
    )

    # Use the first valid entry for /ask test
    for entry in entries:
        if os.path.exists(entry["transcript_path"]):
            return entry["transcript_path"]
    return None


def test_ask(transcript_path: str):
    print("\n=== Stage: ask (transcript Q&A) ===")
    try:
        from app.pipeline.summarizer import _llm_chat, DEFAULT_MODEL
    except Exception as e:
        check("import summarize._llm_chat", False, str(e))
        return

    try:
        with open(transcript_path, encoding="utf-8") as f:
            text = f.read(6000)
    except Exception as e:
        check("read transcript", False, str(e))
        return

    check("read transcript", True, f"{len(text)} chars")

    prompt = (
        f"Based on this transcript, what are the 2-3 key points discussed?\n\n{text}"
    )
    try:
        t0 = time.time()
        answer = _llm_chat(DEFAULT_MODEL, prompt)
        elapsed = time.time() - t0
        ok = bool(answer and len(answer) > 20)
        check("LLM /ask response", ok, f"{elapsed:.0f}s, {len(answer)} chars" if ok else repr(answer))
    except Exception as e:
        check("LLM /ask response", False, str(e))


def test_quiz():
    print("\n=== Stage: quiz (collections + knowledge items) ===")
    try:
        from app.db import get_collections, ensure_collections_tables, get_conn
    except Exception as e:
        check("import db", False, str(e))
        return

    try:
        ensure_collections_tables()
        collections = get_collections()
    except Exception as e:
        check("DB collections query", False, str(e))
        return

    check("DB collections query", True, f"{len(collections)} collections")

    if not collections:
        # Create a temporary test collection to validate the path
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO collections (name, goal_type, description) "
                        "VALUES (%s, %s, %s) ON CONFLICT (name) DO NOTHING RETURNING id",
                        ("__test_collection__", "exam_prep", "Automated test collection"),
                    )
                    row = cur.fetchone()
                    if row:
                        coll_id = row[0]
                        # Insert a test knowledge item
                        cur.execute(
                            "INSERT INTO knowledge_items (collection_id, item_type, content, answer, topic) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (coll_id, "question", "What is 2 + 2?", "4", "math"),
                        )
                conn.commit()
            check("created temp test collection", True)
            cleanup_needed = True
        except Exception as e:
            check("create temp test collection", False, str(e))
            return
    else:
        coll_id = collections[0]["id"]
        cleanup_needed = False
        check(f"collection '{collections[0]['name']}' exists", True,
              f"{collections[0]['item_count']} knowledge items")

    # Try fetching knowledge items
    try:
        from app.db import get_knowledge_items
        items = get_knowledge_items(coll_id)
        check("get_knowledge_items", len(items) > 0, f"{len(items)} items")
    except Exception as e:
        check("get_knowledge_items", False, str(e))

    # Clean up temp collection if we created it
    if cleanup_needed:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM collections WHERE name = '__test_collection__'")
                conn.commit()
        except Exception:
            pass


# ── Runner ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Pipeline integration test")
    parser.add_argument(
        "--stage",
        choices=["imports", "cookies", "download", "transcribe", "summarize",
                 "bot_features", "all"],
        default="all",
    )
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    stage = args.stage

    if stage in ("imports", "all"):
        test_imports()

    if stage in ("cookies", "all"):
        test_cookies()

    if stage in ("download", "transcribe", "summarize", "all"):
        with tempfile.TemporaryDirectory(prefix="yt_test_") as tmp_dir:
            wav_path = None
            txt_path = None

            if stage in ("download", "all"):
                wav_path = test_download(tmp_dir)

            if stage in ("transcribe", "all") and wav_path:
                txt_path = test_transcribe(wav_path)
            elif stage == "transcribe":
                # Need a real WAV — download first
                wav_path = test_download(tmp_dir)
                if wav_path:
                    txt_path = test_transcribe(wav_path)

            if stage in ("summarize", "all") and txt_path:
                test_summarize(txt_path, tmp_dir)
            elif stage == "summarize":
                wav_path = test_download(tmp_dir)
                if wav_path:
                    txt_path = test_transcribe(wav_path)
                    if txt_path:
                        test_summarize(txt_path, tmp_dir)

    if stage in ("bot_features", "all"):
        transcript_path = test_history()
        if transcript_path:
            test_ask(transcript_path)
        else:
            check("ask (skipped — no transcript on disk)", False, "run pipeline first to generate a transcript")
        test_quiz()

    # Summary
    print("\n=== Results ===")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    for name, ok, reason in results:
        tag = PASS if ok else FAIL
        print(f"  [{tag}] {name}" + (f" — {reason}" if reason else ""))

    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
