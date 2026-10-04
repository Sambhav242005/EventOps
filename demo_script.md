# 3-minute demo script — EventOps Agent

**Setup (before the audience arrives):** `uvicorn app.main:app` open on projector.
Logged in as Asha (organizer). Second window logged in as Ravi (member).
`LLM_BACKEND` = ollama (real Gemma) if venue wifi allows, else mock.

## 0:00–0:30 — The room
"College fest tomorrow. Team chats here, tags @agent. Vendors never install anything —
we reach them on WhatsApp and phone."

Show seeded event, 3 vendors, attendance 0/5.

## 0:30–1:10 — The crisis
Click **Simulate: caterer cancels**.

- Alert appears: "Sharma Caterers (catering) is cancelled with ~35h to go."
- Background researcher already ranked 3 backups from the local catalog with
  scores + reasons. "It never contacts anyone without approval."

## 1:10–1:50 — The fix
`@agent call the caterer` → high-risk → **call pending, organizer approval needed**.
Approve → mock call completes → transcript extracted
(available/price/conditions) → vendor row updated → summary posted in room.

Show the approval gate: log in as Ravi and try approving a guest-wide notice → **blocked, organizer only**.

## 1:50–2:20 — Guests + door
`@agent draft a message to guests` → preview with recipient count → approve →
logged in outbound_log.

Check in `QR-0002` → "Checked in". Scan again → "Already checked in at …".
Photo of paper sign-in sheet → high-confidence marked, rest to human review.
One click → attendees CSV (formula-sanitized).

## 2:20–3:00 — Why it wins
- Open-weight Gemma 4 does reasoning + vision + extraction (Ollama default,
  Gemini API switchable). Voice/messaging are swappable adapters.
- Rule-based decision layer: the LLM proposes, plain Python disposes.
- Auth: signed tokens, roles enforced server-side — no name spoofing.
- Close: "Event is ON. Nobody re-messaged anyone by hand."

## Fallbacks if wifi dies
Everything above runs in mock mode with zero keys. Say so out loud —
judges love an honest offline demo.
