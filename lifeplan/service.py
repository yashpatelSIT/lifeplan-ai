"""Application service: turns a PlanRequest into an initial blackboard, runs the multi-agent graph,
re-plans on real-world events, and packages the final PlanResult. Used by the API and the CLI."""

from __future__ import annotations

import copy
import logging
import re
import threading
import uuid
from datetime import datetime, time, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from lifeplan.agents.base import RunContext
from lifeplan.agents.shared import TRIP_MODES, validate_state
from lifeplan.config import get_settings
from lifeplan.graph import build_graph
from lifeplan.memory import SAMPLE_CALENDAR, PlanStore, ProfileStore
from lifeplan.schemas import PlanRequest, PlanResult, ReplanEvent
from lifeplan.tools.geo import geocode
from lifeplan.tools.scheduler import fmt, parse_dt
from lifeplan.trace import Trace

log = logging.getLogger("lifeplan.service")

profile_store = ProfileStore()
plan_store = PlanStore()


class PlanningError(ValueError):
    """Bad or impossible request (reported to the user as HTTP 422)."""


# ------------------------------------------------------------------ helpers
def infer_mode(req: PlanRequest) -> str:
    if req.mode != "auto":
        return req.mode
    q = req.query.lower()
    if req.destination or re.search(r"\b(trip|vacation|holiday|travel to|visit|tour)\b", q):
        if re.search(r"\b(business|conference|client|office visit|work trip)\b", q):
            return "business_trip"
        return "domestic_trip"  # refined to international after geocoding
    if re.search(r"\b(right now|now|currently|at the moment)\b", q):
        return "now"
    if re.search(r"\b(tonight|evening|after work|after college)\b", q):
        return "evening"
    if "weekend" in q:
        return "weekend"
    if re.search(r"\b(explore|near me|nearby|around me)\b", q):
        return "explore"
    if re.search(r"\b(full day|whole day|my day|today|tomorrow|day out|outing)\b", q):
        return "day"
    return "free_time"


def _round_up(dt: datetime, minutes: int = 15) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    return dt + timedelta(minutes=(-dt.minute) % minutes)


def derive_window(mode: str, now: datetime, start: Optional[datetime], end: Optional[datetime]) -> tuple[datetime, datetime]:
    start = start.replace(tzinfo=None) if start else None
    end = end.replace(tzinfo=None) if end else None
    if start and end:
        return start, end
    durations = {"now": 3, "free_time": 3, "explore": 2, "evening": 5, "day": 12, "local_outing": 10,
                 "weekend": 36, "domestic_trip": 72, "international_trip": 120, "business_trip": 48}
    if start:
        return start, start + timedelta(hours=durations.get(mode, 3))
    base = _round_up(now)
    today = now.date()
    if mode in ("now", "free_time", "explore"):
        s = base
    elif mode == "evening":
        s = max(base, datetime.combine(today, time(17, 0)))
        if s.time() > time(22, 30):
            s = datetime.combine(today + timedelta(days=1), time(17, 0))
        return s, datetime.combine(s.date(), time(23, 30))
    elif mode in ("day", "local_outing"):
        s = max(base, datetime.combine(today, time(9, 0)))
        if s.time() > time(17, 0):
            s = datetime.combine(today + timedelta(days=1), time(9, 0))
        return s, datetime.combine(s.date(), time(21, 30))
    elif mode == "weekend":
        days_ahead = (5 - today.weekday()) % 7
        sat = today + timedelta(days=days_ahead)
        if days_ahead == 0 and now.time() > time(14, 0):
            sat = today + timedelta(days=7)
        s = max(base, datetime.combine(sat, time(9, 0)))
        return s, datetime.combine(sat + timedelta(days=1), time(21, 30))
    else:  # trips: default next week
        s = datetime.combine(today + timedelta(days=7), time(8, 0))
        return s, (s + timedelta(hours=durations.get(mode, 72))).replace(hour=21, minute=0)
    e = end or (s + timedelta(hours=durations.get(mode, 3)))
    return s, e


def _sample_calendar() -> Optional[str]:
    try:
        return SAMPLE_CALENDAR.read_text(encoding="utf-8")
    except OSError:
        return None


