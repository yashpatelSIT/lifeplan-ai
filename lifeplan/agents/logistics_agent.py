"""🚗 Travel & Logistics Agent.

Handles walking, public transport, cabs, driving, flights, trains, buses and hotels. It measures
real travel times on the road network, picks sensible modes, prices legs, and decides whether a
proposed set of activities can *realistically* fit the available time and budget.
"""

from __future__ import annotations

import json

from lifeplan.agents.base import RunContext, Workspace, make_tool, run_react_agent
from lifeplan.agents.shared import compact_candidate, request_brief, travel_model
from lifeplan.tools import routing, validator
from lifeplan.tools import travel as tv
from lifeplan.tools.decision_engine import extract_min_star_rating, filter_hotels_by_star_rating
from lifeplan.tools.scheduler import parse_dt

NAME = "logistics"

SYSTEM = """You are the Travel & Logistics Agent in LifePlan AI, a multi-agent planner.
You own HOW the user moves and whether it is feasible.
- Local plans: choose the local travel mode (respect the user's allowed modes; cab when time is tight,
  walk/transit when cheap and close), then call prepare_travel_matrix for the candidates the planner is likely to
  use so the planner has real road-network travel times.
- Trips: compare inter-city options (intercity_options / search_flights), add the chosen outbound and return legs
  with add_intercity_leg, find hotels (search_hotels) and select one (select_hotel) within budget.
- Convert currencies when the destination uses another currency.
- Never invent prices or schedules: anything not from a live API is an estimate and must be called one.
Finish with a short report (max 130 words): chosen mode(s) and why, key travel times, intercity/hotel choices,
estimated travel spend vs budget, and any feasibility risks."""

REVIEW = """You are the Travel & Logistics Agent reviewing a DRAFT plan.
Call check_travel_and_budget. If travel between stops is infeasible or the plan is over budget, say exactly what
to change (switch mode, drop/replace a far stop, shorten a visit). You may call travel_time_between to verify a leg.
Reply: APPROVE or REVISE + concrete fixes. Max 100 words."""


def _point(ws: Workspace, ref: str) -> dict | None:
    ref = (ref or "").strip()
    if ref in ("origin", "home", ""):
        return ws.get("origin")
    if ref == "destination":
        return ws.get("destination")
    if ref == "hotel":
        return (ws.get("logistics") or {}).get("hotel")
    return (ws.get("candidates") or {}).get(ref)


