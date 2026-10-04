"""CallAdapter: mock (default) | elevenlabs (outbound via Twilio number).

Verified (Oct 2026): ElevenLabs outbound_call(agent_id, agent_phone_number_id,
to_number E.164, conversation_initiation_client_data{dynamic_variables,
conversation_config_override{first_message, language}}). Post-call webhooks:
post_call_transcription (+analysis.transcript_summary), call_initiation_failure
{busy,no-answer,...}; HMAC verify; store transcript in call_log; LLM extracts
{available, price, conditions, follow_up}.
Custom-LLM mode (confirmed supported): we expose /v1/chat/completions
OpenAI-compatible SSE in main.py forwarding to Ollama/Gemma — set the agent's
Custom LLM URL to it. Default mode (README): ElevenLabs agent LLM for the call,
Gemma 4 for post-call extraction. Hindi/Hinglish: tested flag; default 'en',
fallback documented.
"""
from __future__ import annotations

import logging
import os
import time
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("eventops.call")

CALL_BRIEF_GUARDRAILS = (
    "Introduce as an AI assistant calling for the event team. Ask availability, "
    "price, conditions. NEVER agree to bookings/payments, NEVER share budget or "
    "other vendors' contacts. Hand off to a human if asked. Language: {lang}."
)


def build_call_brief(vendor: dict, event: dict, lang: str = "en") -> dict:
    return {
        "purpose": f"Check {vendor.get('category')} availability for {event.get('name')}",
        "ask": ["available at event time?", "price?", "conditions/capacity?"],
        "do_not": ["no bookings", "no payments", "no budget sharing"],
        "first_message": (f"Hello {vendor.get('name')}, this is an AI assistant calling "
                          f"for {event.get('name')}. Do you have two minutes?"),
        "guardrails": CALL_BRIEF_GUARDRAILS.format(lang=lang),
        "dynamic_variables": {"vendor_name": vendor.get("name"),
                               "event_name": event.get("name"),
                               "event_time": event.get("date_time")},
        "max_duration_s": int(os.environ.get("CALL_MAX_DURATION_S", "180")),
    }


class CallAdapter:
    channel = "mock"

    async def start_call(self, to: str, brief: dict) -> dict:
        raise NotImplementedError


class MockCallAdapter(CallAdapter):
    channel = "mock"

    async def start_call(self, to: str, brief: dict) -> dict:
        cid = f"mock-{uuid.uuid4().hex[:8]}"
        transcript = (f"Agent: {brief.get('first_message','Hello')}\n"
                      f"Vendor({to}): Yes, available, 60k, 500 plates veg.")
        log.info("mock call to=%s id=%s", to, cid)
        return {"ok": True, "conversation_id": cid, "status": "completed",
                "transcript": transcript, "duration_s": 42}


class ElevenLabsCallAdapter(CallAdapter):
    channel = "elevenlabs"

    def __init__(self):
        self.key = os.environ.get("ELEVENLABS_API_KEY", "")
        self.agent_id = os.environ.get("ELEVENLABS_AGENT_ID", "")
        self.phone_id = os.environ.get("ELEVENLABS_PHONE_NUMBER_ID", "")

    async def start_call(self, to: str, brief: dict) -> dict:
        if not (self.key and self.agent_id and self.phone_id):
            return {"ok": False, "status": "missing_config",
                    "hint": "set ELEVENLABS_API_KEY/AGENT_ID/PHONE_NUMBER_ID or use CALL_BACKEND=mock"}
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    "https://api.elevenlabs.io/v1/convai/twilio/outbound-call",
                    headers={"xi-api-key": self.key},
                    json={"agent_id": self.agent_id,
                          "agent_phone_number_id": self.phone_id,
                          "to_number": to,
                          "conversation_initiation_client_data": {
                              "conversation_config_override": {
                                  "agent": {"first_message": brief.get("first_message", ""),
                                            "language": brief.get("language", "en")}},
                              "dynamic_variables": brief.get("dynamic_variables", {})}})
                r.raise_for_status()
                data = r.json()
                return {"ok": True, "conversation_id": data.get("conversation_id", ""),
                        "status": "initiated"}
        except Exception as e:
            return {"ok": False, "status": f"error: {e}"}


def get_calls() -> CallAdapter:
    return ElevenLabsCallAdapter() if os.environ.get("CALL_BACKEND", "mock").lower() == "elevenlabs" \
        else MockCallAdapter()


def extract_call_fields(transcript: str) -> dict:
    from .messaging import extract_vendor_fields
    f = extract_vendor_fields(transcript)
    f["follow_up"] = "human callback" if any(
        k in transcript.lower() for k in ("callback", "human", "owner", "come", "visit")) else ""
    return f
