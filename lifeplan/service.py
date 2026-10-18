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
from lifeplan.agents.shared import TRIP_MODES, travel_model, validate_state
from lifeplan.config import get_settings
from lifeplan.graph import build_graph
from lifeplan.memory import SAMPLE_CALENDAR, PlanStore, ProfileStore
from lifeplan.schemas import Budget, CostLedger, PlanRequest, PlanResult, ReplanEvent
from lifeplan.tools.datetime_parser import resolve_canonical_schedule
from lifeplan.tools.decision_engine import compute_trip_ledger
from lifeplan.tools.geo import geocode
from lifeplan.tools.nl_extractor import parse_natural_language_requirements
from lifeplan.tools.scheduler import Window, build_schedule as solve, fmt, parse_dt
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
    if req.destination or re.search(r"\b(trip|vacation|holiday|travel to|tour)\b", q):
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
    # Automatic calendar loading removed per Priority 1
    return None


def prepare_state(req: PlanRequest, trace: Trace) -> dict:
    """Deterministic set-up before the agents start: resolve places, time zone, mode and window."""
    s = get_settings()
    profile = profile_store.get(req.profile_id)

    # Priority 3: Extract structured requirements from natural language. Prompt takes precedence over defaults.
    extracted = parse_natural_language_requirements(
        req.query,
        now=req.now or datetime.now(),
        picker_start=req.start,
        picker_end=req.end,
        mode=req.mode,
    )

    if extracted.get("origin"):
        req.location = extracted["origin"]
        # Reset browser GPS coordinates if the prompt explicitly declared an origin
        req.coordinates = None
    elif not req.location:
        req.location = "Pune"

    if extracted.get("destination"):
        req.destination = extracted["destination"]

    if extracted.get("budget") is not None and (not req.budget or extracted.get("budget") != req.budget.amount):
        req.budget = Budget(amount=float(extracted["budget"]), currency=extracted.get("currency") or "INR")

    if extracted.get("travel_modes") and not req.travel_modes:
        req.travel_modes = extracted["travel_modes"]

    if extracted.get("interests") and not req.interests:
        req.interests = extracted["interests"]

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
    if extracted.get("is_trip") and mode not in TRIP_MODES:
        mode = "domestic_trip"

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
    canonical = resolve_canonical_schedule(
        query=req.query,
        now=now,
        picker_start=req.start,
        picker_end=req.end,
        mode=mode,
    )
    start, end = canonical.start, canonical.end
    if extracted.get("return_deadline") and extracted.get("end"):
        ext_end = parse_dt(extracted["end"])
        if ext_end > start:
            end = ext_end

    if end <= now and mode not in TRIP_MODES and not req.now:
        raise PlanningError("The requested time window is already in the past.")

    currency = (req.budget.currency if req.budget else None) or s.default_currency
    budget = float(req.budget.amount) if req.budget else None
    request = req.model_dump(mode="json")
    request["extracted_requirements"] = extracted
    if not request.get("interests"):
        request["interests"] = profile.get("interests", [])
    setup_msg = f"mode={mode}, window={fmt(start)}→{fmt(end)}, tz={tz_name}"
    if canonical.override_note or extracted.get("override_note"):
        note = canonical.override_note or extracted.get("override_note")
        setup_msg += f" [Precedence: {note}]"
    trace.emit("setup", "coordinator", setup_msg,
               origin=origin.get("name"), destination=(destination or {}).get("name"))
    return {
        "request": request, "requirements": extracted, "profile": profile, "mode": mode, "now": fmt(now), "timezone": tz_name,
        "currency": currency, "budget": budget, "origin": origin, "destination": destination,
        "window": {"start": fmt(start), "end": fmt(end), "locked": True, "source": canonical.source},
        "schedule": {
            "start": fmt(start), "end": fmt(end), "source": canonical.source,
            "query_extracted": canonical.query_extracted, "overridden_picker": canonical.overridden_picker,
            "override_note": canonical.override_note or extracted.get("override_note"), "locked": True,
        },
        "candidates": {}, "logistics": {}, "messages": [],
        "reviews": [], "conflicts": [], "events": [], "step": 0, "revisions": 0, "review_round": 0,
        "event_sources": [], "status": "planning",
    }


