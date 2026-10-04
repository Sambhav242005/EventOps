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
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .env import load as _load_env
_load_env()

from . import auth as _auth
from .auth import hash_code, issue_token, verify_token
from .calls import build_call_brief, extract_call_fields, get_calls
from .db import get_conn, init_db, now_iso, seed_demo
from .decision import approval_rule, draft_notice, lookup_facts, risk_level
from .llm import get_llm
from .messaging import _opted_out, extract_vendor_fields, get_messaging, norm_wa
from .ops import check_in_token, export_csv, table_rows
from .research import candidate_search, research_loop, vendor_watch, weather_check

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("eventops")

app = FastAPI(title="EventOps Agent")
app.add_middleware(
    CORSMiddleware,
    # The API uses bearer tokens rather than cross-origin cookies. Allow any
    # hosted frontend origin; authorization is still enforced by API routes.
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
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
            "SELECT name,category,status,quote,phone,conditions FROM vendors WHERE event_id=?", (event_id,)).fetchall()
    finally:
        conn.close()
    v = ", ".join(
        f"{x['name']}({x['category']}:{x['status']}"
        f"{', ₹'+str(x['quote']) if x['quote'] else ''}"
        f"{', '+x['conditions'] if x['conditions'] else ''})" for x in vendors)
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


CHANNELS = ("room", "vendors", "alerts")


def valid_channel(c: str) -> str:
    return c if c in CHANNELS else "room"


def save_message(event_id: int, sender: str, text: str, channel: str = "room") -> None:
    conn = get_conn()
    try:
        conn.execute("INSERT INTO messages(event_id,sender,text,channel,created_at) VALUES(?,?,?,?,?)",
                     (event_id, sender[:60], text[:2000], valid_channel(channel), now_iso()))
        conn.commit()
    finally:
        conn.close()


def require_auth(req: Request) -> tuple[str, str]:
    """Identity comes from the signed token only — client-supplied names are ignored."""
    auth = req.headers.get("authorization", "")
    tok = auth[7:] if auth.lower().startswith("bearer ") else ""
    member = _auth.verify_membership(tok)
    if member:
        if not _auth.can_access(member, req_event(req)):
            raise _Forbidden()
        return member["name"], member["role"]
    ident = verify_token(tok)
    if not ident:
        raise _Unauthorized()
    return ident


class _Unauthorized(Exception):
    pass


class _Forbidden(Exception):
    pass


@app.exception_handler(_Unauthorized)
async def _unauth_handler(req: Request, exc: _Unauthorized) -> JSONResponse:
    return JSONResponse({"ok": False, "error": "login required"}, status_code=401)


@app.exception_handler(_Forbidden)
async def _forbidden_handler(req: Request, exc: _Forbidden) -> JSONResponse:
    return JSONResponse({"ok": False, "error": "wrong team/org"}, status_code=403)


def req_event(req: Request) -> int:
    """Scoped event id from ?event_id= (default 1 so old clients keep working)."""
    try:
        return int(req.query_params.get("event_id", 1) or 1)
    except (ValueError, TypeError):
        return 1


def req_member(req: Request) -> dict:
    """Membership scoping via verify_membership on the Bearer token.

    401 via existing _Unauthorized if no membership; 403 "wrong team/org"
    when can_access(member, eid) is False. Falls back to verify_token
    (org-wide, no team check) until the data-agent contract lands.
    """
    member = token_member(req)
    eid = req_event(req)
    can = getattr(_auth, "can_access", None)
    if can is not None and not can(member, eid):
        raise _Forbidden()
    return member


def token_member(req: Request) -> dict:
    """Resolve a membership token without assuming which event is selected."""
    auth = req.headers.get("authorization", "")
    tok = auth[7:] if auth.lower().startswith("bearer ") else ""
    vm = getattr(_auth, "verify_membership", None)
    if vm is None:
        ident = verify_token(tok)
        if not ident:
            raise _Unauthorized()
        return {"id": 0, "name": ident[0], "role": ident[1],
                "org_id": 1, "team_id": 1}
    member = vm(tok)
    if not member:
        raise _Unauthorized()
    return member


