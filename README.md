# EventOps Agent — open-weight AI event coordinator

Shared event room where the team chats normally and tags **@agent**.
Gemma 4 proposes structured actions; a plain-Python decision layer gates risk;
a background researcher watches weather/vendors; vendors are reached on
WhatsApp/phone; attendance via QR + sign-in-sheet photo; one-click CSV export.

> Status: **M1 done** (room + agent core, mock LLM default). M2–M7 next.

## 5-minute setup (M1)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # default LLM_BACKEND=mock, no keys needed
uvicorn app.main:app --port 8000
# open http://localhost:8000
```

Run tests: `python -m pytest -q`

## Switch the brain

| Env | What |
|---|---|
| `LLM_BACKEND=mock` | keyword router, no setup (default) |
| `LLM_BACKEND=ollama` | `OLLAMA_MODEL=gemma4:31b-cloud` via `POST {OLLAMA_HOST}/api/chat` (strict JSON + 1 retry). Verify tag with `ollama list`; cloud models may need `ollama signin`. |
| `LLM_BACKEND=gemini` | Gemma 4 via Gemini API: `GEMINI_MODEL=gemma-4-31b-it` (alt `gemma-4-26b-a4b-it`), key from AI Studio. |

Every LLM call logs latency. Live phone turns (M6) use short prompts + `OLLAMA_FAST_MODEL`.

## Architecture (ASCII)

```
[team browser] --WS--> [FastAPI room] --context--> [LLMAdapter: mock|ollama|gemini]
      |                       |                           proposes Action{}
      |                 [SQLite: events/vendors/tasks/attendees/messages/logs]
      +-- M2: MessagingAdapter (telegram/whatsapp/mock) + approval gate
      +-- M5: research worker (Open-Meteo + SearchAdapter + LocalCatalog)
      +-- M6: CallAdapter (elevenlabs+twilio / mock) + post-call webhook
```

## Honest limitations (M1)

- Mock LLM is a keyword router, not real reasoning — plug Ollama/Gemini for the real demo.
- No auth on the room yet; single demo event; SQLite single-writer is fine for a fest team.
- Production WhatsApp needs a verified business account + approved templates; web-found vendors are unverified leads (M5 stores them as such).

## License

MIT — see LICENSE.
