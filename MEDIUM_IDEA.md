# Medium Article Reader & Project Builder

## Concept

A tool that logs into Medium with credentials, reads articles, and scaffolds projects based on what it finds.

## Authentication

- **Playwright browser automation** — most reliable for paywalled content
- **Credentials**: stored in macOS Keychain via `keyring` library (no plaintext passwords)
- **Session persistence**: save cookies after login to avoid re-authenticating every run

## Architecture

```
input: article URL
  ├── auth.py       →  login via Playwright, session/cookie management
  ├── fetch.py      →  navigate to article, extract clean text (strip nav/ads)
  ├── store.py      →  save article + metadata (title, author, date, URL) locally
  └── build.py      →  LLM parses article → identifies project → scaffolds code/plan
```

## Build Step (LLM)

1. Extract article text
2. Prompt LLM: *"Identify the main technical project. Extract: goal, stack, key steps, code snippets"*
3. Use structured output to scaffold a project: directories, starter files, `PLAN.md`

## Open Questions

- Target specific authors/publications, or any Medium URL?
- Save articles locally for a library, or process immediately?
- "Build" step: generate actual code, or just a structured plan first?

## Stack Ideas

- `playwright` — browser automation + auth
- `keyring` — secure credential storage (macOS Keychain)
- `beautifulsoup4` / `readability-lxml` — clean article extraction
- Local LLM (Ollama) or Claude API — project parsing & scaffolding