@app.post("/api/register")
async def register(req: Request) -> JSONResponse:
    """Join the demo event as a member, or create an organization owner account."""
    data = await req.json()
    name = str(data.get("name", "")).strip()[:60]
    code = str(data.get("passcode", ""))
    role = str(data.get("role", "member"))
    eid = req_event(req)
    organization_name = str(data.get("organization_name", "")).strip()[:120]
    if len(name) < 2 or len(code) < 4:
        return JSONResponse({"ok": False, "error": "name (2+) and passcode (4+) required"},
                            status_code=400)
    if role not in {"member", "organizer"}:
        return JSONResponse({"ok": False, "error": "role must be member or organizer"},
                            status_code=400)
    if role == "organizer" and len(organization_name) < 2:
        return JSONResponse({"ok": False, "error": "organization name (2+ characters) required"},
                            status_code=400)
    conn = get_conn()
    try:
        if role == "organizer":
            org_cur = conn.execute("INSERT INTO organizations(name,created_at) VALUES(?,?)",
                                   (organization_name, now_iso()))
            org_id = int(org_cur.lastrowid)
            team_cur = conn.execute("INSERT INTO teams(org_id,name,created_at) VALUES(?,?,?)",
                                    (org_id, "Core Team", now_iso()))
            team_id = int(team_cur.lastrowid)
            # event_id=0 means this organization owner has not created their first event yet.
            conn.execute("INSERT INTO members(event_id,name,role,passcode,phone,org_id,team_id) "
                         "VALUES(0,?,?,?,?,?,?)",
                         (name, role, hash_code(code), "", org_id, team_id))
        else:
            ev = conn.execute("SELECT org_id,team_id FROM events WHERE id=?", (eid,)).fetchone()
            if not ev:
                return JSONResponse({"ok": False, "error": "event not found"}, status_code=404)
            if conn.execute("SELECT id FROM members WHERE event_id=? AND lower(name)=lower(?)",
                            (eid, name)).fetchone():
                return JSONResponse({"ok": False, "error": "name taken"}, status_code=409)
            conn.execute("INSERT INTO members(event_id,name,role,passcode,phone,org_id,team_id) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (eid, name, role, hash_code(code), "", ev["org_id"], ev["team_id"]))
        conn.commit()
    finally:
        conn.close()
    return JSONResponse({"ok": True, "name": name, "role": role})


@app.post("/api/login")
async def login(req: Request) -> JSONResponse:
    data = await req.json()
    name = str(data.get("name", "")).strip()[:60]
    code = str(data.get("passcode", ""))
    conn = get_conn()
    try:
        rows = conn.execute("SELECT m.id,m.event_id,m.name,m.role,m.passcode,m.org_id,m.team_id,"
                            "COALESCE(t.name,'Team') || ' · ' || COALESCE(e.name,'No events yet') AS team_name "
                            "FROM members m LEFT JOIN teams t ON t.id=m.team_id "
                            "LEFT JOIN events e ON e.id=m.event_id "
                            "WHERE lower(m.name)=lower(?) ORDER BY m.id", (name,)).fetchall()
    finally:
        conn.close()
    matches = [r for r in rows if hmac.compare_digest(r["passcode"], hash_code(code))]
    if not matches:
        await asyncio.sleep(0.5)  # slow down guessing; generic error avoids user enum
        return JSONResponse({"ok": False, "error": "bad name or passcode"}, status_code=401)
    requested_team = str(data.get("team", "")).strip()
    requested_member_id = str(data.get("member_id", "")).strip()
    if requested_member_id:
        matches = [r for r in matches if str(r["id"]) == requested_member_id]
        if not matches:
            return JSONResponse({"ok": False, "error": "membership not found"}, status_code=403)
    elif requested_team:
        matches = [r for r in matches if requested_team in
                   (str(r["team_id"] or r["event_id"]), str(r["team_name"]))]
        if not matches:
            return JSONResponse({"ok": False, "error": "team not found"}, status_code=403)
    if len(matches) > 1:
        options = []
        for r in matches:
            options.append({"member_id": int(r["id"]), "team_id": int(r["team_id"] or r["event_id"]),
                            "team": str(r["team_name"]),
                            "role": r["role"]})
        return JSONResponse({"ok": False, "need_team": True, "options": options})
    r = matches[0]
    token = _auth.issue_token_for(dict(r))
    return JSONResponse({"ok": True, "token": token, "role": r["role"], "name": r["name"]})


@app.get("/api/myevents")
async def my_events(req: Request) -> JSONResponse:
    member = token_member(req)
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT e.id,e.name,e.date_time,e.venue,e.org_id,e.team_id "
            "FROM events e JOIN members m ON m.org_id=e.org_id "
            "WHERE m.id=? AND (e.team_id IS NULL OR e.team_id=m.team_id OR m.role='organizer') "
            "ORDER BY e.date_time,e.id", (member["id"],)).fetchall()
    finally:
        conn.close()
    return JSONResponse([dict(r) for r in rows])


@app.post("/api/events")
async def create_event(req: Request) -> JSONResponse:
    """Create an event in the signed-in organizer's organization/team."""
    member = token_member(req)
    if member.get("role") != "organizer":
        return JSONResponse({"ok": False, "error": "organizer only"}, status_code=403)
    data = await req.json()
    name = str(data.get("name", "")).strip()[:120]
    date_time = str(data.get("date_time", "")).strip()[:40]
    if len(name) < 2 or not date_time:
        return JSONResponse({"ok": False, "error": "name and date_time required"}, status_code=400)
    try:
        headcount = max(0, int(data.get("headcount", 0) or 0))
        budget = max(0.0, float(data.get("budget", 0) or 0))
        lat = float(data.get("lat", 0) or 0)
        lng = float(data.get("lng", 0) or 0)
    except (ValueError, TypeError):
        return JSONResponse({"ok": False, "error": "invalid numeric event field"}, status_code=400)
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO events(name,date_time,venue,lat,lng,headcount,budget,timezone,org_id,team_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (name, date_time, str(data.get("venue", ""))[:240], lat, lng,
             headcount, budget, str(data.get("timezone", "Asia/Kolkata"))[:80],
             member["org_id"], member["team_id"]))
        conn.commit()
        event_id = int(cur.lastrowid)
    finally:
        conn.close()
    return JSONResponse({"ok": True, "event_id": event_id}, status_code=201)


@app.get("/api/members")
async def list_members(req: Request) -> JSONResponse:
    member = req_member(req)
    if member.get("role") != "organizer":
        return JSONResponse({"ok": False, "error": "organizer only"}, status_code=403)
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id,name,role,team_id FROM members WHERE org_id=? ORDER BY name,id",
            (member["org_id"],)).fetchall()
    finally:
        conn.close()
    return JSONResponse([dict(r) for r in rows])


@app.patch("/api/members/{member_id}")
async def update_member_role(member_id: int, req: Request) -> JSONResponse:
    member = req_member(req)
    if member.get("role") != "organizer":
        return JSONResponse({"ok": False, "error": "organizer only"}, status_code=403)
    data = await req.json()
    role = str(data.get("role", ""))
    if role not in {"member", "organizer"}:
        return JSONResponse({"ok": False, "error": "role must be member or organizer"}, status_code=400)
    conn = get_conn()
    try:
        target = conn.execute("SELECT id,role FROM members WHERE id=? AND org_id=?",
                              (member_id, member["org_id"])).fetchone()
        if not target:
            return JSONResponse({"ok": False, "error": "member not found"}, status_code=404)
        # Keep at least one organizer so an organization cannot lock itself out.
        if target["role"] == "organizer" and role != "organizer":
            count = conn.execute("SELECT COUNT(*) n FROM members WHERE org_id=? AND role='organizer'",
                                 (member["org_id"],)).fetchone()["n"]
            if count <= 1:
                return JSONResponse({"ok": False, "error": "organization needs at least one organizer"}, status_code=409)
        conn.execute("UPDATE members SET role=? WHERE id=?", (role, member_id))
        conn.commit()
    finally:
        conn.close()
    return JSONResponse({"ok": True, "member_id": member_id, "role": role})


async def broadcast(payload: dict) -> None:
    payload.setdefault("channel", "room")
    event_id = payload.get("event_id")
    dead = []
    for ws in list(connected):
        try:
            if event_id is not None and getattr(ws.state, "event_id", None) != event_id:
                continue
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connected.discard(ws)


