"""📅 Planner & Optimization Agent.

Combines everything on the blackboard into an actual schedule, optimising
time + distance + cost + preferences + opening hours + activity duration.
The LLM decides priorities, must-haves, pace and trade-offs; a deterministic solver tool does the
combinatorial fitting. It can consult the Logistics and Discovery agents directly (peer-to-peer)
when it needs a travel time or an alternative venue, and it re-plans when critiqued.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

from lifeplan.agents import discovery_agent, logistics_agent
from lifeplan.agents.base import RunContext, Workspace, make_tool, run_react_agent
from lifeplan.agents.shared import (TRIP_MODES, compact_candidate, compact_plan, request_brief, travel_model,
                                    validate_state)
from lifeplan.tools import calendar as cal
from lifeplan.tools.decision_engine import ensure_intercity_legs
from lifeplan.tools.places import normalise_interests
from lifeplan.tools.scheduler import Window, build_schedule as solve, fmt, parse_dt

NAME = "planner"

SYSTEM = """You are the Planner & Optimization Agent in LifePlan AI, a multi-agent planner.
Turn the blackboard (free windows, real candidates, travel data, weather, budget, preferences) into the best
concrete schedule.
- Use build_schedule: YOU choose priorities (0-1) for candidate ids, must_include items and pace; the solver fits
  them to time windows, opening hours, travel times and budget. Inspect the result and the validation, then
  improve it (re-run with different priorities/exclusions) if there are high-severity issues.
- Only schedule candidates from the pool (ids). If you need a travel time or an alternative venue, consult the
  Logistics or Discovery agent directly with consult_logistics / consult_discovery.
- Balance: interests first, a meal at sensible times, short travel, within budget, weather-appropriate.
  A candidate's "pref" (0-1) is the user's learned preference for that category from past feedback.
