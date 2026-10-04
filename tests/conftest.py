"""Test hermetics: mock backends + blank live secrets BEFORE app import.

Local .env.local may hold REAL keys (Twilio/ElevenLabs); snapshot/restore in
app.env preserves anything set here, so tests never touch live services.
"""
import os

os.environ.update({
    "LLM_BACKEND": "mock",
    "MESSAGING_BACKEND": "mock",
    "CALL_BACKEND": "mock",
    "RESEARCH_LOOP": "0",
    "BUFFER_MIN": "0",
    # Isolated test DB: the dev server (and other sessions) share ./eventops.db.
    # Tests must NEVER write approved/due rows there — the live sender_loop
    # would dispatch them to real people.
    "DATABASE_URL": "sqlite:////tmp/eventops_test.db",
    "ELEVENLABS_API_KEY": "",
    "ELEVENLABS_WEBHOOK_SECRET": "",
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_AUTH_TOKEN": "",
    "TWILIO_API_KEY_SID": "",
    "TWILIO_API_KEY_SECRET": "",
    "GEMINI_API_KEY": "",
})
