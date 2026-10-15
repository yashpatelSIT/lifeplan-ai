"""LifePlan AI Overhaul Regression Test Suite.

Automated regression tests covering all 20 acceptance and regression criteria
from the comprehensive bug fix, accuracy, performance, and usability overhaul:

1.  test_01_empty_calendar_and_no_commitments
2.  test_02_one_manually_entered_commitment
3.  test_03_editing_and_removing_commitments
4.  test_04_pune_to_mumbai_multi_day_vacation
5.  test_05_mandatory_return_deadline
6.  test_06_three_star_minimum_hotel
7.  test_07_strict_1500_budget
8.  test_08_vacation_budget_10000
9.  test_09_missing_accommodation_prices_labelled_estimates
10. test_10_irrelevant_search_results_filtered
11. test_11_invalid_and_swapped_coordinates_handled
12. test_12_implausible_route_durations_rejected_or_labelled
13. test_13_duplicate_return_journeys_prevented
14. test_14_out_of_order_activities_prevented_chronological_order
15. test_15_venue_closing_time_conflicts_prevented
16. test_16_conflicting_dates_query_precedence
17. test_17_api_failure_and_fallback_resilience
18. test_18_budget_calculation_with_all_ledger_categories
19. test_19_infeasible_plan_fails_honestly
20. test_20_invalid_draft_must_not_be_marked_complete
"""

from __future__ import annotations

from datetime import datetime, timedelta
import pytest

from lifeplan import service
from lifeplan.agents import profile_agent
from lifeplan.agents.shared import validate_state
from lifeplan.schemas import BusyBlock, CostLedger, PlanRequest
from lifeplan.tools import routing, validator
from lifeplan.tools.datetime_parser import resolve_canonical_schedule
from lifeplan.tools.decision_engine import (
    compute_trip_ledger,
    ensure_intercity_legs,
    extract_min_star_rating,
    filter_candidates,
    filter_hotels_by_star_rating,
    is_irrelevant_venue,
    validate_return_deadline,
)
from lifeplan.tools.geo import haversine_km
from lifeplan.tools.http import ExternalAPIError
from lifeplan.tools.scheduler import Window, build_schedule as solve, fmt, parse_dt
from lifeplan.trace import Trace


# =====================================================================
# 1. EMPTY CALENDAR & NO COMMITMENTS (PRIORITY 1)
# =====================================================================

def test_01_empty_calendar_and_no_commitments():
    """Verify that a new plan with no entered commitments treats the calendar as 100% open."""
    req = {
        "commitments": [],
        "commitments_mode": "plan_around",
        "calendar_ics": None,
    }
    window = {"start": "2026-10-20T10:00", "end": "2026-10-20T22:00"}
    avail = profile_agent.compute_availability(req, profile={"work_hours": {}}, window=window)

    assert avail["busy"] == [], "Empty commitments must yield zero busy blocks"
    assert avail["predicted_busy"] == [], "No phantom routines should block user"
    assert len(avail["free_windows"]) == 1
    assert avail["free_windows"][0]["start"] == window["start"]
    assert avail["free_windows"][0]["end"] == window["end"]


# =====================================================================
# 2. ONE MANUALLY ENTERED COMMITMENT (PRIORITY 1)
# =====================================================================

def test_02_one_manually_entered_commitment():
    """Verify that a single manually entered commitment constrains only its specific date and time."""
    commitment = {
        "title": "Doctor Consultation",
        "start": "2026-10-20T14:00",
        "end": "2026-10-20T15:30",
        "date": "2026-10-20",
        "is_hard_constraint": True,
    }
    req = {
        "commitments": [commitment],
        "commitments_mode": "plan_around",
        "calendar_ics": None,
    }
    window = {"start": "2026-10-20T10:00", "end": "2026-10-20T20:00"}
    avail = profile_agent.compute_availability(req, profile={"work_hours": {}}, window=window)

    assert len(avail["busy"]) == 1
    assert avail["busy"][0]["title"] == "Doctor Consultation"
    assert avail["busy"][0]["start"] == "2026-10-20T14:00"
    assert avail["busy"][0]["end"] == "2026-10-20T15:30"

    # Free windows should split before 14:00 and after 15:30
    assert len(avail["free_windows"]) == 2
    assert avail["free_windows"][0]["end"] == "2026-10-20T14:00"
    assert avail["free_windows"][1]["start"] == "2026-10-20T15:30"


