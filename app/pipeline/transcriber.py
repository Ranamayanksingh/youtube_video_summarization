"""
Audio transcription pipeline with four backends (tried in order):

  1. faster-whisper  — local, CTranslate2 engine, whisper-large-v3-turbo model.
                       Best quality/stability for local transcription. Never hangs.
  2. AssemblyAI      — cloud, best multilingual accuracy. Requires ASSEMBLYAI_API_KEY.
                       Uploads WAV, polls for result (~30–90 s latency).
  3. Groq            — cloud, whisper-large-v3 via API. Requires GROQ_API_KEY.
                       25 MB per-request limit; large files are chunked automatically.
  4. mlx-whisper     — local fallback. May hang on certain audio (known upstream bug).

All backends use task="translate" so both Hindi and English audio produce English transcripts.
"""
import glob
import json
import logging
import os
import subprocess
import tempfile
import argparse
import time

import mlx_whisper

logger = logging.getLogger(__name__)

# Explicit paths for ffmpeg/ffprobe — required when running as a launchd
# service where /opt/homebrew/bin is not on PATH.
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

DEFAULT_WHISPER_MODEL = "mlx-community/whisper-large-v3-mlx"
DEFAULT_MODEL = DEFAULT_WHISPER_MODEL  # backward compat alias

# faster-whisper model — large-v3-turbo is same accuracy as large-v3, more stable
FASTER_WHISPER_MODEL = "large-v3-turbo"

# Groq Whisper model
GROQ_WHISPER_MODEL = "whisper-large-v3"

# Groq hard limit is 25 MB per request. We target 20 MB chunks to stay safe.
GROQ_CHUNK_SIZE_MB = 20
GROQ_CHUNK_BYTES = GROQ_CHUNK_SIZE_MB * 1024 * 1024

# Timeout for a single transcription (45 min). Only enforced by the async
# keepalive wrapper in the bot — not here, since signal.alarm can't be used
# from thread-pool workers.
TRANSCRIBE_TIMEOUT_SECS = 45 * 60


# ── Secret helpers ─────────────────────────────────────────────────────────────

def _get_secret(key: str) -> str:
    """Fetch a secret from DB or env."""
    try:
        from app.db import get_secret
        value = get_secret(key)
        if value:
            return value
    except Exception:
        pass
    return os.environ.get(key, "")


# ── ffmpeg/ffprobe helpers ─────────────────────────────────────────────────────

def _wav_duration(wav_path: str) -> float:
    """Return duration in seconds via ffprobe."""
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json",
         "-show_format", wav_path],
        capture_output=True, text=True,
    )
    info = json.loads(r.stdout)
    return float(info["format"]["duration"])


def _split_wav(wav_path: str, chunk_dir: str, chunk_bytes: int = GROQ_CHUNK_BYTES) -> list[str]:
    """
    Split wav_path into chunks small enough for Groq's 25 MB limit.
    Uses ffmpeg segment muxer to cut on silence-safe boundaries.
    Returns list of chunk paths in order.
    """
    file_size = os.path.getsize(wav_path)
    if file_size <= chunk_bytes:
        return [wav_path]  # no split needed

    duration = _wav_duration(wav_path)
    chunk_duration = int((chunk_bytes / file_size) * duration) - 5  # -5s safety margin
    chunk_duration = max(30, chunk_duration)

    pattern = os.path.join(chunk_dir, "chunk_%03d.wav")
    subprocess.run(
        [
            FFMPEG, "-y", "-i", wav_path,
            "-f", "segment",
            "-segment_time", str(chunk_duration),
            "-c", "copy",
            pattern,
        ],
        capture_output=True,
        check=True,
    )
    chunks = sorted(glob.glob(os.path.join(chunk_dir, "chunk_*.wav")))
    logger.info("[TRANSCRIBE] Split into %d chunks (chunk_duration=%ds)", len(chunks), chunk_duration)
    return chunks


# ── Backend 1: faster-whisper (local, primary) ────────────────────────────────

