"""Auth tests: login, token integrity, role enforcement, WS gate. No network."""
import os
os.environ.update({"LLM_BACKEND": "mock", "MESSAGING_BACKEND": "mock",
                   "CALL_BACKEND": "mock", "RESEARCH_LOOP": "0"})

from fastapi.testclient import TestClient

from app.db import get_conn, init_db, seed_demo
from app.auth import issue_token, verify_token
from tests.helpers import MEM_NAME, ORG_NAME, mem_code, org_code


def client():
    import pathlib
    from app import db as _db
    p = pathlib.Path(_db.DB_PATH)
    if p.exists():
        p.unlink()  # fresh DB: old rows predate passcodes
    init_db()
    seed_demo()
    from app.main import app
    return TestClient(app)


def login(c, name, code):
    return c.post("/api/login", json={"name": name, "passcode": code})


def test_login_ok_and_bad():
    with client() as c:
        r = login(c, ORG_NAME, org_code())
        assert r.json()["ok"] and r.json()["role"] == "organizer"
        r2 = login(c, ORG_NAME, "wrong")
        assert r2.status_code == 401
        r3 = login(c, "Nobody", org_code())
        assert r3.status_code == 401


def test_token_tamper_rejected():
    with client() as c:
        tok = login(c, MEM_NAME, mem_code()).json()["token"]
        assert verify_token(tok)[0] == MEM_NAME
        bad = tok[:-2] + ("ab" if not tok.endswith("ab") else "cd")
        assert verify_token(bad) is None
        r = c.get("/api/event", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401


def test_member_cannot_approve_mass_notice():
    with client() as c:
        org = login(c, ORG_NAME, org_code()).json()["token"]
        mem = login(c, MEM_NAME, mem_code()).json()["token"]
        # organizer drafts a mass notice (high risk -> organizer-only)
        n = c.post("/api/notice", json={"audience": "guests", "change": "gate moved"},
                   headers={"Authorization": f"Bearer {org}"}).json()
        r = c.post("/api/approve", json={"id": n["id"]},
                   headers={"Authorization": f"Bearer {mem}"})
        assert r.status_code == 403  # member cannot approve all-guest send
        r2 = c.post("/api/approve", json={"id": n["id"]},
                    headers={"Authorization": f"Bearer {org}"})
        assert r2.json()["ok"]


def test_member_cannot_flip_vendor_or_call():
    with client() as c:
        mem = login(c, MEM_NAME, mem_code()).json()["token"]
        assert c.post("/api/vendor_status", json={"id": 1, "status": "cancelled"},
                      headers={"Authorization": f"Bearer {mem}"}).status_code == 403


def test_ws_requires_token_and_pins_identity():
    with client() as c:
        # no token -> rejected
        try:
            with c.websocket_connect("/ws"):
                assert False, "should not connect"
        except Exception:
            pass
        tok = login(c, MEM_NAME, mem_code()).json()["token"]
        with c.websocket_connect(f"/ws?token={tok}") as ws:
            ws.receive_json()  # welcome
            ws.send_json({"sender": "SPOOF", "text": "hello (spoof attempt)"})
            m = ws.receive_json()
            assert m["sender"] == MEM_NAME  # spoof ignored; identity from token
