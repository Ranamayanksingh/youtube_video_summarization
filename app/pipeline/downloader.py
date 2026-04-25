"""
Downloads audio from YouTube URLs and converts to WAV.

Cookies are managed automatically: pulled from Chrome on first run and
refreshed whenever they expire (every 3 days) — no manual intervention needed.
"""
import os
import time

import yt_dlp

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COOKIES_FILE = os.path.join(_PROJECT_ROOT, "data", "cookies.txt")

# How long to reuse cookies.txt before hitting Chrome/Keychain again (3 days)
COOKIE_REFRESH_INTERVAL = 3 * 24 * 3600  # seconds

# YouTube session/auth cookies that matter for access
_YOUTUBE_AUTH_COOKIES = {"SAPISID", "SSID", "SID", "HSID", "APISID", "__Secure-1PSID", "__Secure-3PSID"}


def _cookie_file_age(cookies_file: str) -> float:
    """Return how many seconds old cookies.txt is. Returns infinity if missing."""
    try:
        return time.time() - os.path.getmtime(cookies_file)
    except OSError:
        return float("inf")


def _parse_cookie_expiry(cookies_file: str) -> int:
    """
    Read a Netscape-format cookies.txt and return the earliest expiry timestamp
    among YouTube auth cookies. Returns 0 if file is missing or unparseable.
    """
    if not os.path.exists(cookies_file):
        return 0
    earliest = None
    try:
        with open(cookies_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _, _, _, expiry_str, name, _ = parts[:7]
                if "youtube.com" not in domain and "google.com" not in domain:
                    continue
                if name not in _YOUTUBE_AUTH_COOKIES:
                    continue
                try:
                    expiry = int(expiry_str)
                except ValueError:
                    continue
                if expiry == 0:
                    continue  # session cookie, no fixed expiry
                if earliest is None or expiry < earliest:
                    earliest = expiry
    except Exception:
        return 0
    return earliest or 0


def _cookies_are_valid(cookies_file: str) -> bool:
    """
    Return True if cookies.txt is fresh enough to reuse without hitting Chrome.

    Strategy (in order):
    1. File doesn't exist → invalid.
    2. File is younger than COOKIE_REFRESH_INTERVAL (3 days) → valid, skip Keychain.
    3. File is older than 3 days → check actual cookie expiry timestamps.
       If cookies haven't expired yet → still valid (just stale but usable).
       If cookies have expired → invalid, must refresh.
    """
    if not os.path.exists(cookies_file):
        return False

    age = _cookie_file_age(cookies_file)

    if age < COOKIE_REFRESH_INTERVAL:
        age_h = int(age // 3600)
        print(f"🍪 Reusing cached cookies (age: {age_h}h, refresh in {int((COOKIE_REFRESH_INTERVAL - age) // 3600)}h).")
        return True

    expiry = _parse_cookie_expiry(cookies_file)
    if expiry == 0:
        print("🍪 Cookies are over 3 days old. Refreshing from Chrome...")
        return False

    remaining = expiry - int(time.time())
    if remaining <= 0:
        print("🍪 Cookies have expired. Refreshing from Chrome...")
        return False

    print(f"🍪 Cookies are over 3 days old (cookie expiry in {remaining // 3600}h). Refreshing from Chrome...")
    return False


def _refresh_cookies(cookies_file: str):
    """Export fresh cookies from Chrome into cookies_file using yt-dlp."""
    print("🍪 Extracting fresh cookies from Chrome...")
    os.makedirs(os.path.dirname(cookies_file), exist_ok=True)
    export_opts = {
        'quiet': True,
        'skip_download': True,
        'cookiesfrombrowser': ('chrome',),
        'cookiefile': cookies_file,
    }
    with yt_dlp.YoutubeDL(export_opts) as ydl:
        ydl.extract_info("https://www.youtube.com", download=False)
    print("🍪 Cookies refreshed and saved.")


def _ensure_cookies(cookies_file: str):
    """
    Ensure cookies.txt is fresh. If it is older than COOKIE_REFRESH_INTERVAL (3 days)
    or missing, refresh automatically from Chrome.
    """
    if _cookies_are_valid(cookies_file):
        return
    if not os.path.exists(cookies_file):
        print("⚠️  No cookies.txt found. Pulling from Chrome (Keychain popup may appear)...")
    _refresh_cookies(cookies_file)


def download_youtube_audio_as_wav(url: str, output_dir: str | None = None) -> str | None:
    """
    Downloads audio from a YouTube URL and converts it to WAV format.

    Cookies are managed automatically: pulled from Chrome on first run and
    refreshed whenever they expire — no manual intervention needed.

    Args:
        url: YouTube video URL
        output_dir: Directory to save the WAV file. Defaults to data/downloads/.

    Returns:
        Path to the downloaded WAV file, or None on failure.
    """
    if output_dir is None:
        output_dir = os.path.join(_PROJECT_ROOT, "data", "downloads")

    os.makedirs(output_dir, exist_ok=True)

    _ensure_cookies(COOKIES_FILE)

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',
                'preferredquality': '192',
            }
        ],
        'quiet': False,
        'noplaylist': True,
        'cookiefile': COOKIES_FILE,
        'ffmpeg_location': '/opt/homebrew/bin',
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
        },
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
        'js_runtimes': {'node': {'path': '/opt/homebrew/bin/node'}},
    }

    try:
        before = set(
            f for f in os.listdir(output_dir) if f.endswith(".wav")
        ) if os.path.exists(output_dir) else set()

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Downloading audio from: {url}")
            ydl.extract_info(url, download=True)

        after = set(f for f in os.listdir(output_dir) if f.endswith(".wav"))
        new_files = after - before

        if not new_files:
            wav_files = list(after)
            if not wav_files:
                print("❌ No WAV file found after download.")
                return None
            filename = os.path.join(
                output_dir,
                max(wav_files, key=lambda f: os.path.getmtime(os.path.join(output_dir, f)))
            )
        else:
            filename = os.path.join(output_dir, new_files.pop())

        print(f"\n✅ Download complete: {filename}")
        return filename

    except Exception as e:
        print(f"❌ Error occurred: {e}")
        return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Download YouTube audio as WAV.")
    parser.add_argument("url", nargs="?", help="YouTube video URL (omit for interactive prompt)")
    parser.add_argument("--dir", default=None, help="Output directory (default: data/downloads)")
    args = parser.parse_args()

    youtube_url = args.url or input("Enter YouTube URL: ").strip()
    download_youtube_audio_as_wav(youtube_url, output_dir=args.dir)
