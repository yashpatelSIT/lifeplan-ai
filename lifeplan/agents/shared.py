"""Helpers shared by several agents: travel model for the solver, compact blackboard views for
prompts (small prompts = fewer tokens = we stay inside free-tier limits), and plan validation."""

from __future__ import annotations

import json
from typing import Optional

from lifeplan.tools import routing, validator
from lifeplan.tools.geo import haversine_km
from lifeplan.tools.scheduler import parse_dt

WALK_KM = 1.2


class TravelModel:
    """Fast leg estimator used inside the solver and validators (no API calls in tight loops).

    Uses the Logistics agent's travel matrix (real road network data from OSRM/Geoapify) when
    both endpoints are in it, walks short hops if walking is allowed, otherwise falls back to a
    distance-based estimate for the chosen mode.
    """

    def __init__(self, logistics: dict, currency: str, party_size: int, allowed_modes: list[str]):
        self.matrix = (logistics or {}).get("matrix") or {}
        self.mode = routing.normalise_mode((logistics or {}).get("mode") or self._default_mode(allowed_modes))
        self.currency = currency
        self.party = party_size
        self.allowed = {routing.normalise_mode(m) for m in allowed_modes}
        self.index = {pid: i for i, pid in enumerate(self.matrix.get("ids", []))}

    @staticmethod
    def _default_mode(allowed: list[str]) -> str:
        for m in ("cab", "transit", "drive", "bike", "walk"):
            if m in allowed:
                return m
        return "cab"

    def leg(self, a: Optional[dict], b: Optional[dict]) -> dict:
        if not a or not b or a.get("lat") is None or b.get("lat") is None:
            return {"minutes": 0, "km": 0, "mode": "none", "source": "n/a", "cost": 0}
        direct = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
        if direct < 0.05:
            return {"minutes": 0, "km": 0, "mode": "walk", "source": "same place", "cost": 0}
        if "walk" in self.allowed and direct * routing.DETOUR <= WALK_KM:
            leg = routing.estimate_leg((a["lat"], a["lon"]), (b["lat"], b["lon"]), "walk")
        else:
            ia, ib = self.index.get(a.get("id")), self.index.get(b.get("id"))
            if ia is not None and ib is not None and self.matrix.get("minutes"):
                minutes = self.matrix["minutes"][ia][ib]
                km_rows = self.matrix.get("km")
                km = km_rows[ia][ib] if km_rows else direct * routing.DETOUR
                leg = {"minutes": minutes, "km": km, "mode": self.matrix.get("mode", self.mode),
                       "source": self.matrix.get("source", "matrix")}
            else:
                leg = routing.estimate_leg((a["lat"], a["lon"]), (b["lat"], b["lon"]), self.mode)
        cost = routing.leg_cost(leg["mode"], leg.get("km") or 0, leg["minutes"], self.currency, self.party)
        leg["cost"] = cost["amount"]
        return leg


def travel_model(state: dict) -> TravelModel:
    req = state.get("request") or {}
    return TravelModel(state.get("logistics") or {}, state.get("currency", "INR"), req.get("party_size", 1),
                       req.get("travel_modes") or ["walk", "transit", "cab"])


def compact_candidate(c: dict) -> dict:
    out = {"id": c["id"], "name": c.get("name"), "cat": c.get("category"), "kind": c.get("kind"),
           "km": c.get("distance_km"), "hours": c.get("opening_hours"), "cost": c.get("est_cost"),
           "indoor": c.get("indoor")}
    if c.get("fixed_start"):
        out["time"] = f"{c['fixed_start'][11:16]}-{(c.get('fixed_end') or '')[11:16]}"
        out["date"] = c["fixed_start"][:10]
    if c.get("unavailable"):
        out["UNAVAILABLE"] = c["unavailable"]
    return {k: v for k, v in out.items() if v not in (None, "")}


def compact_plan(plan: Optional[dict]) -> Optional[list]:
    if not plan:
        return None
    rows = []
    for d in plan.get("days", []):
        for i, it in enumerate(d.get("items", [])):
            if it["kind"] == "travel":
                tr = it.get("travel") or {}
                rows.append(f"{d['date']} #{i} {it['start'][11:]}-{it['end'][11:]} travel {tr.get('mode')} "
                            f"{tr.get('minutes')}min")
            else:
                rows.append(f"{d['date']} #{i} {it['start'][11:]}-{it['end'][11:]} {it['kind']} {it['title']} "
                            f"[{it.get('candidate_id')}] cost {(it.get('cost') or {}).get('amount')}")
    return rows