def build_tools(ws: Workspace, review: bool = False) -> list:
    req = ws.get("request") or {}
    allowed = [routing.normalise_mode(m) for m in req.get("travel_modes", ["walk", "transit", "cab"])]
    currency = ws.get("currency", "INR")

    def choose_travel_mode(mode: str, reason: str) -> dict:
        """Set the main local travel mode for the plan: one of walk | transit | cab | drive | bike."""
        m = routing.normalise_mode(mode)
        if m not in ("walk", "transit", "cab", "drive", "bike"):
            return {"ok": False, "error": "mode must be walk, transit, cab, drive or bike"}
        if m not in allowed and not (m == "drive" and "cab" in allowed):
            return {"ok": False, "error": f"user only allows {allowed}"}
        ws.set_logistics("mode", m)
        ws.set_logistics("mode_reason", reason)
        log = dict(ws.get("logistics") or {})
        if log.get("matrix") and log["matrix"].get("mode") != m:
            log.pop("matrix")  # stale matrix for a different mode
            ws.set("logistics", log)
        return {"ok": True, "mode": m}

    def travel_time_between(from_ref: str, to_ref: str, mode: str = "") -> dict:
        """Real travel time & distance between two points. Refs: "origin", "destination", "hotel" or a candidate id."""
        a, b = _point(ws, from_ref), _point(ws, to_ref)
        if not a or not b:
            return {"ok": False, "error": "unknown from_ref/to_ref"}
        m = routing.normalise_mode(mode or (ws.get("logistics") or {}).get("mode") or "cab")
        leg = routing.travel_time((a["lat"], a["lon"]), (b["lat"], b["lon"]), m)
        leg["cost"] = routing.leg_cost(m, leg["km"], leg["minutes"], currency, req.get("party_size", 1))
        return leg

    def prepare_travel_matrix(candidate_ids: list[str], mode: str = "") -> dict:
        """Compute real travel times between the start point and all given candidates (one road-network API call).
        The Planner's scheduler uses this matrix. Pass up to 18 candidate ids."""
        cands = ws.get("candidates") or {}
        ids = [c for c in candidate_ids if c in cands]
        shortlist_ids = ws.get("shortlist_ids") or []
        if shortlist_ids:
            prioritized = [cid for cid in shortlist_ids if cid in cands]
            for cid in ids:
                if cid not in prioritized and cid in cands:
                    prioritized.append(cid)
            ids = prioritized[:8]
        else:
            ids = ids[:8]

        if not ids:
            return {"ok": False, "error": "no known candidate ids"}
        m = routing.normalise_mode(mode or (ws.get("logistics") or {}).get("mode") or "cab")

        # Reuse existing matrix if already calculated for these points (progressive computation)
        existing_matrix = (ws.get("logistics") or {}).get("matrix")
        if existing_matrix and existing_matrix.get("mode") == m:
            cached_ids = set(existing_matrix.get("ids", []))
            if set(ids).issubset(cached_ids):
                all_ids = existing_matrix["ids"]
                o = 0
                summary = {all_ids[j]: existing_matrix["minutes"][o][j] for j in range(len(all_ids)) if j != o and all_ids[j] in ids}
                return {"ok": True, "mode": m, "source": existing_matrix.get("source", "cached"), "minutes_from_start": summary, "cached": True}

        base_ids, points = [], []
        for ref in ("origin", "hotel", "destination"):
            p = _point(ws, ref)
            if p and p.get("lat") is not None:
                base_ids.append(p.get("id") or ref)
                points.append((p["lat"], p["lon"]))
        all_ids = base_ids + ids
        points += [(cands[i]["lat"], cands[i]["lon"]) for i in ids]
        res = routing.travel_matrix(points, m)
        ws.set_logistics("matrix", {"ids": all_ids, "minutes": res["minutes"], "km": res.get("km"), "mode": m,
                                    "source": res["source"]})
        ws.set_logistics("mode", m)
        o = 0
        summary = {all_ids[j]: res["minutes"][o][j] for j in range(len(all_ids)) if j != o}
        return {"ok": True, "mode": m, "source": res["source"], "minutes_from_start": summary}

    def intercity_options(travel_date: str = "") -> dict:
        """Compare drive / outstation cab / train / bus / flight between origin and destination city."""
        o, d = ws.get("origin") or {}, ws.get("destination") or {}
        if not d:
            return {"ok": False, "error": "no destination in this request"}
        res = tv.intercity_options(o.get("city") or o["name"], d.get("city") or d["name"],
                                   travel_date or (ws.get("window") or {}).get("start", "")[:10], currency)
        ws.set_logistics("intercity", res)
        return res

    def search_flights(outbound_date: str, return_date: str = "", origin_iata: str = "", dest_iata: str = "") -> dict:
        """Live flight offers (Google Flights via SerpApi) — give IATA airport codes (e.g. PNQ, DEL, NRT) for real
        prices; otherwise an estimate is returned. Dates YYYY-MM-DD."""
        o, d = ws.get("origin") or {}, ws.get("destination") or {}
        return tv.search_flights(o.get("city") or o.get("name"), d.get("city") or d.get("name"), outbound_date,
                                 return_date or None, origin_iata or None, dest_iata or None, currency,
                                 req.get("party_size", 1))

    def search_hotels(check_in: str, check_out: str, max_price_per_night: float = 0) -> dict:
        """Find hotels at the destination (live prices via SerpApi if configured, else real listings with estimated
        prices). Results join the candidate pool as kind='hotel'."""
        d = ws.get("destination") or ws.get("origin") or {}
        res = tv.search_hotels(d.get("city") or d.get("name"), check_in, check_out, req.get("party_size", 1),
                               currency, max_price_per_night or None)
        for h in res.get("hotels", []):
            if h.get("lat") is None:
                continue
            ws.add_candidate({"id": h["id"], "name": h["name"], "category": "hotel", "kind": "hotel", "lat": h["lat"],
                              "lon": h["lon"], "est_cost": h.get("price_per_night"), "url": h.get("url"),
                              "source": h.get("source"), "source_id": h["id"], "rating": h.get("rating"),
                              "star_rating": h.get("star_rating"), "star_unverified": h.get("star_unverified", True),
                              "verified": h.get("verified", False),
                              "price_is_estimate": h.get("price_is_estimate", False), "indoor": True})
        res["hotels"] = res.get("hotels", [])[:8]
        return res

    def select_hotel(hotel_id: str) -> dict:
        """Choose the hotel used as the daily base for a trip."""
        h = (ws.get("candidates") or {}).get(hotel_id)
        if not h or h.get("kind") != "hotel":
            return {"ok": False, "error": "unknown hotel id (call search_hotels first)"}
        min_stars = extract_min_star_rating(req.get("query", ""), req)
        if min_stars and h.get("star_rating") and h.get("star_rating") < min_stars:
            return {"ok": False, "error": f"Hotel '{h['name']}' has {h['star_rating']} stars, which violates requested minimum of {min_stars} stars"}
        ws.set_logistics("hotel", {"id": h["id"], "name": h["name"], "lat": h.get("lat"), "lon": h.get("lon"),
                                   "price_per_night": h.get("est_cost"), "url": h.get("url"), "source": h.get("source"),
                                   "star_rating": h.get("star_rating"), "star_unverified": h.get("star_unverified", True),
                                   "verified": h.get("verified", False), "price_is_estimate": h.get("price_is_estimate", False)})
        return {"ok": True, "hotel": h["name"]}

    def add_intercity_leg(kind: str, depart: str, arrive: str, title: str, cost: float = 0,
                          source: str = "estimate") -> dict:
        """Put an outbound/return journey into the plan as a fixed item. kind: flight | train | travel.
        depart/arrive are ISO local datetimes (YYYY-MM-DDTHH:MM)."""
        try:
            s, e = parse_dt(depart), parse_dt(arrive)
        except ValueError:
            return {"ok": False, "error": "depart/arrive must be ISO datetimes"}
        if e <= s:
            return {"ok": False, "error": "arrive must be after depart"}
        k = kind if kind in ("flight", "train") else "travel"
        items = list((ws.get("logistics") or {}).get("fixed_items") or [])
        items.append({"start": s.strftime("%Y-%m-%dT%H:%M"), "end": e.strftime("%Y-%m-%dT%H:%M"), "kind": k,
                      "title": title, "cost": {"amount": round(cost), "currency": currency, "estimate": source == "estimate"},
                      "notes": f"source: {source}"})
        ws.set_logistics("fixed_items", sorted(items, key=lambda x: x["start"]))
        return {"ok": True, "fixed_items": len(items)}

    def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
        """Convert money using today's ECB reference rates."""
        return tv.convert_currency(amount, from_currency, to_currency)

    def check_travel_and_budget() -> dict:
        """Review the draft plan: is there enough time to travel between consecutive stops, and is it within budget?"""
        draft = ws.get("draft")
        if not draft:
            return {"ok": False, "error": "no draft plan"}
        tm = travel_model(ws.state)
        issues = validator.check_logistics(draft, tm.leg, ws.get("budget"), ws.get("origin"))
        return {"ok": True, "issues": issues, "totals": draft.get("totals"), "budget": ws.get("budget"),
                "mode": tm.mode}

    if review:
        return [make_tool(f) for f in (check_travel_and_budget, travel_time_between, choose_travel_mode)]
    return [make_tool(f) for f in (choose_travel_mode, travel_time_between, prepare_travel_matrix, intercity_options,
                                   search_flights, search_hotels, select_hotel, add_intercity_leg, convert_currency)]


