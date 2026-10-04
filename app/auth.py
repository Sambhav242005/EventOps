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
        parts = token.split(".")
        # Membership tokens remain compatible with callers that only need the
        # signed display identity. Authorization uses verify_membership below.
        if len(parts) == 7:
            _b64mid, b64n, b64r, _b64o, _b64team, b64t, sig = parts
            body = ".".join(parts[:6])
        elif len(parts) == 4:
            b64n, b64r, b64t, sig = parts
            body = f"{b64n}.{b64r}.{b64t}"
        else:
            return None
        if not hmac.compare_digest(sig, hmac.new(SECRET.encode(), body.encode(),
                                                 hashlib.sha256).hexdigest()):
            return None
        if time.time() - int(_unb64(b64t).decode()) > TOKEN_TTL_S:
            return None
        return _unb64(b64n).decode(), _unb64(b64r).decode()
    except Exception:
        return None


def issue_token_for(member: dict) -> str:
    """Membership token carrying member_id. Payload: {mid, name, role, org, team}."""
    mid = str(member.get("id", member.get("mid", 0)))
    name = str(member.get("name", ""))
    role = str(member.get("role", "member"))
    org = member.get("org_id", member.get("org", 1))
    team = member.get("team_id", member.get("team", None))
    org_s = str(int(org) if org is not None else 1)
    team_s = "" if team is None else str(int(team))  # type: ignore[arg-type]
    ts = str(int(time.time()))
    parts = [_b64(mid.encode()), _b64(name.encode()), _b64(role.encode()),
             _b64(org_s.encode()), _b64(team_s.encode()), _b64(ts.encode())]
    body = ".".join(parts)
    sig = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_membership(token: str) -> dict | None:
    """Returns {id,name,role,org_id,team_id} looked up live from DB, or None.

    Role/name changes apply immediately since values come from the members
    row, not the token payload. Expiry TTL enforced as in verify_token.
    Legacy 3-part tokens return None (caller falls back to verify_token).
    """
    try:
        parts = token.split(".")
        if len(parts) != 7:
            return None
        b64mid, _b64n, _b64r, _b64o, _b64t, b64ts, sig = parts
        body = ".".join(parts[:6])
        if not hmac.compare_digest(sig, hmac.new(SECRET.encode(), body.encode(),
                                                 hashlib.sha256).hexdigest()):
            return None
        if time.time() - int(_unb64(b64ts).decode() or "0") > TOKEN_TTL_S:
            return None
        mid = int(_unb64(b64mid).decode())
    except Exception:
        return None
    try:
        from .db import get_conn
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT id,name,role,org_id,team_id FROM members WHERE id=?", (mid,)).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row:
        return None
    try:
        keys = row.keys()
    except Exception:
        keys = []
    org_id = row["org_id"] if "org_id" in keys else 1
    team_id = row["team_id"] if "team_id" in keys else None
    return {"id": int(row["id"]), "name": row["name"], "role": row["role"],
            "org_id": int(org_id) if org_id is not None else 1,
            "team_id": int(team_id) if team_id is not None else None}


def can_access(member: dict, event_id: int) -> bool:
    """Same org AND (event.team_id NULL OR equal OR member role organizer)."""
    try:
        from .db import get_conn
        conn = get_conn()
        try:
            ev = conn.execute(
                "SELECT org_id, team_id FROM events WHERE id=?", (event_id,)).fetchone()
        finally:
            conn.close()
        if not ev:
            return False
        try:
            ekeys = ev.keys()
        except Exception:
            ekeys = []
        eorg = ev["org_id"] if "org_id" in ekeys else 1
        eteam = ev["team_id"] if "team_id" in ekeys else None
        morg = member.get("org_id", member.get("org"))
        if morg is None:
            return False
        if int(morg) != int(eorg):
            return False
        if eteam is None:
            return True
        if member.get("role") == "organizer":
            return True
        mteam = member.get("team_id", member.get("team", None))
        if mteam is None:
            return False
        return int(mteam) == int(eteam)
    except Exception:
        return False
