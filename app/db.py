"""SQLite schema + seed. Stdlib only so demo never depends on an ORM."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path


def _code_hash(code: str) -> str:
    return hashlib.sha256(f"eventops:{code}".encode()).hexdigest()

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, date_time TEXT NOT NULL,
  venue TEXT, lat REAL, lng REAL, headcount INTEGER DEFAULT 0,
  budget REAL DEFAULT 0, timezone TEXT DEFAULT 'Asia/Kolkata');
CREATE TABLE IF NOT EXISTS members(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
  name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
  passcode TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS vendors(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
  name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'misc',
  phone TEXT DEFAULT '', whatsapp TEXT DEFAULT '',
  status TEXT DEFAULT 'unknown', quote REAL DEFAULT 0,
  conditions TEXT DEFAULT '', source_url TEXT DEFAULT '', last_updated TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS vendor_candidates(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
  category TEXT NOT NULL, name TEXT NOT NULL, phone TEXT DEFAULT '',
  price_hint REAL DEFAULT 0, distance_km REAL DEFAULT 0,
  availability_note TEXT DEFAULT '', source_url TEXT DEFAULT '',
  fetched_at TEXT DEFAULT '', confidence REAL DEFAULT 0,
  status TEXT DEFAULT 'new');
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
  title TEXT NOT NULL, owner TEXT DEFAULT '', due TEXT DEFAULT '', status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS attendees(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
  name TEXT NOT NULL, phone TEXT DEFAULT '', qr_token TEXT DEFAULT '',
  checked_in INTEGER DEFAULT 0, checked_in_at TEXT DEFAULT '', source TEXT DEFAULT 'manual');
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
  sender TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbound_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
  channel TEXT NOT NULL, recipient TEXT DEFAULT '', body TEXT DEFAULT '',
  status TEXT DEFAULT 'queued', approved_by TEXT DEFAULT '',
  idempotency_key TEXT DEFAULT '', ext_sid TEXT DEFAULT '',
  send_at TEXT DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS call_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
  vendor_id INTEGER DEFAULT 0, conversation_id TEXT DEFAULT '',
  status TEXT DEFAULT 'queued', transcript TEXT DEFAULT '',
  summary_json TEXT DEFAULT '{}', duration_s INTEGER DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
  kind TEXT NOT NULL, severity TEXT DEFAULT 'info', text TEXT NOT NULL,
  evidence_json TEXT DEFAULT '{}', created_at TEXT NOT NULL, acknowledged INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS decision_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
  kind TEXT NOT NULL, input_json TEXT NOT NULL, output_json TEXT NOT NULL,
  rule_fired TEXT DEFAULT '', created_at TEXT NOT NULL);
"""

DB_PATH = os.environ.get("DATABASE_URL", "sqlite:///./eventops.db").replace("sqlite:///", "")


def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
    # migration for DBs created before passcode column existed
    mcols = [r["name"] for r in conn.execute("PRAGMA table_info(members)").fetchall()]
    if "passcode" not in mcols:
        conn.execute("ALTER TABLE members ADD COLUMN passcode TEXT NOT NULL DEFAULT ''")
    if "phone" not in mcols:
        conn.execute("ALTER TABLE members ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
    ocols = [r["name"] for r in conn.execute("PRAGMA table_info(outbound_log)").fetchall()]
    if "ext_sid" not in ocols:  # provider message SID (Twilio) for status callbacks
        conn.execute("ALTER TABLE outbound_log ADD COLUMN ext_sid TEXT NOT NULL DEFAULT ''")
    if "send_at" not in ocols:  # scheduled send time (5-min safety buffer after approval)
        conn.execute("ALTER TABLE outbound_log ADD COLUMN send_at TEXT NOT NULL DEFAULT ''")
    acols = [r["name"] for r in conn.execute("PRAGMA table_info(attendees)").fetchall()]
    if "phone" not in acols:  # participant messaging needs numbers
        conn.execute("ALTER TABLE attendees ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
    conn.commit()
    conn.close()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def seed_file() -> Path:
    return Path(os.environ.get("SEED_FILE", Path(__file__).resolve().parent.parent / "seed.json"))


def seed_demo(db_path: str = DB_PATH) -> int:
    """Seed demo content from seed.json (override path via SEED_FILE). Idempotent."""
    import json
    from datetime import datetime, timedelta
    conn = get_conn(db_path)
    row = conn.execute("SELECT id FROM events LIMIT 1").fetchone()
    if row:
        conn.close()
        return int(row["id"])
    seed = json.loads(seed_file().read_text())
    ev = seed["event"]
    hh, mm = (ev.get("time", "18:00") + ":00").split(":")[:2]
    dt = (datetime.now() + timedelta(days=int(ev.get("days_ahead", 1)))).replace(
        hour=int(hh), minute=int(mm), second=0, microsecond=0)
    cur = conn.execute(
        "INSERT INTO events(name,date_time,venue,lat,lng,headcount,budget,timezone)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (ev["name"], dt.strftime("%Y-%m-%dT%H:%M:%S"), ev.get("venue", ""),
         ev.get("lat", 0), ev.get("lng", 0), ev.get("headcount", 0),
         ev.get("budget", 0), ev.get("timezone", "Asia/Kolkata")),
    )
    eid = int(cur.lastrowid)
    conn.executemany(
        "INSERT INTO members(event_id,name,role,passcode,phone) VALUES(?,?,?,?,?)",
        [(eid, m["name"], m.get("role", "member"),
          _code_hash(os.environ.get(m.get("passcode_env", ""), m.get("default", ""))),
          m.get("phone", ""))
         for m in seed.get("members", [])],
    )
    conn.executemany(
        "INSERT INTO vendors(event_id,name,category,phone,whatsapp,status,quote,conditions)"
        " VALUES(?,?,?,?,?,?,?,?)",
        [(eid, v["name"], v.get("category", "misc"), v.get("phone", ""),
          v.get("whatsapp", ""), v.get("status", "unknown"),
          v.get("quote", 0), v.get("conditions", ""))
         for v in seed.get("vendors", [])],
    )
    conn.executemany(
        "INSERT INTO tasks(event_id,title,owner,due,status) VALUES(?,?,?,?,?)",
        [(eid, t["title"], t.get("owner", ""), t.get("due", ""), t.get("status", "open"))
         for t in seed.get("tasks", [])],
    )
    att = seed.get("attendees", {})
    phones = att.get("phones", [])
    for i in range(1, int(att.get("count", 0)) + 1):
        conn.execute(
            "INSERT INTO attendees(event_id,name,phone,qr_token) VALUES(?,?,?,?)",
            (eid, att["name_pattern"] % i,
             phones[i - 1] if i - 1 < len(phones) else "",
             att["token_pattern"] % i),
        )
    conn.execute(
        "INSERT INTO messages(event_id,sender,text,created_at) VALUES(?,?,?,?)",
        (eid, "system", seed.get("welcome", "Welcome."), now_iso()),
    )
    conn.commit()
    conn.close()
    return eid
