"""Demo-grade auth: passcode login -> HMAC-signed tokens; roles enforced server-side.

Threat model (honest, see README): stops name-spoofing in the room (anyone
could previously approve as the organizer), so organizer-only gates actually hold.
NOT production auth: no TLS/password-hygiene claims, passcodes are short demo
codes — set DEMO_ORG_PASS / DEMO_MEMBER_PASS in .env and change defaults.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

SECRET = os.environ.get("AUTH_SECRET", "dev-only-change-me")
TOKEN_TTL_S = 12 * 3600


def hash_code(code: str) -> str:
    return hashlib.sha256(f"eventops:{code}".encode()).hexdigest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(name: str, role: str) -> str:
    body = f"{_b64(name.encode())}.{_b64(role.encode())}.{_b64(str(int(time.time())).encode())}"
    sig = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(token: str) -> tuple[str, str] | None:
    """Returns (name, role) or None. Constant-time sig compare; checks TTL."""
    try:
        b64n, b64r, b64t, sig = token.split(".")
        body = f"{b64n}.{b64r}.{b64t}"
        if not hmac.compare_digest(sig, hmac.new(SECRET.encode(), body.encode(),
                                                 hashlib.sha256).hexdigest()):
            return None
        if time.time() - int(_unb64(b64t).decode()) > TOKEN_TTL_S:
            return None
        return _unb64(b64n).decode(), _unb64(b64r).decode()
    except Exception:
        return None
