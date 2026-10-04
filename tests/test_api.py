"""API tests: webhooks, approval edges, research/notice/export, custom-LLM. No network."""
import json
import os
os.environ.update({"LLM_BACKEND": "mock", "MESSAGING_BACKEND": "mock",
                   "CALL_BACKEND": "mock", "RESEARCH_LOOP": "0"})

from fastapi.testclient import TestClient

from app.db import get_conn, init_db, seed_demo


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


def auth(c, name="Asha", code=None):
    code = code or os.environ.get("DEMO_ORG_PASS", "1111")
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
        assert c.post("/api/approve", json={"id": n["id"]}, headers=h).json()["ok"]
        r = c.post("/api/approve", json={"id": n["id"]}, headers=h)
        assert r.status_code == 400  # already sent, no double-send


def test_whatsapp_webhook_extract_and_vendor_update():
    with fresh_client() as c:
        h = auth(c)
        r = c.post("/webhooks/whatsapp",
                   json={"id": "w-api-1", "From": "+919100000001",
                         "Body": "Sorry, fully booked, cannot do it"}).json()
        assert r["ok"] and r["extracted"]["available"] is False
        ev = c.get("/api/event", headers=h).json()
        sharma = [v for v in ev["vendors"] if v["name"] == "Sharma Caterers"][0]
        assert sharma["status"] == "cancelled"
        # duplicate delivery -> deduped, no double-processing
        r2 = c.post("/webhooks/whatsapp",
                    json={"id": "w-api-1", "From": "+919100000001", "Body": "Yes!"}).json()
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
        # bad signature rejected when secret configured
        os.environ["ELEVENLABS_WEBHOOK_SECRET"] = "s3cr3t"
        try:
            r3 = c.post("/webhooks/elevenlabs", json={"data": {}},
                        headers={"xi-signature": "wrong"})
            assert r3.status_code == 401
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
        assert "Sharma Caterers" in out["flagged"]
        conn = get_conn()
        alert = conn.execute("SELECT text FROM alerts WHERE kind='vendor_watch' ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert "Suggested next" in alert["text"]