# =====================================================================
# 3. EDITING AND REMOVING COMMITMENTS (PRIORITY 1)
# =====================================================================

def test_03_editing_and_removing_commitments():
    """Verify that editing or removing commitments correctly updates constraints."""
    # User had 2 commitments, then deleted C2 and edited C1 time
    c1_edited = {
        "title": "Project Sync (Rescheduled)",
        "start": "2026-10-20T11:00",
        "end": "2026-10-20T12:00",
        "date": "2026-10-20",
        "is_hard_constraint": True,
    }
    req = {
        "commitments": [c1_edited],  # C2 removed, C1 time changed
        "commitments_mode": "plan_around",
        "calendar_ics": None,
    }
    window = {"start": "2026-10-20T09:00", "end": "2026-10-20T18:00"}
    avail = profile_agent.compute_availability(req, profile={"work_hours": {}}, window=window)

    assert len(avail["busy"]) == 1
    assert avail["busy"][0]["title"] == "Project Sync (Rescheduled)"
    assert avail["busy"][0]["start"] == "2026-10-20T11:00"
    # Afternoon window (previously blocked by C2) is now completely open
    afternoon_window = [w for w in avail["free_windows"] if w["start"] >= "2026-10-20T12:00"]
    assert len(afternoon_window) == 1
    assert afternoon_window[0]["end"] == "2026-10-20T18:00"


# =====================================================================
# 4. PUNE-TO-MUMBAI MULTI-DAY VACATION (PRIORITIES 2 & 7)
# =====================================================================

def test_04_pune_to_mumbai_multi_day_vacation():
    """Verify that a 3-day Pune-to-Mumbai trip generates outbound and return intercity legs."""
    start_dt = datetime(2026, 11, 10, 8, 0)
    end_dt = datetime(2026, 11, 12, 20, 0)
    state = {
        "mode": "domestic_trip",
        "origin": {"name": "Pune", "city": "Pune", "lat": 18.5204, "lon": 73.8567},
        "destination": {"name": "Mumbai", "city": "Mumbai", "lat": 19.0760, "lon": 72.8777},
        "window": {"start": fmt(start_dt), "end": fmt(end_dt)},
        "currency": "INR",
        "budget": 20000.0,
        "logistics": {"mode": "cab"},
    }

    fixed = ensure_intercity_legs(state)
    assert len(fixed) >= 2, "Must contain outbound and return intercity legs"

    outbound = next(f for f in fixed if "pune to mumbai" in f["title"].lower())
    return_leg = next(f for f in fixed if "mumbai to pune" in f["title"].lower())

    assert outbound["start"] == fmt(start_dt)
    assert parse_dt(return_leg["end"]) == end_dt
    assert outbound["travel"]["minutes"] > 120, "Intercity journey duration should reflect realistic driving time"


# =====================================================================
# 5. MANDATORY RETURN DEADLINE (PRIORITIES 2 & 7)
# =====================================================================