def _context(trace: Trace, state: dict) -> RunContext:
    return RunContext(trace=trace, profile_store=profile_store, now=parse_dt(state["now"]),
                      sample_calendar=None)


def _run_graph(state: dict, trace: Trace) -> dict:
    s = get_settings()
    graph = build_graph(_context(trace, state))
    return graph.invoke(state, {"recursion_limit": s.max_coordinator_steps * 4 + 20})


# ------------------------------------------------------------------ result
def _itinerary_sentence(days: list[dict], totals: dict, currency: Optional[str]) -> str:
    """Deterministic one-line summary built from the actual plan (cannot drift from the itinerary)."""
    stops = [(d["date"], it) for d in days for it in d["items"]
             if it["kind"] in ("meal", "snack", "activity", "event", "flight", "train")]
    if not stops:
        return "No feasible plan could be built for the available time."
    multi = len({dt for dt, _ in stops}) > 1
    parts = [f"{(it['start'][5:10] + ' ') if multi else ''}{it['start'][11:16]} {it['title']}" for _, it in stops[:8]]
    more = f" (+{len(stops) - 8} more)" if len(stops) > 8 else ""
    cost = totals.get("cost")
    budget = totals.get("budget")
    money = f" Estimated cost ~{cost} {currency or ''}".rstrip() + (f" of {round(budget)} budget." if budget else ".") \
        if cost is not None else ""
    return f"{len(stops)} stop{'s' if len(stops) != 1 else ''}: " + "; ".join(parts) + more + "." + money


def _local_windows(state: dict) -> list[Window]:
    av = state.get("availability") or {}
    origin = state.get("origin") or {"name": "Current Location", "lat": 18.5204, "lon": 73.8567}
    if av.get("free_windows"):
        return [Window(parse_dt(f["start"]), parse_dt(f["end"]), origin, True) for f in av["free_windows"]
                if f.get("minutes", 0) >= 45]
    w = state.get("window") or {}
    s = w.get("start") or state.get("now")
    e = w.get("end") or (parse_dt(s) + timedelta(hours=3)).isoformat()
    return [Window(parse_dt(s), parse_dt(e), origin, True)]


