"""Constraint checks used by the agents to critique a draft plan.

Each check belongs to the agent whose domain it is, so the *agents* raise the conflicts:
  * Profile agent   -> calendar / predicted-routine conflicts, planning window
  * Logistics agent -> travel feasibility between consecutive stops, budget
  * Discovery agent -> venue verification (anti-hallucination), opening hours, weather exposure
The Coordinator sees the merged list and decides how to resolve it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from lifeplan.tools import opening_hours
from lifeplan.tools.scheduler import fmt, parse_dt

HIGH, MEDIUM, LOW = "high", "medium", "low"


def _overlap(a1: datetime, a2: datetime, b1: datetime, b2: datetime) -> int:
    return int(max(0, (min(a2, b2) - max(a1, b1)).total_seconds() // 60))


def _items(plan: dict):
    for d in plan.get("days", []):
        for idx, it in enumerate(d.get("items", [])):
            yield d.get("date"), idx, it


def check_calendar(plan: dict, busy: list[dict], predicted: list[dict], window: Optional[dict] = None) -> list[dict]:
    issues = []
    w1 = parse_dt(window["start"]) if window else None
    w2 = parse_dt(window["end"]) if window else None
    for day, idx, it in _items(plan):
        if it.get("kind") == "busy":
            continue
        s, e = parse_dt(it["start"]), parse_dt(it["end"])
        for b in busy:
            ov = _overlap(s, e, parse_dt(b["start"]), parse_dt(b["end"]))
            if ov > 0:
                issues.append({
                    "type": "CALENDAR_CONFLICT", "severity": HIGH, "agent": "profile", "day": day, "item_index": idx,
                    "message": f"'{it['title']}' ({it['start'][11:]}–{it['end'][11:]}) overlaps your "
                               f"'{b.get('title', 'busy')}' ({b['start'][11:]}–{b['end'][11:]}) by {ov} min",
                    "busy_until": b["end"],
                })
        for p in predicted:
            ov = _overlap(s, e, parse_dt(p["start"]), parse_dt(p["end"]))
            if ov > 0:
                issues.append({
                    "type": "PREDICTED_BUSY", "severity": MEDIUM if p.get("confidence", 0) >= 0.7 else LOW,
                    "agent": "profile", "day": day, "item_index": idx,
                    "message": f"'{it['title']}' overlaps a usual routine block '{p['title']}' "
                               f"(confidence {p.get('confidence')})",
                })
        if w1 and w2 and (s < w1 or e > w2):
            issues.append({"type": "OUTSIDE_WINDOW", "severity": HIGH, "agent": "profile", "day": day,
                           "item_index": idx,
                           "message": f"'{it['title']}' is outside the requested window {fmt(w1)}–{fmt(w2)}"})
    return issues


def check_logistics(plan: dict, travel: Callable[[dict, dict], dict], budget: Optional[float],
                    origin: Optional[dict] = None, bases: Optional[dict] = None) -> list[dict]:
    """`bases` maps date -> the place the user starts that day from (e.g. hotel on a trip)."""
    issues = []
    total = 0.0
    for d in plan.get("days", []):
        items = d.get("items", [])
        prev_place, prev_end, prev_title = (bases or {}).get(d.get("date"), origin), None, "start"
        for idx, it in enumerate(items):
            total += float((it.get("cost") or {}).get("amount") or 0)
            s, e = parse_dt(it["start"]), parse_dt(it["end"])
            if prev_end and s < prev_end and it.get("kind") not in ("travel", "hotel"):
                issues.append({"type": "OVERLAP", "severity": HIGH, "agent": "logistics", "day": d.get("date"),
                               "item_index": idx, "message": f"'{it['title']}' starts before the previous item ends"})
            if it.get("kind") in ("flight", "train", "busy"):
                prev_place, prev_end, prev_title = None, e, it["title"]  # location after a journey is unknown
                continue
            if it.get("kind") in ("travel", "hotel"):
                continue
            place = it.get("place") or {}
            if prev_place is None and prev_end is None:
                prev_place = (bases or {}).get(d.get("date"), origin)
            if place.get("lat") is not None and prev_place and prev_place.get("lat") is not None and prev_end:
                leg = travel(prev_place, place)
                gap = (s - prev_end).total_seconds() / 60
                if gap + 1 < leg["minutes"]:
                    issues.append({
                        "type": "TRAVEL_INFEASIBLE", "severity": HIGH, "agent": "logistics", "day": d.get("date"),
                        "item_index": idx,
                        "message": f"Getting from '{prev_title}' to '{it['title']}' needs ~{round(leg['minutes'])} min "
                                   f"by {leg.get('mode')} but only {round(gap)} min is planned",
                        "needed_minutes": round(leg["minutes"]),
                    })
            if place.get("lat") is not None:
                prev_place, prev_title = place, it["title"]
            prev_end = e
    if budget is not None:
        if total > budget:
            issues.append({"type": "OVER_BUDGET", "severity": HIGH, "agent": "logistics",
                           "message": f"Estimated total {round(total)} exceeds budget {round(budget)}",
                           "over_by": round(total - budget)})
        elif total > 0.9 * budget:
            issues.append({"type": "NEAR_BUDGET", "severity": LOW, "agent": "logistics",
                           "message": f"Estimated total {round(total)} uses >90% of budget {round(budget)}"})
    return issues


def check_venues(plan: dict, registry: dict[str, dict], wet_hours: set[str]) -> list[dict]:
    issues = []
    for day, idx, it in _items(plan):
        if it.get("kind") in ("travel", "busy", "buffer", "flight", "train"):
            continue
        cid = it.get("candidate_id")
        cand = registry.get(cid) if cid else None
        if it.get("kind") in ("activity", "meal", "snack", "event", "hotel") and not cand:
            issues.append({"type": "UNVERIFIED_PLACE", "severity": HIGH, "agent": "discovery", "day": day,
                           "item_index": idx,
                           "message": f"'{it['title']}' is not in the verified candidate pool (possible hallucination)"})
            continue
        if not cand:
            continue
        s, e = parse_dt(it["start"]), parse_dt(it["end"])
        if not cand.get("fixed_start"):
            open_ok = opening_hours.is_open_between(cand.get("opening_hours"), s, e)
            if open_ok is False:
                issues.append({"type": "CLOSED", "severity": HIGH, "agent": "discovery", "day": day, "item_index": idx,
                               "message": f"'{it['title']}' is closed at {it['start'][11:]}–{it['end'][11:]} "
                                          f"(hours: {cand.get('opening_hours')})"})
            elif open_ok is None and cand.get("kind") != "event":
                issues.append({"type": "HOURS_UNKNOWN", "severity": LOW, "agent": "discovery", "day": day,
                               "item_index": idx,
                               "message": f"Opening hours for '{it['title']}' are not published; verify before going"})
        if not cand.get("indoor", True):
            hrs = set()
            t = s.replace(minute=0)
            while t <= e:
                hrs.add(fmt(t))
                t += timedelta(hours=1)
            if hrs & wet_hours:
                issues.append({"type": "WEATHER_RISK", "severity": MEDIUM, "agent": "discovery", "day": day,
                               "item_index": idx,
                               "message": f"Outdoor '{it['title']}' is scheduled during forecast rain"})
    return issues


def summarise(issues: list[dict]) -> dict:
    high = [i for i in issues if i["severity"] == HIGH]
    return {"ok": not high, "high": len(high), "medium": sum(1 for i in issues if i["severity"] == MEDIUM),
            "low": sum(1 for i in issues if i["severity"] == LOW), "issues": issues}
