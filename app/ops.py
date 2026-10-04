"""CSV export with formula-injection sanitization + check-in helpers."""
from __future__ import annotations

import csv
import difflib
import io
import sqlite3

TABLES = ["attendees", "vendors", "vendor_candidates", "tasks",
          "outbound_log", "call_log", "decision_log"]


def sanitize_cell(v):
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def export_csv(table: str, rows: list[dict]) -> str:
    buf = io.StringIO()
    if not rows:
        return ""
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    for r in rows:
        w.writerow({k: sanitize_cell(v) for k, v in r.items()})
    return buf.getvalue()


def table_rows(table: str, event_id: int = 1) -> list[dict]:
    from .db import get_conn
    if table not in TABLES:
        raise ValueError(f"unknown table {table}")
    conn = get_conn()
    try:
        col = "event_id" if table != "events" else "id"
        rows = conn.execute(f"SELECT * FROM {table} WHERE {col}=?", (event_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def fuzzy_match(name: str, roster: list[str], cutoff: float = 0.82) -> str | None:
    m = difflib.get_close_matches(name.strip(), roster, n=1, cutoff=cutoff)
    return m[0] if m else None


def check_in_token(token: str, event_id: int = 1) -> dict:
    """QR/manual check-in. Duplicate scans report already-checked-in time."""
    from .db import get_conn, now_iso
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM attendees WHERE event_id=? AND qr_token=?",
                           (event_id, token.strip())).fetchone()
        if not row:
            return {"ok": False, "error": "unknown token"}
        if row["checked_in"]:
            return {"ok": True, "duplicate": True, "name": row["name"],
                    "checked_in_at": row["checked_in_at"]}
        conn.execute("UPDATE attendees SET checked_in=1, checked_in_at=?, source='qr'"
                     " WHERE id=?", (now_iso(), row["id"]))
        conn.commit()
        d = dict(row)
        return {"ok": True, "duplicate": False, "name": d["name"]}
    finally:
        conn.close()


def apply_photo_names(items: list[dict], event_id: int = 1) -> dict:
    """High-confidence matches -> present (source=photo); rest -> review list. Never guess."""
    from .db import get_conn, now_iso
    conn = get_conn()
    try:
        roster = [dict(r) for r in conn.execute(
            "SELECT id,name FROM attendees WHERE event_id=?", (event_id,)).fetchall()]
        names = [r["name"] for r in roster]
        marked, review = [], []
        for it in items:
            nm = str(it.get("name", "")).strip()
            conf = float(it.get("confidence", 0) or 0)
            hit = fuzzy_match(nm, names) if nm else None
            if hit and conf >= 0.8:
                conn.execute("UPDATE attendees SET checked_in=1, checked_in_at=?, source='photo'"
                             " WHERE event_id=? AND name=?", (now_iso(), event_id, hit))
                marked.append(hit)
            else:
                review.append({"name": nm, "confidence": conf})
        conn.commit()
        return {"marked": marked, "review": review}
    finally:
        conn.close()
