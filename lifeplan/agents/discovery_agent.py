"""📍 Discovery & Location Agent.

Finds REAL things to do — restaurants, cafés, attractions, parks, events, nightlife, shopping —
near the user's current/selected location, with distance, opening hours and source ids.
It also owns the weather picture and acts as the grounding guard: events must come from live
search results (URL required) and venues from map databases, so nothing in a plan is invented.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from lifeplan.agents.base import RunContext, Workspace, make_tool, run_react_agent
from lifeplan.agents.shared import compact_candidate, request_brief
from lifeplan.tools import opening_hours, validator
from lifeplan.tools import search as web
from lifeplan.tools import weather as wx
from lifeplan.tools.decision_engine import build_shortlist, filter_candidates
from lifeplan.tools.geo import geocode, haversine_km
from lifeplan.tools.places import CATEGORIES, normalise_interests, search_places
from lifeplan.tools.scheduler import parse_dt
from lifeplan.tools.travel import convert_currency

NAME = "discovery"

SYSTEM = """You are the Discovery & Location Agent in LifePlan AI, a multi-agent planner.
Find real, currently-relevant options that match the user's interests near the right location.
- ALWAYS use tools. Never invent venues, events, times or prices. Every venue comes from search_venues /
  find_alternatives; every event must be registered with register_event using a URL from find_events results.
