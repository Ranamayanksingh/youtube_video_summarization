import yt_dlp
from datetime import datetime


def get_channel_video_urls(channel_url):
    """Step 1: Get all video URLs"""
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'skip_download': True
    }

    urls = []

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(channel_url, download=False)

        for entry in result.get('entries', []):
            if entry and entry.get("id"):
                urls.append(f"https://www.youtube.com/watch?v={entry['id']}")

    return urls


def get_video_upload_date(video_url):
    """Step 2: Fetch upload date for each video"""
    ydl_opts = {
        'quiet': True,
        'skip_download': True
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

        upload_date = info.get("upload_date")  # YYYYMMDD

        if upload_date:
            # convert to readable format
            return datetime.strptime(upload_date, "%Y%m%d").strftime("%Y-%m-%d")

        return None


def get_all_videos_with_dates(channel_url):
    video_urls = get_channel_video_urls(channel_url)

    results = []

    for url in video_urls:
        try:
            date = get_video_upload_date(url)

            results.append({
                "url": url,
                "upload_date": date
            })

            print(f"Processed: {url}")

        except Exception as e:
            print(f"Error for {url}: {e}")

    return results


if __name__ == "__main__":
    channel_url = "https://www.youtube.com/@Career247Official/videos"

    data = get_all_videos_with_dates(channel_url)

    for item in data[:5]:
        print(item)