def test_05_mandatory_return_deadline():
    """Verify that activities ending too late to meet the return deadline are rejected."""
    origin = {"name": "Pune", "city": "Pune", "lat": 18.5204, "lon": 73.8567}
    dest = {"name": "Mumbai", "city": "Mumbai", "lat": 19.0760, "lon": 72.8777}
    window = {"start": "2026-11-12T08:00:00", "end": "2026-11-12T20:00:00"}  # Must return by 8:00 PM

    # Infeasible: activity in Mumbai finishes at 18:30 (only 90 min before Pune deadline, needs 210 min)
    infeasible_plan = {
        "days": [{
            "date": "2026-11-12",
            "items": [
                {"title": "Late Sightseeing", "kind": "activity", "start": "2026-11-12T17:00:00", "end": "2026-11-12T18:30:00"},
            ],
        }],
    }
    issues = validate_return_deadline(infeasible_plan, window, origin, dest, return_duration_min=210.0)
    assert any(i["type"] == "RETURN_DEADLINE_VIOLATED" for i in issues)

    # Feasible: activity finishes at 16:00 (allows 240 min before deadline)
    feasible_plan = {
        "days": [{
            "date": "2026-11-12",
            "items": [
                {"title": "Early Sightseeing", "kind": "activity", "start": "2026-11-12T14:00:00", "end": "2026-11-12T16:00:00"},
            ],
        }],
    }
    ok_issues = validate_return_deadline(feasible_plan, window, origin, dest, return_duration_min=210.0)
    assert not any(i["type"] == "RETURN_DEADLINE_VIOLATED" for i in ok_issues)


# =====================================================================
# 6. THREE-STAR MINIMUM HOTEL (PRIORITY 4 & 7)
# =====================================================================

def test_06_three_star_minimum_hotel():
    """Verify that hotel candidates below the requested star rating are strictly filtered."""
    min_stars = extract_min_star_rating("Weekend in Mumbai with at least a 3-star hotel")
    assert min_stars == 3

    hotels = [
        {"id": "h1", "name": "Budget 1-Star Lodge", "star_rating": 1, "price_per_night": 900},
        {"id": "h2", "name": "Decent 2-Star Inn", "star_rating": 2, "price_per_night": 1800},
        {"id": "h3", "name": "Comfort 3-Star Residency", "star_rating": 3, "price_per_night": 3200},
        {"id": "h4", "name": "Grand 5-Star Hotel", "star_rating": 5, "price_per_night": 7500},
    ]

    accepted, rejected = filter_hotels_by_star_rating(hotels, min_stars=3)
    accepted_ids = {h["id"] for h in accepted}
    assert accepted_ids == {"h3", "h4"}
    assert len(rejected) == 2

    # Check validator raises HOTEL_STAR_VIOLATION if 2-star hotel is present
    state = {
        "request": {"query": "3-star hotel needed"},
        "logistics": {"hotel": hotels[1]},  # 2-star
    }
    issues = validator.check_hotel_requirements({"days": []}, state)
    assert any(i["type"] == "HOTEL_STAR_VIOLATION" for i in issues)


# =====================================================================
# 7. STRICT ₹1,500 BUDGET (PRIORITIES 4 & 7)
# =====================================================================

def test_07_strict_1500_budget():
    """Verify that an itinerary costing ₹4,500 with a ₹1,500 budget is strictly rejected as failed."""
    plan = {
        "days": [{
            "date": "2026-10-20",
            "items": [
                {"title": "Luxury Dining", "kind": "meal", "start": "2026-10-20T19:00:00", "end": "2026-10-20T21:00:00",
                 "cost": {"amount": 4500.0, "currency": "INR"}},
            ],
        }],
    }
    issues = validator.check_logistics(plan, lambda a, b: {"minutes": 10, "mode": "cab"}, budget=1500.0)
    over_budget_issues = [i for i in issues if i["type"] == "OVER_BUDGET"]
    assert len(over_budget_issues) == 1
    assert over_budget_issues[0]["severity"] == "high"

    # Status must become 'failed'
    state = {
        "draft": plan,
        "budget": 1500.0,
        "currency": "INR",
        "validation": {"issues": over_budget_issues},
    }
    res = service.build_result(state, Trace("test_budget"), "plan_budget_fail")
    assert res.status == "failed"


# =====================================================================
# 8. VACATION BUDGET ₹10,000 (PRIORITIES 4 & 5)
# =====================================================================

