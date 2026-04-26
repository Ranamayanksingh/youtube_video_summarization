# Product Research & Market Analysis

## What This Project Does

A local-first YouTube intelligence pipeline: download → transcribe (Hindi/English) → summarize → deliver via Telegram. Runs entirely on-device (Apple Silicon), private, no cloud costs.

---

## Potential Users

### Tier 1: High Pain, Ready to Pay

**1. Independent Researchers & Academics**
- Watch hours of lecture/conference YouTube content weekly
- Need citable transcripts, not just summaries
- Pain: no good tool that works offline with Hindi content

**2. Indian Professionals / Multilingual Knowledge Workers**
- Consume Hindi YouTube (finance, tech, news) but work in English
- The Hindi→English translation capability is a *killer differentiator* — almost no tool does this well locally

**3. Content Creators / YouTubers**
- Monitor competitors' channels daily
- Need: topic extraction, timestamps, "what did they cover this week"

**4. Journalists & Newsletter Writers**
- Track specific channels for stories
- Need: alerts, quote extraction, key claims flagged

### Tier 2: Institutional / B2B

**5. EdTech / Corporate L&D Teams**
- Convert training video libraries into searchable knowledge bases
- Would pay per-seat for a web app version

**6. Market Research Firms**
- Monitor industry YouTubers (analysts, influencers) for signal
- Need: multi-channel tracking, structured output, export to CSV/Notion

**7. Podcast/Media Companies**
- Auto-generate show notes, chapters, SEO descriptions from video content

---

## Competitive Landscape

| Tool | Gap vs. This Project |
|---|---|
| Otter.ai / Descript | No YouTube ingestion, cloud-only, no Hindi |
| Riverside.fm | Recording tool, not analysis |
| Summarize.tech | Basic summaries, no offline, no Hindi, no scheduling |
| Glasp / Merlin | Browser extensions, shallow summaries |
| Whisper + GPT-4 | Requires coding, no pipeline, cloud costs |

**Core moat**: offline/private + Hindi→English + scheduler + Telegram delivery. No competitor does all four.

---

## Feature Roadmap

### Phase 1: Make it distributable (unblock non-technical users)
- [ ] **Web UI** (FastAPI + simple HTML or Streamlit) — replace the CLI entirely
- [ ] **Multi-channel watchlist** — track N channels, not just one
- [ ] **Cross-platform support** — currently Mac-only; Docker container would open Linux/Windows
- [ ] **Cloud model option** — let users swap Ollama for OpenAI/Gemini API key (lowers hardware barrier)

### Phase 2: Output quality & depth
- [ ] **Timestamped chapters** — "at 4:32 he explains X" with deeplinks
- [ ] **Q&A over transcript** — "ask a question about this video" (RAG)
- [ ] **Key claims / fact extraction** — structured bullet points separate from prose summary
- [ ] **Multi-language summary output** — summarize in Hindi *or* English based on preference
- [ ] **Sentiment & tone tagging** — useful for news/market monitoring

### Phase 3: Distribution & retention
- [ ] **Email digest** — daily email instead of (or alongside) Telegram
- [ ] **Notion / Obsidian export** — push summaries directly to PKM tools
- [ ] **Searchable archive** — SQLite-backed index across all past summaries
- [ ] **Slack/WhatsApp delivery** — Telegram is niche in the West; Slack matters for teams
- [ ] **Web app with auth** — multi-user, team sharing

### Phase 4: Monetization features
- [ ] **API access** — let developers query your pipeline (charge per video)
- [ ] **Playlist/batch processing** — summarize entire playlists or channels going back N days
- [ ] **Custom summary prompts** — users define "what I want extracted" (e.g., "always extract stock tickers mentioned")
- [ ] **White-label / embed** — sell to media companies

---

## Monetization Models

| Model | Fit |
|---|---|
| **SaaS subscription** ($10–30/mo) | Best for Phase 3+ web app |
| **Self-hosted one-time license** ($49–99) | Targets privacy-conscious power users now |
| **API pricing** (per video) | B2B/developer market |
| **Freemium OSS** | Open source core, paid cloud/hosting |

---

## Sharpest Near-Term Bets

1. **Add multi-channel watchlist** — single biggest UX gap right now, low effort
2. **Package as a Docker image** — removes the Mac-only constraint, 10x addressable market
3. **Build a minimal web UI** — makes it sellable/shareable without CLI knowledge
4. **Hindi→English is the headline feature** — lean into the Indian professional market; it's underserved and large
