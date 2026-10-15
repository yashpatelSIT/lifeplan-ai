"""Tests for multiple itinerary options and sorting metadata."""

import pytest
from lifeplan.schemas import PlanRequest
from lifeplan.service import run_plan
from lifeplan.trace import Trace


def test_mock_plan_generates_multiple_curated_options():
    req = PlanRequest(
        query="Tomorrow 3 PM to 6 PM meet a friend for snacks in Pune",
        location="Pune",
        dev_mock=True,
    )
    trace = Trace(plan_id="test_mock_opt")
    res = run_plan(req, trace)

    assert res.status == "ok"
    assert len(res.options) >= 2
    
    # Check Option 1 (Recommended)
    opt1 = res.options[0]
    assert opt1["id"] == "opt_recommended"
    assert "Recommended" in opt1["title"]
    assert opt1["badge"] == "Top Match"
    assert len(opt1["days"]) > 0

    # Check Option 2 (Budget-Saver)
    opt2 = res.options[1]
    assert opt2["id"] == "opt_budget"
    assert "Budget" in opt2["title"]
    assert opt2["badge"] == "Best Value"
    assert opt2["totals"]["cost"] < opt1["totals"]["cost"]  # Budget option is cheaper

    # Check Option 3 (Quick & Close)
    if len(res.options) >= 3:
        opt3 = res.options[2]
        assert opt3["id"] == "opt_close"
        assert "Quick" in opt3["title"] or "Close" in opt3["title"]
        assert opt3["totals"]["travel_minutes"] < opt1["totals"]["travel_minutes"]

    # Check alternatives sorting attributes
    assert len(res.alternatives) > 0
    for alt in res.alternatives:
        assert "est_cost" in alt
        assert "travel_min" in alt
        assert "distance_km" in alt
