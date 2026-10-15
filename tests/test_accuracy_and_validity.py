"""Evaluation test suite for LifePlan AI accuracy, valid results, and constraint enforcement.

Covers all 11 critical scenarios:
1. Pune-to-Mumbai trip includes intercity travel and accommodation.
2. Strict return deadline enforced against return travel duration.
3. Minimum hotel star rating verification (rejection of < 3-star).
4. Strict budget enforcement (₹1,500 max budget rejects ₹4,500 plan as 'failed').
5. Conflicting dates (natural language query overrides date picker).
6. Unconfirmed calendar events do not block user free windows.
7. Missing hotel prices handled honestly (marked as estimate, not hallucinated).
8. Irrelevant venues (colleges, hospitals, societies) strictly filtered.
9. Travel feasibility and transit gap checks (TRAVEL_INFEASIBLE on tight gaps).
10. API failure and fallback resilience (deterministic fallback produces valid plan).
11. Infeasible request fails honestly with clear explanation and 0 fabricated stops.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
import pytest

from lifeplan.agents import logistics_agent, planner_agent
from lifeplan.agents.base import RunContext, Workspace
from lifeplan.agents.shared import TRIP_MODES, validate_state
from lifeplan.memory import ProfileStore
from lifeplan.schemas import PlanRequest
from lifeplan.service import build_result, prepare_state
from lifeplan.tools import calendar as cal
from lifeplan.tools import routing, travel as tv, validator
from lifeplan.tools.datetime_parser import resolve_canonical_schedule
from lifeplan.tools.decision_engine import (
    ensure_intercity_legs,
    extract_min_star_rating,
    filter_candidates,
    filter_hotels_by_star_rating,
    is_irrelevant_venue,
    validate_return_deadline,
)
from lifeplan.tools.scheduler import Window, build_schedule as solve, fmt, parse_dt
from lifeplan.trace import Trace


# =====================================================================
# 1. PUNE-TO-MUMBAI INTERCITY & ACCOMMODATION
# =====================================================================

def test_pune_to_mumbai_trip_intercity_and_hotel():
    """Verify that an intercity trip (Pune to Mumbai, 4 nights) automatically includes
    outbound and return travel legs and 4 hotel nights."""
    start_dt = datetime(2026, 11, 10, 9, 0)
    end_dt = datetime(2026, 11, 14, 20, 0)
    state = {
        "mode": "domestic_trip",
        "origin": {"name": "Pune", "city": "Pune", "lat": 18.5204, "lon": 73.8567},
        "destination": {"name": "Mumbai", "city": "Mumbai", "lat": 19.0760, "lon": 72.8777},
        "window": {"start": fmt(start_dt), "end": fmt(end_dt)},
        "currency": "INR",
        "budget": 25000.0,
        "logistics": {
            "mode": "cab",
            "hotel": {
                "id": "h_taj", "name": "Taj Lands End", "lat": 19.0434, "lon": 72.8193,
                "price_per_night": 3500.0, "star_rating": 5, "verified": True,
            },
        },
        "candidates": {
            "c_marine": {
                "id": "c_marine", "name": "Marine Drive Promenade", "category": "nature",
                "kind": "activity", "lat": 18.9432, "lon": 72.8230, "est_cost": 0, "duration_min": 90,
            },
            "c_gateway": {
                "id": "c_gateway", "name": "Gateway of India", "category": "attractions",
                "kind": "activity", "lat": 18.9220, "lon": 72.8347, "est_cost": 100, "duration_min": 60,
            },
        },
        "availability": {"free_windows": []},
        "request": {"query": "4-day leisure trip from Pune to Mumbai", "travel_modes": ["cab"]},
    }

    # Planner _trip_windows and build_schedule ensure outbound & return legs
    fixed = ensure_intercity_legs(state)
    assert len(fixed) >= 2, "Expected outbound and return intercity travel legs"

    outbound = next(f for f in fixed if "pune to mumbai" in f["title"].lower())
    return_leg = next(f for f in fixed if "mumbai to pune" in f["title"].lower())
    assert outbound["start"] == fmt(start_dt)
    assert parse_dt(return_leg["end"]) == end_dt

    # When build_schedule runs in planner
    ctx = RunContext(Trace("run_test_1"), profile_store=ProfileStore(), now=start_dt)
    ws = Workspace(dict(state), ctx)
    tools = {t.name: t for t in planner_agent.build_tools(ws)}
    res = tools["build_schedule"].func(["c_marine:0.9", "c_gateway:0.8"])
    assert res.get("ok") is True

    draft = ws.get("draft")
    assert draft is not None

    # Check for hotel check-in with 4 nights
    all_items = [it for d in draft["days"] for it in d["items"]]
    hotel_items = [it for it in all_items if it.get("kind") == "hotel"]
    assert len(hotel_items) >= 1
    assert "4 nights" in hotel_items[0]["title"]
    assert hotel_items[0]["cost"]["amount"] == 3500.0 * 4

    # Check validator confirms intercity legs
    val = validate_state(ws.state)
    high_issues = [i for i in val.get("issues", []) if i["severity"] == "high"]
    assert not any(i["type"] == "MISSING_INTERCITY_TRAVEL" for i in high_issues)


# =====================================================================
# 2. STRICT RETURN DEADLINE ENFORCEMENT
# =====================================================================

def test_strict_return_deadline_enforcement():
    """Verify that a return deadline (e.g. return to Pune by 9 PM) flags activities
    in Mumbai that finish too late to complete the ~3.5h drive."""
    window = {
        "start": "2026-10-20T08:00",
        "end": "2026-10-20T21:00",  # 9 PM return deadline
    }
    origin = {"name": "Pune", "city": "Pune"}
    dest = {"name": "Mumbai", "city": "Mumbai"}

    # An itinerary where an activity in Mumbai finishes at 19:30 (7:30 PM).
    # Since Mumbai to Pune takes 210 min (3.5 hours), departure must be by 17:30 (5:30 PM).
    infeasible_draft = {
        "days": [{
            "date": "2026-10-20",
            "items": [
                {"start": "2026-10-20T08:00", "end": "2026-10-20T11:30", "kind": "travel", "title": "Intercity Pune to Mumbai"},
                {"start": "2026-10-20T12:00", "end": "2026-10-20T14:00", "kind": "activity", "title": "Elephanta Caves"},
                {"start": "2026-10-20T18:00", "end": "2026-10-20T19:30", "kind": "activity", "title": "Late Dinner in Bandra"},
            ],
        }]
    }

    issues = validate_return_deadline(infeasible_draft, window, origin, dest, return_duration_min=210.0)
    assert len(issues) == 1
    assert issues[0]["type"] == "RETURN_DEADLINE_VIOLATED"
    assert issues[0]["severity"] == "high"
    assert "17:30" in issues[0]["message"] or "5:30" in issues[0]["message"]

    # A compliant itinerary where all Mumbai activities end before 17:30
    feasible_draft = {
        "days": [{
            "date": "2026-10-20",
            "items": [
                {"start": "2026-10-20T08:00", "end": "2026-10-20T11:30", "kind": "travel", "title": "Intercity Pune to Mumbai"},
                {"start": "2026-10-20T12:00", "end": "2026-10-20T14:00", "kind": "activity", "title": "Elephanta Caves"},
                {"start": "2026-10-20T15:00", "end": "2026-10-20T17:00", "kind": "activity", "title": "Gateway of India"},
                {"start": "2026-10-20T17:30", "end": "2026-10-20T21:00", "kind": "travel", "title": "Return journey to Pune"},
            ],
        }]
    }
    compliant_issues = validate_return_deadline(feasible_draft, window, origin, dest, return_duration_min=210.0)
    assert len(compliant_issues) == 0


# =====================================================================
# 3. HOTEL STAR RATING ENFORCEMENT
# =====================================================================

def test_hotel_star_rating_enforcement():
    """Verify that a request specifying a minimum star rating rejects sub-standard hotels."""
    query = "Weekend trip to Goa, minimum 3-star hotel with pool"
    req = {"query": query}
    min_stars = extract_min_star_rating(query, req)
    assert min_stars == 3

    hotels = [
        {"id": "h1", "name": "Budget Backpacker Hostel", "star_rating": 1, "verified": True},
        {"id": "h2", "name": "Basic Lodge", "star_rating": 2, "verified": True},
        {"id": "h3", "name": "Goa Beach Resort", "star_rating": 3, "verified": True},
        {"id": "h4", "name": "Taj Exotica", "star_rating": 5, "verified": True},
        {"id": "h5", "name": "Heritage Villa", "star_rating": None, "star_unverified": True},
    ]

    accepted, rejected = filter_hotels_by_star_rating(hotels, min_stars)
    filtered_ids = {h["id"] for h in accepted}
    assert "h1" not in filtered_ids, "1-star hotel must be rejected"
    assert "h2" not in filtered_ids, "2-star hotel must be rejected"
    assert "h3" in filtered_ids, "3-star hotel must be retained"
    assert "h4" in filtered_ids, "5-star hotel must be retained"
    assert "h5" in filtered_ids, "Unverified property retained with warning"

    # Verify LogisticsAgent select_hotel rejects 2-star hotel
    ctx = RunContext(Trace("run_test_stars"), profile_store=ProfileStore(), now=datetime(2026, 11, 1, 10, 0))
    ws = Workspace({
        "request": req, "origin": {"name": "Goa"},
        "candidates": {h["id"]: {**h, "kind": "hotel"} for h in hotels},
    }, ctx)
    tools = {t.name: t for t in logistics_agent.build_tools(ws)}
    res = tools["select_hotel"].func("h2")
    assert res.get("ok") is False
    assert "violates requested minimum of 3 stars" in res.get("error", "")

    # Selecting 3-star hotel succeeds
    res_ok = tools["select_hotel"].func("h3")
    assert res_ok.get("ok") is True


# =====================================================================
# 4. STRICT BUDGET ENFORCEMENT (NEVER ACCEPTABLE WHEN OVERBUDGET)
# =====================================================================

def test_strict_budget_enforcement_never_acceptable():
    """Verify that a ₹1,500 maximum budget never labels a ₹4,500 itinerary as acceptable."""
    state = {
        "budget": 1500.0,
        "currency": "INR",
        "draft": {
            "days": [{
                "date": "2026-10-20",
                "items": [
                    {"start": "2026-10-20T19:00", "end": "2026-10-20T21:00", "kind": "meal", "title": "Luxury Dining",
                     "cost": {"amount": 4500.0, "currency": "INR", "estimate": True}},
                ],
            }],
            "totals": {"cost": 4500.0, "budget": 1500.0},
        },
        "logistics": {"mode": "cab"},
        "candidates": {
            "c_lux": {"id": "c_lux", "name": "Luxury Dining", "kind": "meal", "est_cost": 4500.0, "indoor": True},
        },
    }

    val = validate_state(state)
    assert val["ok"] is False
    high_issues = [i for i in val["issues"] if i["severity"] == "high"]
    assert any(i["type"] == "OVER_BUDGET" for i in high_issues)

    # build_result must mark status = "failed"
    trace = Trace("run_test_budget")
    result = build_result(state, trace, "plan_overbudget")
    assert result.status == "failed"
    assert "exceeds" in result.summary.lower() or "infeasible" in result.summary.lower()
    assert result.options == []


# =====================================================================
# 5. CONFLICTING DATES (NATURAL LANGUAGE PRECEDENCE)
# =====================================================================

def test_conflicting_dates_natural_language_priority():
    """Verify that natural language date in query overrides conflicting form date pickers."""
    ref_now = datetime(2026, 10, 14, 10, 0, 0)
    query = "Tomorrow, I am free from 3 PM to 6 PM. Let's get coffee."
    picker_start = datetime(2026, 10, 15, 20, 15)
    picker_end = datetime(2026, 10, 15, 23, 15)

    canonical = resolve_canonical_schedule(
        query=query,
        now=ref_now,
        picker_start=picker_start,
        picker_end=picker_end,
    )

    assert canonical.locked is True
    assert "query" in canonical.source
    assert canonical.overridden_picker is True
    assert canonical.start.hour == 15
    assert canonical.end.hour == 18
    assert canonical.override_note is not None
    assert "priority over form picker" in canonical.override_note


# =====================================================================
# 6. UNCONFIRMED CALENDAR EVENTS DO NOT BLOCK FREE WINDOWS
# =====================================================================

def test_unconfirmed_calendar_events_do_not_block_free_windows():
    """Verify that TENTATIVE / unconfirmed events in .ics calendars do not erase user free windows."""
    ics_text = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:evt-tentative-1@lifeplan
DTSTART:20261020T100000Z
DTEND:20261020T140000Z
SUMMARY:Strategy Meeting (Tentative)
STATUS:TENTATIVE
END:VEVENT
BEGIN:VEVENT
UID:evt-confirmed-2@lifeplan
DTSTART:20261020T160000Z
DTEND:20261020T170000Z
SUMMARY:Dentist Appointment
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR"""

    start = datetime(2026, 10, 20, 9, 0)
    end = datetime(2026, 10, 20, 18, 0)
    events = cal.parse_ics(ics_text, "UTC", start, end)
    assert len(events) == 2

    tentative_evt = next(e for e in events if "Strategy Meeting" in e["title"])
    confirmed_evt = next(e for e in events if "Dentist Appointment" in e["title"])
    assert tentative_evt["confirmed"] is False
    assert confirmed_evt["confirmed"] is True

    # Availability calculation
    av = cal.availability(
        window_start=start,
        window_end=end,
        events=events,
        manual_busy=[],
        routine=None,
        min_minutes=45,
    )

    # Busy blocks must ONLY contain confirmed event
    busy_titles = [b["title"] for b in av["busy"]]
    assert "Dentist Appointment" in busy_titles
    assert "Strategy Meeting (Tentative)" not in busy_titles

    # Strategy Meeting must be captured in unconfirmed_events
    unconfirmed_titles = [u["title"] for u in av.get("unconfirmed_events", [])]
    assert "Strategy Meeting (Tentative)" in unconfirmed_titles

    # User is free between 09:00 and 16:00 (420 minutes) without tentative event blocking
    long_window = next((w for w in av["free_windows"] if w["minutes"] >= 300), None)
    assert long_window is not None, "Expected long free window unblocked by tentative event"