def request_brief(state: dict) -> str:
    req = state.get("request") or {}
    origin = state.get("origin") or {}
    dest = state.get("destination") or {}
    brief = {
        "query": req.get("query"), "mode": state.get("mode"), "now": state.get("now"),
        "window": state.get("window"), "timezone": state.get("timezone"),
        "origin": {"name": origin.get("name"), "lat": origin.get("lat"), "lon": origin.get("lon")},
        "destination": {"name": dest.get("name"), "country": dest.get("country_code")} if dest else None,
        "budget": state.get("budget"), "currency": state.get("currency"),
        "interests": req.get("interests"), "travel_modes": req.get("travel_modes"),
        "party_size": req.get("party_size"), "pace": req.get("pace"),
    }
    return json.dumps({k: v for k, v in brief.items() if v not in (None, [], {})}, default=str)


def board_summary(state: dict) -> dict:
    """What the Coordinator sees about the blackboard each turn."""
    av = state.get("availability") or {}
    cands = state.get("candidates") or {}
    by_cat: dict[str, int] = {}
    for c in cands.values():
        if not c.get("unavailable"):
            by_cat[c.get("category", "?")] = by_cat.get(c.get("category", "?"), 0) + 1
    log = state.get("logistics") or {}
    val = state.get("validation") or {}
    weather = state.get("weather") or {}
    draft = state.get("draft") or {}
    return {
        "availability": {"free_windows": av.get("free_windows"), "busy": [f"{b['start']}-{b['end'][11:]} {b['title']}"
                                                                           for b in av.get("busy", [])][:8],
                         "predicted_busy": len(av.get("predicted_busy", []))} if av else "not computed",
        "weather": {"kind": weather.get("kind"), "wet_hours": len(weather.get("wet_hours", []))}
        if weather else "not fetched",
        "candidates": by_cat or "none yet",
        "logistics": {"mode": log.get("mode"), "matrix": bool(log.get("matrix")), "hotel": (log.get("hotel") or {}).get("name"),
                      "fixed_items": len(log.get("fixed_items") or []), "notes": log.get("notes")} if log else "none",
        "draft_plan": compact_plan(draft) if draft else "none",
        "draft_totals": draft.get("totals") if draft else None,
        "validation": {"ok": val.get("ok"), "high": val.get("high"), "medium": val.get("medium"),
                       "issues": [f"[{i['severity']}/{i['agent']}] {i['type']}: {i['message']}"
                                  for i in val.get("issues", []) if i["severity"] != "low"][:8]} if val else "not run",
        "revisions": state.get("revisions", 0),
    }


TRIP_MODES = ("domestic_trip", "international_trip", "business_trip")


def day_bases(state: dict) -> dict:
    """Where the user starts each day: the hotel (or destination centre) on trips, else the origin."""
    if state.get("mode") not in TRIP_MODES:
        return {}
    base = (state.get("logistics") or {}).get("hotel") or state.get("destination")
    draft = state.get("draft") or {}
    return {d["date"]: base for d in draft.get("days", [])}


def validate_state(state: dict) -> dict:
    """Run all domain checks on the current draft (used after every Planner draft)."""
    draft = state.get("draft")
    if not draft:
        return {"ok": False, "high": 1, "medium": 0, "low": 0,
                "issues": [{"type": "NO_PLAN", "severity": "high", "agent": "planner", "message": "no draft yet"}]}
    av = state.get("availability") or {}
    tm = travel_model(state)
    wet = set((state.get("weather") or {}).get("wet_hours") or [])
    issues = []
    issues += validator.check_calendar(draft, av.get("busy", []), av.get("predicted_busy", []), state.get("window"))
    issues += validator.check_logistics(draft, tm.leg, state.get("budget"), state.get("origin"), day_bases(state))
    issues += validator.check_venues(draft, state.get("candidates") or {}, wet)
    return validator.summarise(issues)


def within_window(state: dict, iso: str) -> bool:
    w = state.get("window") or {}
    if not w:
        return True
    t = parse_dt(iso)
    return parse_dt(w["start"]) <= t <= parse_dt(w["end"])
