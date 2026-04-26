# Meeting Intelligence Pipeline

## Concept

Access meeting video recordings from Google Drive, transcribe them, build project context, and generate outputs (PDF, slides, flowcharts) stored back in Drive.

## Architecture

```
Google Drive folder
  ├── drive.py        →  auth, list files, download videos
  ├── transcribe.py   →  existing pipeline (reuse)
  ├── extract.py      →  LLM extracts: decisions, actions, blockers per meeting
  ├── index.py        →  builds vector store across all meetings (RAG)
  ├── chat.py         →  Q&A over entire project history
  └── generate.py     →  creates PDF / PPTX / flowchart → uploads back to Drive
```

## Key Concern Areas

### 1. Google Drive Auth (OAuth 2.0)
- One-time browser login → saves token.json locally
- Subsequent runs use token.json (headless, no browser)
- credentials.json from Google Cloud Console + token.json must never be committed

### 2. Downloading Videos
- Resumable downloads for large files (meetings = 500MB–2GB)
- Recursive folder traversal by mimeType
- Drive API quota: 1,000 requests/100s — batch requests

### 3. Transcription at Scale
- Reuse existing whisper pipeline
- Track processed files by Drive file_id to avoid re-transcription
- 1 hour audio ≈ 10-15 min transcription on M-series

### 4. Building Project Context
- RAG needed (10 meetings × 10k tokens = too large for full stuffing)
- Structured extraction: decisions, action items, blockers, timelines per meeting
- Speaker identification matters for context

### 5. Output Generation
| Output | Library |
|---|---|
| PDF | `reportlab` or `weasyprint` |
| Slide deck | `python-pptx` (upload to Drive, auto-previews) |
| Flowchart | `graphviz` or `mermaid` → PNG/SVG |
| Google Slides | Google Slides API (complex, avoid if possible) |

### 6. Security
- Use `drive.readonly` scope if not writing back
- Team use → service account instead of OAuth

## Phased Approach
1. Auth + download + transcribe (reuse existing pipeline)
2. Structured extraction per meeting
3. RAG chat over all meetings
4. Output generation (PDF/slides)