# =====================================================================
# 7. MISSING HOTEL PRICES HANDLED HONESTLY
# =====================================================================

def test_missing_hotel_prices_handled_honestly(monkeypatch):
    """Verify that unverified or estimated hotel prices are clearly marked with estimate flags."""
    mock_data = {
        "properties": [
            {
                "name": "Boutique Heritage Haven",
                "property_token": "token123",
                "gps_coordinates": {"latitude": 18.52, "longitude": 73.85},
                # No rate_per_night provided
            },
            {
                "name": "Grand Palace Hotel",
                "property_token": "token456",
                "hotel_class": 4,
                "rate_per_night": {"extracted_lowest": 4200},
                "gps_coordinates": {"latitude": 18.53, "longitude": 73.86},
            },
        ]
    }
    monkeypatch.setattr(tv, "request_json", lambda *args, **kwargs: mock_data)
    monkeypatch.setattr(tv.get_settings(), "serpapi_api_key", "mock_key")

    res = tv.search_hotels("Pune", "2026-10-20", "2026-10-22", adults=1, currency="INR")
    assert res.get("ok") is True
    hotels = res.get("hotels", [])
    assert len(hotels) == 2

    haven = next(h for h in hotels if h["name"] == "Boutique Heritage Haven")
    assert haven["price_is_estimate"] is True
    assert haven["verified"] is False
    assert haven["star_unverified"] is True

    grand = next(h for h in hotels if h["name"] == "Grand Palace Hotel")
    assert grand["price_is_estimate"] is False
    assert grand["verified"] is True
    assert grand["star_rating"] == 4
    assert grand["star_unverified"] is False