def test_08_vacation_budget_10000():
    """Verify that a ₹10,000 budget transparently accounts for all categories and 5% contingency."""
    plan = {
        "days": [
            {
                "date": "2026-11-10",
                "items": [
                    {"title": "Pune to Mumbai Cab", "kind": "flight", "start": "2026-11-10T08:00", "end": "2026-11-10T11:30", "cost": {"amount": 2000.0}},
                    {"title": "Hotel Check-in", "kind": "hotel", "start": "2026-11-10T12:00", "end": "2026-11-10T12:30", "cost": {"amount": 3500.0}},
                    {"title": "Dinner at Cafe", "kind": "meal", "start": "2026-11-10T19:00", "end": "2026-11-10T20:30", "cost": {"amount": 1000.0}},
                ],
            },
            {
                "date": "2026-11-11",
                "items": [
                    {"title": "Heritage Museum", "kind": "activity", "start": "2026-11-11T10:00", "end": "2026-11-11T12:00", "cost": {"amount": 500.0}},
                    {"title": "Local Taxi", "kind": "travel", "start": "2026-11-11T12:00", "end": "2026-11-11T12:30", "cost": {"amount": 300.0}},
                ],
            },
        ],
    }
    state = {"budget": 10000.0, "currency": "INR"}
    ledger = compute_trip_ledger(plan, state)

    # Subtotal: 2000 + 3500 + 1000 + 500 + 300 = 7300
    # Contingency (5%): round(7300 * 0.05) = 365
    # Total: 7665
    assert ledger["accommodation"] == 3500
    assert ledger["intercity_transport"] == 2000
    assert ledger["food_and_dining"] == 1000
    assert ledger["activities_and_entries"] == 500
    assert ledger["local_transport"] == 300
    assert ledger["contingency"] == 365
    assert ledger["total_cost"] == 7665
    assert ledger["remaining_budget"] == 2335
    assert ledger["is_within_budget"] is True


# =====================================================================
# 9. MISSING ACCOMMODATION PRICES (PRIORITY 5 & 8)
# =====================================================================

def test_09_missing_accommodation_prices_labelled_estimates():
    """Verify that unverified or missing hotel prices are labelled as estimates, not hallucinated facts."""
    plan = {
        "days": [{
            "date": "2026-11-10",
            "items": [
                {
                    "title": "Boutique Hotel",
                    "kind": "hotel",
                    "start": "2026-11-10T14:00:00",
                    "end": "2026-11-10T14:30:00",
                    "cost": {"amount": 2500.0, "currency": "INR", "estimate": True},
                },
            ],
        }],
    }
    ledger = compute_trip_ledger(plan, {"budget": 5000.0, "currency": "INR"})
    assert ledger["estimated_items_count"] == 1
    assert ledger["verified_items_count"] == 0
    assert any("Accommodation" in note for note in ledger["breakdown_notes"])


# =====================================================================
# 10. IRRELEVANT SEARCH RESULTS (PRIORITY 8)
# =====================================================================

def test_10_irrelevant_search_results_filtered():
    """Verify that colleges, hospitals, and residential societies are purged from recommendations."""
    raw_candidates = [
        {"id": "c1", "name": "Symbiosis Institute of Technology", "category": "education", "est_cost": 0},
        {"id": "c2", "name": "Ruby Hall Clinic & Hospital", "category": "health", "est_cost": 500},
        {"id": "c3", "name": "Shree Ganesh Co-op Housing Society", "category": "residential", "est_cost": 0},
        {"id": "c4", "name": "Aga Khan Palace", "category": "attractions", "est_cost": 150},
        {"id": "c5", "name": "German Bakery", "category": "food", "est_cost": 600},
    ]

    # Verify is_irrelevant_venue detection
    assert is_irrelevant_venue(raw_candidates[0])[0] is True
    assert is_irrelevant_venue(raw_candidates[1])[0] is True
    assert is_irrelevant_venue(raw_candidates[2])[0] is True
    assert is_irrelevant_venue(raw_candidates[3])[0] is False
    assert is_irrelevant_venue(raw_candidates[4])[0] is False

    valid, rejected = filter_candidates(raw_candidates, {"query": "Sightseeing and dinner in Pune"})
    valid_ids = {c["id"] for c in valid}
    assert valid_ids == {"c4", "c5"}
    assert len(rejected) == 3


