# Cloudflare Tunnel Setup

Expose your local web UI securely to the internet — no port forwarding, no static IP, HTTPS included.

## Prerequisites

- A Cloudflare account (free tier is fine)
- A domain managed by Cloudflare (even a cheap one works)
- The web UI running locally: `uvicorn web_app:app --host 0.0.0.0 --port 8000`

---

## Step 1: Install cloudflared

```bash
brew install cloudflared
```

Verify:
```bash
cloudflared --version
```

---

## Step 2: Authenticate

```bash
cloudflared tunnel login
```

This opens your browser to Cloudflare. Select the domain you want to use. A credentials file is saved to `~/.cloudflared/`.

---

## Step 3: Create a named tunnel

```bash
cloudflared tunnel create yt-summarizer
```

Note the **Tunnel ID** (UUID) printed — you'll need it in the next step.

---

## Step 4: Create the tunnel config

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: <your-tunnel-UUID>
credentials-file: /Users/<your-username>/.cloudflared/<your-tunnel-UUID>.json

ingress:
  - hostname: summarizer.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

Replace:
- `<your-tunnel-UUID>` with the UUID from Step 3
- `<your-username>` with your macOS username
- `summarizer.yourdomain.com` with your chosen subdomain

---

## Step 5: Add the DNS record

```bash
cloudflared tunnel route dns yt-summarizer summarizer.yourdomain.com
```

This creates a CNAME record in your Cloudflare DNS automatically.

---

## Step 6: Run the tunnel

```bash
cloudflared tunnel run yt-summarizer
```

Your app is now accessible at `https://summarizer.yourdomain.com`.

---

## Step 7: Run tunnel as a background service (auto-start on boot)

```bash
sudo cloudflared service install
sudo launchctl start com.cloudflare.cloudflared
```

To stop it:
```bash
sudo launchctl stop com.cloudflare.cloudflared
```

---

## Quick test (no domain required)

If you don't have a domain, use a temporary public URL for testing:

```bash
cloudflared tunnel --url http://localhost:8000
```

Cloudflare prints a random `trycloudflare.com` URL. This is ephemeral — disappears when you stop the command.

---

## Security checklist before going live

- [ ] `WEB_AUTH_TOKEN` is set in `.env` — your login password
- [ ] `SECRET_KEY` is set in `.env` — used to sign session cookies
- [ ] Both are long (20+ chars), random strings
- [ ] You've tested login/logout works locally first
- [ ] (Optional) Enable **Cloudflare Access** in the Cloudflare dashboard for a second layer of auth (email OTP, Google SSO, etc.)

---

## Running everything together

Open two terminal tabs:

**Tab 1 — Web app:**
```bash
cd ~/youtube-video-audio-data
source .venv/bin/activate
uvicorn web_app:app --host 0.0.0.0 --port 8000
```

**Tab 2 — Tunnel:**
```bash
cloudflared tunnel run yt-summarizer
```

Or if you installed `cloudflared` as a launchd service, only Tab 1 is needed.
