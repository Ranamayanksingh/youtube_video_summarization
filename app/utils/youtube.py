"""YouTube metadata helpers — fetch latest video, list all channel videos."""
import argparse
from datetime import datetime

import yt_dlp


def get_latest_video(channel_url: str) -> dict | None:
    """
    Returns metadata of the latest video from a YouTube channel.

    Args:
        channel_url: YouTube channel URL (e.g. https://www.youtube.com/@ChannelName/videos)

    Returns:
        dict with 'url', 'title', 'id' — or None on failure
    """
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'playlist_items': '1',
        'cookiesfrombrowser': ('chrome',),
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
            entries = info.get('entries', [])
            if not entries:
                print("❌ No videos found for this channel.")
                return None

            entry = entries[0]
            video_id = entry.get('id')
            title = entry.get('title', 'Unknown')
            url = entry.get('url') or f"https://www.youtube.com/watch?v={video_id}"

            return {'id': video_id, 'title': title, 'url': url}

    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def get_channel_video_urls(channel_url: str) -> list[str]:
    """Return all video URLs from a channel (no dates)."""
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'skip_download': True,
    }
    urls = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(channel_url, download=False)
        for entry in result.get('entries', []):
            if entry and entry.get("id"):
                urls.append(f"https://www.youtube.com/watch?v={entry['id']}")
    return urls


def get_video_upload_date(video_url: str) -> str | None:
    """Fetch upload date for a video. Returns 'YYYY-MM-DD' string or None."""
    ydl_opts = {'quiet': True, 'skip_download': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        upload_date = info.get("upload_date")
        if upload_date:
            return datetime.strptime(upload_date, "%Y%m%d").strftime("%Y-%m-%d")
    return None


def get_all_videos_with_dates(channel_url: str) -> list[dict]:
    """Return all videos from a channel with their upload dates."""
    video_urls = get_channel_video_urls(channel_url)
    results = []
    for url in video_urls:
        try:
            date = get_video_upload_date(url)
            results.append({"url": url, "upload_date": date})
            print(f"Processed: {url}")
        except Exception as e:
            print(f"Error for {url}: {e}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Get the latest video URL from a YouTube channel.")
    parser.add_argument("channel_url", help="YouTube channel URL")
    args = parser.parse_args()

    video = get_latest_video(args.channel_url)
    if video:
        print(f"Title : {video['title']}")
        print(f"URL   : {video['url']}")