# =====================================================================
# 11. INVALID AND SWAPPED COORDINATES (PRIORITY 2)
# =====================================================================

def test_11_invalid_and_swapped_coordinates_handled():
    """Verify that swapped lat/lon order is corrected and out-of-bounds coordinates raise errors."""
    # Pune coordinates inverted: lat 73.8567, lon 18.5204
    lat, lon = routing.validate_coordinates((73.8567, 18.5204))
    assert lat == pytest.approx(18.5204, abs=0.001)
    assert lon == pytest.approx(73.8567, abs=0.001)

    # Valid coordinates stay unchanged
    lat2, lon2 = routing.validate_coordinates((18.5204, 73.8567))
    assert (lat2, lon2) == (18.5204, 73.8567)

    # Completely out of bounds coordinates
    with pytest.raises(ValueError, match="Invalid geographical coordinates"):
        routing.validate_coordinates((120.0, 200.0))


# =====================================================================
# 12. IMPLAUSIBLE ROUTE DURATIONS (PRIORITY 2)
# =====================================================================

def test_12_implausible_route_durations_rejected_or_labelled():
    """Verify that unrealistic durations (e.g. 1,204 minutes for 5 km) are rejected as implausible."""
    a = (18.5204, 73.8567)
    b = (18.5300, 73.8600)  # ~1.2 km away

    # Implausible: 1,204 minutes for 5 km (~0.25 km/h driving speed)
    plausible, reason = routing.is_plausible_route(a, b, km=5.0, minutes=1204.0, mode="drive")
    assert plausible is False

    # Implausible: 200 km/h driving speed in city
    plausible_speed, _ = routing.is_plausible_route(a, b, km=20.0, minutes=4.0, mode="drive")
    assert plausible_speed is False

    # Plausible: 5 km in 18 minutes by cab
    plausible_ok, _ = routing.is_plausible_route(a, b, km=5.0, minutes=18.0, mode="cab")
    assert plausible_ok is True


# =====================================================================
# 13. DUPLICATE RETURN JOURNEYS PREVENTED (PRIORITIES 2 & 7)
# =====================================================================

def test_13_duplicate_return_journeys_prevented():
    """Verify that multi-day itineraries do not add duplicate return legs back to origin on intermediate days."""
    pune = {"name": "Pune", "lat": 18.5204, "lon": 73.8567}
    mumbai_hotel = {"name": "Mumbai Hotel", "lat": 19.0760, "lon": 72.8777}
    mumbai_spot = {"id": "c_spot", "name": "Marine Drive", "lat": 18.9432, "lon": 72.8230, "duration_min": 60, "est_cost": 0, "kind": "activity"}

    # Solving Day 1 in Mumbai with base = hotel, but end_at set to Pune (> 50 km away)
    win = Window(datetime(2026, 11, 10, 14, 0), datetime(2026, 11, 10, 20, 0), mumbai_hotel, end_at=pune)
    res = solve([win], [mumbai_spot], lambda a, b: {"minutes": 20, "mode": "cab", "km": 5.0})

    # intermediate day should NOT have a return leg back to Pune (> 50 km)
    items = [it for d in res.days for it in d.get("items", [])]
    return_legs = [it for it in items if it.get("kind") == "travel" and "pune" in it.get("title", "").lower()]
    assert len(return_legs) == 0, "Intermediate day in Mumbai should not route back to Pune"


# =====================================================================
# 14. OUT-OF-ORDER ACTIVITIES (PRIORITY 6)
# =====================================================================

