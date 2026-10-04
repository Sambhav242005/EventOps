"""Extra unit coverage: sender dispatch, audiences, tz-naive hours,
norm_wa, parse_json_lenient, Ollama fallback, queue helpers. No network."""
import asyncio
import os

os.environ.update({"LLM_BACKEND": "mock", "MESSAGING_BACKEND": "mock",
                   "CALL_BACKEND": "mock", "RESEARCH_LOOP": "0"})


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def fresh_db():
    import pathlib
    from app import db as _db
    p = pathlib.Path(_db.DB_PATH)
    if p.exists():
        p.unlink()
    from app.db import init_db, seed_demo
    init_db()
    seed_demo()


# ---------- norm_wa ----------
def test_norm_wa_strips_prefix():
    from app.messaging import norm_wa
    assert norm_wa("whatsapp:+919100000001") == "+919100000001"
    assert norm_wa("whatsapp:+14155238886") == "+14155238886"
    assert norm_wa("+919100000001") == "+919100000001"
    assert norm_wa("  +9191  ") == "+9191"
    assert norm_wa("") == ""


# ---------- parse_json_lenient ----------
def test_parse_json_lenient_fenced():
    from app.llm import parse_json_lenient
    fenced = '```json\n{"type": "answer", "text": "hi"}\n```'
    assert parse_json_lenient(fenced) == {"type": "answer", "text": "hi"}
    fenced2 = '```\n{"type": "answer", "text": "yo"}\n```'
    assert parse_json_lenient(fenced2)["text"] == "yo"


def test_parse_json_lenient_bare():
    from app.llm import parse_json_lenient
    assert parse_json_lenient('{"type": "answer", "text": "bare"}')["text"] == "bare"


def test_parse_json_lenient_embedded():
    from app.llm import parse_json_lenient
    s = 'Here is the result: {"type": "answer", "text": "emb"} thanks!'
    assert parse_json_lenient(s)["text"] == "emb"


# ---------- hours_to_event: naive = Asia/Kolkata wall-clock ----------
def test_hours_to_event_naive_is_kolkata_wallclock():
    from datetime import datetime, timezone
    from app.decision import hours_to_event
    old = os.environ.get("EVENT_TIMEZONE")
    os.environ["EVENT_TIMEZONE"] = "Asia/Kolkata"
    try:
        # 12:00 UTC == 17:30 IST; naive 18:00 wall-clock => 0.5h away.
        now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
        hrs = hours_to_event("2030-01-01T18:00:00", now=now)
        assert 0.4 < hrs < 0.6, f"naive treated as wrong tz: {hrs}"
        # aware +05:30 must agree with naive wall-clock
        hrs2 = hours_to_event("2030-01-01T18:00:00+05:30", now=now)
        assert abs(hrs - hrs2) < 0.01
    finally:
        if old is None:
            os.environ.pop("EVENT_TIMEZONE", None)
        else:
            os.environ["EVENT_TIMEZONE"] = old


def test_hours_to_event_invalid_returns_999():
    from app.decision import hours_to_event
    assert hours_to_event("not-a-date") == 999.0


# ---------- audience_phones ----------
def test_audience_phones_team_guests_all_to_empty():
    fresh_db()
    from app.db import get_conn
    from app.main import audience_phones
    # team: only organizer has a phone in seed
    team = audience_phones("team")
    assert len(team) == 1 and team[0].startswith("+91")
    # guests: seed attendees have no phones
    assert audience_phones("guests") == []
    # give one guest a number
    conn = get_conn()
    try:
        conn.execute("UPDATE attendees SET phone='+911234567890' WHERE id=1")
        conn.commit()
    finally:
        conn.close()
    guests = audience_phones("guests")
    assert guests == ["+911234567890"]
    allp = audience_phones("all")
    assert set(allp) == set(team + guests)
    assert audience_phones("team+guests") == allp
    # direct to: + whatsapp: prefix stripping
    assert audience_phones("to:+919999999999") == ["+919999999999"]
    assert audience_phones("to:whatsapp:+919999999999") == ["+919999999999"]
    assert audience_phones("to:") == []
    # empty audience -> empty
    assert audience_phones("") == []
    # bare number passthrough
    assert audience_phones("+918888888888") == ["+918888888888"]


# ---------- queue helpers ----------
def test_queue_outbound_and_queue_call_helpers():
    fresh_db()
    from app.db import get_conn
    from app.main import queue_call, queue_outbound
    oid = queue_outbound("mock", "team", "hello team", {}, "tester")
    assert isinstance(oid, int) and oid > 0
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM outbound_log WHERE id=?", (oid,)).fetchone()
    finally:
        conn.close()
    assert row["status"] == "pending" and row["recipient"] == "audience:team"
    assert row["body"] == "hello team"
    cid = queue_call({"vendor_id": 1}, "tester")
    assert isinstance(cid, int) and cid > 0
    conn = get_conn()
    try:
        crow = conn.execute("SELECT * FROM call_log WHERE id=?", (cid,)).fetchone()
    finally:
        conn.close()
    assert crow["status"] == "pending"


