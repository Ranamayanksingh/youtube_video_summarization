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
        # Use Node.js for JS extraction (avoids missing-format warnings)
        'extractor_args': {'youtube': {'player_client': ['tv_embedded']}},
        'js_runtimes': {'node': {}},
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Downloading audio from: {url}")
            info = ydl.extract_info(url, download=True)

            title = info.get("title", "output")
            filename = os.path.join(output_dir, f"{title}.wav")

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