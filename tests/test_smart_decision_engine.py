"""Comprehensive unit and integration tests for the deterministic Smart Decision Engine.

Covers:
1. Irrelevant venue detection and rejection (colleges, universities, hospitals, banks, societies).
2. Hard budget constraint enforcement (candidates exceeding budget rejected; itinerary costs strictly bounded).
3. Window bounds and calendar conflict validation.
4. Search service fallback resilience (Geoapify failure falls back to OpenStreetMap).
5. Deterministic scoring, shortlist-first selection, and progressive expansion.
6. Route matrix caching and computational efficiency.
"""

from __future__ import annotations

from datetime import datetime
import pytest

from lifeplan.tools.decision_engine import (
    build_shortlist,
    expand_shortlist,
    filter_candidates,
    is_irrelevant_venue,
    score_candidate,
    validate_itinerary_budget_and_constraints,
)
from lifeplan.tools.places import search_places
from lifeplan.agents.base import RunContext, Workspace
from lifeplan.agents import logistics_agent, planner_agent
from lifeplan.trace import Trace


# =====================================================================
# 1. IRRELEVANT VENUE FILTERING TESTS
# =====================================================================

def test_irrelevant_venue_rejection():
    """Verify that institutional, medical, administrative, and residential venues are rejected."""
    irrelevant_venues = [
        {"name": "Symbiosis Institute of Technology Canteen", "category": "food"},
        {"name": "Pune University Hostel Mess", "category": "food"},
        {"name": "Dr. DY Patil Medical College Campus", "category": "attractions"},
        {"name": "Ruby Hall Clinic Hospital", "category": "attractions"},
        {"name": "HDFC Bank & ATM", "category": "shopping"},
        {"name": "Green Acres Co-op Housing Society", "category": "attractions"},
    ]
    for venue in irrelevant_venues:
        is_irrel, reason = is_irrelevant_venue(venue, user_query="Dinner and coffee tonight")
        assert is_irrel is True, f"Expected {venue['name']} to be flagged as irrelevant"
        assert reason is not None

    # Valid consumer venues must NOT be rejected
    valid_venues = [
        {"name": "Cafe Goodluck", "category": "cafe"},
        {"name": "Vaishali Restaurant", "category": "food"},
        {"name": "Raja Dinkar Kelkar Museum", "category": "museum"},
        {"name": "High Spirits Cafe & Bar", "category": "nightlife"},
    ]
    for venue in valid_venues:
        is_irrel, reason = is_irrelevant_venue(venue, user_query="Dinner and coffee tonight")
        assert is_irrel is False, f"Expected {venue['name']} to be considered valid"
        assert reason is None


def test_irrelevant_venue_allowed_when_explicit_learning_intent():
    """If user explicitly asks to visit a university/campus/heritage, it is allowed."""
    campus_venue = {"name": "Savitribai Phule Pune University", "category": "attractions"}
    
    # Casual dinner query -> rejected
    is_irrel, _ = is_irrelevant_venue(campus_venue, user_query="Find me a cafe for dinner")
    assert is_irrel is True

    # Explicit university visit query -> allowed
    is_irrel_allowed, _ = is_irrelevant_venue(
        campus_venue, user_query="I want to visit Savitribai Phule Pune University campus and library"
    )
    assert is_irrel_allowed is False


# =====================================================================
# 2. HARD BUDGET & CONSTRAINT FILTERING TESTS
# =====================================================================

def test_filter_candidates_hard_constraints():
    """Deterministic filters remove candidates violating hard budget, closed hours, dislikes, or relevance."""
    request = {
        "query": "Dinner for two",
        "budget": {"amount": 1500, "currency": "INR"},
        "interests": ["food", "cafe"],
    }
    profile = {
        "dislikes": ["nightlife"],
    }
    window = {
        "start": "2026-10-14T19:00",
        "end": "2026-10-14T22:30",
    }
    candidates = [
        {"id": "c1", "name": "Vaishali", "category": "food", "est_cost": 600, "opening_hours": "Mo-Su 07:00-23:00"},
        {"id": "c2", "name": "Luxury Tasting Menu", "category": "food", "est_cost": 2500, "opening_hours": "Mo-Su 18:00-23:00"},  # exceeds budget
        {"id": "c3", "name": "Club Pulse", "category": "nightlife", "est_cost": 800, "opening_hours": "Mo-Su 19:00-01:00"},  # in dislikes
        {"id": "c4", "name": "Engineering College Canteen", "category": "food", "est_cost": 200, "opening_hours": "Mo-Su 09:00-21:00"},  # irrelevant college
        {"id": "c5", "name": "Day Cafe", "category": "cafe", "est_cost": 400, "opening_hours": "Mo-Su 08:00-17:00"},  # closed during evening window
    ]

    valid, rejected = filter_candidates(candidates, request, profile=profile, window=window)
    valid_ids = [c["id"] for c in valid]
    rejected_ids = [r["id"] for r in rejected]

    assert valid_ids == ["c1"], f"Only c1 should pass, got {valid_ids}"
    assert "c2" in rejected_ids  # budget violation
    assert "c3" in rejected_ids  # dislike violation
    assert "c4" in rejected_ids  # irrelevant venue
    assert "c5" in rejected_ids  # closed during window


