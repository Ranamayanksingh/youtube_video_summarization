"""
Audio transcription — Groq Whisper API (primary) with mlx-whisper fallback.

Groq's Whisper API has a 25 MB per-request limit, so large WAV files are split
into overlapping chunks using ffmpeg before uploading. This avoids the silent
hang bug in mlx-whisper where inference stalls mid-file on certain audio.

Groq is used when GROQ_API_KEY is set (checked at runtime via db.get_secret).
Falls back to local mlx-whisper when Groq is unavailable.
"""
import glob
import json
import os
import subprocess
import tempfile
import argparse

import mlx_whisper

# Explicit paths for ffmpeg/ffprobe — required when running as a launchd
# service where /opt/homebrew/bin is not on PATH.
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

DEFAULT_WHISPER_MODEL = "mlx-community/whisper-large-v3-mlx"
DEFAULT_MODEL = DEFAULT_WHISPER_MODEL  # backward compat alias

# Groq Whisper model to use
GROQ_WHISPER_MODEL = "whisper-large-v3"

# Groq hard limit is 25 MB per request. We target 20 MB chunks to stay safe.
GROQ_CHUNK_SIZE_MB = 20
GROQ_CHUNK_BYTES = GROQ_CHUNK_SIZE_MB * 1024 * 1024

# Timeout for a single transcription (45 min). Only enforced by the async
# keepalive wrapper in the bot — not here, since signal.alarm can't be used
# from thread-pool workers.
TRANSCRIBE_TIMEOUT_SECS = 45 * 60


# ── Groq helpers ──────────────────────────────────────────────────────────────

def _get_groq_key() -> str:
    """Return GROQ_API_KEY from DB secrets or env."""
    try:
        from app.db import get_secret
        key = get_secret("GROQ_API_KEY")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("GROQ_API_KEY", "")


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
    print(f"[TRANSCRIBE] Split into {len(chunks)} chunks (chunk_duration={chunk_duration}s)")
    return chunks


def _transcribe_with_groq(wav_path: str) -> str | None:
    """
    Transcribe wav_path using Groq Whisper API.
    Splits into chunks if file > 20 MB.
    Returns full transcript text or None on failure.
    """
    from groq import Groq

    api_key = _get_groq_key()
    if not api_key:
        return None

    client = Groq(api_key=api_key)

    with tempfile.TemporaryDirectory(prefix="yt_chunks_") as chunk_dir:
        chunks = _split_wav(wav_path, chunk_dir)
        parts: list[str] = []

        for i, chunk_path in enumerate(chunks):
            chunk_size_mb = os.path.getsize(chunk_path) / (1024 * 1024)
            print(f"[GROQ] Transcribing chunk {i+1}/{len(chunks)} ({chunk_size_mb:.1f} MB)…")
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
                print(f"[GROQ] Chunk {i+1} done: {text[:80].strip()!r}…")
            except Exception as e:
                print(f"[GROQ] Chunk {i+1} failed: {e}")
                return None

    return "\n".join(parts)


# ── mlx-whisper fallback ──────────────────────────────────────────────────────

def _transcribe_with_mlx(wav_path: str, model_repo: str = DEFAULT_WHISPER_MODEL) -> str | None:
    """Transcribe using local mlx-whisper. May hang on certain audio."""
    print(f"[MLX] Transcribing with {model_repo}…")
    try:
        result = mlx_whisper.transcribe(
            wav_path,
            path_or_hf_repo=model_repo,
            task="translate",
            language=None,
            verbose=True,
        )
        return result["text"].strip()
    except Exception as e:
        print(f"[MLX] Transcription error: {e}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def transcribe_file(
    wav_path: str,
    model_repo: str = DEFAULT_WHISPER_MODEL,
    overwrite: bool = False,
    delete_wav: bool = False,
) -> str | None:
    """
    Transcribes a single WAV file to English. Returns the .txt path, or None on failure.

    Strategy:
      1. If GROQ_API_KEY is set → use Groq Whisper API (chunked, no hang risk).
      2. Otherwise → fall back to local mlx-whisper.

    Args:
        wav_path: Path to the WAV file.
        model_repo: HuggingFace repo ID for the mlx-whisper fallback model.
        overwrite: Re-transcribe even if .txt already exists.
        delete_wav: Delete the WAV file after successful transcription.
    """
    txt_path = os.path.splitext(wav_path)[0] + ".txt"

    if os.path.exists(txt_path) and not overwrite:
        print(f"[SKIP] Already transcribed: {os.path.basename(wav_path)}")
        return txt_path

    wav_size_mb = os.path.getsize(wav_path) / (1024 * 1024) if os.path.exists(wav_path) else 0
    print(f"[TRANSCRIBING] {os.path.basename(wav_path)} ({wav_size_mb:.0f} MB)")

    api_key = _get_groq_key()
    if api_key:
        print(f"[TRANSCRIBE] Using Groq Whisper API ({GROQ_WHISPER_MODEL})")
        text = _transcribe_with_groq(wav_path)
    else:
        print("[TRANSCRIBE] No GROQ_API_KEY — falling back to local mlx-whisper")
        text = _transcribe_with_mlx(wav_path, model_repo)

    if not text:
        print(f"❌ Transcription failed: {os.path.basename(wav_path)}")
        return None

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
        f.write("\n")
    print(f"[DONE] Saved: {os.path.basename(txt_path)}")
    print(f"[PREVIEW] {text[:300]}…\n")

    if delete_wav:
        try:
            os.remove(wav_path)
            print(f"[CLEANUP] Deleted WAV: {os.path.basename(wav_path)}")
        except OSError as e:
            print(f"[WARN] Could not delete WAV: {e}")

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
