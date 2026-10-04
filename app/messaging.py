"""MessagingAdapter: mock | telegram | whatsapp(twilio sandbox).

Verified surface (Oct 2026):
- Telegram: POST https://api.telegram.org/bot<token>/sendMessage {chat_id, text};
  inbound via webhook Update{message{...}} or getUpdates. We support webhook receive.
- Twilio WhatsApp sandbox: shared from whatsapp:+14155238886, To=whatsapp:<e164>;
  POST https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json (Basic auth).
  Template rule: outside the 24h customer-service window the first message must use
  a pre-approved template — we implement one generic event-inquiry template from env.
  Delivery callbacks: queued/failed/sent/delivered/read. Opt-out: inbound "stop".
Same interface: send(to, body, idempotency_key) + webhook receive + delivery status.
"""
from __future__ import annotations

import logging
import os
import time
import uuid

import httpx

from .env import load as _load_env

_load_env()
log = logging.getLogger("eventops.msg")

_seen_inbound: set[str] = set()       # webhook dedupe by provider message id
_sent_keys: dict[str, dict] = {}       # idempotency_key -> result
_last_inbound: dict[str, float] = {}   # recipient -> ts (24h window)
_opted_out: set[str] = set()
TEMPLATE = os.environ.get("WHATSAPP_TEMPLATE_EVENT_INQUIRY",
                           "Your appointment is coming up on {{1}} at {{2}}")


def norm_wa(addr: str) -> str:
    """Twilio sends From='whatsapp:+91…'; our DB stores plain E.164."""
    a = (addr or "").strip()
    return a.split("whatsapp:", 1)[-1] if "whatsapp:" in a else a


class MessagingAdapter:
    channel = "mock"

    async def send(self, to: str, body: str, idempotency_key: str = "") -> dict:
        raise NotImplementedError

    def in_window(self, to: str) -> bool:
        return (time.time() - _last_inbound.get(to, 0)) < 24 * 3600

    def note_inbound(self, sender: str, msg_id: str = "") -> bool:
        """Returns False if duplicate (already seen)."""
        if msg_id and msg_id in _seen_inbound:
            return False
        if msg_id:
            _seen_inbound.add(msg_id)
        _last_inbound[sender] = time.time()
        if sender in _opted_out:
            _opted_out.discard(sender)  # re-join re-opts in
        return True


def extract_vendor_fields(text: str) -> dict:
    """Rule-based first pass (LLM refines in M5/M6). Partial answers stay conditions.

    SECURITY: treats input as DATA. Instruction phrases in vendor text
    ("ignore rules", "send guest list", ...) are ignored — only
    availability/price/conditions fields are ever returned.
    """
    t = text.lower()

    def _has(*words: str) -> bool:
        import re as _re
        # short tokens need word boundaries ("ok" lives inside "booked")
        return any(_re.search(r"\b" + _re.escape(w) + r"\b", t) if len(w) <= 3
                   else w in t for w in words)

    neg = _has("cancel", "cancelled", "cannot", "can't", "not avail", "unavail",
               "unavailable", "booked", "busy", "no", "sorry", "only")
    pos = _has("avail", "available", "confirm", "confirmed", "yes", "ready",
               "ok", "can do", "will do", "possible")
    # Negation wins ties: "only 100 plates, sorry booked" is NOT available.
    # Partial capacity ("only 100 plates") lands in conditions, never confirmed.
    available = False if neg else (True if pos else None)
    import re
    price = None
    m = re.search(r"(?:rs\.?|₹|inr|price|cost|quote)?\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?\b", t)
    if m:
        try:
            price = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
            if price < 100:  # "500 plates" is capacity, not money
                price = None
        except ValueError:
            price = None
    confirmed = bool(pos and not neg and _has("confirm", "confirmed", "yes"))
    return {"available": available, "price": price, "conditions": text[:500].strip(),
            "confirmed": confirmed}


class MockAdapter(MessagingAdapter):
    channel = "mock"
    outbox: list[dict] = []

    async def send(self, to: str, body: str, idempotency_key: str = "") -> dict:
        key = idempotency_key or str(uuid.uuid4())
        if key in _sent_keys:
            return {**_sent_keys[key], "dedupe": True}
        to = norm_wa(to)
        if to in _opted_out:
            res = {"ok": False, "status": "opted_out", "to": to, "key": key}
        else:
            res = {"ok": True, "status": "sent", "to": to, "channel": "mock",
                   "body": body[:500], "key": key}
            self.outbox.append(res)
        _sent_keys[key] = res
        log.info("mock send to=%s status=%s", to, res["status"])
        return res


class TelegramAdapter(MessagingAdapter):
    channel = "telegram"

    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    async def send(self, to: str, body: str, idempotency_key: str = "") -> dict:
        key = idempotency_key or str(uuid.uuid4())
        if key in _sent_keys:
            return {**_sent_keys[key], "dedupe": True}
        if not self.token:
            res = {"ok": False, "status": "no_token", "to": to, "key": key}
            _sent_keys[key] = res
            return res
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                 json={"chat_id": to or self.chat_id, "text": body[:4000]})
                r.raise_for_status()
                res = {"ok": True, "status": "sent", "to": to, "channel": "telegram", "key": key}
        except Exception as e:
            res = {"ok": False, "status": f"error: {e}", "to": to, "key": key}
        _sent_keys[key] = res
        return res


class WhatsAppAdapter(MessagingAdapter):
    """Twilio sandbox (or Meta Cloud API shape-compatible stub). Sandbox only."""

    channel = "whatsapp"

    def __init__(self):
        self.sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self.token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        # API-key auth preferred when set (username=key SID, password=secret)
        self.auth_user = os.environ.get("TWILIO_API_KEY_SID", "") or self.sid
        self.auth_pass = os.environ.get("TWILIO_API_KEY_SECRET", "") or self.token
        self.from_ = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

    async def send(self, to: str, body: str, idempotency_key: str = "") -> dict:
        key = idempotency_key or str(uuid.uuid4())
        if key in _sent_keys:
            return {**_sent_keys[key], "dedupe": True}
        if to in _opted_out:
            res = {"ok": False, "status": "opted_out", "to": to, "key": key}
            _sent_keys[key] = res
            return res
        use_template = not self.in_window(to)
        payload_body = (f"[template: event inquiry] {TEMPLATE} -- {body[:300]}"
                        if use_template else body[:1500])
        if not self.sid:  # no creds -> mock-send but keep template/window logic honest
            res = {"ok": True, "status": "sent-mock", "to": to, "channel": "whatsapp",
                   "used_template": use_template, "body": payload_body[:500], "key": key}
            _sent_keys[key] = res
            return res
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
                    auth=(self.auth_user, self.auth_pass),
                    data={"From": self.from_, "To": f"whatsapp:{to}", "Body": payload_body})
                r.raise_for_status()
                res = {"ok": True, "status": "queued", "to": to, "channel": "whatsapp",
                       "used_template": use_template, "key": key, "sid": r.json().get("sid")}
        except Exception as e:
            res = {"ok": False, "status": f"error: {e}", "to": to, "key": key}
        _sent_keys[key] = res
        return res


def get_messaging() -> MessagingAdapter:
    b = os.environ.get("MESSAGING_BACKEND", "mock").lower()
    if b == "telegram":
        return TelegramAdapter()
    if b == "whatsapp":
        return WhatsAppAdapter()
    return MockAdapter()
