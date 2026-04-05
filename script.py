import yt_dlp
import os


def download_youtube_audio_as_wav(url: str, output_dir: str = "downloads"):
    """
    Downloads audio from a YouTube URL and converts it to WAV format.
    
    Args:
        url (str): YouTube video URL
        output_dir (str): Directory to save output files
    """

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # yt-dlp configuration
    ydl_opts = {
        'format': 'bestaudio/best',  # best available audio
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',   # convert to WAV
                'preferredquality': '192', # bitrate (not very relevant for wav but okay)
            }
        ],
        'quiet': False,
        'noplaylist': True,
        # Avoid bot detection: pass browser cookies and use a realistic user-agent
        'cookiesfrombrowser': ('chrome',),
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
        },
        # web client supports cookies; use node (v25 available) for JS challenge solving
        'extractor_args': {'youtube': {'player_client': ['web']}},
        'js_runtimes': {'node': {}},
        'remote_components': {'ejs:github'},
    }

    try:
        # Snapshot existing WAV files before download
        before = set(
            f for f in os.listdir(output_dir) if f.endswith(".wav")
        ) if os.path.exists(output_dir) else set()

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Downloading audio from: {url}")
            ydl.extract_info(url, download=True)

        # Find the newly created WAV file by diffing the directory
        after = set(f for f in os.listdir(output_dir) if f.endswith(".wav"))
        new_files = after - before

        if not new_files:
            # File already existed (same title); find by most recently modified
            wav_files = [f for f in after]
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
    parser.add_argument("--dir", default="downloads", help="Output directory (default: downloads)")
    args = parser.parse_args()

    youtube_url = args.url or input("Enter YouTube URL: ").strip()
    download_youtube_audio_as_wav(youtube_url, output_dir=args.dir)