# =====================================================================
# 8. IRRELEVANT VENUES STRICTLY FILTERED
# =====================================================================

def test_irrelevant_venues_strictly_filtered():
    """Verify that colleges, universities, hospitals, and societies are removed from leisure candidates."""
    candidates = [
        {"id": "1", "name": "Symbiosis Institute of Technology Canteen", "category": "food", "est_cost": 200},
        {"id": "2", "name": "Ruby Hall Clinic Hospital", "category": "attractions", "est_cost": 0},
        {"id": "3", "name": "Green Acres Co-op Housing Society", "category": "attractions", "est_cost": 0},
        {"id": "4", "name": "Cafe Goodluck", "category": "cafe", "est_cost": 300},
        {"id": "5", "name": "Vaishali Restaurant", "category": "food", "est_cost": 500},
    ]
    req = {"query": "Find good places for dinner and coffee", "budget": {"amount": 2000}}

    valid, rejected = filter_candidates(candidates, req)
    valid_names = {c["name"] for c in valid}
    assert "Cafe Goodluck" in valid_names
    assert "Vaishali Restaurant" in valid_names
    assert "Symbiosis Institute of Technology Canteen" not in valid_names
    assert "Ruby Hall Clinic Hospital" not in valid_names
    assert "Green Acres Co-op Housing Society" not in valid_names

    rejected_reasons = {r["name"]: r["reason"] for r in rejected}
    assert "institute of technology" in rejected_reasons["Symbiosis Institute of Technology Canteen"].lower()


