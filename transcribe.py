import os
import glob
import argparse
import mlx_whisper

DEFAULT_WHISPER_MODEL = "mlx-community/whisper-large-v3-mlx"
DEFAULT_MODEL = DEFAULT_WHISPER_MODEL  # backward compat alias


def transcribe_file(wav_path: str, model_repo: str = DEFAULT_WHISPER_MODEL, overwrite: bool = False) -> str | None:
    """
    Transcribes a single WAV file to English. Returns the .txt path, or None on failure.
    """
    txt_path = os.path.splitext(wav_path)[0] + ".txt"

    if os.path.exists(txt_path) and not overwrite:
        print(f"[SKIP] Already transcribed: {os.path.basename(wav_path)}")
        return txt_path

    print(f"[TRANSCRIBING] {os.path.basename(wav_path)}")
    try:
        result = mlx_whisper.transcribe(
            wav_path,
            path_or_hf_repo=model_repo,
            task="translate",
            language=None,
            verbose=False,
        )
        text = result["text"].strip()
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")
        print(f"[DONE] Saved: {os.path.basename(txt_path)}")
        print(f"[PREVIEW] {text[:300]}...\n")
        return txt_path
    except Exception as e:
        print(f"❌ Transcription error: {e}")
        return None


def transcribe_all(downloads_dir: str, model_repo: str, overwrite: bool = False):
    """
    Transcribes all WAV files in downloads_dir to English text files.
    Hindi and English audio are both supported; output is always English.

    Args:
        downloads_dir: Directory containing WAV files
        model_repo: HuggingFace repo ID for the MLX Whisper model
        overwrite: If True, re-transcribe files that already have a .txt
    """
    wav_files = sorted(glob.glob(os.path.join(downloads_dir, "*.wav")))

    if not wav_files:
        print(f"No WAV files found in '{downloads_dir}'")
        return

    print(f"Found {len(wav_files)} WAV file(s).")
    print(f"Model: {model_repo}\n")

    for wav_path in wav_files:
        base = os.path.splitext(wav_path)[0]
        txt_path = base + ".txt"

        transcribe_file(wav_path, model_repo=model_repo, overwrite=overwrite)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Transcribe WAV files to English text using Whisper."
    )
    parser.add_argument(
        "--dir", default="downloads", help="Directory containing WAV files (default: downloads)"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help="MLX Whisper HuggingFace model repo"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Re-transcribe files that already have a .txt"
    )
    args = parser.parse_args()

    transcribe_all(args.dir, args.model, args.overwrite)