def test_budget_cannot_be_overridden_by_high_score():
    """A candidate exceeding the budget must NEVER be accepted merely because it has a high suitability score."""
    request = {
        "interests": ["food"],
        "budget": {"amount": 1000, "currency": "INR"},
    }
    profile = {"learned_weights": {"food": 1.0}}
    expensive_place = {
        "id": "exp", "name": "5-Star Dining", "category": "food",
        "est_cost": 3000, "opening_hours": "Mo-Su 12:00-23:00",
        "distance_km": 0.5, "indoor": True,
    }
    
    # Filter candidates rejects it deterministically before scoring
    valid, rejected = filter_candidates([expensive_place], request, profile=profile)
    assert len(valid) == 0
    assert rejected[0]["id"] == "exp"
    assert "exceeds user's total budget" in rejected[0]["reason"]


# =====================================================================
# 3. DETERMINISTIC SCORING & SHORTLIST-FIRST STRATEGY
# =====================================================================

def test_deterministic_scoring_and_shortlist():
    """Verify suitability scoring, category diversity, and progressive shortlist selection."""
    request = {
        "interests": ["food", "museum", "cafe"],
        "budget": {"amount": 2000, "currency": "INR"},
    }
    profile = {
        "learned_weights": {"food": 0.9, "museum": 0.7, "cafe": 0.8},
    }
    candidates = [
        {"id": "f1", "name": "Food Spot 1", "category": "food", "distance_km": 1.0, "est_cost": 400, "opening_hours": "Mo-Su 10:00-23:00", "indoor": True},
        {"id": "f2", "name": "Food Spot 2", "category": "food", "distance_km": 5.0, "est_cost": 500, "opening_hours": "Mo-Su 10:00-23:00", "indoor": True},
        {"id": "m1", "name": "Museum 1", "category": "museum", "distance_km": 2.0, "est_cost": 200, "opening_hours": "Mo-Su 10:00-18:00", "indoor": True},
        {"id": "c1", "name": "Cafe 1", "category": "cafe", "distance_km": 0.8, "est_cost": 250, "opening_hours": "Mo-Su 08:00-22:00", "indoor": True},
        {"id": "c2", "name": "Cafe 2", "category": "cafe", "distance_km": 4.0, "est_cost": 300, "opening_hours": "Mo-Su 08:00-22:00", "indoor": True},
        {"id": "s1", "name": "Shopping 1", "category": "shopping", "distance_km": 1.5, "est_cost": 300, "opening_hours": "Mo-Su 10:00-21:00", "indoor": True},
    ]

    shortlist, reserve = build_shortlist(candidates, request, profile=profile, shortlist_size=4)
    assert len(shortlist) == 4
    assert len(reserve) == 2

    # Verify category diversity: must include distinct categories first
    shortlist_cats = {c["category"] for c in shortlist}
    assert {"food", "museum", "cafe"} <= shortlist_cats

    # Verify progressive expansion
    expanded_shortlist, new_reserve = expand_shortlist(shortlist, reserve, count=1)
    assert len(expanded_shortlist) == 5
    assert len(new_reserve) == 1


# =====================================================================
# 4. ITINERARY VALIDATION & BUDGET ENFORCEMENT
# =====================================================================

def test_validate_itinerary_budget_and_constraints():
    """Verify strict itinerary validation against total budget, window bounds, and commitments."""
    request = {
        "budget": {"amount": 1500, "currency": "INR"},
    }
    window = {
        "start": "2026-10-14T18:00",
        "end": "2026-10-14T22:00",
    }
    availability = {
        "busy": [{"start": "2026-10-14T19:00", "end": "2026-10-14T20:00", "title": "Team Meeting"}],
    }

    # Case A: Over-budget itinerary
    over_budget_draft = {
        "days": [{
            "date": "2026-10-14",
            "items": [
                {"title": "Luxury Dining", "start": "2026-10-14T20:15", "end": "2026-10-14T21:45", "cost": {"amount": 2400}},
            ],
        }],
        "totals": {"cost": 2400},
    }
    res = validate_itinerary_budget_and_constraints(over_budget_draft, request, window, availability)
    assert res["ok"] is False
    assert any(i["type"] == "OVER_BUDGET" for i in res["issues"])

    # Case B: Item outside window
    outside_window_draft = {
        "days": [{
            "date": "2026-10-14",
            "items": [
                {"title": "Late Night Club", "start": "2026-10-14T22:30", "end": "2026-10-14T23:45", "cost": {"amount": 600}},
            ],
        }],
        "totals": {"cost": 600},
    }
    res_w = validate_itinerary_budget_and_constraints(outside_window_draft, request, window, availability)
    assert res_w["ok"] is False
    assert any(i["type"] == "OUTSIDE_WINDOW" for i in res_w["issues"])

    # Case C: Overlapping calendar commitment
    clash_draft = {
        "days": [{
            "date": "2026-10-14",
            "items": [
                {"title": "Early Dinner", "start": "2026-10-14T19:15", "end": "2026-10-14T20:15", "cost": {"amount": 500}},
            ],
        }],
        "totals": {"cost": 500},
    }
    res_c = validate_itinerary_budget_and_constraints(clash_draft, request, window, availability)
    assert res_c["ok"] is False
    assert any(i["type"] == "CALENDAR_CONFLICT" for i in res_c["issues"])

    # Case D: Valid itinerary
    valid_draft = {
        "days": [{
            "date": "2026-10-14",
            "items": [
                {"title": "Post-meeting Dinner", "start": "2026-10-14T20:15", "end": "2026-10-14T21:30", "cost": {"amount": 800}},
            ],
        }],
        "totals": {"cost": 800},
    }
    res_valid = validate_itinerary_budget_and_constraints(valid_draft, request, window, availability)
    assert res_valid["ok"] is True
    assert len(res_valid["issues"]) == 0