def test_14_out_of_order_activities_prevented_chronological_order():
    """Verify that scheduled activities are chronologically valid and never overlap out of order."""
    plan = {
        "days": [{
            "date": "2026-10-20",
            "items": [
                {"title": "Activity A", "kind": "activity", "start": "2026-10-20T14:00:00", "end": "2026-10-20T16:00:00"},
                {"title": "Activity B (Overlapping)", "kind": "activity", "start": "2026-10-20T15:00:00", "end": "2026-10-20T17:00:00"},
            ],
        }],
    }
    issues = validator.check_logistics(plan, lambda a, b: {"minutes": 10, "mode": "cab"}, budget=None)
    assert any(i["type"] == "OVERLAP" and i["severity"] == "high" for i in issues)

    # In service.build_result, items are sorted strictly by start time
    state = {
        "draft": {
            "days": [{
                "date": "2026-10-20",
                "items": [
                    {"title": "Evening Coffee", "kind": "snack", "start": "2026-10-20T17:00:00", "end": "2026-10-20T17:30:00"},
                    {"title": "Morning Walk", "kind": "activity", "start": "2026-10-20T08:00:00", "end": "2026-10-20T09:00:00"},
                ],
            }],
        },
        "validation": {"issues": []},
        "status": "ok",
    }
    res = service.build_result(state, Trace("test_sort"), "p_sort")
    day_starts = [it.start for it in res.days[0].items]
    assert day_starts == sorted(day_starts), "All timeline stops must be strictly chronological"


# =====================================================================
# 15. VENUE CLOSING-TIME CONFLICTS (PRIORITIES 4 & 7)
# =====================================================================

def test_15_venue_closing_time_conflicts_prevented():
    """Verify that venues are never scheduled when closed."""
    museum = {
        "id": "c_museum",
        "name": "Kelkar Museum",
        "opening_hours": "Tu-Su 10:00-17:00",
        "kind": "activity",
    }
    # Scheduled after closing time (18:00 to 19:30)
    plan_closed = {
        "days": [{
            "date": "2026-10-20",  # a Tuesday
            "items": [{
                "candidate_id": "c_museum",
                "title": "Kelkar Museum",
                "kind": "activity",
                "start": "2026-10-20T18:00:00",
                "end": "2026-10-20T19:30:00",
            }],
        }],
    }
    issues = validator.check_venues(plan_closed, {"c_museum": museum}, wet_hours=set())
    assert any(i["type"] == "CLOSED" and i["severity"] == "high" for i in issues)

    # Scheduled during valid hours (11:00 to 12:30)
    plan_open = {
        "days": [{
            "date": "2026-10-20",
            "items": [{
                "candidate_id": "c_museum",
                "title": "Kelkar Museum",
                "kind": "activity",
                "start": "2026-10-20T11:00:00",
                "end": "2026-10-20T12:30:00",
            }],
        }],
    }
    open_issues = validator.check_venues(plan_open, {"c_museum": museum}, wet_hours=set())
    assert not any(i["type"] == "CLOSED" for i in open_issues)


# =====================================================================
# 16. CONFLICTING DATES QUERY PRECEDENCE (PRIORITY 3)
# =====================================================================

def test_16_conflicting_dates_query_precedence():
    """Verify that dates explicitly stated in natural language take precedence over form pickers."""
    req = PlanRequest(
        query="Trip from Pune to Mumbai from 2026-11-20 to 2026-11-23",
        start=datetime(2026, 10, 15, 18, 0),  # Conflicting form picker start
        location="Pune",
    )
    state = service.prepare_state(req, Trace("test_dates"))

    assert state["window"]["start"].startswith("2026-11-20")
    assert state["window"]["end"].startswith("2026-11-23")
    assert state["schedule"]["overridden_picker"] is True
    assert any(w in (state["schedule"]["override_note"] or "").lower() for w in ("priority", "precedence", "override"))


# =====================================================================
# 17. API FAILURE AND FALLBACK RESILIENCE (PRIORITY 9)
# =====================================================================

