"""Decision layer — plain Python rules. The LLM proposes, these rules decide.

Risk: low=auto-run, medium=preview+approve, high=organizer-only approval.
Ranking: price vs budget, distance, availability, reliability, response speed.
Weights shift under 24h: availability+speed outweigh price. Missing data is
penalized+flagged, never zero. Freshness penalty for old leads. Top-two close
call -> show both, ask human.
Triage: safety > venue > food > program > extras + time-to-event.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ---- Risk gate ----
LOW = {"answer", "research", "export_csv"}
MEDIUM = {"draft_message"}
HIGH = {"call_vendor"}

MONEY_WORDS = ("book", "pay", "payment", "advance", "price", "quote", "budget", "rs", "₹", "$")


def risk_level(action_type: str, args: dict[str, Any] | None = None,
               text: str = "") -> str:
    args = args or {}
    blob = f"{action_type} {text} {args}".lower()
    if action_type in HIGH:
        return "high"
    if action_type in MEDIUM:
        # mass notices to everyone are high even if draft
        aud = str(args.get("audience", "")).lower()
        if aud in ("all", "everyone", "guests", "team+guests"):
            return "high"
        return "medium"
    if any(w in blob for w in MONEY_WORDS):
        return "high"
    if action_type in LOW:
        return "low"
    return "medium"  # unknown -> conservative


def approval_rule(risk: str) -> dict[str, Any]:
    if risk == "low":
        return {"auto": True, "organizer_only": False, "label": "auto-run"}
    if risk == "medium":
        return {"auto": False, "organizer_only": False, "label": "preview + approve (any member)"}
    return {"auto": False, "organizer_only": True, "label": "organizer-only approval"}


# ---- Triage ----
SEVERITY_ORDER = {"safety": 0, "venue": 1, "food": 2, "program": 3, "extras": 4}


def hours_to_event(event_iso: str, now: datetime | None = None) -> float:
    import os
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(os.environ.get("EVENT_TIMEZONE", "Asia/Kolkata"))
    except Exception:
        from datetime import timezone as _tz
        local_tz = _tz.utc  # type: ignore
    try:
        dt = datetime.fromisoformat(event_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=local_tz)  # seed stores wall-clock; never assume UTC
        return max(0.0, (dt - now).total_seconds() / 3600)
    except Exception:
        return 999.0


def triage(failures: list[dict[str, Any]], event_iso: str) -> list[dict[str, Any]]:
    hrs = hours_to_event(event_iso)
    urgency = "crisis" if hrs < 5 else "urgent" if hrs < 24 else "watch" if hrs < 168 else "normal"

    def key(f: dict[str, Any]):
        return (SEVERITY_ORDER.get(str(f.get("category", "extras")).lower(), 9),
                0 if f.get("status") == "cancelled" else 1)
    ordered = sorted(failures, key=key)
    steps = []
    for f in ordered:
        steps.append({"vendor": f.get("name"), "category": f.get("category"),
                      "do": ["find backups", "contact top 2", "notify team/guests"],
                      "mode": urgency})
    return steps


# ---- Ranking ----
def rank_candidates(candidates: list[dict[str, Any]], budget: float,
                    hours_left: float) -> list[dict[str, Any]]:
    """Score 0..1 with per-factor reasons. Never trust missing data."""
    urgent = hours_left < 24
    w_price, w_dist, w_avail, w_rel, w_speed = (
        (0.15, 0.15, 0.35, 0.15, 0.20) if urgent else (0.30, 0.20, 0.20, 0.20, 0.10))
    out = []
    for c in candidates:
        reasons, missing = [], []
        price = c.get("price_hint") or 0
        if price and budget:
            s_price = max(0.0, 1 - abs(price - budget * 0.5) / (budget or 1))
            reasons.append(f"price ₹{price:g} vs budget ₹{budget:g}")
        else:
            s_price, missing = 0.4, missing + ["price"]
        dist = c.get("distance_km")
        if dist is not None and dist != "":
            try:
                s_dist = max(0.0, 1 - float(dist) / 30)
                reasons.append(f"{dist} km away")
            except (TypeError, ValueError):
                s_dist, missing = 0.4, missing + ["distance"]
        else:
            s_dist, missing = 0.4, missing + ["distance"]
        avail = str(c.get("availability_note", "")).lower()
        if any(k in avail for k in ("avail", "confirm", "ready", "yes")):
            s_avail = 1.0
            reasons.append("says available")
        elif any(k in avail for k in ("no", "busy", "booked", "unavail")):
            s_avail = 0.1
            reasons.append("says unavailable")
        else:
            s_avail, missing = 0.4, missing + ["availability"]
        rel = c.get("confidence", 0.5) or 0.5
        try:
            s_rel = max(0.0, min(1.0, float(rel)))
        except (TypeError, ValueError):
            s_rel = 0.5
        s_speed = 0.7  # placeholder until reply latency is tracked
        # freshness penalty: -0.02/day after 3 days, capped -0.2
        try:
            from datetime import datetime as _dt
            fetched = str(c.get("fetched_at", ""))
            age_days = 0.0
            if fetched:
                age_days = max(0.0, (_dt.now() - _dt.fromisoformat(fetched)).total_seconds() / 86400)
            fresh_pen = min(0.2, max(0.0, (age_days - 3)) * 0.02)
        except Exception:
            fresh_pen, missing = 0.1, missing + ["fetched_at"]
        if missing:
            reasons.append("missing: " + ", ".join(missing) + " (penalized, not trusted)")
        score = (w_price * s_price + w_dist * s_dist + w_avail * s_avail
                 + w_rel * s_rel + w_speed * s_speed) - fresh_pen
        out.append({**c, "score": round(max(0.0, score), 3), "reasons": reasons})
    out.sort(key=lambda x: x["score"], reverse=True)
    if len(out) >= 2 and abs(out[0]["score"] - out[1]["score"]) < 0.05:
        out[0]["reasons"] = out[0].get("reasons", []) + ["close call: top two within 0.05 — ask human"]
        out[1]["reasons"] = out[1].get("reasons", []) + ["close call: top two within 0.05 — ask human"]
    return out


def draft_notice(audience: str, event_name: str, change: str) -> str:
    if audience == "team":
        return (f"Team update — {event_name}: {change} "
                "Backup options are being contacted. Please hold your posts; new assignments shortly.")
    if audience == "vendors":
        return (f"Hello, {event_name} team here: {change} "
                "Please confirm your availability today. Reply with price + conditions.")
    return (f"Hello! Update about {event_name}: {change} "
            "The event is ON as scheduled. We will share any new details here. Thank you!")
