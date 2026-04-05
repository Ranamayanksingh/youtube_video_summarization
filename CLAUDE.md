# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Python pipeline that downloads audio from YouTube videos, transcribes it to English (supports Hindi and English audio), and summarizes the content using a local LLM. Designed for Apple Silicon (M-series) Macs.

## Setup

```bash
bash setup.sh        # one-time: installs all system deps, creates .venv, verifies
source .venv/bin/activate
```

`setup.sh` checks and installs: Homebrew, Python 3.12, uv, FFmpeg, Node.js, Ollama, llama3 model, Chrome (manual). Creates `.venv` via `uv` from `pyproject.toml`. Use `.venv/` going forward — the old `venv/` is superseded.

System requirements (managed by setup.sh): FFmpeg, Node.js (yt-dlp JS challenges), Ollama running locally, Google Chrome (YouTube cookie extraction).

## Core Commands

```bash
# Summarize a specific video (download → transcribe → summarize)
python main.py video "<youtube_video_url>"

# Fetch latest video from a channel and summarize it
python main.py channel "<youtube_channel_url>"
```

## Scheduler (Automated Daily Run at 7 AM IST)

```bash
python scheduler.py install --channel "<youtube_channel_url>"  # activate
python scheduler.py status      # check if active
python scheduler.py uninstall   # remove
tail -f scheduler.log           # monitor automated run output
```

Uses macOS `launchd` — persists across reboots. Runs `main.py channel` at 01:30 UTC (7:00 AM IST) daily.

## Individual Module Commands

```bash
python script.py "<url>"              # download audio as WAV only
python get_latest_video.py "<channel_url>"  # print latest video URL, no download
python transcribe.py [--overwrite] [--dir downloads]  # transcribe all WAVs
python summarize.py [--overwrite] [--model llama3] [--summaries-dir summaries]
```

## Architecture

The pipeline has four stages, each in its own module. `main.py` is the single entry point that composes them.

```
main.py
  ├── get_latest_video.py  →  fetches latest video metadata from a channel (yt-dlp, extract_flat)
  ├── script.py            →  downloads audio as WAV (yt-dlp + FFmpeg postprocessor)
  ├── transcribe.py        →  transcribes WAV → English .txt (mlx-whisper large-v3, task="translate")
  └── summarize.py         →  summarizes .txt → .summary.txt (Ollama llama3, structured prompt)
```

**Key design decisions:**
- `transcribe.py` exposes both `transcribe_file(wav_path)` (single file, used by `main.py`) and `transcribe_all(dir)` (batch, used standalone). Always uses `task="translate"` so Hindi and English both produce English output.
- `summarize.py` saves to a separate `summaries/` directory, not alongside the transcripts.
- Bot detection in `script.py` is handled via `cookiesfrombrowser: ('chrome',)` + a Chrome User-Agent. The `tv_embedded` player client is configured but yt-dlp falls back to `android_vr` automatically.
- `scheduler.py` writes a plist to `~/Library/LaunchAgents/com.youtube-summarizer.daily.plist`.

## Output Locations

| File | Location |
|---|---|
| Audio (WAV) | `downloads/<title>.wav` |
| Transcript | `downloads/<title>.txt` |
| Summary | `summaries/<title>.summary.txt` |
| Scheduler log | `scheduler.log` |

## Models

- **Transcription**: `mlx-community/whisper-large-v3-mlx` — cached at `~/.cache/huggingface/` after first run (~1.5 GB download)
- **Summarization**: `llama3` via Ollama — already pulled locally (4.7 GB). Other available models: `mistral`, `deepseek-r1:8b`, `qwen3-coder:30b`
