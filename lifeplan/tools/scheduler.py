"""Deterministic schedule optimiser used by the Planner agent as a tool.

The LLM Planner decides *what* matters (which candidates, priorities, pace, travel mode,
must-include items); this solver does the combinatorial part reliably:
time windows + travel time + opening hours + activity duration + budget + weather.

Greedy insertion with a value function:
    value = 100*priority + must_include_bonus - 0.8*travel_min - 0.3*wait_min - weather_penalty
At each step it picks the feasible candidate with the highest value, so it naturally
prefers nearby, open, high-priority places and keeps time to get back home.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from lifeplan.tools import opening_hours

PACE_BUFFER = {"relaxed": 20, "balanced": 10, "packed": 5}
MEAL_GAP_MIN = 150


def parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, second=0, microsecond=0)
    return datetime.fromisoformat(str(value)).replace(tzinfo=None, second=0, microsecond=0)


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def _ceil5(dt: datetime) -> datetime:
    extra = (-dt.minute) % 5
    return (dt + timedelta(minutes=extra)).replace(second=0, microsecond=0)


@dataclass
class Window:
    start: datetime
    end: datetime
    base: dict  # {'lat','lon','name'} where the user starts this window (home / hotel / office)
    return_to_base: bool = True
    end_at: Optional[dict] = None  # alternative end location (e.g. railway station)


@dataclass
class Stop:
    cand: dict
    start: datetime
    end: datetime
    travel_min: float
    travel_km: float
    travel_mode: str
    travel_source: str
    leg_cost: float


@dataclass
class ScheduleResult:
    days: list = field(default_factory=list)
    unscheduled: list = field(default_factory=list)
    totals: dict = field(default_factory=dict)


TravelFn = Callable[[dict, dict], dict]  # returns {'minutes','km','mode','source','cost'}


def _earliest_open_start(cand: dict, earliest: datetime, duration: int) -> Optional[datetime]:
    """First start >= earliest at which the venue stays open for `duration` minutes."""
    spec = cand.get("opening_hours")
    if cand.get("fixed_start"):
        fs = parse_dt(cand["fixed_start"])
        return fs if fs >= earliest else None
    intervals = opening_hours.intervals_on(spec, earliest.date())
    if intervals is None:  # unknown hours: assume open, validator will flag it as 'unknown'
        return earliest
    for a, b in intervals:
        s = max(a, earliest)
        if s + timedelta(minutes=duration) <= b:
            return s
    return None


def build_schedule(
    windows: list[Window],
    candidates: list[dict],
    travel: TravelFn,
    *,
    budget: Optional[float] = None,
    pace: str = "balanced",
    must_include: Optional[list[str]] = None,
    wet_hours: Optional[set[str]] = None,
    max_stops_per_window: int = 6,
    max_meals_per_window: int = 2,
    spent_already: float = 0.0,
) -> ScheduleResult:
    buffer = PACE_BUFFER.get(pace, 10)
    must = set(must_include or [])
    wet = wet_hours or set()
    remaining = {c["id"]: c for c in candidates}
    spent = spent_already
    result = ScheduleResult()
    total_travel = 0.0
    total_activity = 0.0

    for w in windows:
        here = w.base
        t = w.start
        stops: list[Stop] = []
        meals = 0
        snacks = 0
        last_meal_end: Optional[datetime] = None
        while len(stops) < max_stops_per_window:
            best = None
            for cid, c in remaining.items():
                duration = int(c.get("duration_min") or 60)
                if c.get("fixed_end") and c.get("fixed_start"):
                    duration = int((parse_dt(c["fixed_end"]) - parse_dt(c["fixed_start"])).total_seconds() // 60)
                is_meal = c.get("kind") == "meal"
                if is_meal and meals >= max_meals_per_window:
                    continue
                if c.get("kind") == "snack" and snacks >= 1:
                    continue
                leg = travel(here, c)
                arrive = _ceil5(t + timedelta(minutes=leg["minutes"] + (buffer if stops else 0)))
                start = _earliest_open_start(c, arrive, duration)
                if start is None:
                    continue
                if is_meal and last_meal_end and (start - last_meal_end).total_seconds() / 60 < MEAL_GAP_MIN:
                    continue
                end = start + timedelta(minutes=duration)
                back_target = w.end_at or (w.base if w.return_to_base else None)
                back = travel(c, back_target)["minutes"] if back_target else 0
                if end + timedelta(minutes=back) > w.end:
                    continue
                cost = float(c.get("est_cost") or 0) + float(leg.get("cost") or 0)
                if budget is not None and spent + cost > budget and cid not in must:
                    continue
                wait = (start - arrive).total_seconds() / 60
                if wait > 90 and cid not in must:
                    continue
                value = 100 * float(c.get("priority", 0.5)) + (400 if cid in must else 0)
                value -= 0.8 * leg["minutes"] + 0.3 * wait
                if not c.get("indoor", True):
                    hours = {fmt(start.replace(minute=0)), fmt(end.replace(minute=0))}
                    if hours & wet:
                        value -= 60
                if best is None or value > best[0]:
                    best = (value, c, start, end, leg, cost)
            if best is None:
                break
            _, c, start, end, leg, cost = best
            stops.append(Stop(c, start, end, leg["minutes"], leg.get("km") or 0, leg.get("mode", "?"),
                              leg.get("source", "?"), float(leg.get("cost") or 0)))
            spent += cost
            total_travel += leg["minutes"]
            total_activity += (end - start).total_seconds() / 60
            remaining.pop(c["id"])
            if c.get("kind") == "meal":
                meals += 1
                last_meal_end = end
            elif c.get("kind") == "snack":
                snacks += 1
            here, t = c, end

        items = []
        prev_end = w.start
        for s in stops:
            dep = s.start - timedelta(minutes=s.travel_min)
            dep = max(dep, prev_end)
            if s.travel_min >= 1:
                items.append({
                    "start": fmt(dep), "end": fmt(dep + timedelta(minutes=s.travel_min)), "kind": "travel",
                    "title": f"{s.travel_mode.title()} to {s.cand['name']}",
                    "travel": {"mode": s.travel_mode, "minutes": round(s.travel_min), "km": round(s.travel_km, 1),
                               "source": s.travel_source},
                    "cost": {"amount": round(s.leg_cost), "estimate": True},
                })
            items.append({
                "start": fmt(s.start), "end": fmt(s.end),
                "kind": "event" if s.cand.get("fixed_start") else s.cand.get("kind", "activity"),
                "title": s.cand["name"], "candidate_id": s.cand["id"],
                "place": {k: s.cand.get(k) for k in ("name", "lat", "lon", "address", "source", "source_id")}
                         | {"url": s.cand.get("url") or s.cand.get("website")},
                "cost": {"amount": round(float(s.cand.get("est_cost") or 0)), "estimate": True},
                "notes": s.cand.get("why"),
            })
            prev_end = s.end
        back_target = w.end_at or (w.base if w.return_to_base else None)
        if stops and back_target:
            last = stops[-1].cand
            leg = travel(last, back_target)
            items.append({
                "start": fmt(stops[-1].end), "end": fmt(stops[-1].end + timedelta(minutes=leg["minutes"])),
                "kind": "travel", "title": f"{leg.get('mode', 'travel').title()} back to {back_target.get('name', 'base')}",
                "travel": {"mode": leg.get("mode"), "minutes": round(leg["minutes"]), "km": round(leg.get("km") or 0, 1),
                           "source": leg.get("source")},
                "cost": {"amount": round(float(leg.get("cost") or 0)), "estimate": True},
            })
            spent += float(leg.get("cost") or 0)
            total_travel += leg["minutes"]
        result.days.append({"date": w.start.date().isoformat(), "window": [fmt(w.start), fmt(w.end)],
                            "items": items})

    for cid in must:
        if cid in remaining:
            result.unscheduled.append({"id": cid, "name": remaining[cid].get("name"),
                                       "reason": "could not fit (time window, opening hours, travel or budget)"})
    result.totals = {"cost": round(spent), "travel_minutes": round(total_travel),
                     "activity_minutes": round(total_activity),
                     "stops": sum(1 for d in result.days for i in d["items"] if i["kind"] != "travel")}
    return result