def prepare_state(req: PlanRequest, trace: Trace) -> dict:
    """Deterministic set-up before the agents start: resolve places, time zone, mode and window."""
    s = get_settings()
    profile = profile_store.get(req.profile_id)
    if req.coordinates:
        origin = {"name": req.location or "Your location", "lat": req.coordinates.lat, "lon": req.coordinates.lon,
                  "city": (req.location or "").split(",")[0] or None, "country_code": None, "source": "user"}
        if req.location:
            g = geocode(req.location)
            if g.get("ok"):
                origin.update({k: g[k] for k in ("city", "country", "country_code", "timezone") if g.get(k)})
    else:
        origin = geocode(req.location)
        trace.tool_call("coordinator", "geocode", {"text": req.location}, origin, 0, origin.get("ok", False))
        if not origin.get("ok"):
            raise PlanningError(f"Could not find the location '{req.location}'. Try a city or a full address.")
    origin["id"] = "origin"

    mode = infer_mode(req)
    destination = None
    if mode in TRIP_MODES:
        if not req.destination:
            raise PlanningError("Trips need a 'destination' (e.g. 'Goa' or 'Tokyo').")
        destination = geocode(req.destination)
        trace.tool_call("coordinator", "geocode", {"text": req.destination}, destination, 0, destination.get("ok", False))
        if not destination.get("ok"):
            raise PlanningError(f"Could not find the destination '{req.destination}'.")
        destination["id"] = "destination"
        if (mode == "domestic_trip" and origin.get("country_code") and destination.get("country_code")
                and origin["country_code"] != destination["country_code"]):
            mode = "international_trip"

    tz_name = req.timezone or origin.get("timezone") or s.default_timezone
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz_name, tz = s.default_timezone, ZoneInfo(s.default_timezone)
    now = req.now.replace(tzinfo=None) if req.now else datetime.now(tz).replace(tzinfo=None)
    start, end = derive_window(mode, now, req.start, req.end)
    if end <= now and mode not in TRIP_MODES and not req.now:
        raise PlanningError("The requested time window is already in the past.")

    currency = (req.budget.currency if req.budget else None) or s.default_currency
    budget = float(req.budget.amount) if req.budget else None
    request = req.model_dump(mode="json")
    if not request.get("interests"):
        request["interests"] = profile.get("interests", [])
    trace.emit("setup", "coordinator", f"mode={mode}, window={fmt(start)}→{fmt(end)}, tz={tz_name}",
               origin=origin.get("name"), destination=(destination or {}).get("name"))
    return {
        "request": request, "profile": profile, "mode": mode, "now": fmt(now), "timezone": tz_name,
        "currency": currency, "budget": budget, "origin": origin, "destination": destination,
        "window": {"start": fmt(start), "end": fmt(end)}, "candidates": {}, "logistics": {}, "messages": [],
        "reviews": [], "conflicts": [], "events": [], "step": 0, "revisions": 0, "review_round": 0,
        "event_sources": [], "status": "planning",
    }


def _context(trace: Trace, state: dict) -> RunContext:
    return RunContext(trace=trace, profile_store=profile_store, now=parse_dt(state["now"]),
                      sample_calendar=_sample_calendar())


def _run_graph(state: dict, trace: Trace) -> dict:
    s = get_settings()
    graph = build_graph(_context(trace, state))
    return graph.invoke(state, {"recursion_limit": s.max_coordinator_steps * 4 + 20})


