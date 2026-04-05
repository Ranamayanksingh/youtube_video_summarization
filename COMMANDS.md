# Commands Reference

## Setup (one-time)

```bash
# Activate the virtual environment — required before running any script
source venv/bin/activate
```

---

## Core Commands

### 1. Summarize a specific YouTube video

```bash
python main.py video "<youtube_video_url>"
```

**What it does:** Downloads the audio from the given video URL, transcribes it to English (supports Hindi and English audio), and generates a detailed summary. Output is saved to `summaries/`.

**Example:**
```bash
python main.py video "https://youtu.be/abc123"
```

---

### 2. Summarize the latest video from a channel

```bash
python main.py channel "<youtube_channel_url>"
```

**What it does:** Fetches the latest video from the channel, then runs the full pipeline — download → transcribe → summarize. Output is saved to `summaries/`.

**Example:**
```bash
python main.py channel "https://www.youtube.com/@DLSNews/videos"
```

---

## Scheduler (Automated Daily Pipeline)

Runs `main.py channel` every day at **7:00 AM IST (01:30 UTC)** without any manual intervention. Uses macOS `launchd` — works even after system restarts.

### Install (activate daily schedule)

```bash
python scheduler.py install --channel "<youtube_channel_url>"
```

**Example:**
```bash
python scheduler.py install --channel "https://www.youtube.com/@DLSNews/videos"
```

### Check status

```bash
python scheduler.py status
```

**What it shows:** Whether the job is active, which channel it monitors, and the schedule time.

### View live logs

```bash
tail -f scheduler.log
```

**What it does:** Streams the output of the automated runs in real time.

### Change the monitored channel

```bash
python scheduler.py install --channel "<new_channel_url>"
```

Re-running install replaces the previous schedule with the new channel.

### Remove the schedule

```bash
python scheduler.py uninstall
```

---

## Individual Module Commands

These run each step of the pipeline independently, useful for debugging or reprocessing.

### Download audio only

```bash
python script.py "<youtube_video_url>"
```

Saves a `.wav` file to `downloads/`.

### Get latest video URL from a channel

```bash
python get_latest_video.py "<youtube_channel_url>"
```

Prints the title and URL of the most recent video. Does not download anything.

### Transcribe all WAV files in downloads/

```bash
python transcribe.py
python transcribe.py --overwrite        # re-transcribe already processed files
python transcribe.py --dir /other/path  # use a different folder
```

Saves `.txt` transcripts alongside each `.wav` in `downloads/`.

### Summarize all transcripts in downloads/

```bash
python summarize.py
python summarize.py --overwrite         # re-summarize already processed files
python summarize.py --model mistral     # use a different Ollama model
```

Saves `.summary.txt` files to `summaries/`.

---

## Output Locations

| Content | Location |
|---|---|
| Downloaded audio (WAV) | `downloads/<video_title>.wav` |
| Transcripts (English text) | `downloads/<video_title>.txt` |
| Summaries | `summaries/<video_title>.summary.txt` |
| Scheduler logs | `scheduler.log` |
