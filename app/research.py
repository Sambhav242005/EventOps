"""Background research worker (M5). Separate from chat path; runs on asyncio loop.

Triggers: (a) vendor cancelled/unresponsive, (b) schedule T-7d/T-24h/T-5h,
(c) weather alert, (d) manual "@agent research ...".
Jobs: weather (Open-Meteo, no key), vendor watch, candidate search.
Guards: dedupe alerts, cap searches/hour, log every run in decision_log,
show sources+timestamps. Never contacts anyone without approval.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from .db import get_conn, now_iso
from .decision import hours_to_event, rank_candidates
from .search import get_search

log = logging.getLogger("eventops.research")
_search_times: list[float] = []
_last_weather_alert: float = 0.0


def log_run(event_id: int, kind: str, inp: dict, out: dict, rule: str = "") -> None:
    conn = get_conn()
    try:
        conn.execute("INSERT INTO decision_log(event_id,kind,input_json,output_json,rule_fired,created_at)"
                     " VALUES(?,?,?,?,?,?)",
                     (event_id, kind, json.dumps(inp)[:2000], json.dumps(out)[:2000], rule, now_iso()))
        conn.commit()
    finally:
        conn.close()


def add_alert(event_id: int, kind: str, severity: str, text: str, evidence: dict) -> bool:
    """Dedupe: same kind+text within 6h -> skip. Returns True if inserted."""
    conn = get_conn()
    try:
        dup = conn.execute(
            "SELECT id FROM alerts WHERE event_id=? AND kind=? AND text=? AND created_at > datetime('now','-6 hours')",
            (event_id, kind, text)).fetchone()
        if dup:
            return False
        conn.execute("INSERT INTO alerts(event_id,kind,severity,text,evidence_json,created_at)"
                     " VALUES(?,?,?,?,?,?)",
                     (event_id, kind, severity, text, json.dumps(evidence)[:2000], now_iso()))
        conn.commit()
        return True
    finally:
        conn.close()


async def weather_check(event_id: int = 1) -> dict:
    conn = get_conn()
    try:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    finally:
        conn.close()
    if not ev:
        return {"ok": False}
    lat, lng = ev["lat"] or 22.72, ev["lng"] or 75.86
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get("https://api.open-meteo.com/v1/forecast",
                            params={"latitude": lat, "longitude": lng,
                                    "daily": "precipitation_probability_max,weathercode",
                                    "timezone": "auto", "forecast_days": 3})
            r.raise_for_status()
            daily = r.json().get("daily", {})
    except Exception as e:
        log_run(event_id, "weather_check", {"lat": lat}, {"error": str(e)}, "weather-fetch")
        return {"ok": False, "error": str(e)}
    probs = daily.get("precipitation_probability_max", [0])
    risk = max(probs) if probs else 0
    venue = str(ev["venue"]).lower()
    outdoor = any(k in venue for k in ("outdoor", "ground", "garden", "open"))
    out = {"risk_pct": risk, "outdoor": outdoor}
    if risk >= 50 and outdoor:
        if add_alert(event_id, "weather", "high",
                     f"Rain risk {risk}% near event time; venue is outdoor.",
                     {"source": "open-meteo", "daily": daily}):
            out["alerted"] = True
    log_run(event_id, "weather_check", {"lat": lat, "lng": lng}, out, "weather-rule")
    return {"ok": True, **out}


async def vendor_watch(event_id: int = 1) -> dict:
    conn = get_conn()
    try:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        vendors = [dict(r) for r in conn.execute(
            "SELECT * FROM vendors WHERE event_id=?", (event_id,)).fetchall()]
    finally:
        conn.close()
    hrs = hours_to_event(ev["date_time"]) if ev else 999
    flagged = []
    for v in vendors:
        if v["status"] in ("cancelled",):
            flagged.append(v)
        elif v["status"] in ("unknown", "contacted") and hrs < 24:
            flagged.append(v)
    for v in flagged:
        add_alert(event_id, "vendor_watch", "high" if v["status"] == "cancelled" else "medium",
                  f"{v['name']} ({v['category']}) is {v['status']} with {hrs:.0f}h to go.",
                  {"vendor_id": v["id"], "hours_left": round(hrs, 1)})
    log_run(event_id, "vendor_watch", {"hours_left": hrs}, {"flagged": len(flagged)}, "watch-rule")
    return {"flagged": [v["name"] for v in flagged], "hours_left": hrs}


async def candidate_search(event_id: int, category: str) -> dict:
    global _search_times
    now = time.time()
    _search_times = [t for t in _search_times if now - t < 3600]
    if len(_search_times) >= 10:  # cap searches/hour
        return {"ok": False, "error": "search cap reached (10/h)"}
    _search_times.append(now)
    conn = get_conn()
    try:
        ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    finally:
        conn.close()
    leads = await get_search().find_vendors(category, ev["lat"], ev["lng"])
    ranked = rank_candidates(leads, ev["budget"] or 0,
                             hours_to_event(ev["date_time"]))
    conn = get_conn()
    try:
        for c in ranked:
            conn.execute("""INSERT INTO vendor_candidates(event_id,category,name,phone,price_hint,
                          distance_km,availability_note,source_url,fetched_at,confidence,status)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                         (event_id, category, c.get("name", ""), c.get("phone", ""),
                          c.get("price_hint", 0), c.get("distance_km", 0),
                          c.get("availability_note", ""), c.get("source_url", ""),
                          c.get("fetched_at", ""), c.get("confidence", 0), "new"))
        conn.commit()
    finally:
        conn.close()
    names = ", ".join(f"{c['name']} ({c['score']})" for c in ranked[:3])
    add_alert(event_id, "candidates", "info", f"{category}: {len(ranked)} backups ranked: {names}",
              {"top": ranked[:3]})
    log_run(event_id, "candidate_search", {"category": category},
            {"found": len(ranked)}, "search-rule")
    return {"ok": True, "ranked": ranked}


async def research_loop(event_id: int = 1, interval_s: int = 300) -> None:
    """Scheduled checks: weather + vendor watch every interval (default 5m)."""
    while True:
        try:
            await weather_check(event_id)
            await vendor_watch(event_id)
        except Exception as e:
            log.warning("research loop error: %s", e)
        await asyncio.sleep(interval_s)