# =====================================================================
# 5. SERVICE FALLBACK RESILIENCE (GEOAPIFY -> OPENSTREETMAP)
# =====================================================================

def test_search_places_graceful_fallback(offline):
    """When Geoapify fails, search_places automatically falls back to OpenStreetMap."""
    offline.fail = {"geoapify"}
    res = search_places(18.53, 73.84, ["food"])
    assert res["ok"] is True
    assert res["source"] == "openstreetmap"
    assert any("geoapify unavailable" in w for w in res["warnings"])
    assert len(res["places"]) > 0


# =====================================================================
# 6. ROUTING MATRIX CACHING & EFFICIENCY
# =====================================================================

def test_logistics_matrix_caching_and_reuse():
    """Verify that logistics agent reuses cached matrix for repeated candidate subsets."""
    ctx = RunContext(Trace(), None, datetime(2026, 10, 14, 12, 0))
    ws = Workspace({
        "origin": {"id": "origin", "lat": 18.53, "lon": 73.84},
        "candidates": {
            "c1": {"id": "c1", "lat": 18.52, "lon": 73.85, "name": "Place 1"},
            "c2": {"id": "c2", "lat": 18.51, "lon": 73.86, "name": "Place 2"},
            "c3": {"id": "c3", "lat": 18.54, "lon": 73.83, "name": "Place 3"},
        },
        "shortlist_ids": ["c1", "c2"],
        "logistics": {"mode": "cab"},
    }, ctx)

    tools = {t.name: t for t in logistics_agent.build_tools(ws)}
    # First call: computes matrix
    res1 = tools["prepare_travel_matrix"].func(["c1", "c2"])
    assert res1["ok"] is True
    assert not res1.get("cached")

    # Second call for same or subset of candidates: reuses cached matrix without re-querying
    res2 = tools["prepare_travel_matrix"].func(["c1"])
    assert res2["ok"] is True
    assert res2.get("cached") is True


# =====================================================================
# 7. UNIVERSAL CALENDAR AUTO-DETECTION TESTS
# =====================================================================

def test_detect_local_calendar_from_custom_path(tmp_path, monkeypatch):
    from lifeplan.tools import calendar as cal
    from lifeplan.config import get_settings

    dummy_ics = "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nSUMMARY:Dentist\nDTSTART:20261014T100000\nDTEND:20261014T110000\nEND:VEVENT\nEND:VCALENDAR"
    cal_file = tmp_path / "calendar.ics"
    cal_file.write_text(dummy_ics, encoding="utf-8")

    s = get_settings()
    monkeypatch.setattr(s, "local_calendar_path", str(cal_file))

    content, src_type, src_name = cal.detect_local_calendar()
    assert content is not None
    assert "BEGIN:VCALENDAR" in content
    assert src_type == "local_file"
    assert "calendar.ics" in src_name


def test_detect_calendar_from_google_feed(monkeypatch):
    from lifeplan.tools import calendar as cal
    from lifeplan.config import get_settings

    dummy_ics = "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nSUMMARY:Google Meeting\nDTSTART:20261014T140000\nDTEND:20261014T150000\nEND:VEVENT\nEND:VCALENDAR"

    monkeypatch.setattr(cal, "fetch_calendar_feed", lambda url, **kw: (True, dummy_ics, None))
    s = get_settings()
    monkeypatch.setattr(s, "calendar_feed_url", "https://calendar.google.com/calendar/ical/user%40gmail.com/private-123/basic.ics")

    content, src_type, src_name = cal.detect_local_calendar()
    assert content is not None
    assert src_type == "google_calendar"
    assert src_name == "Google Calendar"


def test_api_calendar_detect_endpoint():
    from fastapi.testclient import TestClient
    from lifeplan.api import app

    client = TestClient(app)
    res = client.get("/api/calendar/detect")
    assert res.status_code == 200
    data = res.json()
    assert "detected" in data
    assert "events_count" in data
    assert "upcoming_events" in data