- When the Coordinator passes critiques from other agents, address each one explicitly.
Finish with a short report (max 150 words): the plan outline (times + places), totals vs budget, which conflicts you
resolved and how, and anything you could not fit."""


def _trip_windows(state: dict) -> list[Window]:
    ensure_intercity_legs(state)
    w = state["window"]
    start, end = parse_dt(w["start"]), parse_dt(w["end"])
    log = state.get("logistics") or {}
    base = log.get("hotel") or state.get("destination") or state.get("origin")
    fixed = log.get("fixed_items") or []
    arrive = max((parse_dt(f["end"]) for f in fixed if parse_dt(f["start"]) <= start + timedelta(hours=36)),
                 default=None)
    depart = min((parse_dt(f["start"]) for f in fixed if parse_dt(f["start"]) >= end - timedelta(hours=36)),
                 default=None)
    is_flight = any(f.get("kind") == "flight" for f in fixed)
    arr_buffer = 45 if is_flight else 15
    dep_buffer = 120 if is_flight else 20
    windows = []
    d = start.date()
    while d <= end.date():
        ws_ = max(datetime.combine(d, time(9, 0)), start)
        we = min(datetime.combine(d, time(21, 30)), end)
        if arrive and arrive.date() == d:
            ws_ = max(ws_, arrive + timedelta(minutes=arr_buffer))
        if depart and depart.date() == d:
            we = min(we, depart - timedelta(minutes=dep_buffer))
        if (we - ws_).total_seconds() >= 1800:
            windows.append(Window(ws_, we, base, True))
        d += timedelta(days=1)
    return windows


def _local_windows(state: dict) -> list[Window]:
    av = state.get("availability") or {}
    origin = state["origin"]
    if av.get("free_windows"):
        return [Window(parse_dt(f["start"]), parse_dt(f["end"]), origin, True) for f in av["free_windows"]
                if f["minutes"] >= 45]
    w = state["window"]
    return [Window(parse_dt(w["start"]), parse_dt(w["end"]), origin, True)]


def _intersect_with_availability(windows: list[Window], state: dict) -> list[Window]:
    av = state.get("availability") or {}
    if not av.get("busy") and not av.get("predicted_busy"):
        return windows
    busy = [(parse_dt(b["start"]), parse_dt(b["end"])) for b in av.get("busy", [])]
    busy += [(parse_dt(b["start"]), parse_dt(b["end"])) for b in av.get("predicted_busy", [])
             if b.get("confidence", 0) >= 0.7]
    out = []
    for w in windows:
        for s, e in cal.free_windows(busy, w.start, w.end, 45):
            out.append(Window(s, e, w.base, w.return_to_base))
    return out


def build_tools(ws: Workspace) -> list:
    ctx = ws.ctx
    req = ws.get("request") or {}

    def list_candidates(category: str = "", include_unavailable: bool = False) -> dict:
        """List the verified candidate pool (optionally one category: food, cafe, museum, nature, event, hotel ...)."""
        cands = [c for c in (ws.get("candidates") or {}).values()
                 if (include_unavailable or not c.get("unavailable")) and (not category or c.get("category") == category)]
        return {"ok": True, "count": len(cands), "candidates": [compact_candidate(c) for c in cands[:40]]}

    def build_schedule(priorities: list[str], must_include: list[str] = [], exclude: list[str] = [],
                       pace: str = "", max_stops_per_window: int = 5, windows: list[str] = []) -> dict:
        """Build a draft itinerary with the optimisation solver.
        priorities: ["<candidate_id>:<0..1>", ...] — your preference scores; only these candidates are used.
        must_include: ids that must appear if at all feasible. exclude: ids to leave out.
        pace: relaxed | balanced | packed. windows: optional ["YYYY-MM-DDTHH:MM|YYYY-MM-DDTHH:MM", ...] to override
        the free windows. Returns the draft and an automatic validation (conflicts) report."""
        pool = ws.get("candidates") or {}
        chosen = []
        for p in priorities:
            cid, _, score = str(p).rpartition(":")
            if not cid:
                cid, score = str(p), "0.5"
            try:
                sc = max(0.0, min(1.0, float(score)))
            except ValueError:
                sc = 0.5
            c = pool.get(cid)
            if c and not c.get("unavailable") and cid not in exclude and c.get("kind") != "hotel":
                chosen.append({**c, "priority": sc})
        for cid in must_include:
            if cid in pool and cid not in {c["id"] for c in chosen} and not pool[cid].get("unavailable"):
                chosen.append({**pool[cid], "priority": 0.9})
        unknown = [str(p).rpartition(":")[0] or p for p in priorities if (str(p).rpartition(":")[0] or p) not in pool]
        if not chosen:
            shortlist_ids = ws.get("shortlist_ids") or []
            for cid in shortlist_ids:
                if cid in pool and not pool[cid].get("unavailable") and cid not in exclude and pool[cid].get("kind") != "hotel":
                    chosen.append({**pool[cid], "priority": 0.7})
        if not chosen:
            return {"ok": False, "error": "no valid candidate ids in priorities", "unknown_ids": unknown[:10]}

        origin = ws.get("origin") or {}
        dest = ws.get("destination") or {}
        origin_city = (origin.get("city") or origin.get("name") or "").strip().lower()
        dest_city = (dest.get("city") or dest.get("name") or "").strip().lower()
        is_intercity = (ws.get("mode") in TRIP_MODES) or bool(origin_city and dest_city and origin_city != dest_city)
        if is_intercity:
            ensure_intercity_legs(ws.state)
            ws.set("logistics", ws.state.get("logistics"))
        trip = (ws.get("mode") in TRIP_MODES) or is_intercity

        if windows:
            base = ((ws.get("logistics") or {}).get("hotel") or ws.get("destination")) if trip else ws.get("origin")
            wins = []
            for w in windows:
                try:
                    a, b = str(w).split("|")
                    wins.append(Window(parse_dt(a), parse_dt(b), base, True))
                except ValueError:
                    return {"ok": False, "error": f"bad window '{w}', use START|END"}
        else:
            wins = _intersect_with_availability(_trip_windows(ws.state), ws.state) if trip else _local_windows(ws.state)
        if not wins:
            return {"ok": False, "error": "no free time window of at least 45 minutes"}

        tm = travel_model(ws.state)
        wet = set((ws.get("weather") or {}).get("wet_hours") or [])
        log = ws.get("logistics") or {}
        fixed = list(log.get("fixed_items") or [])
        hotel = log.get("hotel")
        fixed_cost = sum(float((f.get("cost") or {}).get("amount") or 0) for f in fixed)
        nights = max(0, (parse_dt(ws.get("window")["end"]).date() - parse_dt(ws.get("window")["start"]).date()).days) if trip else 0
        hotel_cost = float((hotel or {}).get("price_per_night") or 0) * nights
        res = solve(wins, chosen, tm.leg, budget=ws.get("budget"), pace=pace or req.get("pace", "balanced"),
                    must_include=must_include, wet_hours=wet, max_stops_per_window=max(1, min(max_stops_per_window, 8)),
                    spent_already=fixed_cost + hotel_cost)
        days = {d["date"]: d for d in res.days}
        for f in fixed:
            day = days.setdefault(f["start"][:10], {"date": f["start"][:10], "items": []})
            day["items"].append(f)
        if hotel and nights:
            w_start = parse_dt(ws.get("window")["start"])
            ci = max(w_start, datetime.combine(w_start.date(), time(14, 0)))
            arrivals = [parse_dt(f["end"]) for f in fixed if parse_dt(f["end"]).date() == w_start.date()]
            if arrivals:
                ci = max(ci, min(arrivals) + timedelta(minutes=45))
            first = ci.date().isoformat()
            days.setdefault(first, {"date": first, "items": []})["items"].append({
                "start": fmt(ci), "end": fmt(ci + timedelta(minutes=30)),
                "kind": "hotel", "title": f"Check in: {hotel['name']} ({nights} night{'s' if nights > 1 else ''})",
                "candidate_id": hotel.get("id"),
                "place": {k: hotel.get(k) for k in ("name", "lat", "lon", "url", "source")},
                "cost": {"amount": round(hotel_cost), "estimate": True}})
        ordered = []
        for date_key in sorted(days):
            d = days[date_key]
            d["items"].sort(key=lambda it: it["start"])
            ordered.append(d)
        totals = dict(res.totals)
        totals["cost"] = round(res.totals["cost"])
        totals["currency"] = ws.get("currency")
        totals["budget"] = ws.get("budget")

        # Deterministic Smart Decision Engine: strictly enforce total budget limit
        budget_limit = ws.get("budget")
        if budget_limit is not None and budget_limit > 0 and totals["cost"] > budget_limit:
            for d in ordered:
                removable = [
                    it for it in d["items"]
                    if it.get("kind") not in ("travel", "hotel", "flight", "train", "busy")
                    and it.get("candidate_id") not in must_include
                ]
                removable.sort(key=lambda it: pool.get(it.get("candidate_id", ""), {}).get("priority", 0.5))
                for it in removable:
                    if totals["cost"] <= budget_limit:
                        break
                    d["items"].remove(it)
                    item_cost = float((it.get("cost") or {}).get("amount") or 0)
                    totals["cost"] = round(totals["cost"] - item_cost)
                    res.unscheduled.append({
                        "id": it.get("candidate_id"),
                        "reason": f"Pruned by Smart Decision Engine to strictly satisfy budget limit ({budget_limit})"
                    })

        draft = {"days": ordered, "totals": totals, "unscheduled": res.unscheduled,
                 "pace": pace or req.get("pace", "balanced"), "travel_mode": tm.mode,
                 "priorities": {c["id"]: c["priority"] for c in chosen}}
        ws.set("draft", draft)
        ws.state["draft"] = draft
        validation = validate_state(ws.state)
        ws.set("validation", validation)
        return {"ok": True, "plan": compact_plan(draft), "totals": totals, "unscheduled": res.unscheduled,
                "unknown_ids_ignored": unknown[:10],
                "validation": {"ok": validation["ok"], "issues": [f"[{i['severity']}] {i['type']}: {i['message']}"
                                                                 for i in validation["issues"]][:10]}}

    def validate_plan() -> dict:
        """Re-run all constraint checks (calendar, travel feasibility, budget, opening hours, weather, grounding)."""
        v = validate_state(ws.state)
        ws.set("validation", v)
        return v

    def _consult(agent_mod, agent_name: str, question: str) -> dict:
        ctx.trace.message(NAME, agent_name, question, kind="consult")
        updates, answer = agent_mod.run(ws.state, ctx, f"(Direct request from the Planner) {question}")
        for k, v in updates.items():
            ws.set(k, v)
        ctx.trace.message(agent_name, NAME, answer, kind="consult_reply")
        return {"ok": True, "answer": answer, "updated": sorted(updates)}

    def consult_logistics(question: str) -> dict:
        """Ask the Travel & Logistics agent directly (e.g. 'Is cab or metro faster from X to Y at 18:00?' or
        'prepare a travel matrix for ids ...'). Its blackboard updates become visible to you."""
        return _consult(logistics_agent, "logistics", question)

    def consult_discovery(request: str) -> dict:
        """Ask the Discovery agent directly (e.g. 'find an indoor dessert place open after 21:00 near evt:..').
        New candidates it finds join the pool."""
        return _consult(discovery_agent, "discovery", request)

    return [make_tool(f) for f in (list_candidates, build_schedule, validate_plan, consult_logistics,
                                   consult_discovery)]


def run(state: dict, ctx: RunContext, instruction: str, review: bool = False) -> tuple[dict, str]:
    ws = Workspace(dict(state), ctx)
    tools = build_tools(ws)
    interests = normalise_interests((state.get("request") or {}).get("interests") or [])
    cands = [compact_candidate(c) for c in (state.get("candidates") or {}).values()
             if not c.get("unavailable") and c.get("kind") != "hotel"]
    av = state.get("availability") or {}
    prompt = (f"Request: {request_brief(state)}\nInterest categories: {interests}\n"
              f"Free windows: {json.dumps(av.get('free_windows')) if av else 'not computed (whole window usable)'}\n"
              f"Busy: {json.dumps([b['start'] + '-' + b['end'][11:] + ' ' + b['title'] for b in av.get('busy', [])])}\n"
              f"Weather wet hours: {(state.get('weather') or {}).get('wet_hours', [])[:12]}\n"
              f"Travel: mode={(state.get('logistics') or {}).get('mode')}, hotel={((state.get('logistics') or {}).get('hotel') or {}).get('name')}, "
              f"fixed legs={len((state.get('logistics') or {}).get('fixed_items') or [])}\n"
              f"Candidate pool ({len(cands)}): {json.dumps(cands[:30])}\n"
              f"Current draft: {json.dumps(compact_plan(state.get('draft'))) if state.get('draft') else 'none'}\n\n"
              f"Task from Coordinator: {instruction}")
    report = run_react_agent(NAME, SYSTEM, prompt, tools, ctx, max_iterations=6)
    return ws.updates(), report


def fallback(state: dict, ctx: RunContext) -> tuple[dict, str]:
    """Deterministic behaviour if no LLM is reachable: schedule by interest match and distance."""
    ws = Workspace(dict(state), ctx)
    tools = {t.name: t for t in build_tools(ws)}
    interests = set(normalise_interests((state.get("request") or {}).get("interests") or []))
    pri = []
    for c in (state.get("candidates") or {}).values():
        if c.get("unavailable") or c.get("kind") == "hotel":
            continue
        score = 0.8 if c.get("category") in interests or c.get("kind") == "event" else 0.4
        score -= min(0.3, (c.get("distance_km") or 0) / 30)
        if c.get("learned_weight") is not None:
            score += (float(c["learned_weight"]) - 0.5) * 0.4
        pri.append(f"{c['id']}:{round(max(score, 0.05), 2)}")
    res = tools["build_schedule"].func(pri)
    return ws.updates(), f"[deterministic fallback, LLM unavailable] draft built: {res.get('ok')}"
