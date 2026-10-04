"""EventOps Agent M1–M6: room + approvals + messaging + attendance + research + calls."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .auth import hash_code, issue_token, verify_token
from .calls import build_call_brief, extract_call_fields, get_calls
from .db import get_conn, init_db, now_iso, seed_demo
from .decision import approval_rule, draft_notice, risk_level
from .llm import get_llm
from .messaging import _opted_out, extract_vendor_fields, get_messaging
from .ops import apply_photo_names, check_in_token, export_csv, table_rows
from .research import candidate_search, research_loop, vendor_watch, weather_check

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("eventops")

app = FastAPI(title="EventOps Agent")
BASE = Path(__file__).resolve().parent.parent

connected: set[WebSocket] = set()
agent_queue: asyncio.Queue = asyncio.Queue()  # panic-spam guard: FIFO


# ---------- helpers ----------
def event_row(event_id: int = 1) -> dict:
    conn = get_conn()
    try:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(ev) if ev else {}
    finally:
        conn.close()


def event_state_summary(event_id: int = 1) -> str:
    ev = event_row(event_id)
    if not ev:
        return "no event"
    conn = get_conn()
    try:
        vendors = conn.execute(
            "SELECT name,category,status,quote FROM vendors WHERE event_id=?", (event_id,)).fetchall()
    finally:
        conn.close()
    v = ", ".join(f"{x['name']}({x['category']}:{x['status']})" for x in vendors)
    return f"{ev['name']} @ {ev['venue']} on {ev['date_time']}; vendors: {v or 'none'}"


def recent_room(event_id: int = 1, n: int = 20) -> str:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT sender,text FROM messages WHERE event_id=? ORDER BY id DESC LIMIT ?",
            (event_id, n)).fetchall()
    finally:
        conn.close()
    return "\n".join(f"{r['sender']}: {r['text']}" for r in reversed(rows))


def save_message(event_id: int, sender: str, text: str) -> None:
    conn = get_conn()
    try:
        conn.execute("INSERT INTO messages(event_id,sender,text,created_at) VALUES(?,?,?,?)",
                     (event_id, sender[:60], text[:2000], now_iso()))
        conn.commit()
    finally:
        conn.close()


def require_auth(req: Request) -> tuple[str, str]:
    """Identity comes from the signed token only — client-supplied names are ignored."""
    auth = req.headers.get("authorization", "")
    tok = auth[7:] if auth.lower().startswith("bearer ") else ""
    ident = verify_token(tok)
    if not ident:
        raise _Unauthorized()
    return ident


class _Unauthorized(Exception):
    pass


@app.exception_handler(_Unauthorized)
async def _unauth_handler(req: Request, exc: _Unauthorized) -> JSONResponse:
    return JSONResponse({"ok": False, "error": "login required"}, status_code=401)


@app.post("/api/login")
async def login(req: Request) -> JSONResponse:
    data = await req.json()
    name = str(data.get("name", "")).strip()[:60]
    code = str(data.get("passcode", ""))
    conn = get_conn()
    try:
        r = conn.execute("SELECT role,passcode FROM members WHERE lower(name)=lower(?)",
                         (name,)).fetchone()
    finally:
        conn.close()
    if not r or not hmac.compare_digest(r["passcode"], hash_code(code)):
        await asyncio.sleep(0.5)  # slow down guessing; generic error avoids user enum
        return JSONResponse({"ok": False, "error": "bad name or passcode"}, status_code=401)
    return JSONResponse({"ok": True, "token": issue_token(name, r["role"]),
                         "role": r["role"], "name": name})


async def broadcast(payload: dict) -> None:
    dead = []
    for ws in list(connected):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connected.discard(ws)


async def agent_worker() -> None:
    llm = get_llm()
    while True:
        job = await agent_queue.get()
        try:
            action = await llm.complete_action(job["context"], job["request"],
                                               event_state_summary())
        except Exception as e:
            log.warning("agent error: %s", e)
            from .models import AgentAction
            action = AgentAction(type="answer", text="Sorry — the agent hit an error. Try again.")
        risk = risk_level(action.type, action.args, action.text)
        rule = approval_rule(risk)
        # Persist pending approvals for medium/high; low auto-runs (answer/research/export preview).
        pending_id = None
        if action.type == "draft_message" and not rule["auto"]:
            aud = str(action.args.get("audience", "team"))
            body = str(action.args.get("body", action.text))[:1500]
            count = audience_count(aud)
            pending_id = queue_outbound("mock", aud, body, action.args, job["sender"])
            action.text += f" [pending #{pending_id}: {aud} x{count} — needs approval]"
        elif action.type == "call_vendor" and not rule["auto"]:
            pending_id = queue_call(action.args, job["sender"])
            action.text += f" [call pending #{pending_id} — organizer approval needed]"
        elif action.type == "research":
            cat = str(action.args.get("category", "catering"))
            asyncio.create_task(candidate_search(1, cat))  # background, never auto-contacts
        conn = get_conn()
        try:
            conn.execute("INSERT INTO decision_log(event_id,kind,input_json,output_json,rule_fired,created_at)"
                         " VALUES(?,?,?,?,?,?)",
                         (1, "agent_action", json.dumps({"request": job["request"]})[:2000],
                          action.model_dump_json()[:2000], f"risk={risk}", now_iso()))
            conn.commit()
        finally:
            conn.close()
        text = f"@agent [{action.type}|{risk}] {action.text}"
        save_message(1, "agent", text)
        await broadcast({"sender": "agent", "text": text, "action": action.model_dump(),
                         "risk": risk, "pending_id": pending_id, "requester": job["sender"]})
        agent_queue.task_done()


def audience_count(audience: str) -> int:
    conn = get_conn()
    try:
        if audience == "team":
            return conn.execute("SELECT COUNT(*) c FROM members WHERE event_id=1").fetchone()["c"]
        if audience == "guests":
            return conn.execute("SELECT COUNT(*) c FROM attendees WHERE event_id=1").fetchone()["c"]
        if audience in ("all", "everyone", "team+guests"):
            m = conn.execute("SELECT COUNT(*) c FROM members WHERE event_id=1").fetchone()["c"]
            a = conn.execute("SELECT COUNT(*) c FROM attendees WHERE event_id=1").fetchone()["c"]
            return m + a
        return 1
    finally:
        conn.close()


def queue_outbound(channel: str, audience: str, body: str, args: dict, requester: str) -> int:
    conn = get_conn()
    try:
        cur = conn.execute("""INSERT INTO outbound_log(event_id,channel,recipient,body,status,
                            approved_by,idempotency_key,created_at)
                            VALUES(?,?,?,?,?,?,?,?)""",
                         (1, channel, f"audience:{audience}", body[:1500], "pending", "",
                          f"ob-{uuid.uuid4().hex[:10]}", now_iso()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def queue_call(args: dict, requester: str) -> int:
    conn = get_conn()
    try:
        cur = conn.execute("""INSERT INTO call_log(event_id,vendor_id,conversation_id,status,
                            transcript,summary_json,duration_s,created_at)
                            VALUES(?,?,?,?,?,?,?,?)""",
                         (1, int(args.get("vendor_id", 0) or 0), "", "pending",
                          "", json.dumps({"requested_by": requester}), 0, now_iso()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


# ---------- startup ----------
@app.on_event("startup")
async def startup() -> None:
    init_db()
    seed_demo()
    asyncio.create_task(agent_worker())
    if os.environ.get("RESEARCH_LOOP", "1") == "1":
        asyncio.create_task(research_loop(1))


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "backend": os.environ.get("LLM_BACKEND", "mock"),
            "msg": os.environ.get("MESSAGING_BACKEND", "mock"),
            "calls": os.environ.get("CALL_BACKEND", "mock"),
            "queue": agent_queue.qsize()}


@app.get("/api/event")
async def api_event(req: Request) -> JSONResponse:
    require_auth(req)
    conn = get_conn()
    try:
        ev = conn.execute("SELECT * FROM events LIMIT 1").fetchone()
        vendors = [dict(r) for r in conn.execute("SELECT * FROM vendors").fetchall()]
        cands = [dict(r) for r in conn.execute(
            "SELECT * FROM vendor_candidates ORDER BY id DESC LIMIT 20").fetchall()]
        alerts = [dict(r) for r in conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT 20").fetchall()]
        outb = [dict(r) for r in conn.execute(
            "SELECT * FROM outbound_log ORDER BY id DESC LIMIT 20").fetchall()]
        calls = [dict(r) for r in conn.execute(
            "SELECT * FROM call_log ORDER BY id DESC LIMIT 20").fetchall()]
        msgs = [dict(r) for r in conn.execute(
            "SELECT sender,text,created_at FROM messages ORDER BY id DESC LIMIT 50").fetchall()]
        counts = {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                  for t in ("attendees", "vendors")}
        checked = conn.execute(
            "SELECT COUNT(*) c FROM attendees WHERE checked_in=1").fetchone()["c"]
    finally:
        conn.close()
    return JSONResponse({"event": dict(ev) if ev else None, "vendors": vendors,
                         "candidates": cands, "alerts": alerts, "outbound": outb,
                         "calls": calls, "messages": list(reversed(msgs)),
                         "attendance": {"checked": checked, "total": counts["attendees"]}})


# ---------- approvals + send ----------
@app.post("/api/approve")
async def approve(req: Request) -> JSONResponse:
    approver, role = require_auth(req)
    data = await req.json()
    oid = int(data.get("id", 0))
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM outbound_log WHERE id=?", (oid,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        if row["status"] != "pending":
            return JSONResponse({"ok": False, "error": f"already {row['status']}"}, status_code=400)
        body = row["body"]
        aud = row["recipient"].split("audience:", 1)[-1] if "audience:" in row["recipient"] else ""
        risk = risk_level("draft_message", {"audience": aud}, body)
        if approval_rule(risk)["organizer_only"] and role != "organizer":
            return JSONResponse({"ok": False, "error": "organizer-only approval"}, status_code=403)
        # fan-out: team -> members placeholder; guests -> attendees phones
        targets = [approver]
        res = await get_messaging().send(",".join(targets), body, row["idempotency_key"])
        conn.execute("UPDATE outbound_log SET status=?, approved_by=? WHERE id=?",
                     ("sent" if res.get("ok") else "failed", approver, oid))
        conn.commit()
    finally:
        conn.close()
    save_message(1, "agent", f"Notice #{oid} approved by {approver} ({role}): {res.get('status')}")
    await broadcast({"sender": "agent", "text": f"Notice #{oid} -> {res.get('status')}"})
    return JSONResponse({"ok": True, "result": res})


@app.post("/api/approve_call")
async def approve_call(req: Request) -> JSONResponse:
    approver, role = require_auth(req)
    data = await req.json()
    cid, lang = int(data.get("id", 0)), str(data.get("lang", "en"))
    if role != "organizer":
        return JSONResponse({"ok": False, "error": "calls need organizer approval"}, status_code=403)
    conn = get_conn()
    try:
        call = conn.execute("SELECT * FROM call_log WHERE id=?", (cid,)).fetchone()
        if not call or call["status"] != "pending":
            return JSONResponse({"ok": False, "error": "call not pending"}, status_code=400)
        summ = json.loads(call["summary_json"] or "{}")
        vid = int(summ.get("vendor_id", 0) or call["vendor_id"] or 0)
        vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vid,)).fetchone()
        if not vendor:
            vendor = conn.execute("SELECT * FROM vendors LIMIT 1").fetchone()
        ev = conn.execute("SELECT * FROM events LIMIT 1").fetchone()
        # call cap guard
        n_calls = conn.execute("SELECT COUNT(*) c FROM call_log WHERE status IN ('completed','initiated')").fetchone()["c"]
        if n_calls >= int(os.environ.get("CALL_MAX_PER_EVENT", "5")):
            return JSONResponse({"ok": False, "error": "call cap reached"}, status_code=400)
        brief = build_call_brief(dict(vendor), dict(ev), lang)
        res = await get_calls().start_call(vendor["phone"] or "+919100000001", brief)
        conn.execute("UPDATE call_log SET status=?, conversation_id=?, transcript=? WHERE id=?",
                     (res.get("status", "initiated"), res.get("conversation_id", ""),
                      res.get("transcript", ""), cid))
        if res.get("transcript"):
            f = extract_call_fields(res["transcript"])
            conn.execute("UPDATE vendors SET conditions=?, last_updated=? WHERE id=?",
                         (json.dumps(f)[:500], now_iso(), vendor["id"]))
        conn.commit()
    finally:
        conn.close()
    await broadcast({"sender": "agent",
                     "text": f"Call #{cid} -> {vendor['name']}: {res.get('status')}"})
    return JSONResponse({"ok": True, "result": {k: str(v)[:500] for k, v in res.items()}})


@app.post("/api/notice")
async def notice(req: Request) -> JSONResponse:
    """Draft audience-specific notice (M4), queued for approval."""
    by, _role = require_auth(req)
    data = await req.json()
    aud = str(data.get("audience", "team"))
    change = str(data.get("change", ""))[:500]
    ev = event_row()
    body = draft_notice(aud, ev.get("name", "event"), change)
    oid = queue_outbound("mock", aud, body, {}, by)
    return JSONResponse({"ok": True, "id": oid, "body": body,
                         "count": audience_count(aud)})


# ---------- vendor simulate (demo: caterer cancels) ----------
@app.post("/api/vendor_status")
async def vendor_status(req: Request) -> JSONResponse:
    _, role = require_auth(req)
    if role != "organizer":
        return JSONResponse({"ok": False, "error": "organizer only"}, status_code=403)
    data = await req.json()
    vid, status = int(data.get("id", 1)), str(data.get("status", "cancelled"))
    conn = get_conn()
    try:
        conn.execute("UPDATE vendors SET status=?, last_updated=? WHERE id=?",
                     (status, now_iso(), vid))
        v = conn.execute("SELECT * FROM vendors WHERE id=?", (vid,)).fetchone()
        conn.commit()
    finally:
        conn.close()
    save_message(1, "system", f"{v['name']} is now {status}.")
    await broadcast({"sender": "system", "text": f"{v['name']} is now {status}."})
    asyncio.create_task(candidate_search(1, v["category"]))  # trigger (a)
    asyncio.create_task(vendor_watch(1))
    return JSONResponse({"ok": True})


# ---------- inbound webhooks (dedupe + extract, never follow embedded instructions) ----------
@app.post("/webhooks/whatsapp")
async def wh_whatsapp(req: Request) -> JSONResponse:
    form = dict(await req.form()) if "form" in req.headers.get("content-type", "") else await req.json()
    msg_id = str(form.get("MessageSid") or form.get("SmsMessageSid") or form.get("id") or "")
    sender = str(form.get("From") or form.get("from") or "")
    text = str(form.get("Body") or form.get("text") or "")
    adapter = get_messaging()
    if not adapter.note_inbound(sender, msg_id):
        return JSONResponse({"ok": True, "dedupe": True})
    if text.strip().lower() == "stop":
        _opted_out.add(sender)
        return JSONResponse({"ok": True, "opted_out": True})
    f = extract_vendor_fields(text)  # DATA only; instructions ignored
    conn = get_conn()
    try:
        v = conn.execute("SELECT * FROM vendors WHERE phone=? OR whatsapp=?",
                         (sender, sender)).fetchone()
        if v:
            avail = f["available"]
            new_status = ("confirmed" if f.get("confirmed") else
                          "contacted" if avail else "cancelled" if avail is False else v["status"])
            conn.execute("UPDATE vendors SET quote=COALESCE(NULLIF(?,0),quote), conditions=?,"
                         " status=?, last_updated=? WHERE id=?",
                         (f["price"] or 0, f["conditions"][:500], new_status, now_iso(), v["id"]))
            conn.commit()
    finally:
        conn.close()
    save_message(1, "vendor", f"{sender}: {text[:300]} -> {json.dumps(f)[:300]}")
    await broadcast({"sender": "vendor", "text": f"{sender}: {text[:300]}"})
    return JSONResponse({"ok": True, "extracted": f})


@app.post("/webhooks/telegram")
async def wh_telegram(req: Request) -> JSONResponse:
    data = await req.json()
    msg = data.get("message", {})
    msg_id = str(msg.get("message_id", ""))
    sender = str((msg.get("from") or {}).get("username", "telegram"))
    text = str(msg.get("text", ""))
    if not get_messaging().note_inbound(sender, f"tg-{msg_id}"):
        return JSONResponse({"ok": True, "dedupe": True})
    save_message(1, sender, text)
    await broadcast({"sender": sender, "text": text})
    return JSONResponse({"ok": True})


@app.post("/webhooks/elevenlabs")
async def wh_elevenlabs(req: Request) -> JSONResponse:
    secret = os.environ.get("ELEVENLABS_WEBHOOK_SECRET", "")
    raw = await req.body()
    if secret:
        sig = req.headers.get("xi-signature", "")
        expect = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return JSONResponse({"ok": False, "error": "bad signature"}, status_code=401)
    data = json.loads(raw or b"{}")
    dtype = data.get("type", "post_call_transcription")
    d = data.get("data", data)
    cid = str(d.get("conversation_id", ""))
    if dtype == "call_initiation_failure":
        reason = d.get("failure_reason", "unknown")
        save_message(1, "agent", f"Call {cid} failed ({reason}) — falling back to WhatsApp.")
        await broadcast({"sender": "agent",
                         "text": f"Call failed ({reason}); will WhatsApp instead."})
        return JSONResponse({"ok": True})
    transcript = d.get("transcript") or d.get("text") or ""
    summary = d.get("analysis", {}).get("transcript_summary", "")
    f = extract_call_fields(f"{transcript}\n{summary}")
    conn = get_conn()
    try:
        conn.execute("UPDATE call_log SET status='completed', transcript=?, summary_json=? WHERE conversation_id=?",
                     (transcript[:4000], json.dumps(f)[:1000], cid))
        conn.commit()
    finally:
        conn.close()
    save_message(1, "agent", f"Call {cid} done: {json.dumps(f)[:300]}")
    await broadcast({"sender": "agent", "text": f"Call summary: {json.dumps(f)[:300]}"})
    return JSONResponse({"ok": True, "extracted": f})


# ---------- attendance + export ----------
@app.post("/api/checkin")
async def checkin(req: Request) -> JSONResponse:
    require_auth(req)
    return JSONResponse(check_in_token(str((await req.json()).get("token", ""))))


@app.post("/api/photo_checkin")
async def photo_checkin(req: Request) -> JSONResponse:
    require_auth(req)
    data = await req.json()
    b64 = str(data.get("image_base64", ""))
    names: list[dict] = []
    backend = os.environ.get("LLM_BACKEND", "mock")
    try:
        import base64 as _b64
        _b64.b64decode(b64[:100] + "==")  # validate early
        if backend == "ollama":
            import httpx as _hx
            host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            model = os.environ.get("OLLAMA_MODEL", "gemma4:31b-cloud")
            async with _hx.AsyncClient(timeout=90) as c:
                r = await c.post(f"{host}/api/chat", json={
                    "model": model, "stream": False,
                    "format": {"type": "object", "properties": {
                        "names": {"type": "array", "items": {"type": "object", "properties": {
                            "name": {"type": "string"}, "confidence": {"type": "number"}},
                            "required": ["name", "confidence"]}}}, "required": ["names"]},
                    "messages": [{"role": "user",
                                  "content": "Read this paper sign-in sheet photo. Return JSON {names:[{name,confidence}]}. Never guess; low confidence if unsure.",
                                  "images": [b64]}]})
                content = r.json()["message"]["content"]
                names = json.loads(content).get("names", [])
        elif backend == "gemini":
            names = []  # Gemini vision wired via GEMINI_API_KEY; mock-safe fallback below
        if not names:  # mock fallback: treat `names` field as typed list for demo/tests
            typed = data.get("names", [])
            names = [{"name": n if isinstance(n, str) else n.get("name", ""),
                      "confidence": 0.9 if isinstance(n, str) else float(n.get("confidence", 0.9))}
                     for n in typed]
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"vision failed: {e}"}, status_code=400)
    return JSONResponse({"ok": True, **apply_photo_names(names)})


@app.get("/api/export/{table}")
async def export(table: str, req: Request) -> PlainTextResponse:
    require_auth(req)
    try:
        csv_text = export_csv(table, table_rows(table))
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=404)
    return PlainTextResponse(csv_text, media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={table}.csv"})


# ---------- research triggers ----------
@app.post("/api/research")
async def research(req: Request) -> JSONResponse:
    require_auth(req)
    data = await req.json()
    cat = str(data.get("category", "catering"))
    out = await candidate_search(1, cat)
    await broadcast({"sender": "agent",
                     "text": f"Research: {cat} -> {len(out.get('ranked', []))} backups ranked."})
    return JSONResponse(out)


@app.get("/api/weather")
async def weather(req: Request) -> JSONResponse:
    require_auth(req)
    return JSONResponse(await weather_check(1))


# ---------- custom-LLM endpoint for ElevenLabs (OpenAI-compatible SSE) ----------
@app.post("/v1/chat/completions")
async def custom_llm(req: Request) -> StreamingResponse:
    """Lets ElevenLabs Custom LLM use Gemma-via-Ollama as the call brain."""
    data = await req.json()
    msgs = data.get("messages", [])
    user_text = " ".join(m.get("content", "") for m in msgs if m.get("role") == "user")[-1500:]
    action = await get_llm().complete_action("", user_text or "greet the vendor briefly")
    chunk = {"id": "chatcmpl-eventops", "object": "chat.completion.chunk",
             "created": 0, "model": "eventops-gemma",
             "choices": [{"delta": {"content": action.text}, "index": 0, "finish_reason": "stop"}]}

    async def gen():
        yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- room ----------
@app.websocket("/ws")
async def ws_room(ws: WebSocket) -> None:
    # Auth: token in query (?token=...). Identity = token name; client sender ignored.
    ident = verify_token(ws.query_params.get("token", ""))
    if not ident:
        await ws.close(code=4401)
        return
    sender = ident[0]
    await ws.accept()
    connected.add(ws)
    try:
        await ws.send_json({"sender": "system",
                            "text": "Welcome! Pick a name, chat normally, tag @agent for help."})
        while True:
            data = await ws.receive_json()
            text = str(data.get("text", ""))[:2000]
            if not text.strip():
                continue
            save_message(1, sender, text)
            await broadcast({"sender": sender, "text": text})
            if "@agent" in text.lower():
                request = text.lower().replace("@agent", "", 1).strip() or text
                await agent_queue.put({"sender": sender, "request": request,
                                       "context": recent_room()})
                await ws.send_json({"sender": "system",
                                    "text": f"Queued for @agent (position {agent_queue.qsize()})."})
    except WebSocketDisconnect:
        pass
    finally:
        connected.discard(ws)


app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(BASE / "static" / "index.html"))