def _public_agent_reply(action_type: str, action_text: str, action_args: dict,
                        pending_id: int | None) -> str:
    """Render an agent action as user-facing copy, never internal type/risk tags."""
    if action_type == "draft_message":
        audience = str(action_args.get("audience", "team"))
        body = str(action_args.get("body", action_text)).strip()
        if pending_id is not None:
            return (f"@agent Draft for {audience} is ready:\n\n{body}\n\n"
                    f"Saved as pending approval #{pending_id}. An organizer must approve it before it sends.")
        return f"@agent Draft for {audience}:\n\n{body}"
    if action_type == "call_vendor" and pending_id is not None:
        return f"@agent {action_text.strip()}\n\nCall request #{pending_id} is waiting for organizer approval."
    return f"@agent {action_text.strip()}"


async def agent_worker() -> None:
    llm = get_llm()
    while True:
        job = await agent_queue.get()
        eid = int(job.get("event_id", 1) or 1)
        # Factual fast-path: menu/price/status/phone questions are answered
        # straight from the vendor table — no LLM round-trip, no guessing.
        facts = lookup_facts(job["request"], eid)
        if facts:
            from .models import AgentAction as _AA
            action = _AA(type="answer", text=facts, args={"source": "vendor_table"})
            risk, pending_id = "low", None
        else:
            try:
                action = await llm.complete_action(job["context"], job["request"],
                                                   event_state_summary(eid))
            except Exception as e:
                log.warning("agent error: %s", e)
                from .models import AgentAction
                action = AgentAction(type="answer", text="Sorry — the agent hit an error. Try again.")
            risk = risk_level(action.type, action.args, action.text)
            rule = approval_rule(risk)
            pending_id = None
            if action.type == "draft_message" and not rule["auto"]:
                aud = str(action.args.get("audience", "team"))
                body = str(action.args.get("body", action.text))[:1500]
                pending_id = queue_outbound("mock", aud, body, action.args, job["sender"], eid)
            elif action.type == "call_vendor" and not rule["auto"]:
                pending_id = queue_call(action.args, job["sender"], eid)
            elif action.type == "research":
                cat = str(action.args.get("category", "catering"))
                asyncio.create_task(candidate_search(eid, cat))  # background, never auto-contacts
        conn = get_conn()
        try:
            conn.execute("INSERT INTO decision_log(event_id,kind,input_json,output_json,rule_fired,created_at)"
                         " VALUES(?,?,?,?,?,?)",
                         (eid, "agent_action", json.dumps({"request": job["request"]})[:2000],
                          action.model_dump_json()[:2000], f"risk={risk}", now_iso()))
            conn.commit()
        finally:
            conn.close()
        reply = _public_agent_reply(action.type, action.text, action.args, pending_id)
        text = reply
        ch = "vendors" if action.type in ("call_vendor", "research") else "room"
        save_message(eid, "agent", text, ch)
        await broadcast({"sender": "agent", "text": text, "channel": ch, "event_id": eid,
                         "action": action.model_dump(),
                         "risk": risk, "pending_id": pending_id, "requester": job["sender"]})
        agent_queue.task_done()


def audience_phones(audience: str, event_id: int = 1) -> list[str]:
    """Resolve an audience to E.164 numbers. 'to:+91…' sends direct."""
    audience = (audience or "").strip()
    if audience.startswith("to:"):
        n = norm_wa(audience[3:])
        return [n] if n else []
    conn = get_conn()
    try:
        if audience in ("team",):
            rows = conn.execute("SELECT phone FROM members WHERE event_id=? AND phone<>''",
                                (event_id,)).fetchall()
        elif audience in ("guests",):
            rows = conn.execute("SELECT phone FROM attendees WHERE event_id=? AND phone<>''",
                                (event_id,)).fetchall()
        elif audience in ("all", "everyone", "team+guests"):
            rows = conn.execute("SELECT phone FROM members WHERE event_id=? AND phone<>''",
                                (event_id,)).fetchall()
            rows = list(rows) + list(conn.execute(
                "SELECT phone FROM attendees WHERE event_id=? AND phone<>''", (event_id,)).fetchall())
        else:  # single number passed as audience
            return [norm_wa(audience)] if norm_wa(audience) else []
        return [norm_wa(r["phone"]) for r in rows if norm_wa(r["phone"])]
    finally:
        conn.close()


def audience_count(audience: str, event_id: int = 1) -> int:
    conn = get_conn()
    try:
        if audience == "team":
            return conn.execute("SELECT COUNT(*) c FROM members WHERE event_id=?", (event_id,)).fetchone()["c"]
        if audience == "guests":
            return conn.execute("SELECT COUNT(*) c FROM attendees WHERE event_id=?", (event_id,)).fetchone()["c"]
        if audience in ("all", "everyone", "team+guests"):
            m = conn.execute("SELECT COUNT(*) c FROM members WHERE event_id=?", (event_id,)).fetchone()["c"]
            a = conn.execute("SELECT COUNT(*) c FROM attendees WHERE event_id=?", (event_id,)).fetchone()["c"]
            return m + a
        return 1
    finally:
        conn.close()


