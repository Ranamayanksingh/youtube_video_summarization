import yt_dlp
import argparse


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
        'extract_flat': True,   # don't download, just list
        'playlist_items': '1',  # only fetch the first (latest) entry
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Get the latest video URL from a YouTube channel.")
    parser.add_argument("channel_url", help="YouTube channel URL (e.g. https://www.youtube.com/@ChannelName/videos)")
    args = parser.parse_args()

    video = get_latest_video(args.channel_url)
    if video:
        print(f"Title : {video['title']}")
        print(f"URL   : {video['url']}")