- Fetch the weather for the planning dates; if rain is likely, favour indoor options and say so.
- Consider distance and opening hours; skip places closed during the user's free time.
- Respect dislikes / dietary needs from the request.
Finish with a short report (max 150 words) for the Coordinator: what you found (counts by type), the
3–6 best candidates with ids and why, events with their times, and any weather risk."""

REVIEW = """You are the Discovery & Location Agent reviewing a DRAFT plan.
Call check_venues. For each CLOSED / UNVERIFIED / WEATHER_RISK problem, call find_alternatives to propose a real
replacement that is open at that time (indoor if it rains). Reply: APPROVE or REVISE + concrete swaps
("replace X [id] with Y [id], open until 22:00"). Max 120 words."""


def _fx_from_inr(ws: Workspace) -> float:
    cur = ws.get("currency", "INR")
    if cur == "INR":
        return 1.0
    cache = (ws.get("logistics") or {}).get("fx_inr")
    if cache and cache.get("currency") == cur:
        return cache["rate"]
    res = convert_currency(1, "INR", cur)
    rate = res["rate"] if res.get("ok") else 1.0
    ws.set_logistics("fx_inr", {"currency": cur, "rate": rate})
    return rate


def _anchor(ws: Workspace, near: str) -> Optional[dict]:
    near = (near or "origin").strip()
    if near == "destination" and ws.get("destination"):
        return ws.get("destination")
    if near == "hotel" and (ws.get("logistics") or {}).get("hotel"):
        return ws.get("logistics")["hotel"]
    cands = ws.get("candidates") or {}
    if near in cands:
        return cands[near]
    if near not in ("origin", "", None):
        g = geocode(near, near=((ws.get("origin") or {}).get("lat"), (ws.get("origin") or {}).get("lon")))
        if g.get("ok"):
            return g
    trip = ws.get("mode") in ("domestic_trip", "international_trip", "business_trip")
    return ws.get("destination") if trip and ws.get("destination") else ws.get("origin")


def _register(ws: Workspace, places: list[dict], anchor: dict) -> list[dict]:
    rate = _fx_from_inr(ws)
    profile = ws.get("profile") or {}
    weights = profile.get("learned_weights") or {}
    dislikes = set(normalise_interests(profile.get("dislikes") or []))
    req = ws.get("request") or {}
    window = ws.get("window")

    candidates_to_eval = []
    for p in places:
        if p["category"] in dislikes:
            continue
        p = dict(p)
        p["est_cost"] = round(p.pop("est_cost_inr", 0) * rate * req.get("party_size", 1))
        p["distance_km"] = round(haversine_km(anchor["lat"], anchor["lon"], p["lat"], p["lon"]), 2)
        p["learned_weight"] = weights.get(p["category"])
        candidates_to_eval.append(p)

    valid_places, rejected = filter_candidates(candidates_to_eval, req, profile=profile, window=window)
    out = []
    for p in valid_places:
        ws.add_candidate(p)
        out.append(compact_candidate(p))

    all_cands = [c for c in (ws.get("candidates") or {}).values() if not c.get("unavailable") and c.get("kind") != "hotel"]
    if all_cands:
        weather = ws.get("weather")
        shortlist, reserve = build_shortlist(
            all_cands, req, profile=profile, weather=weather,
            shortlist_size=5, anchor_lat=anchor.get("lat"), anchor_lon=anchor.get("lon")
        )
        ws.set("shortlist_ids", [c["id"] for c in shortlist])
        ws.set("reserve_ids", [c["id"] for c in reserve])

    return out


def build_tools(ws: Workspace, review: bool = False) -> list:
    def search_venues(interests: list[str], radius_km: float = 5.0, near: str = "origin") -> dict:
        """Search real venues (OpenStreetMap/Geoapify) for the given interests, e.g. ["food","museum","park",
        "nightlife","cafe","shopping","movies","entertainment","attractions","culture"]. `near` = "origin",
        "destination", "hotel", a candidate id, or a place name. Results are added to the shared candidate pool."""
        anchor = _anchor(ws, near)
        if not anchor:
            return {"ok": False, "error": "no location to search around"}
        q = (ws.get("request") or {}).get("query", "")
        res = search_places(anchor["lat"], anchor["lon"], interests, radius_km=radius_km, limit_per_category=6, query=q)
        if not res["ok"]:
            return {"ok": False, "error": "no venues found", "warnings": res["warnings"]}
        found = _register(ws, res["places"], anchor)
        shortlist_ids = ws.get("shortlist_ids") or []
        return {"ok": True, "source": res["source"], "near": anchor.get("name"), "count": len(found),
                "shortlist_count": len(shortlist_ids), "shortlist_ids": shortlist_ids,
                "categories": res["categories"], "places": found[:24], "warnings": res["warnings"]}

    def find_events(when: str = "today", topic: str = "") -> dict:
        """Search live events (concerts, comedy, workshops, exhibitions, sports...) in the relevant city.
        `when`: today | tomorrow | weekend | week | next_week | month. Returns results with URLs. Use
        register_event to add a specific event (with its real start/end time) to the candidate pool."""
        place = _anchor(ws, "destination" if ws.get("destination") else "origin") or {}
        city = place.get("city") or (place.get("name") or "").split(",")[0]
        res = web.find_events(city, when, topic)
        urls = list((ws.get("event_sources") or [])) + [e.get("url") for e in res.get("events", []) if e.get("url")]
        ws.set("event_sources", urls[-80:])
        return res

    def register_event(title: str, venue: str, start: str, end: str, url: str, est_cost: float = 0,
                       indoor: bool = True) -> dict:
        """Add a real event to the candidate pool. start/end are ISO local datetimes (YYYY-MM-DDTHH:MM) exactly as
        stated by the source; url MUST be one of the URLs returned by find_events (grounding check)."""
        sources = ws.get("event_sources") or []
        if url not in sources:
            return {"ok": False, "error": "url was not returned by find_events — cannot verify this event is real"}
        try:
            s, e = parse_dt(start), parse_dt(end)
        except ValueError:
            return {"ok": False, "error": "start/end must be ISO datetimes like 2026-10-14T19:00"}
        if e <= s:
            return {"ok": False, "error": "end must be after start"}
        anchor = _anchor(ws, "destination" if ws.get("destination") else "origin") or {}
        city = anchor.get("city") or ""
        loc = geocode(f"{venue}, {city}" if city and city.lower() not in venue.lower() else venue,
                      near=(anchor.get("lat"), anchor.get("lon")))
        if not loc.get("ok") or haversine_km(anchor["lat"], anchor["lon"], loc["lat"], loc["lon"]) > 60:
            loc = {"lat": anchor.get("lat"), "lon": anchor.get("lon"), "name": venue, "approx": True}
        cid = "evt:" + hashlib.sha1(f"{title}|{start}|{url}".encode()).hexdigest()[:10]
        cand = {"id": cid, "name": title, "category": "event", "kind": "event", "lat": loc["lat"], "lon": loc["lon"],
                "address": venue if loc.get("approx") else loc.get("name"), "url": url, "source": "web",
                "source_id": url, "fixed_start": s.strftime("%Y-%m-%dT%H:%M"), "fixed_end": e.strftime("%Y-%m-%dT%H:%M"),
                "duration_min": int((e - s).total_seconds() // 60), "est_cost": round(est_cost), "indoor": indoor,
                "distance_km": round(haversine_km(anchor["lat"], anchor["lon"], loc["lat"], loc["lon"]), 2),
                "location_approximate": bool(loc.get("approx"))}
        ws.add_candidate(cand)
        return {"ok": True, "candidate": compact_candidate(cand), "location_approximate": bool(loc.get("approx"))}

    def search_web(query: str) -> dict:
        """General live web search (ticket availability, opening times, local tips). Returns titles, URLs, snippets."""
        return web.web_search(query, max_results=5)

    def get_weather(start_date: str = "", end_date: str = "") -> dict:
        """Weather forecast (Open-Meteo) for the plan location and dates (YYYY-MM-DD; default = planning window).
        Returns daily summary and the list of wet hours. Stored on the blackboard for the other agents."""
        w = ws.get("window") or {}
        s = parse_dt((start_date or w["start"][:10]) + "T00:00").date()
        e = parse_dt((end_date or w["end"][:10]) + "T00:00").date()
        place = _anchor(ws, "destination" if ws.get("destination") else "origin")
        res = wx.get_weather(place["lat"], place["lon"], s, e)
        if res.get("ok"):
            extra_wet = set((ws.get("weather") or {}).get("forced_wet_hours", []))
            res["wet_hours"] = sorted(set(res["wet_hours"]) | extra_wet)
            res["forced_wet_hours"] = sorted(extra_wet)
            ws.set("weather", res)
        return {"ok": res.get("ok", False), **wx.summarise(res)}

    def destination_brief(name: str) -> dict:
        """Short encyclopedia summary of a destination (for trips)."""
        return web.destination_brief(name)

    def find_alternatives(category: str, start: str, end: str, near: str = "origin", radius_km: float = 3.0,
                          indoor_only: bool = False) -> dict:
        """Find real venues of `category` near `near` (candidate id / origin / destination / hotel) that are OPEN
        for the whole [start, end] interval (ISO local). Use to replace closed venues or outdoor plans in rain."""
        anchor = _anchor(ws, near)
        if not anchor:
            return {"ok": False, "error": "unknown anchor"}
        q = (ws.get("request") or {}).get("query", "")
        res = search_places(anchor["lat"], anchor["lon"], [category], radius_km=radius_km, limit_per_category=10, query=q)
        s, e = parse_dt(start), parse_dt(end)
        good = []
        for p in res.get("places", []):
            if indoor_only and not p.get("indoor", True):
                continue
            open_ok = opening_hours.is_open_between(p.get("opening_hours"), s, e)
            if open_ok is None:  # no published hours: use the category's typical hours
                open_ok = opening_hours.is_open_between(p.get("default_hours"), s, e)
            if open_ok is False:
                continue
            good.append(p)
        found = _register(ws, good[:6], anchor)
        return {"ok": bool(found), "alternatives": found,
                "note": "hours unknown for some places" if any("hours" not in f for f in found) else None}

    def mark_unavailable(candidate_id: str, reason: str) -> dict:
        """Exclude a candidate from planning (e.g. permanently closed, sold out, cancelled, user dislikes it)."""
        cands = ws.get("candidates") or {}
        if candidate_id not in cands:
            return {"ok": False, "error": "unknown candidate id"}
        ws.add_candidate({**cands[candidate_id], "unavailable": reason})
        return {"ok": True}

    def check_venues() -> dict:
        """Review the draft plan: are all venues verified (no hallucination), open at the planned time, and
        not outdoors during forecast rain?"""
        draft = ws.get("draft")
        if not draft:
            return {"ok": False, "error": "no draft plan"}
        wet = set((ws.get("weather") or {}).get("wet_hours") or [])
        return {"ok": True, "issues": validator.check_venues(draft, ws.get("candidates") or {}, wet)}

    if review:
        return [make_tool(f) for f in (check_venues, find_alternatives, mark_unavailable)]
    return [make_tool(f) for f in (search_venues, find_events, register_event, search_web, get_weather,
                                   destination_brief, find_alternatives, mark_unavailable)]


def run(state: dict, ctx: RunContext, instruction: str, review: bool = False) -> tuple[dict, str]:
    ws = Workspace(dict(state), ctx)
    tools = build_tools(ws, review)
    pool = [compact_candidate(c) for c in (state.get("candidates") or {}).values()][:15]
    prompt = (f"Request: {request_brief(state)}\nCurrent time: {state.get('now')}\n"
              f"Known categories: {sorted(CATEGORIES)}\n"
              f"Candidates already found ({len(state.get('candidates') or {})}): {json.dumps(pool) if pool else 'none'}\n\n"
              f"Task from Coordinator{' (review round)' if review else ''}: {instruction}")
    report = run_react_agent(NAME, REVIEW if review else SYSTEM, prompt, tools, ctx, max_iterations=4 if review else 6)
    return ws.updates(), report


def fallback(state: dict, ctx: RunContext) -> tuple[dict, str]:
    """Deterministic behaviour if no LLM is reachable: weather + venues for the stated interests."""
    ws = Workspace(dict(state), ctx)
    tools = {t.name: t for t in build_tools(ws)}
    tools["get_weather"].func()
    interests = (state.get("request") or {}).get("interests") or (state.get("profile") or {}).get("interests") or []
    res = tools["search_venues"].func(list(interests) + ["food"])
    return ws.updates(), f"[deterministic fallback, LLM unavailable] found {res.get('count', 0)} venues"