# =====================================================================
# 9. TRAVEL FEASIBILITY AND TRANSIT GAPS
# =====================================================================

def test_travel_feasibility_transit_gap():
    """Verify that unrealistic travel gaps between distant locations trigger TRAVEL_INFEASIBLE."""
    state = {
        "budget": 5000.0,
        "origin": {"name": "FC Road", "lat": 18.5284, "lon": 73.8415},
        "logistics": {"mode": "cab"},
        "candidates": {
            "c_fc": {"id": "c_fc", "name": "FC Road Cafe", "lat": 18.5284, "lon": 73.8415, "kind": "cafe"},
            "c_loni": {"id": "c_loni", "name": "MIT Loni Kalbhor", "lat": 18.4900, "lon": 74.0200, "kind": "activity"},  # ~25 km away
        },
        "draft": {
            "days": [{
                "date": "2026-10-20",
                "items": [
                    {"start": "2026-10-20T10:00", "end": "2026-10-20T11:00", "kind": "cafe", "title": "FC Road Cafe",
                     "place": {"lat": 18.5284, "lon": 73.8415}},
                    # Only a 5-minute gap planned for a 25 km trip that takes 40+ minutes by cab
                    {"start": "2026-10-20T11:05", "end": "2026-10-20T12:30", "kind": "activity", "title": "MIT Loni Kalbhor",
                     "place": {"lat": 18.4900, "lon": 74.0200}},
                ],
            }],
            "totals": {"cost": 500.0},
        },
    }

    val = validate_state(state)
    assert val["ok"] is False
    travel_issues = [i for i in val["issues"] if i["type"] == "TRAVEL_INFEASIBLE"]
    assert len(travel_issues) >= 1
    assert travel_issues[0]["severity"] == "high"


