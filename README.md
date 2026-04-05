# YouTube Summarizer

A local, fully offline pipeline that takes a YouTube video (or a channel's latest video), downloads its audio, transcribes it to English, and produces a detailed summary — all from the command line.

Supports **Hindi and English** audio. No API keys required. Everything runs on your machine.

---

## What it does

```
YouTube URL / Channel URL
        │
        ▼
  Download audio (WAV)          ← yt-dlp + FFmpeg
        │
        ▼
  Transcribe to English         ← mlx-whisper (Whisper large-v3, Apple Silicon)
        │
        ▼
  Summarize content             ← Ollama (llama3, local LLM)
        │
        ▼
  summaries/<title>.summary.txt
```

---

## Requirements

- macOS with Apple Silicon (M1/M2/M3/M4)
- [Google Chrome](https://www.google.com/chrome/) — installed and signed into YouTube (used for cookie-based bot bypass)
- Internet connection for first-time model downloads (~6 GB total: Whisper large-v3 + llama3)

Everything else is installed automatically by `setup.sh`.

---

## Setup

Clone or download the project, then run:

```bash
bash setup.sh
```

This single command will:
1. Verify you're on macOS Apple Silicon
2. Install Homebrew (if missing)
3. Install Python 3.12, uv, FFmpeg, Node.js, Ollama (via Homebrew, if missing)
4. Pull the `llama3` model into Ollama
5. Create a Python virtual environment (`.venv/`) using `uv`
6. Install all Python dependencies from `pyproject.toml`
7. Verify every component is working

Once complete, activate the environment:

```bash
source .venv/bin/activate
```

---

## Usage

### Summarize a specific video

```bash
python main.py video "https://youtu.be/abc123"
```

### Summarize the latest video from a channel

```bash
python main.py channel "https://www.youtube.com/@ChannelName/videos"
```

Both commands save output to:
- `downloads/<title>.wav` — audio
- `downloads/<title>.txt` — English transcript
- `summaries/<title>.summary.txt` — structured summary

### Example summary output

```
**Topic**
A discussion on Google's latest Android updates including dark mode, Maps AI, and Gmail changes.

**Detailed Summary**
Google has announced several updates for Android users...

**Key Takeaways**
- Android will get a dark mode feature similar to iPhone
- Google Maps now supports natural language voice queries
- Gmail users can change their email address once every 12 months
...

**Conclusion**
These updates bring Android closer to feature parity with iOS...
```

---

## Automated Daily Runs (No Manual Intervention)

To automatically run the channel pipeline every morning at **7:00 AM IST**:

```bash
# Install the schedule
python scheduler.py install --channel "https://www.youtube.com/@ChannelName/videos"

# Check it's active
python scheduler.py status

# Watch live output when it runs
tail -f scheduler.log

# Remove the schedule
python scheduler.py uninstall
```

Uses macOS `launchd` — runs automatically in the background, even after reboots, without any terminal open.

---

## All Commands

See [COMMANDS.md](COMMANDS.md) for the full command reference including individual module commands for debugging each pipeline step.

---

## Project Structure

```
main.py              # Entry point — video and channel modes
script.py            # Downloads YouTube audio as WAV
get_latest_video.py  # Fetches latest video URL from a channel
transcribe.py        # Transcribes WAV → English text (Whisper)
summarize.py         # Summarizes text → structured summary (Ollama)
scheduler.py         # Installs/manages the daily launchd job
setup.sh             # One-shot setup script
pyproject.toml       # Python project config and dependencies
downloads/           # WAV files and transcripts
summaries/           # Summary files
scheduler.log        # Output log from automated runs
```

---

## Troubleshooting

**"Sign in to confirm you're not a bot"**
Make sure Google Chrome is installed and you are signed into YouTube in Chrome. The pipeline reads Chrome's cookies automatically.

**"Ollama server not running"**
Start Ollama before running the pipeline:
```bash
ollama serve
```

**Whisper model download is slow**
The `whisper-large-v3` model (~1.5 GB) downloads once to `~/.cache/huggingface/` on first run. Subsequent runs are fully offline.

**Re-running setup**
`setup.sh` is safe to re-run. It skips already-installed components and only installs what's missing.