# ---------- sender dispatch ----------
def test_send_outbound_now_dispatches_due_team():
    fresh_db()
    from app import messaging as _m
    from app.db import get_conn
    from app.main import audience_phones, queue_outbound, send_outbound_now
    from app.messaging import MockAdapter
    _m._opted_out.clear()
    MockAdapter.outbox.clear()
    oid = queue_outbound("mock", "team", "doors open at gate A", {}, "tester")
    conn = get_conn()
    try:
        conn.execute("UPDATE outbound_log SET status='approved', send_at='2000-01-01T00:00:00+0000'"
                     " WHERE id=?", (oid,))
        conn.commit()
    finally:
        conn.close()
    out = run(send_outbound_now(oid))
    assert out["ok"] is True and out["sent"]
    assert set(out["sent"]) == set(audience_phones("team"))
    conn = get_conn()
    try:
        st = conn.execute("SELECT status FROM outbound_log WHERE id=?", (oid,)).fetchone()["status"]
    finally:
        conn.close()
    assert st == "sent"


def test_send_outbound_now_rejects_pending_and_empty_audience():
    fresh_db()
    from app.main import queue_outbound, send_outbound_now
    # pending (not approved) must not send
    oid = queue_outbound("mock", "team", "not approved yet", {}, "tester")
    out = run(send_outbound_now(oid))
    assert out["ok"] is False
    # approved but no phones (fresh seed guests have none) -> failed
    oid2 = queue_outbound("mock", "guests", "no numbers here", {}, "tester")
    from app.db import get_conn
    conn = get_conn()
    try:
        conn.execute("UPDATE outbound_log SET status='approved', send_at='2000-01-01T00:00:00+0000'"
                     " WHERE id=?", (oid2,))
        conn.commit()
    finally:
        conn.close()
    out2 = run(send_outbound_now(oid2))
    assert out2["ok"] is False
    assert "no phone" in out2.get("error", "").lower()
    conn = get_conn()
    try:
        st = conn.execute("SELECT status FROM outbound_log WHERE id=?", (oid2,)).fetchone()["status"]
    finally:
        conn.close()
    assert st == "failed"


def test_due_query_matches_sender_loop():
    """Direct due-query: same WHERE as sender_loop dispatches approved-past only."""
    fresh_db()
    from app.db import get_conn
    from app.main import queue_outbound
    past = queue_outbound("mock", "team", "past due", {}, "t")
    future = queue_outbound("mock", "team", "future", {}, "t")
    pending = queue_outbound("mock", "team", "pending past", {}, "t")
    conn = get_conn()
    try:
        conn.execute("UPDATE outbound_log SET status='approved', send_at='2000-01-01T00:00:00+0000'"
                     " WHERE id=?", (past,))
        conn.execute("UPDATE outbound_log SET status='approved', send_at='2999-01-01T00:00:00+0000'"
                     " WHERE id=?", (future,))
        # pending keeps send_at '' (as queued)
        conn.commit()
        due = conn.execute(
            "SELECT id FROM outbound_log WHERE status='approved' AND send_at<>'' "
            "AND send_at <= strftime('%Y-%m-%dT%H:%M:%S','now','localtime')").fetchall()
    finally:
        conn.close()
    ids = {r["id"] for r in due}
    assert past in ids
    assert future not in ids
    assert pending not in ids


# ---------- Ollama fallback ----------
def test_double_dispatch_claimed_once():
    import asyncio
    from app.db import get_conn, init_db, seed_demo
    from app.main import send_outbound_now
    import pathlib
    from app import db as _db
    p = pathlib.Path(_db.DB_PATH)
    if p.exists():
        p.unlink()
    init_db()
    seed_demo()
    conn = get_conn()
    cur = conn.execute("""INSERT INTO outbound_log(event_id,channel,recipient,body,status,
                        approved_by,idempotency_key,send_at,created_at)
                        VALUES(1,'mock','audience:team','hi','approved','t','k-claim','2000-01-01T00:00:00+0000','t')""")
    oid = int(cur.lastrowid)
    conn.commit()
    conn.close()
    loop = asyncio.new_event_loop()
    first = loop.run_until_complete(send_outbound_now(oid))
    second = loop.run_until_complete(send_outbound_now(oid))
    assert first["ok"] and second["ok"] is False


def test_whatsapp_no_fake_template_text():
    import asyncio
    import os
    from app.messaging import WhatsAppAdapter
    os.environ["TWILIO_ACCOUNT_SID"] = "ACx"
    try:
        os.environ.pop("TWILIO_TEMPLATE_SID", None)
        a = WhatsAppAdapter()
        r = asyncio.new_event_loop().run_until_complete(a.send("+911234567890", "hello", "k-notpl"))
        assert r["ok"] is False and r["status"] == "template_needed"
        assert "[template" not in r.get("body", "")
    finally:
        del os.environ["TWILIO_ACCOUNT_SID"]


def test_ollama_fallback_graceful_on_unreachable_host():
    from app.llm import OllamaAdapter
    a = OllamaAdapter(model="no-such-primary", host="http://127.0.0.1:1", timeout_s=2)
    a.fallback = "no-such-fallback"
    a.timeout = 2
    action = run(a.complete_action("ctx", "hello vendor"))
    assert action.type == "answer"
    assert "unreachable" in action.text.lower()
    # no exception raised -> graceful


def test_channel_routing_and_validation():
    from app.main import valid_channel, save_message
    from app.db import get_conn, init_db, seed_demo
    import pathlib
    from app import db as _db
    assert valid_channel("vendors") == "vendors"
    assert valid_channel("evil") == "room"
    p = pathlib.Path(_db.DB_PATH)
    if p.exists():
        p.unlink()
    init_db()
    seed_demo()
    save_message(1, "vendor", "hi", "vendors")
    save_message(1, "x", "hello", "evil-channel")
    conn = get_conn()
    rows = conn.execute("SELECT channel FROM messages WHERE sender IN ('vendor','x')").fetchall()
    conn.close()
    assert [r["channel"] for r in rows] == ["vendors", "room"]