# ------------------------------------------------------------------ result
def build_result(state: dict, trace: Trace, plan_id: str) -> PlanResult:
    draft = state.get("draft") or {}
    val = state.get("validation") or (validate_state(state) if draft else None) or {"issues": []}
    final_keys = {(i["type"], i["message"]) for i in val.get("issues", []) if i["severity"] == "high"}
    conflicts = []
    seen = set()
    for c in state.get("conflicts") or []:
        key = (c["type"], c["description"])
        if key in seen:
            continue
        seen.add(key)
        resolved = key not in final_keys
        conflicts.append({"type": c["type"], "description": c["description"], "raised_by": c.get("raised_by", []),
                          "resolution": (c.get("resolution_attempt") or "addressed by re-planning") if resolved
                          else "UNRESOLVED — see warnings"})
    warnings = [f"{i['type']}: {i['message']}" for i in val.get("issues", [])]
    if state.get("degraded"):
        warnings.append("Some agents ran in degraded mode (LLM unavailable); plan built by deterministic fallbacks.")
    warnings.append("Costs and some travel times are estimates; check venue hours before leaving.")

    days = copy.deepcopy(draft.get("days", []))
    av = state.get("availability") or {}
    for b in av.get("busy", []):
        for d in days:
            if d["date"] == b["start"][:10]:
                d["items"].append({"start": b["start"], "end": b["end"], "kind": "busy", "title": b["title"]})
    for d in days:
        d["items"].sort(key=lambda it: it["start"])
        for it in d["items"]:
            if it.get("cost") is not None:
                it["cost"].setdefault("currency", state.get("currency"))
                it["cost"].setdefault("estimate", True)

    used = {it.get("candidate_id") for d in days for it in d["items"]}
    pri = draft.get("priorities") or {}
    cands = state.get("candidates") or {}
    alternatives = sorted((c for c in cands.values() if c["id"] not in used and not c.get("unavailable")
                           and c.get("kind") != "hotel"),
                          key=lambda c: (-pri.get(c["id"], 0), c.get("distance_km") or 99))[:5]
    sources = sorted({str(it["place"].get("url") or f"{it['place'].get('source')}:{it['place'].get('source_id')}")
                      for d in days for it in d["items"] if it.get("place") and it["place"].get("source")})
    weather = state.get("weather") or {}
    agents_seen = sorted({e["agent"] for e in trace.events if e["type"] in ("agent_start", "decision")})
    summary = state.get("final_summary") or (
        f"Planned {draft.get('totals', {}).get('stops', 0)} stops." if draft else "No feasible plan could be built.")
    return PlanResult(
        plan_id=plan_id, run_id=trace.run_id, status=state.get("status") or ("ok" if draft else "failed"),
        mode=state.get("mode", "auto"), summary=summary, timezone=state.get("timezone", ""), days=days,
        totals=draft.get("totals", {}),
        weather={"kind": weather.get("kind"), "daily": weather.get("daily"), "wet_hours": weather.get("wet_hours")}
        if weather else None,
        conflicts=conflicts, warnings=warnings,
        alternatives=[{"id": c["id"], "name": c["name"], "category": c.get("category"), "distance_km": c.get("distance_km"),
                       "opening_hours": c.get("opening_hours"), "url": c.get("url") or c.get("website")} for c in alternatives],
        sources=sources[:40],
        stats={**trace.stats, "coordinator_steps": state.get("step"), "planner_revisions": state.get("revisions"),
               "review_rounds": state.get("review_round"), "agents": agents_seen,
               "duration_s": round(trace.to_dict()["duration_s"], 1)},
    )


def _finish(state: dict, trace: Trace, plan_id: str) -> PlanResult:
    result = build_result(state, trace, plan_id)
    trace.emit("result", "system", result.summary, status=result.status, plan_id=plan_id)
    keep = {k: v for k, v in state.items() if k not in ("routine",)}
    plan_store.put(plan_id, {"state": keep, "result": result.model_dump(mode="json")})
    try:
        trace.save(get_settings().trace_dir)
    except OSError:
        log.warning("could not save trace")
    return result


# ------------------------------------------------------------------ public API
def run_plan(req: PlanRequest, trace: Optional[Trace] = None) -> PlanResult:
    plan_id = f"plan_{uuid.uuid4().hex[:10]}"
    trace = trace or Trace(plan_id=plan_id)
    trace.plan_id = plan_id
    try:
        state = prepare_state(req, trace)
        trace.message("user", "coordinator", req.query, kind="request")
        final = _run_graph(state, trace)
        return _finish(final, trace, plan_id)
    finally:
        trace.close()