def _transcribe_with_faster_whisper(wav_path: str, model_size: str = FASTER_WHISPER_MODEL) -> str | None:
    """
    Transcribe using faster-whisper (CTranslate2 engine).

    Uses whisper-large-v3-turbo by default — same accuracy as large-v3, more
    stable, never hangs. Model is downloaded on first use (~1.5 GB) and cached
    at ~/.cache/huggingface/.

    Always runs with task="translate" so Hindi audio produces English output.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.warning("[FASTER-WHISPER] Not installed — skipping. Run: uv add faster-whisper")
        return None

    logger.info("[FASTER-WHISPER] Loading model '%s'…", model_size)
    try:
        model = WhisperModel(model_size, device="auto", compute_type="default")

        # Get audio duration for progress reporting
        duration = _wav_duration(wav_path)
        logger.info(
            "[FASTER-WHISPER] Starting transcription | file=%s | duration=%.0fs (%.1f min)",
            os.path.basename(wav_path), duration, duration / 60,
        )

        segments, info = model.transcribe(
            wav_path,
            task="translate",
            language=None,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )

        logger.info(
            "[FASTER-WHISPER] Detected language: %s (confidence: %.0f%%)",
            info.language, info.language_probability * 100,
        )

        # Consume segments with live progress logging every 30 seconds of audio
        parts = []
        last_log_at = 0.0   # audio timestamp of last progress log
        LOG_EVERY_SECS = 30  # log a progress line every 30s of audio processed
        start_wall = time.time()

        for segment in segments:
            parts.append(segment.text.strip())
            seg_end = segment.end  # position in audio (seconds)

            if seg_end - last_log_at >= LOG_EVERY_SECS:
                pct = (seg_end / duration * 100) if duration else 0
                elapsed = time.time() - start_wall
                logger.info(
                    "[FASTER-WHISPER] Progress: %.0fs / %.0fs (%.0f%%) — wall time elapsed: %.0fs | last: %r",
                    seg_end, duration, pct, elapsed, segment.text.strip()[:60],
                )
                last_log_at = seg_end

        text = " ".join(parts).strip()
        if not text:
            logger.warning("[FASTER-WHISPER] Empty transcript returned for %s", os.path.basename(wav_path))
            return None

        elapsed_total = time.time() - start_wall
        logger.info(
            "[FASTER-WHISPER] Done | chars=%d | wall_time=%.0fs (%.1f min) | file=%s",
            len(text), elapsed_total, elapsed_total / 60, os.path.basename(wav_path),
        )
        return text

    except Exception as e:
        logger.exception("[FASTER-WHISPER] Failed for %s: %s", os.path.basename(wav_path), e)
        return None


# ── Backend 2: AssemblyAI (cloud, best multilingual accuracy) ─────────────────

def _transcribe_with_assemblyai(wav_path: str) -> str | None:
    """
    Transcribe using AssemblyAI's best multilingual model.

    Uploads the WAV file, then polls for the result (~30–90 s).
    Requires ASSEMBLYAI_API_KEY in .env or app_secrets.
    Free tier: 100 hours/month at assemblyai.com.

    Always requests English output via speech_model="best" + language_detection.
    """
    api_key = _get_secret("ASSEMBLYAI_API_KEY")
    if not api_key:
        return None

    try:
        import assemblyai as aai
    except ImportError:
        print("[ASSEMBLYAI] Not installed — skipping. Run: uv add assemblyai")
        return None

    logger.info("[ASSEMBLYAI] Uploading %s and requesting transcription…", os.path.basename(wav_path))
    try:
        aai.settings.api_key = api_key

        config = aai.TranscriptionConfig(
            speech_model=aai.SpeechModel.best,
            language_detection=True,
        )

        transcriber = aai.Transcriber(config=config)
        start_wall = time.time()
        transcript = transcriber.transcribe(wav_path)

        if transcript.status == aai.TranscriptStatus.error:
            logger.error("[ASSEMBLYAI] Transcription failed: %s", transcript.error)
            return None

        text = (transcript.text or "").strip()
        if not text:
            logger.warning("[ASSEMBLYAI] Empty transcript returned for %s", os.path.basename(wav_path))
            return None

        elapsed = time.time() - start_wall
        logger.info(
            "[ASSEMBLYAI] Done | chars=%d | wall_time=%.0fs | file=%s",
            len(text), elapsed, os.path.basename(wav_path),
        )
        return text

    except Exception as e:
        logger.exception("[ASSEMBLYAI] Failed for %s: %s", os.path.basename(wav_path), e)
        return None


# ── Backend 3: Groq Whisper API (cloud fallback) ──────────────────────────────

def _transcribe_with_groq(wav_path: str) -> str | None:
    """
    Transcribe wav_path using Groq Whisper API.
    Splits into chunks if file > 20 MB.
    Returns full transcript text or None on failure.
    """
    from groq import Groq

    api_key = _get_secret("GROQ_API_KEY")
    if not api_key:
        return None

    client = Groq(api_key=api_key)

    with tempfile.TemporaryDirectory(prefix="yt_chunks_") as chunk_dir:
        chunks = _split_wav(wav_path, chunk_dir)
        parts: list[str] = []

        for i, chunk_path in enumerate(chunks):
            chunk_size_mb = os.path.getsize(chunk_path) / (1024 * 1024)
            logger.info("[GROQ] Chunk %d/%d | size=%.1f MB | sending…", i + 1, len(chunks), chunk_size_mb)
            chunk_start = time.time()
            try:
                with open(chunk_path, "rb") as f:
                    response = client.audio.transcriptions.create(
                        file=(os.path.basename(chunk_path), f),
                        model=GROQ_WHISPER_MODEL,
                        response_format="text",
                        language="en",
                    )
                text = response if isinstance(response, str) else response.text
                parts.append(text.strip())
                logger.info(
                    "[GROQ] Chunk %d/%d done | %.0fs | preview: %r",
                    i + 1, len(chunks), time.time() - chunk_start, text.strip()[:60],
                )
            except Exception as e:
                logger.error("[GROQ] Chunk %d/%d failed: %s", i + 1, len(chunks), e)
                return None

    return "\n".join(parts)


# ── Backend 4: mlx-whisper (local, last resort) ───────────────────────────────

def _transcribe_with_mlx(wav_path: str, model_repo: str = DEFAULT_WHISPER_MODEL) -> str | None:
    """
    Transcribe using local mlx-whisper.
    WARNING: Known to hang mid-file on certain audio. Use only as last resort.
    """
    logger.info("[MLX] Starting transcription with %s | file=%s", model_repo, os.path.basename(wav_path))
    start_wall = time.time()
    try:
        result = mlx_whisper.transcribe(
            wav_path,
            path_or_hf_repo=model_repo,
            task="translate",
            language=None,
            verbose=True,
        )
        text = result["text"].strip()
        elapsed = time.time() - start_wall
        logger.info(
            "[MLX] Done | chars=%d | wall_time=%.0fs (%.1f min) | file=%s",
            len(text), elapsed, elapsed / 60, os.path.basename(wav_path),
        )
        return text
    except Exception as e:
        logger.exception("[MLX] Transcription failed for %s: %s", os.path.basename(wav_path), e)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def transcribe_file(
    wav_path: str,
    model_repo: str = DEFAULT_WHISPER_MODEL,
    overwrite: bool = False,
    delete_wav: bool = False,
) -> str | None:
    """
    Transcribe a single WAV file to English. Returns the .txt path, or None on failure.

    Backend priority:
      1. faster-whisper  (local, large-v3-turbo — best quality, never hangs)
      2. AssemblyAI      (cloud, best multilingual — needs ASSEMBLYAI_API_KEY)
      3. Groq            (cloud, whisper-large-v3  — needs GROQ_API_KEY)
      4. mlx-whisper     (local fallback           — may hang on some audio)

    Args:
        wav_path:   Path to the WAV file.
        model_repo: HuggingFace repo ID for the mlx-whisper last-resort model.
        overwrite:  Re-transcribe even if .txt already exists.
        delete_wav: Delete the WAV file after successful transcription.
    """
    txt_path = os.path.splitext(wav_path)[0] + ".txt"

    if os.path.exists(txt_path) and not overwrite:
        logger.info("[TRANSCRIBE] Already transcribed, skipping: %s", os.path.basename(wav_path))
        return txt_path

    wav_size_mb = os.path.getsize(wav_path) / (1024 * 1024) if os.path.exists(wav_path) else 0
    logger.info(
        "[TRANSCRIBE] Starting | file=%s | size=%.0f MB",
        os.path.basename(wav_path), wav_size_mb,
    )

    # ── Try each backend in order ──────────────────────────────────────────────

    text: str | None = None

    # 1. faster-whisper (local, always tried first)
    logger.info("[TRANSCRIBE] Backend 1/4: faster-whisper (local, %s)", FASTER_WHISPER_MODEL)
    text = _transcribe_with_faster_whisper(wav_path)

    # 2. AssemblyAI (cloud, best multilingual)
    if not text:
        assemblyai_key = _get_secret("ASSEMBLYAI_API_KEY")
        if assemblyai_key:
            logger.warning("[TRANSCRIBE] faster-whisper failed — Backend 2/4: AssemblyAI (cloud)")
            text = _transcribe_with_assemblyai(wav_path)
        else:
            logger.info("[TRANSCRIBE] No ASSEMBLYAI_API_KEY — skipping Backend 2/4")

    # 3. Groq (cloud whisper API)
    if not text:
        groq_key = _get_secret("GROQ_API_KEY")
        if groq_key:
            logger.warning("[TRANSCRIBE] AssemblyAI failed/skipped — Backend 3/4: Groq (cloud)")
            text = _transcribe_with_groq(wav_path)
        else:
            logger.info("[TRANSCRIBE] No GROQ_API_KEY — skipping Backend 3/4")

    # 4. mlx-whisper (last resort — may hang)
    if not text:
        logger.warning("[TRANSCRIBE] All cloud backends failed — Backend 4/4: mlx-whisper (may hang)")
        text = _transcribe_with_mlx(wav_path, model_repo)

    # ── Write result ───────────────────────────────────────────────────────────

    if not text:
        logger.error("[TRANSCRIBE] All backends failed for %s", os.path.basename(wav_path))
        return None

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
        f.write("\n")
    logger.info("[TRANSCRIBE] Saved transcript: %s | chars=%d", os.path.basename(txt_path), len(text))
    logger.debug("[TRANSCRIBE] Preview: %s…", text[:200])

    if delete_wav:
        try:
            os.remove(wav_path)
            logger.info("[TRANSCRIBE] Deleted WAV after transcription: %s", os.path.basename(wav_path))
        except OSError as e:
            logger.warning("[TRANSCRIBE] Could not delete WAV %s: %s", os.path.basename(wav_path), e)

    return txt_path


def transcribe_all(downloads_dir: str, model_repo: str, overwrite: bool = False):
    """Transcribe all WAV files in downloads_dir."""
    wav_files = sorted(glob.glob(os.path.join(downloads_dir, "*.wav")))
    if not wav_files:
        print(f"No WAV files found in '{downloads_dir}'")
        return
    print(f"Found {len(wav_files)} WAV file(s).\n")
    for wav_path in wav_files:
        transcribe_file(wav_path, model_repo=model_repo, overwrite=overwrite)


if __name__ == "__main__":
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _DEFAULT_DIR = os.path.join(_PROJECT_ROOT, "data", "downloads")

    parser = argparse.ArgumentParser(description="Transcribe WAV files to English text.")
    parser.add_argument("--dir", default=_DEFAULT_DIR, help="Directory containing WAV files")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="mlx-whisper fallback model repo")
    parser.add_argument("--overwrite", action="store_true", help="Re-transcribe existing .txt files")
    args = parser.parse_args()
    transcribe_all(args.dir, args.model, args.overwrite)