def run(state: dict, ctx: RunContext, instruction: str, review: bool = False) -> tuple[dict, str]:
    ws = Workspace(dict(state), ctx)
    tools = build_tools(ws, review)
    cands = [compact_candidate(c) for c in (state.get("candidates") or {}).values() if not c.get("unavailable")][:20]
    av = (state.get("availability") or {}).get("free_windows")
    prompt = (f"Request: {request_brief(state)}\nFree windows: {json.dumps(av) if av else 'unknown'}\n"
              f"Candidates: {json.dumps(cands) if cands else 'none yet'}\n"
              f"Current logistics: {json.dumps({k: v for k, v in (state.get('logistics') or {}).items() if k != 'matrix'}, default=str)[:800]}\n\n"
              f"Task from Coordinator{' (review round)' if review else ''}: {instruction}")
    report = run_react_agent(NAME, REVIEW if review else SYSTEM, prompt, tools, ctx, max_iterations=2 if review else 5)
    return ws.updates(), report


def fallback(state: dict, ctx: RunContext) -> tuple[dict, str]:
    """Deterministic behaviour if no LLM is reachable: default mode + travel matrix (+ cheapest hotel on trips)."""
    ws = Workspace(dict(state), ctx)
    tools = {t.name: t for t in build_tools(ws)}
    allowed = (state.get("request") or {}).get("travel_modes") or ["cab"]
    mode = next((m for m in ("cab", "transit", "drive", "walk") if m in allowed), "cab")
    tools["choose_travel_mode"].func(mode, "fallback default")
    req = state.get("request") or {}
    if state.get("mode") in ("domestic_trip", "international_trip", "business_trip"):
        w = state["window"]
        res = tools["search_hotels"].func(w["start"][:10], w["end"][:10])
        hotels = res.get("hotels", [])
        min_stars = extract_min_star_rating(req.get("query", ""), req)
        if min_stars and hotels:
            accepted, _ = filter_hotels_by_star_rating(hotels, min_stars)
            hotels = accepted
        hotels = sorted(hotels, key=lambda h: h.get("price_per_night") or 1e9)
        if hotels:
            tools["select_hotel"].func(hotels[0]["id"])
    shortlist_ids = state.get("shortlist_ids") or []
    if shortlist_ids:
        cands_to_matrix = [cid for cid in shortlist_ids if cid in (ws.get("candidates") or {})]
    else:
        cands = sorted((c for c in (ws.get("candidates") or {}).values() if c.get("kind") != "hotel"),
                       key=lambda c: c.get("distance_km") or 99)[:8]
        cands_to_matrix = [c["id"] for c in cands]
    if cands_to_matrix:
        tools["prepare_travel_matrix"].func(cands_to_matrix)
    return ws.updates(), f"[deterministic fallback, LLM unavailable] mode={mode}, matrix for {len(cands_to_matrix)} places"