def queue_outbound(channel: str, audience: str, body: str, args: dict, requester: str,
                   event_id: int = 1) -> int:
    conn = get_conn()
    try:
        cur = conn.execute("""INSERT INTO outbound_log(event_id,channel,recipient,body,status,
                            approved_by,idempotency_key,created_at)
                            VALUES(?,?,?,?,?,?,?,?)""",
                         (event_id, channel, f"audience:{audience}", body[:1500], "pending", "",
                          f"ob-{uuid.uuid4().hex[:10]}", now_iso()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def queue_call(args: dict, requester: str, event_id: int = 1) -> int:
    conn = get_conn()
    try:
        cur = conn.execute("""INSERT INTO call_log(event_id,vendor_id,conversation_id,status,
                            transcript,summary_json,duration_s,created_at)
                            VALUES(?,?,?,?,?,?,?,?)""",
                         (event_id, int(args.get("vendor_id", 0) or 0), "", "pending",
                          "", json.dumps({"requested_by": requester}), 0, now_iso()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


# ---------- startup ----------
@app.on_event("startup")
async def startup() -> None:
    global agent_queue
    # Test clients and app restarts may run on different asyncio loops.
    agent_queue = asyncio.Queue()
    init_db()
    seed_demo()

    async def _room_notify(text: str, channel: str = "room", event_id: int = 1) -> None:
        save_message(event_id, "agent", text, channel)
        await broadcast({"sender": "agent", "text": text, "channel": channel, "event_id": event_id})

    from .research import set_notifier
    set_notifier(_room_notify)
    asyncio.create_task(agent_worker())
    asyncio.create_task(sender_loop())
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
    member = req_member(req)
    eid = req_event(req)
    conn = get_conn()
    try:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        vendors = [dict(r) for r in conn.execute("SELECT * FROM vendors WHERE event_id=?", (eid,)).fetchall()]
        cands = [dict(r) for r in conn.execute(
            "SELECT * FROM vendor_candidates WHERE event_id=? ORDER BY id DESC LIMIT 20", (eid,)).fetchall()]
        alerts = [dict(r) for r in conn.execute(
            "SELECT * FROM alerts WHERE event_id=? ORDER BY id DESC LIMIT 20", (eid,)).fetchall()]
        outb = [dict(r) for r in conn.execute(
            "SELECT * FROM outbound_log WHERE event_id=? ORDER BY id DESC LIMIT 20", (eid,)).fetchall()]
        calls = [dict(r) for r in conn.execute(
            "SELECT * FROM call_log WHERE event_id=? ORDER BY id DESC LIMIT 20", (eid,)).fetchall()]
        msgs = [dict(r) for r in conn.execute(
            "SELECT sender,text,channel,created_at FROM messages WHERE event_id=? ORDER BY id DESC LIMIT 100", (eid,)).fetchall()]
        roster = [dict(r) for r in conn.execute(
            "SELECT id,name,phone,qr_token,checked_in,checked_in_at FROM attendees WHERE event_id=?", (eid,)).fetchall()]
        counts = {t: conn.execute(f"SELECT COUNT(*) c FROM {t} WHERE event_id=?", (eid,)).fetchone()["c"]
                  for t in ("attendees", "vendors")}
        checked = conn.execute(
            "SELECT COUNT(*) c FROM attendees WHERE event_id=? AND checked_in=1", (eid,)).fetchone()["c"]
    finally:
        conn.close()
    return JSONResponse({"event": dict(ev) if ev else None, "vendors": vendors,
                         "candidates": cands, "alerts": alerts, "outbound": outb,
                         "calls": calls, "messages": list(reversed(msgs)),
                         "attendees": roster,
                         "attendance": {"checked": checked, "total": counts["attendees"]}})


# ---------- approvals + send ----------
@app.post("/api/approve")
async def approve(req: Request) -> JSONResponse:
    approver, role = require_auth(req)
    member = req_member(req)
    eid = req_event(req)
    data = await req.json()
    oid = int(data.get("id", 0))
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM outbound_log WHERE id=? AND event_id=?", (oid, eid)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        if row["status"] != "pending":
            return JSONResponse({"ok": False, "error": f"already {row['status']}"}, status_code=400)
        body = row["body"]
        aud = row["recipient"].split("audience:", 1)[-1] if "audience:" in row["recipient"] else ""
        risk = risk_level("draft_message", {"audience": aud}, body)
        if approval_rule(risk)["organizer_only"] and role != "organizer":
            return JSONResponse({"ok": False, "error": "organizer-only approval"}, status_code=403)
        # 5-min safety buffer: approval schedules, worker sends. Edit/cancel freely until send_at.
        from datetime import datetime, timedelta
        buf_min = float(os.environ.get("BUFFER_MIN", "5"))
        send_at = (datetime.now() + timedelta(minutes=buf_min)).strftime("%Y-%m-%dT%H:%M:%S%z")
        conn.execute("UPDATE outbound_log SET status='approved', approved_by=?, send_at=? WHERE id=?",
                     (approver, send_at, oid))
        conn.commit()
    finally:
        conn.close()
    save_message(eid, "agent", f"Notice #{oid} approved by {approver} ({role}): "
                            f"sends at {send_at} — edit/cancel within {buf_min:g} min")
    await broadcast({"sender": "agent", "event_id": eid,
                     "text": f"Notice #{oid} scheduled for {send_at} (buffer {buf_min:g} min)"})
    return JSONResponse({"ok": True, "id": oid, "send_at": send_at, "buffer_min": buf_min})


@app.post("/api/edit_outbound")
async def edit_outbound(req: Request) -> JSONResponse:
    """Fix a mistake during the buffer window. Only pending/approved can change."""
    approver, _role = require_auth(req)
    eid = req_event(req)
    req_member(req)
    data = await req.json()
    oid, body = int(data.get("id", 0)), str(data.get("body", ""))[:1500]
    if not body.strip():
        return JSONResponse({"ok": False, "error": "empty body"}, status_code=400)
    conn = get_conn()
    try:
        row = conn.execute("SELECT status FROM outbound_log WHERE id=? AND event_id=?", (oid, eid)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        if row["status"] not in ("pending", "approved"):
            return JSONResponse({"ok": False, "error": f"already {row['status']}, too late to edit"},
                                status_code=400)
        conn.execute("UPDATE outbound_log SET body=? WHERE id=? AND event_id=?", (body, oid, eid))
        conn.commit()
    finally:
        conn.close()
    await broadcast({"sender": "agent", "event_id": eid, "text": f"Notice #{oid} edited by {approver}"})
    return JSONResponse({"ok": True})


@app.post("/api/cancel_outbound")
async def cancel_outbound(req: Request) -> JSONResponse:
    approver, _role = require_auth(req)
    eid = req_event(req)
    req_member(req)
    oid = int((await req.json()).get("id", 0))
    conn = get_conn()
    try:
        row = conn.execute("SELECT status FROM outbound_log WHERE id=? AND event_id=?", (oid, eid)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        if row["status"] not in ("pending", "approved"):
            return JSONResponse({"ok": False, "error": f"already {row['status']}, too late to cancel"},
                                status_code=400)
        conn.execute("UPDATE outbound_log SET status='cancelled' WHERE id=? AND event_id=?", (oid, eid))
        conn.commit()
    finally:
        conn.close()
    await broadcast({"sender": "agent", "event_id": eid, "text": f"Notice #{oid} cancelled by {approver}"})
    return JSONResponse({"ok": True})


async def send_outbound_now(oid: int) -> dict:
    """Worker send: fan-out an approved (due) notice. Returns {sent:[...]}.

    Atomic claim (approved->sending) so two workers/restarts can never
    double-send the same notice to real people.
    """
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE outbound_log SET status='sending' WHERE id=? AND status='approved'",
                           (oid,))
        conn.commit()
        if cur.rowcount == 0:
            return {"ok": False, "error": "not approved/due (already claimed or sent)"}
        row = conn.execute("SELECT * FROM outbound_log WHERE id=?", (oid,)).fetchone()
        body = row["body"]
        aud = row["recipient"].split("audience:", 1)[-1] if "audience:" in row["recipient"] else ""
        targets = audience_phones(aud if aud else row["recipient"])
        if not targets:
            conn.execute("UPDATE outbound_log SET status='failed' WHERE id=?", (oid,))
            conn.commit()
            return {"ok": False, "error": "no phone numbers for audience"}
        res, sent, last_sid = {"ok": True, "status": "sent"}, [], ""
        adapter = get_messaging()
        for t in targets:
            r = await adapter.send(t, body, f"{row['idempotency_key']}-{t}")
            if r.get("ok"):
                sent.append(t)
                last_sid = str(r.get("sid", last_sid))
            else:
                res = r
            await asyncio.sleep(3.1 if adapter.channel == "whatsapp" else 0)
        conn.execute("UPDATE outbound_log SET status=?, ext_sid=? WHERE id=?",
                     ("sent" if sent else "failed", last_sid, oid))
        conn.commit()
        return {"ok": bool(sent), "sent": sent, "result": res}
    finally:
        conn.close()


async def sender_loop(interval_s: int = 15) -> None:
    """Background sender: dispatch approved notices whose buffer expired."""
    while True:
        try:
            conn = get_conn()
            try:
                due = conn.execute(
                    "SELECT id FROM outbound_log WHERE status='approved' AND send_at<>'' "
                    "AND idempotency_key NOT LIKE 'demo:%' "
                    "AND send_at <= strftime('%Y-%m-%dT%H:%M:%S','now','localtime')").fetchall()
            finally:
                conn.close()
            for row in due:
                out = await send_outbound_now(int(row["id"]))
                if out.get("sent"):
                    await broadcast({"sender": "agent",
                                     "text": f"Notice #{row['id']} sent to {len(out['sent'])} recipient(s)"})
        except Exception as e:
            log.warning("sender loop error: %s", e)
        await asyncio.sleep(interval_s)


@app.post("/api/attendee_phone")
async def attendee_phone(req: Request) -> JSONResponse:
    """Set a participant's number so audience 'guests'/'all' can reach them."""
    _, role = require_auth(req)
    member = req_member(req)
    eid = req_event(req)
    if role != "organizer":
        return JSONResponse({"ok": False, "error": "organizer only"}, status_code=403)
    data = await req.json()
    from .messaging import norm_wa
    phone = norm_wa(str(data.get("phone", "")))
    if not phone:
        return JSONResponse({"ok": False, "error": "bad number"}, status_code=400)
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE attendees SET phone=? WHERE id=? AND event_id=?",
                           (phone, int(data.get("id", 0)), eid))
        conn.commit()
        if cur.rowcount == 0:
            return JSONResponse({"ok": False, "error": "attendee not found"}, status_code=404)
    finally:
        conn.close()
    return JSONResponse({"ok": True, "phone": phone})


@app.post("/api/approve_call")
async def approve_call(req: Request) -> JSONResponse:
    approver, role = require_auth(req)
    member = req_member(req)
    eid = req_event(req)
    data = await req.json()
    cid, lang = int(data.get("id", 0)), str(data.get("lang", "en"))
    if role != "organizer":
        return JSONResponse({"ok": False, "error": "calls need organizer approval"}, status_code=403)
    conn = get_conn()
    try:
        call = conn.execute("SELECT * FROM call_log WHERE id=? AND event_id=?", (cid, eid)).fetchone()
        if not call or call["status"] != "pending":
            return JSONResponse({"ok": False, "error": "call not pending"}, status_code=400)
        summ = json.loads(call["summary_json"] or "{}")
        vid = int(summ.get("vendor_id", 0) or call["vendor_id"] or 0)
        vendor = conn.execute("SELECT * FROM vendors WHERE id=? AND event_id=?", (vid, eid)).fetchone()
        if not vendor:
            vendor = conn.execute("SELECT * FROM vendors WHERE event_id=? LIMIT 1", (eid,)).fetchone()
        if not vendor:
            return JSONResponse({"ok": False, "error": "no vendors yet — add one first"}, status_code=400)
        ev = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        # call cap guard
        n_calls = conn.execute("SELECT COUNT(*) c FROM call_log WHERE event_id=? AND status IN ('completed','initiated')", (eid,)).fetchone()["c"]
        if n_calls >= int(os.environ.get("CALL_MAX_PER_EVENT", "5")):
            return JSONResponse({"ok": False, "error": "call cap reached"}, status_code=400)
        brief = build_call_brief(dict(vendor), dict(ev), lang)
        to = vendor["phone"] or (conn.execute(
            "SELECT phone FROM vendors WHERE event_id=? AND phone<>'' LIMIT 1", (eid,)).fetchone() or {"phone": ""})["phone"]
        if not to:
            return JSONResponse({"ok": False, "error": "vendor has no phone number"}, status_code=400)
        res = await get_calls().start_call(to, brief)
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



@app.post("/api/register_attendee")
async def register_attendee(req: Request) -> JSONResponse:
    """Public registration; door=1 also records attendance immediately."""
    import uuid as _uuid
    data = await req.json()
    eid = req_event(req)
    door_checkin = req.query_params.get("door") == "1"
    from .messaging import norm_wa
    name = str(data.get("name", "")).strip()[:80]
    phone = norm_wa(str(data.get("phone", "")))
    if len(name) < 2 or len(phone) < 7:
        return JSONResponse({"ok": False, "error": "name and valid phone required"},
                            status_code=400)
    conn = get_conn()
    try:
        dup = conn.execute("SELECT id,qr_token FROM attendees WHERE event_id=? AND phone=?",
                           (eid, phone)).fetchone()
        if dup:
            if door_checkin:
                conn.execute("UPDATE attendees SET checked_in=1,checked_in_at=?,source='door_qr' WHERE id=?",
                             (now_iso(), dup["id"]))
                conn.commit()
            return JSONResponse({"ok": True, "duplicate": True, "checked_in": door_checkin,
                                 "id": dup["id"], "qr_token": dup["qr_token"], "name": name})
        for _ in range(5):
            token = "QR-" + _uuid.uuid4().hex[:6].upper()
            if not conn.execute("SELECT id FROM attendees WHERE qr_token=?", (token,)).fetchone():
                break
        cur = conn.execute(
            "INSERT INTO attendees(event_id,name,phone,qr_token,checked_in,checked_in_at,source) "
            "VALUES(?,?,?,?,?,?,?)",
            (eid, name, phone, token, int(door_checkin), now_iso() if door_checkin else "",
             "door_qr" if door_checkin else "self_register"))
        conn.commit()
        return JSONResponse({"ok": True, "checked_in": door_checkin,
                             "id": int(cur.lastrowid), "qr_token": token, "name": name})
    finally:
        conn.close()


@app.get("/api/qr/{token}")
async def qr_code(token: str) -> Response:
    """QR PNG encoding the check-in token. Shown at the door + on /join."""
    import io
    import qrcode
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM attendees WHERE qr_token=?", (token.strip(),)).fetchone()
    finally:
        conn.close()
    if not row:
        return JSONResponse({"ok": False, "error": "unknown token"}, status_code=404)
    img = qrcode.make(token.strip(), box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/api/event-qr/{event_id}")
async def event_checkin_qr(event_id: int, req: Request) -> Response:
    """Generate the public door check-in QR for an event."""
    import io
    import qrcode
    conn = get_conn()
    try:
        if not conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone():
            return JSONResponse({"ok": False, "error": "event not found"}, status_code=404)
    finally:
        conn.close()
    from urllib.parse import urlsplit
    origin = req.query_params.get("origin", "").rstrip("/")
    parsed_origin = urlsplit(origin)
    valid_origin = (
        parsed_origin.scheme in {"http", "https"}
        and bool(parsed_origin.netloc)
        and parsed_origin.username is None
        and parsed_origin.password is None
        and not parsed_origin.path
        and not parsed_origin.query
        and not parsed_origin.fragment
    )
    if not valid_origin:
        origin = os.environ.get("PUBLIC_FRONTEND_URL", "http://localhost:3000").rstrip("/")
    link = f"{origin}/join?event_id={event_id}&door=1"
    img = qrcode.make(link, box_size=9, border=3)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/vendors")
async def create_vendor(req: Request) -> JSONResponse:
    _, role = require_auth(req)
    member = req_member(req)
    eid = req_event(req)
    if role != "organizer":
        return JSONResponse({"ok": False, "error": "organizer only"}, status_code=403)
    data = await req.json()
    from .messaging import norm_wa
    name = str(data.get("name", "")).strip()[:80]
    if len(name) < 2:
        return JSONResponse({"ok": False, "error": "vendor name required"}, status_code=400)
    conn = get_conn()
    try:
        cur = conn.execute("""INSERT INTO vendors(event_id,name,category,phone,whatsapp,status,quote,conditions)
                            VALUES(?,?,?,?,?,?,?,?)""",
                         (eid, name, str(data.get("category", "misc"))[:30],
                          norm_wa(str(data.get("phone", ""))), norm_wa(str(data.get("whatsapp", ""))),
                          str(data.get("status", "unknown"))[:20], float(data.get("quote", 0) or 0),
                          str(data.get("conditions", ""))[:500]))
        conn.commit()
        return JSONResponse({"ok": True, "id": int(cur.lastrowid)})
    except (ValueError, TypeError):
        return JSONResponse({"ok": False, "error": "bad quote"}, status_code=400)
    finally:
        conn.close()


@app.patch("/api/vendors/{vid}")
async def update_vendor(vid: int, req: Request) -> JSONResponse:
    _, role = require_auth(req)
    member = req_member(req)
    eid = req_event(req)
    if role != "organizer":
        return JSONResponse({"ok": False, "error": "organizer only"}, status_code=403)
    from .messaging import norm_wa
    data = await req.json()
    allowed = {"name": 80, "category": 30, "status": 20, "conditions": 500}
    sets, vals = [], []
    for k, lim in allowed.items():
        if k in data:
            sets.append(f"{k}=?")
            vals.append(str(data[k])[:lim])
    for k in ("phone", "whatsapp"):
        if k in data:
            sets.append(f"{k}=?")
            vals.append(norm_wa(str(data[k])))
    if "quote" in data:
        try:
            sets.append("quote=?")
            vals.append(float(data["quote"] or 0))
        except (ValueError, TypeError):
            return JSONResponse({"ok": False, "error": "bad quote"}, status_code=400)
    if not sets:
        return JSONResponse({"ok": False, "error": "nothing to update"}, status_code=400)
    conn = get_conn()
    try:
        cur = conn.execute(f"UPDATE vendors SET {', '.join(sets)}, last_updated=? WHERE id=? AND event_id=?",
                           (*vals, now_iso(), vid, eid))
        conn.commit()
        if cur.rowcount == 0:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return JSONResponse({"ok": True})
    finally:
        conn.close()


@app.get("/api/chat_summary")
async def chat_summary(req: Request) -> PlainTextResponse:
    """Briefing markdown: paste into any AI to continue with full context."""
    require_auth(req)
    eid = req_event(req)
    ev = event_row(eid)
    conn = get_conn()
    try:
        vendors = [dict(r) for r in conn.execute("SELECT name,category,status,quote,conditions FROM vendors WHERE event_id=?", (eid,)).fetchall()]
        msgs = [dict(r) for r in conn.execute(
            "SELECT sender,channel,text,created_at FROM messages WHERE event_id=? ORDER BY id DESC LIMIT 40", (eid,)).fetchall()]
        pend = conn.execute("SELECT COUNT(*) c FROM outbound_log WHERE event_id=? AND status='pending'", (eid,)).fetchone()["c"]
        sched = conn.execute("SELECT COUNT(*) c FROM outbound_log WHERE event_id=? AND status='approved'", (eid,)).fetchone()["c"]
        alerts = [dict(r) for r in conn.execute(
            "SELECT severity,text,created_at FROM alerts WHERE event_id=? AND acknowledged=0 ORDER BY id DESC LIMIT 10", (eid,)).fetchall()]
        att = conn.execute("SELECT COUNT(*) c FROM attendees WHERE event_id=?", (eid,)).fetchone()["c"]
        chk = conn.execute("SELECT COUNT(*) c FROM attendees WHERE event_id=? AND checked_in=1", (eid,)).fetchone()["c"]
    finally:
        conn.close()
    L = [f"# EventOps briefing — {ev.get('name', 'event')}",
         f"- Venue: {ev.get('venue', '?')} | When: {ev.get('date_time', '?')} | "
         f"Headcount: {ev.get('headcount', '?')} | Budget: Rs.{ev.get('budget', '?')}",
         f"- Attendance: {chk}/{att} checked in | Notices: {pend} pending, {sched} scheduled",
         "", "## Vendors"]
    for v in vendors:
        L.append(f"- {v['name']} ({v['category']}): {v['status']}"
                 + (f", Rs.{v['quote']:g}" if v["quote"] else "")
                 + (f" -- {v['conditions']}" if v["conditions"] else ""))
    if alerts:
        L.append("")
        L.append("## Open alerts")
        L += [f"- [{a['severity']}] {a['text']}" for a in alerts]
    L += ["", "## Recent chat (newest last)"]
    for m in reversed(msgs):
        L.append(f"- **{m['sender']}** [#{m.get('channel') or 'room'}]: {m['text'][:300]}")
    return PlainTextResponse("\n".join(L), media_type="text/markdown",
                             headers={"Content-Disposition": "attachment; filename=briefing.md"})


@app.get("/api/search")
async def search(req: Request) -> JSONResponse:
    """Search chat history (+ vendor/attendee names). The agent's memory tool."""
    require_auth(req)
    eid = req_event(req)
    q = req.query_params.get("q", "").strip()[:80]
    if len(q) < 2:
        return JSONResponse({"messages": [], "vendors": [], "attendees": []})
    like = f"%{q}%"
    conn = get_conn()
    try:
        msgs = [dict(r) for r in conn.execute(
            "SELECT sender,channel,text,created_at FROM messages WHERE event_id=? AND text LIKE ? "
            "ORDER BY id DESC LIMIT 30", (eid, like)).fetchall()]
        vendors = [dict(r) for r in conn.execute(
            "SELECT id,name,category,status FROM vendors WHERE event_id=? AND (name LIKE ? OR conditions LIKE ?)",
            (eid, like, like)).fetchall()]
        atts = [dict(r) for r in conn.execute(
            "SELECT id,name,checked_in FROM attendees WHERE event_id=? AND (name LIKE ? OR phone LIKE ?)",
            (eid, like, like)).fetchall()]
    finally:
        conn.close()
    return JSONResponse({"messages": msgs, "vendors": vendors, "attendees": atts})


@app.post("/api/alerts/ack")
async def ack_alert(req: Request) -> JSONResponse:
    require_auth(req)
    eid = req_event(req)
    aid = int((await req.json()).get("id", 0))
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE alerts SET acknowledged=1 WHERE id=? AND event_id=?", (aid, eid))
        conn.commit()
        if cur.rowcount == 0:
            return JSONResponse({"ok": False, "error": "alert not found"}, status_code=404)
    finally:
        conn.close()
    return JSONResponse({"ok": True})


@app.get("/api/alerts")
async def list_alerts(req: Request) -> JSONResponse:
    require_auth(req)
    eid = req_event(req)
    try:
        limit = min(100, max(1, int(req.query_params.get("limit", "50"))))
        offset = max(0, int(req.query_params.get("offset", "0")))
    except ValueError:
        return JSONResponse({"ok": False, "error": "invalid pagination"}, status_code=400)
    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) c FROM alerts WHERE event_id=?", (eid,)).fetchone()["c"]
        rows = conn.execute("SELECT * FROM alerts WHERE event_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                            (eid, limit, offset)).fetchall()
    finally:
        conn.close()
    return JSONResponse({"items": [dict(r) for r in rows], "total": total,
                         "limit": limit, "offset": offset})


@app.get("/api/schedule")
async def list_schedule(req: Request) -> JSONResponse:
    require_auth(req)
    eid = req_event(req)
    try:
        limit = min(100, max(1, int(req.query_params.get("limit", "50"))))
        offset = max(0, int(req.query_params.get("offset", "0")))
    except ValueError:
        return JSONResponse({"ok": False, "error": "invalid pagination"}, status_code=400)
    status = req.query_params.get("status", "all")
    where = "event_id=?"
    params: list[object] = [eid]
    if status in ("pending", "approved"):
        where += " AND status=?"
        params.append(status)
    elif status == "history":
        where += " AND status NOT IN ('pending','approved')"
    elif status != "all":
        return JSONResponse({"ok": False, "error": "invalid status filter"}, status_code=400)
    conn = get_conn()
    try:
        total = conn.execute(f"SELECT COUNT(*) c FROM outbound_log WHERE {where}", params).fetchone()["c"]
        rows = conn.execute(f"SELECT * FROM outbound_log WHERE {where} ORDER BY "
                            "CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, "
                            "COALESCE(NULLIF(send_at,''),created_at), id DESC LIMIT ? OFFSET ?",
                            (*params, limit, offset)).fetchall()
    finally:
        conn.close()
    return JSONResponse({"items": [dict(r) for r in rows], "total": total,
                         "limit": limit, "offset": offset})



@app.post("/api/notice")
async def notice(req: Request) -> JSONResponse:
    """Draft audience-specific notice (M4), queued for approval."""
    by, _role = require_auth(req)
    req_member(req)
    eid = req_event(req)
    data = await req.json()
    aud = str(data.get("audience", "team"))
    change = str(data.get("change", ""))[:500]
    ev = event_row(eid)
    body = draft_notice(aud, ev.get("name", "event"), change)
    oid = queue_outbound("mock", aud, body, {}, by, eid)
    return JSONResponse({"ok": True, "id": oid, "body": body,
                         "count": audience_count(aud, eid)})


# ---------- vendor simulate (demo: caterer cancels) ----------
@app.post("/api/vendor_status")
async def vendor_status(req: Request) -> JSONResponse:
    _, role = require_auth(req)
    req_member(req)
    eid = req_event(req)
    if role != "organizer":
        return JSONResponse({"ok": False, "error": "organizer only"}, status_code=403)
    data = await req.json()
    vid, status = int(data.get("id", 1)), str(data.get("status", "cancelled"))
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE vendors SET status=?, last_updated=? WHERE id=? AND event_id=?",
                           (status, now_iso(), vid, eid))
        v = conn.execute("SELECT * FROM vendors WHERE id=? AND event_id=?", (vid, eid)).fetchone()
        conn.commit()
        if cur.rowcount == 0 or not v:
            return JSONResponse({"ok": False, "error": "vendor not found"}, status_code=404)
    finally:
        conn.close()
    save_message(eid, "system", f"{v['name']} is now {status}.", "vendors")
    await broadcast({"sender": "system", "text": f"{v['name']} is now {status}.", "channel": "vendors", "event_id": eid})
    asyncio.create_task(candidate_search(eid, v["category"]))  # trigger (a)
    asyncio.create_task(vendor_watch(eid))
    return JSONResponse({"ok": True})


# ---------- inbound webhooks (dedupe + extract, never follow embedded instructions) ----------
@app.post("/webhooks/whatsapp")
async def wh_whatsapp(req: Request) -> JSONResponse:
    form = dict(await req.form()) if "form" in req.headers.get("content-type", "") else await req.json()
    msg_id = str(form.get("MessageSid") or form.get("SmsMessageSid") or form.get("id") or "")
    sender = norm_wa(str(form.get("From") or form.get("from") or ""))
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
    save_message(1, "vendor", f"{sender}: {text[:300]} -> {json.dumps(f)[:300]}", "vendors")
    await broadcast({"sender": "vendor", "text": f"{sender}: {text[:300]}", "channel": "vendors"})
    return JSONResponse({"ok": True, "extracted": f})


@app.post("/webhooks/twilio_status")
async def wh_twilio_status(req: Request) -> JSONResponse:
    """Delivery receipts: queued/sent/delivered/read/failed (+ErrorCode). Updates outbound_log."""
    form = dict(await req.form()) if "form" in req.headers.get("content-type", "") else await req.json()
    sid, status = str(form.get("MessageSid", "")), str(form.get("MessageStatus", ""))
    if not (sid and status):
        return JSONResponse({"ok": False}, status_code=400)
    conn = get_conn()
    try:
        conn.execute("UPDATE outbound_log SET status=? WHERE ext_sid=?", (status, sid))
        conn.commit()
    finally:
        conn.close()
    await broadcast({"sender": "system", "text": f"Delivery {sid[:10]}… -> {status}"})
    return JSONResponse({"ok": True})


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
        # Exact scheme per official SDK (webhooks_custom.construct_event):
        # header 'ElevenLabs-Signature: t=<unix>,v0=<hex over "{t}.{rawBody}">', 30-min tolerance.
        try:
            from elevenlabs import ElevenLabs
            data = ElevenLabs().webhooks.construct_event(
                raw.decode(), req.headers.get("ElevenLabs-Signature", ""), secret)
        except Exception:
            return JSONResponse({"ok": False, "error": "bad signature"}, status_code=401)
    else:
        data = json.loads(raw or b"{}")
    dtype = data.get("type", "post_call_transcription")
    d = data.get("data", data)
    cid = str(d.get("conversation_id", ""))
    if dtype == "call_initiation_failure":
        reason = d.get("failure_reason", "unknown")
        save_message(1, "agent", f"Call {cid} failed ({reason}) — falling back to WhatsApp.", "vendors")
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
    save_message(1, "agent", f"Call {cid} done: {json.dumps(f)[:300]}", "vendors")
    await broadcast({"sender": "agent", "text": f"Call summary: {json.dumps(f)[:300]}", "channel": "vendors"})
    return JSONResponse({"ok": True, "extracted": f})


# ---------- attendance + export ----------
@app.post("/api/checkin")
async def checkin(req: Request) -> JSONResponse:
    require_auth(req)
    return JSONResponse(check_in_token(str((await req.json()).get("token", "")), req_event(req)))


@app.get("/api/export/{table}")
async def export(table: str, req: Request) -> PlainTextResponse:
    require_auth(req)
    eid = req_event(req)
    try:
        csv_text = export_csv(table, table_rows(table, eid))
    except ValueError as e:
        return PlainTextResponse(str(e), status_code=404)
    return PlainTextResponse(csv_text, media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={table}.csv"})


# ---------- research triggers ----------
@app.post("/api/research")
async def research(req: Request) -> JSONResponse:
    require_auth(req)
    eid = req_event(req)
    data = await req.json()
    cat = str(data.get("category", "catering"))
    out = await candidate_search(eid, cat)
    await broadcast({"sender": "agent",
                     "text": f"Research: {cat} -> {len(out.get('ranked', []))} backups ranked.",
                     "event_id": eid})
    return JSONResponse(out)


@app.get("/api/weather")
async def weather(req: Request) -> JSONResponse:
    require_auth(req)
    return JSONResponse(await weather_check(req_event(req)))


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
    token = ws.query_params.get("token", "")
    member = _auth.verify_membership(token)
    ident = verify_token(token) if member is None else (member["name"], member["role"])
    try:
        eid = int(ws.query_params.get("event_id", "1"))
    except ValueError:
        eid = 0
    if not ident or (member is not None and not _auth.can_access(member, eid)):
        await ws.close(code=4401)
        return
    sender = ident[0]
    await ws.accept()
    ws.state.event_id = eid
    connected.add(ws)
    try:
        await ws.send_json({"sender": "system",
                            "text": "Welcome! Chat normally and tag @agent for help.", "event_id": eid})
        while True:
            data = await ws.receive_json()
            text = str(data.get("text", ""))[:2000]
            channel = valid_channel(str(data.get("channel", "room")))
            if not text.strip():
                continue
            save_message(eid, sender, text, channel)
            await broadcast({"sender": sender, "text": text, "channel": channel, "event_id": eid})
            if "@agent" in text.lower():
                request = text.lower().replace("@agent", "", 1).strip() or text
                await agent_queue.put({"sender": sender, "request": request,
                                       "event_id": eid, "context": recent_room(eid)})
                await ws.send_json({"sender": "system",
                                    "text": f"Queued for @agent (position {agent_queue.qsize()}).",
                                    "event_id": eid})
    except WebSocketDisconnect:
        pass
    finally:
        connected.discard(ws)


app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(BASE / "static" / "index.html"))
