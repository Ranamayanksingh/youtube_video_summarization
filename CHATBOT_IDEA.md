# Video Transcript Chatbot

## Concept

A local chatbot that lets you have a conversation about a transcribed YouTube video. The bot has knowledge of the video content and maintains conversation history.

## Two Approaches

### Approach 1: Full Context Stuffing (Start Here)
Simpler, faster to build. Stuff the entire transcript into the LLM's system prompt.

```
transcript .txt → system prompt → "Answer questions about this video: {transcript}"
                                        ↓
                              user asks questions
                                        ↓
                              LLM answers with context
```

- Works well for most videos (llama3 supports ~8k context)
- No chunking, no embeddings, no vector store
- Zero extra dependencies beyond what's already in the project

### Approach 2: RAG (If Transcripts Get Too Long)
For longer videos where full stuffing exceeds context window.

```
transcribed .txt
  ├── chunk.py     →  split into overlapping chunks (~500 tokens, 50 overlap)
  ├── embed.py     →  embed chunks → local vector store
  ├── chat.py      →  query → find top-k relevant chunks → LLM answers
  └── memory.py    →  maintain conversation history
```

**Vector store options (local, no server):**
- `sqlite-vec` — single SQLite file, zero extra deps (preferred)
- `ChromaDB` — easy, runs in-process
- `FAISS` — fastest, pure in-memory

## Performance on Apple Silicon

| Model | Speed | Quality | Size |
|---|---|---|---|
| `llama3` | ~15 tok/s | Good | 4.7GB |
| `mistral` | ~18 tok/s | Good | 4.1GB |
| `qwen2.5:3b` | ~35 tok/s | Decent | 1.9GB |
| `gemma3:2b` | ~40 tok/s | Decent | 1.6GB |

**Expected response time**: 5-15 seconds per reply with llama3. Acceptable for a chatbot.

Vector search on a single transcript (~5-50 chunks) is near-instant — not a bottleneck.

## Integration with Existing Project

```
main.py (existing pipeline)
  └── after transcribe.py → launch chat.py with the transcript
```

Or as a standalone:
```bash
python chat.py downloads/<title>.txt
```

## Open Questions

- CLI chat interface, or web UI (already have Flask from web_app.py)?
- Should it remember context across sessions, or fresh each time?
- Single video or multi-video knowledge base?

## Recommendation

Start with **Approach 1** (full context stuffing) using the existing `llama3` Ollama setup. Add a simple CLI loop. Upgrade to RAG only if needed.