def apply_event(state: dict, ev: ReplanEvent) -> dict:
    state = copy.deepcopy(state)
    tz = ZoneInfo(state["timezone"])
    now = datetime.now(tz).replace(tzinfo=None)
    w = state["window"]
    if parse_dt(w["start"]) < now < parse_dt(w["end"]) and state.get("mode") not in TRIP_MODES:
        state["window"] = {"start": fmt(_round_up(now)), "end": w["end"]}  # only re-plan the remaining time
        state["now"] = fmt(now)
    items = [it for d in (state.get("draft") or {}).get("days", []) for it in d["items"]]
    affected = items[ev.item_index] if ev.item_index is not None and 0 <= ev.item_index < len(items) else None
    note = ev.details
    if ev.event == "weather":
        start = parse_dt(affected["start"]) if affected else max(parse_dt(state["window"]["start"]), now)
        end = parse_dt(state["window"]["end"])
        forced, t = [], start.replace(minute=0)
        while t <= end:
            forced.append(fmt(t))
            t += timedelta(hours=1)
        wx = dict(state.get("weather") or {"ok": True, "kind": "user-reported", "daily": [], "hourly": []})
        wx["forced_wet_hours"] = forced
        wx["wet_hours"] = sorted(set(wx.get("wet_hours") or []) | set(forced))
        state["weather"] = wx
        note += f" (treating {forced[0][11:]}–{forced[-1][11:]} as wet)" if forced else ""
    if ev.event in ("cancellation", "closed") and affected and affected.get("candidate_id"):
        cid = affected["candidate_id"]
        if cid in state["candidates"]:
            state["candidates"][cid] = {**state["candidates"][cid], "unavailable": f"{ev.event}: {ev.details}"}
            note += f" (affected: {affected['title']} [{cid}])"
    if ev.new_busy_block:
        state["request"]["busy_blocks"] = list(state["request"].get("busy_blocks") or []) + \
            [ev.new_busy_block.model_dump(mode="json")]
        state["availability"] = None  # must be recomputed by the Profile agent
        note += f" (new busy block {ev.new_busy_block.start:%H:%M}–{ev.new_busy_block.end:%H:%M}; availability reset)"
    state.setdefault("events", []).append({"event": ev.event, "details": note, "at": fmt(now)})
    state.update({"step": 0, "revisions": 0, "review_round": 0, "reviews": [], "conflicts": [], "status": "replanning",
                  "next": "", "task": "", "final_summary": "", "degraded": False,
                  "validation": validate_state(state) if state.get("draft") else None})
    state["messages"] = (state.get("messages") or [])[-6:] + [
        {"from": "environment", "to": "coordinator", "content": f"REPLAN EVENT {ev.event}: {note}", "kind": "event",
         "step": 0}]
    return state


def replan(ev: ReplanEvent, trace: Optional[Trace] = None) -> PlanResult:
    rec = plan_store.get(ev.plan_id)
    if not rec:
        raise PlanningError(f"Unknown plan_id '{ev.plan_id}' (plans are kept in memory on the free hosting tier).")
    trace = trace or Trace(plan_id=ev.plan_id, kind="replan")
    trace.plan_id = ev.plan_id
    trace.kind = "replan"
    try:
        state = apply_event(rec["state"], ev)
        trace.message("environment", "coordinator", state["messages"][-1]["content"], kind="event")
        final = _run_graph(state, trace)
        return _finish(final, trace, ev.plan_id)
    finally:
        trace.close()


# ------------------------------------------------------------------ background runs for the UI
class RunManager:
    def __init__(self, keep: int = 100):
        self.runs: dict[str, dict] = {}
        self.keep = keep
        self._lock = threading.Lock()

    def start(self, fn: Callable[[Trace], PlanResult], kind: str = "plan", plan_id: Optional[str] = None) -> Trace:
        trace = Trace(kind=kind, plan_id=plan_id)
        rec = {"trace": trace, "result": None, "error": None, "status": "running"}
        with self._lock:
            self.runs[trace.run_id] = rec
            if len(self.runs) > self.keep:
                for old in list(self.runs)[: len(self.runs) - self.keep]:
                    self.runs.pop(old, None)

        def work():
            try:
                rec["result"] = fn(trace)
                rec["status"] = "done"
            except PlanningError as exc:
                rec["error"], rec["status"] = str(exc), "error"
                trace.emit("error", "system", str(exc))
                trace.close()
            except Exception as exc:  # noqa: BLE001
                log.exception("run failed")
                rec["error"], rec["status"] = f"{type(exc).__name__}: {exc}", "error"
                trace.emit("error", "system", rec["error"])
                trace.close()

        threading.Thread(target=work, daemon=True, name=trace.run_id).start()
        return trace

    def get(self, run_id: str) -> Optional[dict]:
        return self.runs.get(run_id)


runs = RunManager()
