"""Unit tests for the deterministic tools the agents rely on."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from lifeplan.tools import calendar as cal
from lifeplan.tools import opening_hours as oh
from lifeplan.tools import routing, validator
from lifeplan.tools.geo import geocode
from lifeplan.tools.places import normalise_interests, search_places
from lifeplan.tools.scheduler import Window, build_schedule
from lifeplan.tools.weather import get_weather

ROOT = Path(__file__).resolve().parent.parent
WED = date(2026, 10, 14)


# ------------------------------------------------------------------ opening hours
def test_opening_hours_common_formats():
    assert oh.is_open_between("Mo-Fr 09:00-18:00; Sa 10:00-14:00", datetime(2026, 10, 14, 10), datetime(2026, 10, 14, 11))
    assert oh.is_open_between("Mo-Fr 09:00-18:00; Su off", datetime(2026, 10, 18, 10), datetime(2026, 10, 18, 11)) is False
    assert oh.is_open_between("24/7", datetime(2026, 10, 14, 3), datetime(2026, 10, 14, 4))
    # split shift
    spec = "Mo-Su 11:00-15:30,19:00-23:00"
    assert oh.is_open_between(spec, datetime(2026, 10, 14, 16), datetime(2026, 10, 14, 17)) is False
    assert oh.is_open_between(spec, datetime(2026, 10, 14, 19, 30), datetime(2026, 10, 14, 21))
    # past midnight
    assert oh.is_open_between("Tu-Su 18:00-02:00", datetime(2026, 10, 14, 23), datetime(2026, 10, 15, 0, 30))
    # unknown / garbage -> None, never a guess
    assert oh.is_open_between("by appointment", datetime(2026, 10, 14, 10), datetime(2026, 10, 14, 11)) is None
    assert oh.is_open_between(None, datetime(2026, 10, 14, 10), datetime(2026, 10, 14, 11)) is None


# ------------------------------------------------------------------ calendar
def _ics() -> str:
    return (ROOT / "samples" / "sample_calendar.ics").read_text(encoding="utf-8")


def test_calendar_expands_recurring_events():
    events = cal.parse_ics(_ics(), "Asia/Kolkata", datetime(2026, 10, 12), datetime(2026, 10, 19))
    titles = [e["title"] for e in events]
    assert titles.count("Lectures") == 5
    assert "CA3 team meeting" in titles and "Guest lecture: Agentic AI" in titles


def test_routine_learning_predicts_weekly_blocks():
    now = datetime(2026, 10, 14, 12)
    history = cal.parse_ics(_ics(), "Asia/Kolkata", now - timedelta(weeks=4), now)
    routine = cal.learn_routine(history)
    wed = [b for b in routine["blocks"] if b["weekday_name"] == "Wednesday"]
    assert any(b["label"] == "CA3 team meeting" and b["start"] == "17:30" and b["end"] == "19:30" for b in wed)
    assert routine["frequent_locations"][0]["location"].startswith("Symbiosis")


def test_availability_merges_calendar_manual_and_prediction():
    start, end = datetime(2026, 10, 14, 17), datetime(2026, 10, 14, 23, 30)
    events = cal.parse_ics(_ics(), "Asia/Kolkata", start, end)
    manual = [{"start": datetime(2026, 10, 14, 21), "end": datetime(2026, 10, 14, 22), "title": "Call"}]
    av = cal.availability(start, end, events, manual, None, 45)
    assert [w["start"][11:] for w in av["free_windows"]] == ["19:30", "22:00"]
    assert av["free_windows"][0]["minutes"] == 90


def test_prediction_used_when_calendar_is_silent():
    routine = {"blocks": [{"weekday": 2, "start": "18:00", "end": "20:00", "confidence": 0.9, "label": "Gym"}]}
    av = cal.availability(datetime(2026, 10, 14, 17), datetime(2026, 10, 14, 23), [], [], routine, 45)
    assert av["predicted_busy"] and av["free_windows"][0]["end"].endswith("18:00")


# ------------------------------------------------------------------ places / geo / routing / weather (fake HTTP)
def test_interest_normalisation():
    assert normalise_interests(["Dinner", "live music", "parks", "party", "xyz"]) == ["food", "nightlife", "nature"]


def test_places_fall_back_to_openstreetmap(offline):
    offline.fail = {"geoapify"}
    res = search_places(18.53, 73.84, ["food"])
    assert res["ok"] and res["source"] == "openstreetmap"
    assert any("geoapify" in w for w in res["warnings"])
    assert res["places"][0]["id"].startswith("osm:")


def test_geocode_falls_back_to_nominatim(offline):
    offline.fail = {"geoapify"}
    g = geocode("Pune")
    assert g["ok"] and g["source"] == "openstreetmap"


def test_routing_degrades_to_estimate_when_all_services_fail(offline):
    offline.fail = {"geoapify", "openstreetmap.de"}
    leg = routing.travel_time((18.53, 73.84), (18.51, 73.86), "cab")
    assert leg["ok"] and leg["source"] == "estimate" and leg["minutes"] > 0


def test_weather_marks_wet_hours():
    w = get_weather(18.53, 73.84, date.today())
    assert w["ok"] and len(w["wet_hours"]) == 4


def test_cab_fare_is_currency_aware():
    assert routing.cab_fare(5, 15, "INR")["amount"] == round(50 + 14 * 5 + 1.5 * 15)
    assert routing.cab_fare(5, 15, "XYZ")["currency"] == "USD"


# ------------------------------------------------------------------ scheduler
def _travel(a, b):
    return {"minutes": 15, "km": 4, "mode": "cab", "source": "test", "cost": 100}


HOME = {"id": "origin", "name": "Home", "lat": 18.53, "lon": 73.84}


def _cand(cid, kind="activity", hours="Mo-Su 09:00-23:00", dur=60, cost=200, **kw):
    return {"id": cid, "name": cid, "kind": kind, "lat": 18.52, "lon": 73.85, "opening_hours": hours,
            "duration_min": dur, "est_cost": cost, "priority": kw.pop("priority", 0.5), **kw}


def test_scheduler_respects_window_hours_and_return_trip():
    w = [Window(datetime(2026, 10, 14, 19, 30), datetime(2026, 10, 14, 23, 0), HOME, True)]
    cands = [_cand("museum", hours="Mo-Su 10:00-17:30"), _cand("dinner", kind="meal", dur=75), _cand("cafe", kind="snack", dur=45)]
    res = build_schedule(w, cands, _travel)
    titles = [i["title"] for i in res.days[0]["items"] if i["kind"] != "travel"]
    assert "museum" not in titles  # closed in the evening
    assert titles == ["dinner", "cafe"] or titles == ["cafe", "dinner"]
    last = res.days[0]["items"][-1]
    assert last["kind"] == "travel" and last["end"] <= "2026-10-14T23:00"


def test_scheduler_budget_and_must_include():
    w = [Window(datetime(2026, 10, 14, 10), datetime(2026, 10, 14, 20), HOME, True)]
    cands = [_cand("a", cost=900, priority=0.9), _cand("b", cost=100, priority=0.3), _cand("c", cost=100, priority=0.2)]
    res = build_schedule(w, cands, _travel, budget=700)
    used = {i.get("candidate_id") for i in res.days[0]["items"]}
    assert "a" not in used and {"b", "c"} <= used
    res2 = build_schedule(w, cands, _travel, budget=700, must_include=["a"])
    assert "a" in {i.get("candidate_id") for i in res2.days[0]["items"]}


def test_scheduler_fixed_time_event():
    w = [Window(datetime(2026, 10, 14, 17), datetime(2026, 10, 14, 23), HOME, True)]
    ev = _cand("show", kind="event", fixed_start="2026-10-14T19:00", fixed_end="2026-10-14T20:30", priority=1.0)
    res = build_schedule(w, [ev], _travel)
    item = [i for i in res.days[0]["items"] if i.get("candidate_id") == "show"][0]
    assert (item["start"], item["end"]) == ("2026-10-14T19:00", "2026-10-14T20:30") and item["kind"] == "event"


def test_scheduler_avoids_outdoor_in_rain():
    w = [Window(datetime(2026, 10, 14, 18), datetime(2026, 10, 14, 20), HOME, True)]
    park = _cand("park", indoor=False, priority=0.6)
    mall = _cand("mall", indoor=True, priority=0.5)
    wet = {f"2026-10-14T{h}:00" for h in ("18", "19", "20")}
    res = build_schedule(w, [park, mall], _travel, wet_hours=wet, max_stops_per_window=1)
    assert [i["title"] for i in res.days[0]["items"] if i["kind"] != "travel"] == ["mall"]


# ------------------------------------------------------------------ validator (the three-way conflict)
def test_validator_detects_three_way_conflict():
    """Event at 19:00 (Discovery) vs meeting until 19:30 (Profile) vs 40 min to reach (Logistics)."""
    plan = {"days": [{"date": "2026-10-14", "items": [
        {"start": "2026-10-14T19:00", "end": "2026-10-14T20:30", "kind": "event", "title": "Comedy show",
         "candidate_id": "evt:1", "place": {"name": "The Box", "lat": 18.56, "lon": 73.91}, "cost": {"amount": 499}}]}]}
    busy = [{"start": "2026-10-14T17:30", "end": "2026-10-14T19:30", "title": "Project meeting"}]
    cal_issues = validator.check_calendar(plan, busy, [], None)
    assert cal_issues and cal_issues[0]["type"] == "CALENDAR_CONFLICT" and cal_issues[0]["agent"] == "profile"

    plan["days"][0]["items"].insert(0, {"start": "2026-10-14T18:50", "end": "2026-10-14T18:55", "kind": "meal",
                                        "title": "Snack at office", "candidate_id": "x",
                                        "place": {"name": "Office", "lat": 18.59, "lon": 73.74}})

    def slow(a, b):
        return {"minutes": 40, "km": 18, "mode": "cab"}

    log_issues = validator.check_logistics(plan, slow, 1500)
    assert any(i["type"] == "TRAVEL_INFEASIBLE" and i["agent"] == "logistics" for i in log_issues)

    venue_issues = validator.check_venues(plan, {"evt:1": {"id": "evt:1", "fixed_start": "x"}}, set())
    assert any(i["type"] == "UNVERIFIED_PLACE" for i in venue_issues)  # 'x' is not in the verified pool