def test_17_api_failure_and_fallback_resilience(monkeypatch):
    """Verify that routing falls back gracefully to deterministic estimates when APIs fail."""
    def fail_routing(*args, **kwargs):
        raise ExternalAPIError("routing", "Geoapify and OSRM unreachable")

    monkeypatch.setattr(routing, "_geoapify_leg", fail_routing)
    monkeypatch.setattr(routing, "_osrm_leg", fail_routing)

    leg = routing.travel_time((18.5204, 73.8567), (18.5300, 73.8600), mode="cab")
    assert leg["ok"] is True
    assert leg["status"] == "estimate"
    assert leg["minutes"] > 0
    assert leg["km"] > 0
    assert len(leg.get("warnings", [])) > 0


# =====================================================================
# 18. BUDGET CALCULATION WITH ALL CATEGORIES (PRIORITY 5)
# =====================================================================

def test_18_budget_calculation_with_all_ledger_categories():
    """Verify cost ledger calculates all required categories: accommodation, transit, dining, activities, contingency."""
    plan = {
        "days": [{
            "date": "2026-10-20",
            "items": [
                {"title": "Hotel Taj", "kind": "hotel", "cost": {"amount": 5000.0}},
                {"title": "Pune-Mumbai Express", "kind": "train", "cost": {"amount": 800.0}},
                {"title": "Local Auto", "kind": "travel", "cost": {"amount": 200.0}},
                {"title": "Lunch at Thali House", "kind": "meal", "cost": {"amount": 600.0}},
                {"title": "Fort Entry Ticket", "kind": "activity", "cost": {"amount": 400.0}},
            ],
        }],
    }
    state = {"budget": 10000.0, "currency": "INR"}
    ledger = compute_trip_ledger(plan, state)

    # Subtotal = 5000 + 800 + 200 + 600 + 400 = 7000
    # Contingency (5%) = 350
    # Total = 7350
    assert ledger["accommodation"] == 5000
    assert ledger["intercity_transport"] == 800
    assert ledger["local_transport"] == 200
    assert ledger["food_and_dining"] == 600
    assert ledger["activities_and_entries"] == 400
    assert ledger["contingency"] == 350
    assert ledger["total_cost"] == 7350
    assert ledger["remaining_budget"] == 2650
    assert ledger["is_within_budget"] is True


# =====================================================================
# 19. INFEASIBLE PLAN FAILS HONESTLY (PRIORITY 7)
# =====================================================================

def test_19_infeasible_plan_fails_honestly():
    """Verify that an infeasible request results in status 'failed' rather than hallucinating success."""
    state = {
        "draft": {
            "days": [{
                "date": "2026-10-20",
                "items": [],  # 0 activities scheduled
            }],
        },
        "validation": {"issues": []},
    }
    res = service.build_result(state, Trace("test_infeasible"), "p_infeasible")
    assert res.status == "failed"
    assert "No feasible" in res.summary


# =====================================================================
# 20. INVALID DRAFT MUST NOT BE MARKED COMPLETE (PRIORITY 7)
# =====================================================================

def test_20_invalid_draft_must_not_be_marked_complete():
    """Verify that drafts with high-severity constraint violations are never marked 'ok'."""
    high_severity_issue = {
        "type": "CALENDAR_CONFLICT",
        "severity": "high",
        "agent": "profile",
        "message": "Direct conflict with user meeting",
    }
    state = {
        "draft": {
            "days": [{
                "date": "2026-10-20",
                "items": [
                    {"title": "Conflicting Party", "kind": "activity", "start": "2026-10-20T14:00", "end": "2026-10-20T16:00"},
                ],
            }],
        },
        "validation": {"issues": [high_severity_issue]},
        "status": "ok",  # Attempted to declare ok despite high-severity issue
    }
    res = service.build_result(state, Trace("test_invalid_draft"), "p_invalid")
    assert res.status == "failed", "Draft with high-severity issue must be failed, never ok"