# =====================================================================
# 10. API FAILURES AND FALLBACK RESILIENCE
# =====================================================================

def test_api_fallback_resilience():
    """Verify that planner and logistics agents run deterministically when LLM is unavailable."""
    start_dt = datetime(2026, 10, 20, 10, 0)
    end_dt = datetime(2026, 10, 20, 18, 0)
    state = {
        "mode": "day",
        "origin": {"name": "Pune", "lat": 18.5204, "lon": 73.8567},
        "window": {"start": fmt(start_dt), "end": fmt(end_dt)},
        "currency": "INR",
        "budget": 3000.0,
        "request": {"query": "Day out in Pune", "interests": ["food", "cafe"], "travel_modes": ["cab"]},
        "candidates": {
            "c1": {"id": "c1", "name": "Cafe Goodluck", "category": "cafe", "kind": "meal",
                   "lat": 18.5186, "lon": 73.8415, "est_cost": 400, "indoor": True},
            "c2": {"id": "c2", "name": "Shaniwar Wada", "category": "attractions", "kind": "activity",
                   "lat": 18.5196, "lon": 73.8553, "est_cost": 50, "indoor": False},
        },
        "availability": {"free_windows": [{"start": fmt(start_dt), "end": fmt(end_dt), "minutes": 480}]},
    }

    ctx = RunContext(Trace("run_fallback_test"), profile_store=ProfileStore(), now=start_dt)

    # 1. Logistics fallback
    log_updates, log_rep = logistics_agent.fallback(state, ctx)
    assert "[deterministic fallback" in log_rep
    assert "logistics" in log_updates
    state.update(log_updates)

    # 2. Planner fallback
    plan_updates, plan_rep = planner_agent.fallback(state, ctx)
    assert "[deterministic fallback" in plan_rep
    assert "draft" in plan_updates
    state.update(plan_updates)

    # Verify a complete draft with scheduled items was constructed
    draft = state["draft"]
    assert len(draft["days"]) >= 1
    items = [it for it in draft["days"][0]["items"] if it.get("kind") not in ("travel", "busy")]
    assert len(items) >= 1


