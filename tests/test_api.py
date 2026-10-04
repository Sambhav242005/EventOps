"""API tests: webhooks, approval edges, research/notice/export, custom-LLM. No network."""
import json
import os
os.environ.update({"LLM_BACKEND": "mock", "MESSAGING_BACKEND": "mock",
                   "CALL_BACKEND": "mock", "RESEARCH_LOOP": "0"})

from fastapi.testclient import TestClient

from app.db import get_conn, init_db, seed_demo
from app.main import send_outbound_now
from tests.helpers import MEM_NAME, ORG_NAME, first_vendor, mem_code, org_code


def fresh_client():
    import pathlib
    from app import db as _db
    p = pathlib.Path(_db.DB_PATH)
    if p.exists():
        p.unlink()
    init_db()
    seed_demo()
    from app.main import app
    return TestClient(app)


def auth(c, name=None, code=None):
    name = name or ORG_NAME
    code = code or org_code()
    tok = c.post("/api/login", json={"name": name, "passcode": code}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_protected_routes_need_login():
    with fresh_client() as c:
        assert c.get("/api/event").status_code == 401
        assert c.post("/api/approve", json={"id": 1}).status_code == 401
        assert c.get("/api/export/attendees").status_code == 401


def test_approve_edges():
    with fresh_client() as c:
        h = auth(c)
        assert c.post("/api/approve", json={"id": 9999}, headers=h).status_code == 404
        n = c.post("/api/notice", json={"audience": "team", "change": "x"}, headers=h).json()
        assert n["count"] >= 2
        a = c.post("/api/approve", json={"id": n["id"]}, headers=h).json()
        assert a["ok"] and a["send_at"]  # scheduled, NOT sent yet (buffer)
        r = c.post("/api/approve", json={"id": n["id"]}, headers=h)
        assert r.status_code == 400  # already approved, no double-schedule


def test_buffer_edit_cancel_and_worker_send():
    import asyncio
    with fresh_client() as c:
        h = auth(c)
        n = c.post("/api/notice", json={"audience": "team", "change": "gate A"}, headers=h).json()
        c.post("/api/approve", json={"id": n["id"]}, headers=h)
        # edit within buffer
        assert c.post("/api/edit_outbound", json={"id": n["id"], "body": "gate B"}, headers=h).json()["ok"]
        # force due + worker sends via mock backend
        conn = get_conn()
        conn.execute("UPDATE outbound_log SET send_at='2000-01-01T00:00:00+0000' WHERE id=?", (n["id"],))
        conn.commit()
        conn.close()
        out = asyncio.new_event_loop().run_until_complete(send_outbound_now(n["id"]))
        assert out["ok"] and out["sent"]
        # too late to edit/cancel after send
        assert c.post("/api/edit_outbound", json={"id": n["id"], "body": "x"}, headers=h).status_code == 400
        assert c.post("/api/cancel_outbound", json={"id": n["id"]}, headers=h).status_code == 400
        # cancel path stops the worker
        n2 = c.post("/api/notice", json={"audience": "team", "change": "y"}, headers=h).json()
        c.post("/api/approve", json={"id": n2["id"]}, headers=h)
        assert c.post("/api/cancel_outbound", json={"id": n2["id"]}, headers=h).json()["ok"]
        out2 = asyncio.new_event_loop().run_until_complete(send_outbound_now(n2["id"]))
        assert out2["ok"] is False


def test_participant_messaging_fanout():
    import asyncio
    with fresh_client() as c:
        h = auth(c)
        conn = get_conn()
        aid = conn.execute("SELECT id FROM attendees LIMIT 1").fetchone()["id"]
        conn.close()
        assert c.post("/api/attendee_phone", json={"id": aid, "phone": "+911234567890"}, headers=h).json()["ok"]
        n = c.post("/api/notice", json={"audience": "guests", "change": "doors open"}, headers=h).json()
        assert n["count"] >= 1
        conn = get_conn()
        conn.execute("UPDATE outbound_log SET status='approved', send_at='2000-01-01T00:00:00+0000' WHERE id=?", (n["id"],))
        conn.commit()
        conn.close()
        out = asyncio.new_event_loop().run_until_complete(send_outbound_now(n["id"]))
        assert out["ok"] and "+911234567890" in out["sent"]


def test_thresholds_fire_once_and_notify():
    import asyncio
    from app.research import set_notifier, threshold_checks
    notes: list[str] = []

    async def hook(text: str) -> None:
        notes.append(text)

    try:
        with fresh_client():
            set_notifier(hook)  # must be after startup: startup registers room notifier
            conn = get_conn()
            from datetime import datetime, timedelta
            soon = (datetime.now() + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S")
            conn.execute("UPDATE events SET date_time=?", (soon,))
            conn.commit()
            conn.close()
            loop = asyncio.new_event_loop()
            fired = loop.run_until_complete(threshold_checks(1))
            assert "T-5h" in fired and notes  # posted to room
            fired2 = loop.run_until_complete(threshold_checks(1))
            assert fired2 == []  # deduped, fires once
    finally:
        set_notifier(None)


def test_whatsapp_webhook_extract_and_vendor_update():
    with fresh_client() as c:
        h = auth(c)
        r = c.post("/webhooks/whatsapp",
                   json={"id": "w-api-1", "From": first_vendor()["phone"],
                         "Body": "Sorry, fully booked, cannot do it"}).json()
        assert r["ok"] and r["extracted"]["available"] is False
        ev = c.get("/api/event", headers=h).json()
        sharma = [v for v in ev["vendors"] if v["name"] == first_vendor()["name"]][0]
        assert sharma["status"] == "cancelled"
        # duplicate delivery -> deduped, no double-processing
        r2 = c.post("/webhooks/whatsapp",
                    json={"id": "w-api-1", "From": first_vendor()["phone"], "Body": "Yes!"}).json()
        assert r2.get("dedupe") is True
        # opt-out honored on next send path
        c.post("/webhooks/whatsapp", json={"id": "w-api-2", "From": "+9191",
                                            "Body": "stop"})
        from app import messaging as _m
        assert "+9191" in _m._opted_out


def test_telegram_webhook_posts_to_room():
    with fresh_client() as c:
        h = auth(c)
        r = c.post("/webhooks/telegram", json={"message": {"message_id": 7, "from": {"username": "ravi"},
                                                            "text": "on my way"}}).json()
        assert r["ok"]
        ev = c.get("/api/event", headers=h).json()
        assert any(m["text"] == "on my way" for m in ev["messages"])


def test_elevenlabs_webhook_transcript_and_failure():
    with fresh_client() as c:
        # failure path
        r = c.post("/webhooks/elevenlabs",
                   json={"type": "call_initiation_failure",
                         "data": {"conversation_id": "x", "failure_reason": "no-answer"}}).json()
        assert r["ok"]
        # transcription path stores + extracts
        conn = get_conn()
        conn.execute("INSERT INTO call_log(event_id,conversation_id,status,transcript,summary_json,duration_s,created_at)"
                     " VALUES(1,'conv-1','initiated','','{}',0,'t')")
        conn.commit()
        conn.close()
        r2 = c.post("/webhooks/elevenlabs",
                    json={"type": "post_call_transcription",
                          "data": {"conversation_id": "conv-1",
                                   "transcript": "Yes available, 45k for sound",
                                   "analysis": {}}}).json()
        assert r2["extracted"]["available"] is True
        # bad signature rejected when secret configured; valid SDK-scheme passes
        import hashlib
        import hmac as _hmac
        import time as _time
        os.environ["ELEVENLABS_WEBHOOK_SECRET"] = "s3cr3t"
        try:
            r3 = c.post("/webhooks/elevenlabs", json={"data": {}},
                        headers={"ElevenLabs-Signature": "wrong"})
            assert r3.status_code == 401
            body = b'{"type":"post_call_transcription","data":{"conversation_id":"c9"}}'
            t = str(int(_time.time()))
            sig = "t=%s,v0=%s" % (t, _hmac.new(b"s3cr3t", f"{t}.".encode() + body,
                                               hashlib.sha256).hexdigest())
            r4 = c.post("/webhooks/elevenlabs", content=body,
                        headers={"ElevenLabs-Signature": sig,
                                 "Content-Type": "application/json"})
            assert r4.json()["ok"]
        finally:
            del os.environ["ELEVENLABS_WEBHOOK_SECRET"]


def test_research_and_export_shapes():
    with fresh_client() as c:
        h = auth(c)
        r = c.post("/api/research", json={"category": "tent"}, headers=h).json()
        assert r["ok"] and len(r["ranked"]) >= 1
        assert r["ranked"][0]["reasons"]
        assert c.get("/api/export/nope", headers=h).status_code == 404
        csv_text = c.get("/api/export/tasks", headers=h).text
        assert csv_text.splitlines()[0].startswith("id,event_id")


def test_custom_llm_sse_shape():
    with fresh_client() as c:
        r = c.post("/v1/chat/completions",
                   json={"messages": [{"role": "user", "content": "hello"}]})
        assert r.status_code == 200
        assert "chat.completion.chunk" in r.text and "[DONE]" in r.text


def test_vendor_watch_suggests_next():
    import asyncio
    from app.research import candidate_search, vendor_watch
    with fresh_client():
        asyncio.new_event_loop().run_until_complete(candidate_search(1, "catering"))
        conn = get_conn()
        conn.execute("UPDATE vendors SET status='cancelled' WHERE id=1")
        conn.commit()
        conn.close()
        out = asyncio.new_event_loop().run_until_complete(vendor_watch(1))
        assert first_vendor()["name"] in out["flagged"]
        conn = get_conn()
        alert = conn.execute("SELECT text FROM alerts WHERE kind='vendor_watch' ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert "Suggested next" in alert["text"]
