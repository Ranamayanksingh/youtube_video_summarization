"""
YouTube Audio Summarizer — unified CLI entry point.

Usage:
  python main.py video <youtube_video_url>
  python main.py channel <youtube_channel_url>
"""
import os
import sys
import argparse

from app.pipeline.downloader import download_youtube_audio_as_wav
from app.utils.youtube import get_latest_video
from app.pipeline.transcriber import transcribe_file, DEFAULT_WHISPER_MODEL
from app.pipeline.summarizer import summarize_file, DEFAULT_SUMMARIES_DIR, DEFAULT_MODEL as DEFAULT_LLM_MODEL
from app.pipeline.extractor import extract_and_store
from app.knowledge.builder import build_knowledge_file

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOWNLOADS_DIR = os.path.join(_PROJECT_ROOT, "data", "downloads")
SUMMARIES_DIR = DEFAULT_SUMMARIES_DIR


def _step(n: int, title: str):
    print(f"\n{'=' * 60}")
    print(f"STEP {n}: {title}")
    print('=' * 60)


def process_video(video_url: str, title: str = None, collection: str = None) -> str | None:
    """
    Full pipeline for a single video URL:
      download → transcribe → summarize → (optionally) extract + knowledge build
    Returns the path to the summary file, or None on failure.
    """
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    os.makedirs(SUMMARIES_DIR, exist_ok=True)

    _step(1, f"Downloading audio{f' — {title}' if title else ''}...")
    wav_path = download_youtube_audio_as_wav(video_url, output_dir=DOWNLOADS_DIR)
    if not wav_path:
        print("❌ Download failed. Aborting.")
        return None

    _step(2, "Transcribing audio to English...")
    txt_path = transcribe_file(wav_path, model_repo=DEFAULT_WHISPER_MODEL, delete_wav=True)
    if not txt_path:
        print("❌ Transcription failed. Aborting.")
        return None

    _step(3, "Summarizing transcript...")
    summary_path = summarize_file(
        txt_path,
        summaries_dir=SUMMARIES_DIR,
        model=DEFAULT_LLM_MODEL,
        overwrite=True,
    )
    if not summary_path:
        print("❌ Summarization failed.")
        return None

    print(f"\n✅ Done. Summary saved to: {summary_path}")

    if collection:
        video_title = title or os.path.splitext(os.path.basename(txt_path))[0]
        _step(4, f"Extracting knowledge → collection: {collection}...")
        try:
            result = extract_and_store(
                collection_name=collection,
                transcript_path=txt_path,
                summary_path=summary_path,
                video_url=video_url,
                video_title=video_title,
                model=DEFAULT_LLM_MODEL,
            )
            print(f"✅ Extracted {result['items_count']} knowledge items.")
            _step(5, f"Rebuilding knowledge file for: {collection}...")
            knowledge_path = build_knowledge_file(collection)
            print(f"✅ Knowledge file updated: {knowledge_path}")
        except Exception as e:
            print(f"⚠️  Knowledge extraction failed: {e}")

    return summary_path


def run_video_mode(video_url: str, collection: str = None):
    """Mode 1: given a video URL, download → transcribe → summarize."""
    process_video(video_url, collection=collection)


def run_channel_mode(channel_url: str, collection: str = None):
    """Mode 2: given a channel URL, find latest video → download → transcribe → summarize."""
    _step(1, "Fetching latest video from channel...")
    video = get_latest_video(channel_url)
    if not video:
        print("❌ Could not retrieve latest video. Aborting.")
        sys.exit(1)

    print(f"Title : {video['title']}")
    print(f"URL   : {video['url']}")

    process_video(video['url'], title=video['title'], collection=collection)


def main():
    parser = argparse.ArgumentParser(
        description="YouTube Audio Summarizer — download, transcribe, and summarize YouTube videos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py video https://youtu.be/abc123
  python main.py channel https://www.youtube.com/@SomeChannel/videos
        """,
    )

    subparsers = parser.add_subparsers(dest="mode", required=True)

    video_parser = subparsers.add_parser("video", help="Summarize a specific YouTube video")
    video_parser.add_argument("url", help="YouTube video URL (quote URLs containing & or ?)")
    video_parser.add_argument("--collection", default=None, help="Collection name to extract knowledge into")
    video_parser.add_argument("extra", nargs="*", help=argparse.SUPPRESS)

    channel_parser = subparsers.add_parser("channel", help="Summarize the latest video from a channel")
    channel_parser.add_argument("url", help="YouTube channel URL")
    channel_parser.add_argument("--collection", default=None, help="Collection name to extract knowledge into")
    channel_parser.add_argument("extra", nargs="*", help=argparse.SUPPRESS)

    args = parser.parse_args()

    if getattr(args, "extra", None):
        print(
            f"⚠️  Warning: extra arguments detected: {args.extra}\n"
            "   This usually means the URL contains '&' and was not quoted.\n"
            f"   Re-run with quotes: python main.py {args.mode} \"{args.url}&{'&'.join(args.extra)}\"\n"
        )
        sys.exit(1)

    if args.mode == "video":
        run_video_mode(args.url, collection=getattr(args, "collection", None))
    elif args.mode == "channel":
        run_channel_mode(args.url, collection=getattr(args, "collection", None))
