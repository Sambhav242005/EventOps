# EventOps Agent — open-weight AI event coordinator

Shared event room where the team chats normally and tags **@agent**.
Gemma 4 proposes structured actions; a plain-Python **decision layer** gates risk;
a background **researcher** watches weather/vendors and ranks backups; vendors are
reached on **WhatsApp/phone**; attendance via QR + sign-in-sheet photo; one-click CSV.

> Status: **M1–M7 complete** in mock mode. Live backends (Ollama/Gemini/ElevenLabs/
> Twilio/Telegram) are real code behind env switches, untested here for lack of keys.

## 5-minute setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # defaults: all mock backends, demo passcodes
uvicorn app.main:app --port 8000
# open http://localhost:8000 — login Asha/1111 (organizer) or Ravi/2222 (member)
```

Tests: `python -m pytest -q` (22 tests, no network).

## Switch the brain / channels

| Env | What |
|---|---|
| `LLM_BACKEND=mock` | keyword router, zero setup (default) |
| `LLM_BACKEND=ollama` | `OLLAMA_MODEL=gemma4:31b-cloud` via `POST {OLLAMA_HOST}/api/chat`, strict-JSON schema + 1 retry. Confirm tag with `ollama list`; cloud models may need `ollama signin`. |
| `LLM_BACKEND=gemini` | Gemma 4 via Gemini API (`GEMINI_MODEL=gemma-4-31b-it`, alt `gemma-4-26b-a4b-it`), key from AI Studio. |
| `MESSAGING_BACKEND=telegram\|whatsapp\|mock` | Telegram Bot API `sendMessage`; Twilio sandbox (`whatsapp:+14155238886`, template outside 24h window, `stop` opt-out) |
| `CALL_BACKEND=elevenlabs\|mock` | ElevenLabs outbound via Twilio number + HMAC post-call webhook; mock returns a canned transcript |
| `SEARCH_BACKEND=local\|web` | Local `vendors_catalog.json` (default) vs web-grounding stub |

Every LLM call logs latency. Live phone turns use short prompts + `OLLAMA_FAST_MODEL`.
ElevenLabs **Custom LLM mode is supported**: point the agent's Custom LLM URL at our
`POST /v1/chat/completions` (OpenAI-compatible SSE) to make Gemma the call brain.
Default mode: ElevenLabs voice LLM for the call + Gemma 4 post-call extraction.
Hindi/Hinglish: `lang` param plumbed through; quality untested — default `en` for demo.

## Architecture (ASCII)

```
[team browser] --WS(token)--> [FastAPI room] --context--> [LLMAdapter: mock|ollama|gemini]
      |                              |                        proposes Action{}
      |                        [SQLite: events/vendors/candidates/tasks/
      |                         attendees/messages/outbound/call/alerts/logs]
      +-- MessagingAdapter (telegram/whatsapp/mock): approvals, idempotency,
      |      24h-window templates, opt-out, webhook dedupe, field extraction
      +-- CallAdapter (elevenlabs/mock): brief+guardrails, caps, webhook summary
      +-- Research worker: Open-Meteo weather, vendor watch, candidate search
      |      (dedupe alerts, 10 searches/h cap, decision_log, sources+timestamps)
      +-- Door: QR/manual/photo check-in (vision JSON, fuzzy-match, human review)
      +-- CSV export (formula-sanitized) for all 7 tables
```

## Auth (demo-grade, honest)

Passcode login → HMAC-signed expiring tokens; WS identity = token (no spoofing);
organizer-only enforced **server-side** for mass sends, vendor flips, calls.
NOT production auth: short demo codes, no TLS/rotation story. Change
`AUTH_SECRET`, `DEMO_ORG_PASS`, `DEMO_MEMBER_PASS` beyond local demo.

## Security notes

- All inbound text (vendor replies, transcripts, web) treated as untrusted DATA;
  extraction returns fields only — `test_prompt_injection_ignored` covers a
  "send the guest list" attack.
- Webhook dedupe by provider message ID; idempotency keys on sends; CSV cells
  starting `=+‑@` quoted; duplicate check-ins reported, never double-counted.

## Honest limitations

- Mock LLM is a keyword router, not reasoning — plug Ollama/Gemini for real demos.
- No TLS/auth hardening; single demo event; SQLite single-writer (fine for a fest team).
- Production WhatsApp needs a verified business account + approved templates;
  web-found vendors are unverified leads (stored with source + confidence + timestamp).
- whatsapp-web.js (unofficial Puppeteer client) was considered and rejected:
  violates WhatsApp ToS (ban risk), breaks on Web changes, needs always-on Chromium —
  wrong dependency for a crisis tool. Twilio sandbox/Meta Cloud API instead.

## License

MIT — see LICENSE.