def _build_plan_options(state: dict, primary_days: list[dict], primary_totals: dict, currency: Optional[str]) -> list[dict]:
    cur = currency or state.get("currency") or "INR"
    options = []

    # Option 1: Recommended (Balanced)
    opt1_totals = copy.deepcopy(primary_totals or {})
    opt1_totals.setdefault("currency", cur)
    options.append({
        "id": "opt_recommended",
        "title": "Option 1: Recommended",
        "badge": "Top Match",
        "summary": "Balanced itinerary optimizing for your primary interests and comfortable travel pace.",
        "totals": opt1_totals,
        "days": copy.deepcopy(primary_days or []),
    })

    cands = state.get("candidates") or {}
    all_cands = [c for c in cands.values() if not c.get("unavailable") and c.get("kind") != "hotel"]
    if len(all_cands) < 2 and not primary_days:
        return options

    try:
        trip = state.get("mode") in TRIP_MODES
        if trip:
            from lifeplan.agents.planner_agent import _trip_windows
            wins = _trip_windows(state)
        else:
            wins = _local_windows(state)

        if not wins:
            return options

        tm = travel_model(state)
        budget_limit = float(state.get("budget") or 0) or None

        # Option 2: Budget-Saver (lowest estimated cost and transit/walking)
        cands_budget = sorted(all_cands, key=lambda c: (float(c.get("est_cost") or 0), -(c.get("rating") or 4.0)))
        chosen_budget = [{**c, "priority": max(0.2, 0.95 - idx * 0.1)} for idx, c in enumerate(cands_budget[:6])]
        budget_target = (budget_limit * 0.7) if budget_limit else None
        res_budget = solve(wins, chosen_budget, tm.leg, budget=budget_target, pace="relaxed", max_stops_per_window=4)

        if res_budget.days and any(d.get("items") for d in res_budget.days):
            b_days = copy.deepcopy(res_budget.days)
            for d in b_days:
                d["items"].sort(key=lambda it: it["start"])
                for it in d["items"]:
                    if it.get("cost") is not None:
                        it["cost"].setdefault("currency", cur)
                        it["cost"].setdefault("estimate", True)
            b_totals = dict(res_budget.totals)
            b_totals["cost"] = round(b_totals.get("cost", 0))
            b_totals["currency"] = cur
            b_totals["budget"] = state.get("budget")
            options.append({
                "id": "opt_budget",
                "title": "Option 2: Budget-Saver",
                "badge": "Best Value",
                "summary": "Cost-conscious route focusing on economical stops and relaxed travel.",
                "totals": b_totals,
                "days": b_days,
            })

        # Option 3: Quick & Close (closest distance to origin and minimal transit)
        cands_close = sorted(all_cands, key=lambda c: (float(c.get("distance_km") or 99), -(c.get("rating") or 4.0)))
        chosen_close = [{**c, "priority": max(0.2, 0.95 - idx * 0.1)} for idx, c in enumerate(cands_close[:6])]
        res_close = solve(wins, chosen_close, tm.leg, budget=budget_limit, pace="packed", max_stops_per_window=5)

        if res_close.days and any(d.get("items") for d in res_close.days):
            c_days = copy.deepcopy(res_close.days)
            for d in c_days:
                d["items"].sort(key=lambda it: it["start"])
                for it in d["items"]:
                    if it.get("cost") is not None:
                        it["cost"].setdefault("currency", cur)
                        it["cost"].setdefault("estimate", True)
            c_totals = dict(res_close.totals)
            c_totals["cost"] = round(c_totals.get("cost", 0))
            c_totals["currency"] = cur
            c_totals["budget"] = state.get("budget")
            options.append({
                "id": "opt_close",
                "title": "Option 3: Quick & Close",
                "badge": "Shortest Travel",
                "summary": "Minimizes transit distance and time with venues clustered nearest to you.",
                "totals": c_totals,
                "days": c_days,
            })
    except Exception as e:
        log.warning("Could not compute supplementary options: %s", e)

    return options


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
    if state.get("schedule", {}).get("override_note"):
        warnings.insert(0, state["schedule"]["override_note"])
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
    has_high_issue = any(i.get("severity") == "high" for i in val.get("issues", []))
    valid_stops = [
        it for d in days
        for it in d.get("items", [])
        if it.get("kind") not in ("travel", "busy")
    ]
    if not draft or has_high_issue or len(valid_stops) == 0:
        status = "failed"
    elif state.get("status") in ("ok", "partial", "failed"):
        status = state.get("status")
    else:
        status = "ok" if (val.get("ok") and not state.get("degraded")) else "partial"

    if status == "failed":
        if has_high_issue:
            first_high = next(i for i in val.get("issues", []) if i.get("severity") == "high")
            summary = f"Plan Infeasible: {first_high['message']} Please consider relaxing constraints or adjusting the budget."
        elif len(valid_stops) == 0:
            summary = "No feasible activities or stops could be scheduled within the specified constraints and free time windows."
        else:
            summary = "Unable to establish a feasible plan satisfying all constraints."
        if state.get("final_summary"):
            summary += f" ({state['final_summary']})"
    else:
        summary = _itinerary_sentence(days, draft.get("totals", {}), state.get("currency"))
        if state.get("final_summary"):
            summary += f" Coordinator: {state['final_summary']}"

    options = _build_plan_options(state, days, draft.get("totals", {}), state.get("currency")) if status != "failed" else []
    ledger_data = compute_trip_ledger(draft, state)
    cost_ledger = CostLedger(**ledger_data) if ledger_data else None

    return PlanResult(
        plan_id=plan_id, run_id=trace.run_id, status=status,
        mode=state.get("mode", "auto"), summary=summary, timezone=state.get("timezone", ""), days=days,
        totals=draft.get("totals", {}),
        cost_ledger=cost_ledger,
        requirements=state.get("requirements") or state.get("request", {}).get("extracted_requirements"),
        weather={"kind": weather.get("kind"), "daily": weather.get("daily"), "wet_hours": weather.get("wet_hours")}
        if weather else None,
        conflicts=conflicts, warnings=warnings,
        options=options,
        alternatives=[{
            "id": c["id"],
            "name": c["name"],
            "category": c.get("category"),
            "distance_km": round(float(c["distance_km"]), 1) if c.get("distance_km") is not None else None,
            "est_cost": round(float(c.get("est_cost") or ((c.get("cost") or {}).get("amount") if isinstance(c.get("cost"), dict) else 0) or 0)),
            "travel_min": round(float(c.get("travel_min") or ((c.get("travel") or {}).get("minutes") if isinstance(c.get("travel"), dict) else 0) or (c.get("distance_km") or 1) * 3)),
            "opening_hours": c.get("opening_hours"),
            "url": c.get("url") or c.get("website")
        } for c in alternatives],
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


def run_mock_plan(req: PlanRequest, trace: Trace) -> PlanResult:
    import time as pytime
    plan_id = trace.plan_id or f"plan_{uuid.uuid4().hex[:10]}"
    s = get_settings()
    tz_name = req.timezone or s.default_timezone
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz_name, tz = s.default_timezone, ZoneInfo(s.default_timezone)

    now = req.now.replace(tzinfo=None) if req.now else datetime.now(tz).replace(tzinfo=None)
    canonical = resolve_canonical_schedule(req.query, now, req.start, req.end, mode=req.mode)
    start, end = canonical.start, canonical.end
    date_str = start.strftime("%Y-%m-%d")
    loc_name = req.location or "Pune"

    def delay():
        pytime.sleep(0.06)

    setup_note = f"mode={req.mode}, window={fmt(start)}→{fmt(end)}, tz={tz_name} [DEMONSTRATION MOCK MODE]"
    if canonical.override_note:
        setup_note += f" [{canonical.override_note}]"
    trace.emit("setup", "coordinator", setup_note)
    delay()
    trace.message("user", "coordinator", req.query, kind="request")
    delay()

    trace.emit("agent_start", "profile")
    trace.message("coordinator", "profile", "Check user availability and commitments", kind="request")
    delay()
    trace.tool_call("profile", "compute_availability", {"start": fmt(start), "end": fmt(end)},
                    {"ok": True, "free_windows": [{"start": fmt(start), "end": fmt(end), "minutes": int((end - start).total_seconds() // 60)}]}, 15, True)
    trace.message("profile", "coordinator", f"Verified availability: user is clear from {start.strftime('%I:%M %p')} to {end.strftime('%I:%M %p')}.", kind="report")
    trace.emit("agent_end", "profile")
    delay()

    trace.emit("agent_start", "discovery")
    trace.message("coordinator", "discovery", f"Find verified places for '{req.query}' in {loc_name}", kind="request")
    delay()
    trace.tool_call("discovery", "search_venues", {"location": loc_name, "interests": req.interests or ["food", "cafe"]},
                    {"ok": True, "venues_found": 6}, 25, True)
    trace.tool_call("discovery", "get_weather", {"date": date_str},
                    {"ok": True, "forecast": "Pleasant, clear skies, 28°C, 0% rain"}, 10, True)
    trace.message("discovery", "coordinator", "Found 6 matching venues. Weather is ideal.", kind="report")
    trace.emit("agent_end", "discovery")
    delay()

    trace.emit("agent_start", "logistics")
    trace.message("coordinator", "logistics", "Analyze travel routes, road network duration & budget", kind="request")
    delay()
    trace.tool_call("logistics", "prepare_travel_matrix", {"mode": "cab"},
                    {"ok": True, "average_transit_min": 15}, 20, True)
    trace.message("logistics", "coordinator", "Road network measured; cab & walk routes confirmed within budget.", kind="report")
    trace.emit("agent_end", "logistics")
    delay()

    trace.emit("agent_start", "planner")
    trace.message("coordinator", "planner", f"Generate schedule within {start.strftime('%I:%M %p')}–{end.strftime('%I:%M %p')}", kind="request")
    delay()
    trace.tool_call("planner", "build_schedule", {"pace": req.pace},
                    {"ok": True, "scheduled_stops": 2}, 30, True)
    trace.message("planner", "coordinator", "Draft created with optimal meal & activity sequence.", kind="report")
    trace.emit("agent_end", "planner")
    delay()

    trace.emit("decision", "coordinator", "Draft itinerary meets constraints. Calling parallel review round.")
    delay()
    trace.message("profile", "coordinator", "APPROVE: schedule is conflict-free and fits user routine.", kind="critique")
    trace.message("discovery", "coordinator", "APPROVE: all places confirmed open during visit times.", kind="critique")
    trace.message("logistics", "coordinator", "APPROVE: travel legs verified on road network.", kind="critique")
    delay()
    trace.emit("final", "coordinator", "Plan finalized and validated.", status="ok")

    duration_total = max(30.0, (end - start).total_seconds() / 60)
    t1_start = start
    t1_end = start + timedelta(minutes=min(75, max(30, int(duration_total * 0.45))))
    travel_end = t1_end + timedelta(minutes=min(20, max(10, int(duration_total * 0.15))))
    t2_start = travel_end
    t2_end = min(end, travel_end + timedelta(minutes=min(90, max(30, int(duration_total * 0.4)))))

    currency = (req.budget.currency if req.budget else None) or "INR"
    budget_amt = float(req.budget.amount) if req.budget else 1500.0

    items = [
        {
            "start": fmt(t1_start),
            "end": fmt(t1_end),
            "kind": "meal",
            "title": f"Dining at {loc_name.split(',')[0]} Bistro",
            "place": {
                "name": f"{loc_name.split(',')[0]} Bistro & Cafe",
                "address": f"Central District, {loc_name}",
                "lat": 18.5204 if "pune" in loc_name.lower() else 19.0760,
                "lon": 73.8567 if "pune" in loc_name.lower() else 72.8777,
                "source": "OpenStreetMap [Mock Data]",
                "url": "https://www.openstreetmap.org",
            },
            "cost": {"amount": min(450.0, budget_amt * 0.35), "currency": currency, "estimate": True},
            "notes": "Cozy dining with great local cuisine. [Demonstration Item]",
        },
        {
            "start": fmt(t1_end),
            "end": fmt(travel_end),
            "kind": "travel",
            "title": "Travel via cab / ride share",
            "travel": {"mode": "cab", "minutes": int((travel_end - t1_end).total_seconds() // 60), "km": 3.8, "source": "OSRM road network [Mock Data]"},
            "cost": {"amount": min(120.0, budget_amt * 0.12), "currency": currency, "estimate": True},
        },
        {
            "start": fmt(t2_start),
            "end": fmt(t2_end),
            "kind": "activity",
            "title": f"Activity & Leisure at {loc_name.split(',')[0]} Cultural Center",
            "place": {
                "name": f"{loc_name.split(',')[0]} Cultural Center & Gardens",
                "address": f"Heritage Lane, {loc_name}",
                "lat": 18.5280 if "pune" in loc_name.lower() else 19.0820,
                "lon": 73.8640 if "pune" in loc_name.lower() else 72.8850,
                "source": "Geoapify [Mock Data]",
                "url": "https://www.geoapify.com",
            },
            "cost": {"amount": min(250.0, budget_amt * 0.25), "currency": currency, "estimate": True},
            "notes": "Relaxed environment with cultural exhibitions. [Demonstration Item]",
        }
    ]

    warnings = [
        "[Demonstration Data]: This itinerary was generated in Mock Mode for UI testing without consuming API quotas."
    ]
    if canonical.override_note:
        warnings.insert(0, canonical.override_note)

    opt1_totals = {"cost": 820.0, "budget": budget_amt, "currency": currency, "stops": 2, "travel_minutes": 15}
    opt1 = {
        "id": "opt_recommended",
        "title": "Option 1: Recommended",
        "badge": "Top Match",
        "summary": f"Balanced itinerary in {loc_name} optimizing for comfort, top-rated dining, and cultural leisure.",
        "totals": opt1_totals,
        "days": [{"date": date_str, "items": items}],
    }

    # Option 2: Budget-Saver mock
    walk_end = t1_start + timedelta(minutes=12)
    b2_end = min(end, walk_end + timedelta(minutes=int(duration_total * 0.45)))
    items_opt2 = [
        {
            "start": fmt(t1_start),
            "end": fmt(walk_end),
            "kind": "travel",
            "title": "Short walking stroll through historic market",
            "travel": {"mode": "walk", "minutes": 12, "km": 0.8, "source": "OSRM Pedestrian [Mock Data]"},
            "cost": {"amount": 0.0, "currency": currency, "estimate": False},
        },
        {
            "start": fmt(walk_end),
            "end": fmt(walk_end + timedelta(minutes=45)),
            "kind": "snack",
            "title": f"Artisan Bakery & Tea Stall, {loc_name.split(',')[0]}",
            "place": {
                "name": f"Corner Heritage Tea House & Bakery",
                "address": f"Market Square, {loc_name}",
                "lat": 18.5190 if "pune" in loc_name.lower() else 19.0720,
                "lon": 73.8540 if "pune" in loc_name.lower() else 72.8730,
                "source": "OpenStreetMap [Mock Data]",
                "url": "https://www.openstreetmap.org",
            },
            "cost": {"amount": min(180.0, budget_amt * 0.15), "currency": currency, "estimate": True},
            "notes": "Authentic local snacks and tea at friendly prices.",
        },
        {
            "start": fmt(walk_end + timedelta(minutes=45)),
            "end": fmt(b2_end),
            "kind": "activity",
            "title": f"Public Botanical Garden & Heritage Trail",
            "place": {
                "name": f"Green Valley Public Garden",
                "address": f"Garden Avenue, {loc_name}",
                "lat": 18.5240 if "pune" in loc_name.lower() else 19.0780,
                "lon": 73.8590 if "pune" in loc_name.lower() else 72.8790,
                "source": "OpenStreetMap [Mock Data]",
                "url": "https://www.openstreetmap.org",
            },
            "cost": {"amount": min(50.0, budget_amt * 0.05), "currency": currency, "estimate": True},
            "notes": "Scenic green open spaces with nominal entry fee.",
        }
    ]
    opt2_totals = {"cost": 230.0, "budget": budget_amt, "currency": currency, "stops": 2, "travel_minutes": 12}
    opt2 = {
        "id": "opt_budget",
        "title": "Option 2: Budget-Saver",
        "badge": "Best Value",
        "summary": f"Economical day out in {loc_name} featuring street treats, walking, and serene public gardens.",
        "totals": opt2_totals,
        "days": [{"date": date_str, "items": items_opt2}],
    }

    # Option 3: Quick & Close mock
    q_walk = t1_start + timedelta(minutes=5)
    q2_end = min(end, q_walk + timedelta(minutes=int(duration_total * 0.4)))
    items_opt3 = [
        {
            "start": fmt(t1_start),
            "end": fmt(t1_start + timedelta(minutes=45)),
            "kind": "meal",
            "title": f"Local Express Cafe & Book Lounge",
            "place": {
                "name": f"Station Side Express Cafe",
                "address": f"Near Transit Hub, {loc_name}",
                "lat": 18.5210 if "pune" in loc_name.lower() else 19.0765,
                "lon": 73.8570 if "pune" in loc_name.lower() else 72.8780,
                "source": "OpenStreetMap [Mock Data]",
                "url": "https://www.openstreetmap.org",
            },
            "cost": {"amount": min(320.0, budget_amt * 0.25), "currency": currency, "estimate": True},
            "notes": "Immediate proximity, delicious bites, quick service.",
        },
        {
            "start": fmt(t1_start + timedelta(minutes=45)),
            "end": fmt(q_walk + timedelta(minutes=45)),
            "kind": "travel",
            "title": "Quick 4-minute walk across the promenade",
            "travel": {"mode": "walk", "minutes": 4, "km": 0.3, "source": "OSRM Pedestrian [Mock Data]"},
            "cost": {"amount": 0.0, "currency": currency, "estimate": False},
        },
        {
            "start": fmt(q_walk + timedelta(minutes=45)),
            "end": fmt(q2_end),
            "kind": "activity",
            "title": f"Contemporary Art Gallery & Studio",
            "place": {
                "name": f"City Art Space & Studio",
                "address": f"Station Road, {loc_name}",
                "lat": 18.5220 if "pune" in loc_name.lower() else 19.0775,
                "lon": 73.8580 if "pune" in loc_name.lower() else 72.8790,
                "source": "Geoapify [Mock Data]",
                "url": "https://www.geoapify.com",
            },
            "cost": {"amount": min(200.0, budget_amt * 0.2), "currency": currency, "estimate": True},
            "notes": "Just 300 meters away with engaging contemporary exhibits.",
        }
    ]
    opt3_totals = {"cost": 520.0, "budget": budget_amt, "currency": currency, "stops": 2, "travel_minutes": 4}
    opt3 = {
        "id": "opt_close",
        "title": "Option 3: Quick & Close",
        "badge": "Shortest Travel",
        "summary": f"Fast and hyper-local plan in {loc_name} with only 4 minutes total transit.",
        "totals": opt3_totals,
        "days": [{"date": date_str, "items": items_opt3}],
    }

    mock_draft = {"days": [{"date": date_str, "items": items}], "totals": opt1_totals}
    mock_state = {"request": req.model_dump(mode="json"), "currency": currency, "budget": budget_amt}
    mock_ledger_data = compute_trip_ledger(mock_draft, mock_state)
    mock_ledger = CostLedger(**mock_ledger_data) if mock_ledger_data else None
    mock_reqs = parse_natural_language_requirements(req.query, now=now, picker_start=req.start, picker_end=req.end)

    result = PlanResult(
        plan_id=plan_id,
        run_id=trace.run_id,
        status="ok",
        mode=req.mode if req.mode != "auto" else "free_time",
        summary=f"[Demo Plan] Itinerary in {loc_name} from {start.strftime('%I:%M %p')} to {end.strftime('%I:%M %p')} ({start.strftime('%a, %d %b')})",
        timezone=tz_name,
        days=[{"date": date_str, "items": items}],
        totals=opt1_totals,
        cost_ledger=mock_ledger,
        requirements=mock_reqs,
        weather={"kind": "forecast", "daily": [{"date": date_str, "desc": "Pleasant, clear skies", "t_min": 21, "t_max": 31, "precip_prob_max": 0}]},
        conflicts=[],
        warnings=warnings,
        options=[opt1, opt2, opt3],
        alternatives=[
            {"id": "mock_alt_1", "name": f"Green Valley Garden, {loc_name.split(',')[0]}", "category": "nature", "distance_km": 2.1, "est_cost": 50, "travel_min": 8},
            {"id": "mock_alt_2", "name": f"City Heritage Museum, {loc_name.split(',')[0]}", "category": "culture", "distance_km": 3.4, "est_cost": 150, "travel_min": 14},
            {"id": "mock_alt_3", "name": f"Skyline Rooftop Lounge, {loc_name.split(',')[0]}", "category": "dining", "distance_km": 4.2, "est_cost": 650, "travel_min": 18},
        ],
        stats={
            "llm_calls": 0, "tool_calls": 5, "messages": 8, "input_tokens": 0, "output_tokens": 0,
            "llm_fallbacks": 0, "tool_errors": 0, "coordinator_steps": 4, "planner_revisions": 1,
            "review_rounds": 1, "agents": ["coordinator", "profile", "discovery", "logistics", "planner"],
            "duration_s": 0.6,
        }
    )
    plan_store.put(plan_id, {"state": {"request": req.model_dump(mode="json"), "window": {"start": fmt(start), "end": fmt(end)}}, "result": result.model_dump(mode="json")})
    trace.emit("result", "system", result.summary, status=result.status, plan_id=plan_id)
    return result


# ------------------------------------------------------------------ public API
def run_plan(req: PlanRequest, trace: Optional[Trace] = None) -> PlanResult:
    plan_id = f"plan_{uuid.uuid4().hex[:10]}"
    trace = trace or Trace(plan_id=plan_id)
    trace.plan_id = plan_id
    try:
        if req.dev_mock or get_settings().dev_mock_planning:
            return run_mock_plan(req, trace)
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
