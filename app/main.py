"""M1: room + agent core. FastAPI + WebSocket + SQLite. `uvicorn app.main:app`."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import DB_PATH, get_conn, init_db, now_iso, seed_demo
from .llm import get_llm

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("eventops")

app = FastAPI(title="EventOps Agent (M1)")
BASE = Path(__file__).resolve().parent.parent

connected: set[WebSocket] = set()
# Panic-spam guard: serialize @agent jobs in FIFO order.
agent_queue: asyncio.Queue = asyncio.Queue()


def event_state_summary(event_id: int = 1) -> str:
    conn = get_conn()
    try:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        vendors = conn.execute(
            "SELECT name,category,status,quote FROM vendors WHERE event_id=?", (event_id,)).fetchall()
    finally:
        conn.close()
    if not ev:
        return "no event"
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
        conn.execute(
            "INSERT INTO messages(event_id,sender,text,created_at) VALUES(?,?,?,?)",
            (event_id, sender[:60], text[:2000], now_iso()))
        conn.execute(
            "INSERT INTO decision_log(event_id,kind,input_json,output_json,rule_fired,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (event_id, "room_message", json.dumps({"sender": sender}),
             json.dumps({"len": len(text)}), "none", now_iso()))
        conn.commit()
    finally:
        conn.close()


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
            action = await llm.complete_action(
                job["context"], job["request"], event_state_summary())
        except Exception as e:  # graceful degradation, never break the room
            log.warning("agent error: %s", e)
            from .models import AgentAction
            action = AgentAction(type="answer",
                                 text="Sorry — the agent hit an error. Please try again.")
        text = f"@agent [{action.type}] {action.text}"
        save_message(1, "agent", text)
        await broadcast({"sender": "agent", "text": text,
                         "action": action.model_dump(), "requester": job["sender"]})
        agent_queue.task_done()


@app.on_event("startup")
async def startup() -> None:
    init_db()
    seed_demo()
    asyncio.create_task(agent_worker())


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "backend": os.environ.get("LLM_BACKEND", "mock"),
            "queue": agent_queue.qsize()}


@app.get("/api/event")
async def api_event() -> JSONResponse:
    conn = get_conn()
    try:
        ev = conn.execute("SELECT * FROM events LIMIT 1").fetchone()
        vendors = [dict(r) for r in conn.execute("SELECT * FROM vendors").fetchall()]
        msgs = [dict(r) for r in conn.execute(
            "SELECT sender,text,created_at FROM messages ORDER BY id DESC LIMIT 50").fetchall()]
    finally:
        conn.close()
    return JSONResponse({"event": dict(ev) if ev else None,
                         "vendors": vendors, "messages": list(reversed(msgs))})


@app.websocket("/ws")
async def ws_room(ws: WebSocket) -> None:
    await ws.accept()
    connected.add(ws)
    try:
        await ws.send_json({"sender": "system",
                            "text": "Welcome! Pick a name, chat normally, tag @agent for help."})
        while True:
            data = await ws.receive_json()
            sender = str(data.get("sender", "guest"))[:60] or "guest"
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
