"""LLMAdapter: Ollama (default) | Gemini (switchable) | Mock (tests/demo).

Verified against official docs (Oct 2026):
- Ollama POST {host}/api/chat {model, messages[{role,content,images?}],
  tools?, format(JSON schema)?, stream:false}. Images = base64 strings.
  Cloud models (e.g. gemma4:31b-cloud) may require `ollama signin`; verify tag with `ollama list`.
  Tool-calling support is model-dependent -> we use strict JSON output validated
  with Pydantic + one retry, so we work even without tool support.
- Gemini API: Gemma 4 via Gemini API, model IDs `gemma-4-31b-it`,
  `gemma-4-26b-a4b-it` (ai.google.dev/gemma/docs/core/gemma_on_gemini_api),
  google-genai client: client.models.generate_content(model, contents).
  REST: POST .../v1beta/models/{id}:generateContent?key=KEY.
Latency of every call is measured and logged (31B cloud can be slow for live
phone turns -> CALL_FAST_MODEL env + short prompts; see README).
"""
from __future__ import annotations

import json
import logging
import os
import time

import httpx
from dotenv import load_dotenv

from .models import ACTION_JSON_SCHEMA, SYSTEM_PROMPT, AgentAction

load_dotenv()
log = logging.getLogger("eventops.llm")


class LLMAdapter:
    async def complete_action(self, room_context: str, request: str,
                              event_state: str = "") -> AgentAction:
        raise NotImplementedError


def coerce_action(data: dict) -> AgentAction:
    try:
        return AgentAction.model_validate(data)
    except Exception:
        # Never crash the room on a bad model reply.
        txt = str(data)[:500] if isinstance(data, dict) else str(data)[:500]
        return AgentAction(type="answer", text=txt or "Sorry, I could not parse that.")


class MockLLMAdapter(LLMAdapter):
    """Keyword router so M1 demo + tests run with zero setup."""

    async def complete_action(self, room_context: str, request: str,
                              event_state: str = "") -> AgentAction:
        t0 = time.time()
        r = request.lower()
        if any(k in r for k in ("call ", "call the", "phone", "ring")):
            a = AgentAction(type="call_vendor",
                            text="I can call the vendor. Proposing a call brief for organizer approval.",
                            args={"category": "catering"})
        elif any(k in r for k in ("backup", "replace", "research", "find", "alternative")):
            a = AgentAction(type="research",
                            text="On it — researching backup vendors now.",
                            args={"category": "catering"})
        elif any(k in r for k in ("draft", "message", "notify", "tell ", "announce", "whatsapp")):
            a = AgentAction(type="draft_message",
                            text="Draft ready for preview + approval.",
                            args={"audience": "team", "body": request})
        elif any(k in r for k in ("attend", "check-in", "check in", "present")):
            a = AgentAction(type="mark_attendance",
                            text="Tell me the names (or upload the sign-in photo in M3) and I'll mark them.",
                            args={})
        elif any(k in r for k in ("csv", "export", "spreadsheet")):
            a = AgentAction(type="export_csv",
                            text="I can export attendees/vendors/tasks to CSV.",
                            args={"tables": ["attendees", "vendors"]})
        elif "?" in request or len(request.split()) < 3:
            a = AgentAction(type="ask_clarification",
                            text="Could you say a little more — who/what is this for?",
                            args={})
        else:
            a = AgentAction(type="answer", text=f"Got it: {request[:200]}", args={})
        log.info("mock llm latency_ms=%d", int((time.time() - t0) * 1000))
        return a


class OllamaAdapter(LLMAdapter):
    def __init__(self, model: str | None = None, host: str | None = None,
                 timeout_s: float = 60.0):
        self.model = model or os.environ.get("OLLAMA_MODEL", "gemma4:31b-cloud")
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.timeout = float(os.environ.get("OLLAMA_TIMEOUT_S", timeout_s))

    async def complete_action(self, room_context: str, request: str,
                              event_state: str = "") -> AgentAction:
        prompt = (f"{SYSTEM_PROMPT}\n\nEvent state:\n{event_state}\n\n"
                  f"Recent room:\n{room_context}\n\nRequest: {request}\nJSON:")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": ACTION_JSON_SCHEMA,
        }
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.host}/api/chat", json=payload)
                r.raise_for_status()
                content = r.json().get("message", {}).get("content", "")
        except Exception as e:
            log.warning("ollama failed: %s", e)
            return AgentAction(type="answer",
                               text="LLM backend unreachable (Ollama). Running in mock mode — check OLLAMA_HOST/MODEL.")
        log.info("ollama latency_ms=%d model=%s", int((time.time() - t0) * 1000), self.model)
        for _ in range(2):  # strict JSON + one retry
            try:
                return coerce_action(json.loads(content))
            except Exception:
                content = '{"type": "answer", "text": ' + json.dumps(content[:400]) + "}"
        return AgentAction(type="answer", text="Sorry, I could not understand the model reply.")


class GeminiAdapter(LLMAdapter):
    """Gemma 4 through the Gemini API. REST generateContent, JSON mode."""

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.environ.get("GEMINI_MODEL", "gemma-4-31b-it")
        self.key = api_key or os.environ.get("GEMINI_API_KEY", "")

    async def complete_action(self, room_context: str, request: str,
                              event_state: str = "") -> AgentAction:
        if not self.key:
            return AgentAction(type="answer",
                               text="GEMINI_API_KEY not set. Set it or use LLM_BACKEND=mock/ollama.")
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}"
               f":generateContent?key={self.key}")
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text":
                f"Event state:\n{event_state}\n\nRecent room:\n{room_context}\n\nRequest: {request}"}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseJsonSchema": ACTION_JSON_SCHEMA},
        }
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(url, json=body)
                r.raise_for_status()
                parts = r.json()["candidates"][0]["content"]["parts"]
                content = "".join(p.get("text", "") for p in parts)
        except Exception as e:
            log.warning("gemini failed: %s", e)
            return AgentAction(type="answer", text="Gemini backend unreachable. Try mock/ollama mode.")
        log.info("gemini latency_ms=%d model=%s", int((time.time() - t0) * 1000), self.model)
        try:
            return coerce_action(json.loads(content))
        except Exception:
            return AgentAction(type="answer", text=content[:500])


def get_llm() -> LLMAdapter:
    backend = os.environ.get("LLM_BACKEND", "mock").lower()
    if backend == "ollama":
        return OllamaAdapter()
    if backend == "gemini":
        return GeminiAdapter()
    return MockLLMAdapter()