# =====================================================================
# 11. INFEASIBLE REQUESTS FAIL HONESTLY
# =====================================================================

def test_infeasible_request_fails_honestly():
    """Verify that an impossible request (e.g. ₹500 budget for 5-day intercity luxury trip)
    produces status: 'failed', 0 scheduled stops, and an honest explanation."""
    state = {
        "budget": 500.0,  # Impossible budget for Pune to Mumbai trip with hotel
        "currency": "INR",
        "mode": "domestic_trip",
        "origin": {"name": "Pune", "city": "Pune", "lat": 18.5204, "lon": 73.8567},
        "destination": {"name": "Mumbai", "city": "Mumbai", "lat": 19.0760, "lon": 72.8777},
        "window": {"start": "2026-11-10T09:00", "end": "2026-11-14T20:00"},
        "logistics": {
            "mode": "cab",
            "hotel": {"id": "h_taj", "name": "Taj Lands End", "price_per_night": 5000.0, "star_rating": 5},
            "fixed_items": [
                {"start": "2026-11-10T09:00", "end": "2026-11-10T12:30", "kind": "travel", "title": "Pune to Mumbai",
                 "cost": {"amount": 2000.0}},
                {"start": "2026-11-14T16:30", "end": "2026-11-14T20:00", "kind": "travel", "title": "Mumbai to Pune",
                 "cost": {"amount": 2000.0}},
            ],
        },
        "candidates": {},
        "draft": {
            "days": [{
                "date": "2026-11-10",
                "items": [
                    {"start": "2026-11-10T09:00", "end": "2026-11-10T12:30", "kind": "travel", "title": "Pune to Mumbai",
                     "cost": {"amount": 2000.0}},
                ],
            }],
            # Total cost ₹24,000 against ₹500 budget
            "totals": {"cost": 24000.0, "budget": 500.0},
        },
    }

    val = validate_state(state)
    assert val["ok"] is False

    trace = Trace("run_infeasible")
    res = build_result(state, trace, "plan_impossible")
    assert res.status == "failed"
    assert "infeasible" in res.summary.lower() or "exceeds" in res.summary.lower()
    # No fake stops fabricated
    scheduled_stops = [it for d in res.days for it in d.items if it.kind not in ("travel", "busy")]
    assert len(scheduled_stops) == 0
