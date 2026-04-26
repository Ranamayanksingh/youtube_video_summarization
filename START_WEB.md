# How to Start the Web App + Telegram Bot (Public Access)

Every time you want to run the full stack, open **three separate terminal tabs**.

---

## Tab 1 — Start the web app

```bash
cd ~/youtube-video-audio-data
source .venv/bin/activate
uvicorn web_app:app --host 0.0.0.0 --port 8000
```

Keep this tab open. You'll see request logs here.

---

## Tab 2 — Start the Telegram bot

```bash
cd ~/youtube-video-audio-data
source .venv/bin/activate
python telegram_bot.py
```

This runs independently from the web app. Even if the web app goes down, the bot keeps working.

**Bot features:**
- `/start` — send a YouTube URL → get a summary
- Follow-up Q&A about the video
- `/new` — start fresh with a different video
- Scheduled morning briefings (managed via the web app's subscriptions page)

---

## Tab 3 — Start the Cloudflare tunnel

```bash
cloudflared tunnel --url http://localhost:8000
```

Wait a few seconds. You'll see a line like:

```
Your quick Tunnel has been created! Visit it at:
https://some-random-name.trycloudflare.com
```

Open that URL on any device. Login with your token from `.env` → `WEB_AUTH_TOKEN`.

---

## To stop

- **Tab 1**: `Ctrl+C`
- **Tab 2**: `Ctrl+C`
- **Tab 3**: `Ctrl+C`

---

## Notes

- The `trycloudflare.com` URL changes every time you restart the tunnel — share the new one each session.
- The Telegram bot and web app are **independent services** — either can run without the other.
- Ollama must be running in the background for summarization to work:
  ```bash
  ollama serve
  ```
  (only needed if Ollama isn't already running as a background service)
