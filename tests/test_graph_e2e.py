"""End-to-end runs of the full LangGraph multi-agent system with scripted LLMs and fake APIs."""

from __future__ import annotations

import json
from datetime import datetime

import fakes
from lifeplan import llm, service
from lifeplan.config import get_settings
from lifeplan.memory import SAMPLE_CALENDAR
from lifeplan.schemas import BusyBlock, PlanRequest, ReplanEvent
from lifeplan.trace import Trace, to_markdown

NOW = datetime(2026, 10, 14, 16, 45)  # a Wednesday: CA3 team meeting 17:30–19:30 in the sample calendar


def evening_request(**kw) -> PlanRequest:
    data = dict(query="Plan my evening: something fun, then dinner. Budget 1500.", mode="evening",
                location="Shivajinagar, Pune", interests=["food", "comedy", "museum"],
                budget={"amount": 1500, "currency": "INR"}, now=NOW,
                calendar_ics=SAMPLE_CALENDAR.read_text(encoding="utf-8"))
    data.update(kw)
    return PlanRequest(**data)


def test_full_run_produces_grounded_plan_and_trace():
    trace = Trace()
    result = service.run_plan(evening_request(), trace)
    assert result.status in ("ok", "partial")
    assert result.days, "plan should have at least one day"
    items = [it for d in result.days for it in d.items]
    activities = [it for it in items if it.kind in ("meal", "snack", "activity", "event")]
    assert activities, "should schedule at least one activity"
    # grounding: every scheduled venue comes from the verified pool and has a source
    for it in activities:
        assert it.candidate_id and it.place and it.place.source
    # availability respected: nothing before the meeting + 45 min commute ends (19:30 + 0:45)
    for it in activities:
        assert it.start >= "2026-10-14T20:15", it
    # every agent took part and communicated through messages
    agents = {e["agent"] for e in trace.events if e["type"] == "agent_start"}
    assert {"profile", "discovery", "logistics", "planner"} <= agents
    kinds = {(e["agent"], (e.get("data") or {}).get("kind")) for e in trace.events if e["type"] == "message"}
    assert ("coordinator", "request") in kinds and ("planner", "report") in kinds
    assert any(k == "critique" for _, k in kinds), "review round should produce critiques"
    assert trace.stats["llm_calls"] > 10 and trace.stats["tool_calls"] > 8
    md = to_markdown(trace.to_dict())
    assert "coordinator" in md and "|" in md


def test_conflict_is_detected_and_resolved_by_replanning():
    """Planner first forces the 19:00 comedy show (overlapping the 17:30–19:30 meeting); the constraint checks
    raise a CALENDAR_CONFLICT, the Coordinator sends it back, the Planner revises."""
    attempts = {"n": 0}

    def responder(agent, tools, messages):
        if agent == "planner" and "build_schedule" in tools:
            done = fakes._tool_results(messages)
            if "list_candidates" in done and "build_schedule" not in done:
                attempts["n"] += 1
                ids = [c["id"] for c in done["list_candidates"][0]["candidates"]]
                if attempts["n"] == 1:  # naive first draft: whole evening window, event forced in
                    return fakes.AIMessage("", tool_calls=[fakes.call(
                        "build_schedule", priorities=[f"{i}:0.9" for i in ids], must_include=[i for i in ids if i.startswith("evt:")],
                        windows=["2026-10-14T17:00|2026-10-14T23:30"])])
                return fakes.AIMessage("", tool_calls=[fakes.call(
                    "build_schedule", priorities=[f"{i}:0.7" for i in ids if not i.startswith("evt:")])])
        return fakes.default_responder(agent, tools, messages)

    llm.set_model_factory(fakes.fake_factory(responder))
    trace = Trace()
    result = service.run_plan(evening_request(), trace)
    conflict_events = [e for e in trace.events if e["type"] == "conflict"]
    assert any((e.get("data") or {}).get("type") == "CALENDAR_CONFLICT" for e in conflict_events)
    assert attempts["n"] >= 2, "planner should have been asked to revise"
    assert any(c.type == "CALENDAR_CONFLICT" and not c.resolution.startswith("UNRESOLVED") for c in result.conflicts)
    assert not any(it.start < "2026-10-14T19:30" and it.kind == "event" for d in result.days for it in d.items)


def test_llm_outage_degrades_gracefully():
    """If every model fails, agents fall back to deterministic behaviour and still return a plan."""
    llm.set_model_factory(fakes.fake_factory(failing={f"fake:{a}" for a in
                                                     ("coordinator", "profile", "discovery", "logistics", "planner")}))
    trace = Trace()
    result = service.run_plan(evening_request(), trace)
    assert result.status == "partial"
    assert any(e["type"] == "degraded_mode" for e in trace.events)
    assert any("degraded" in w for w in result.warnings)


def test_primary_model_failure_falls_back_to_second_provider(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "agent_models", {**s.agent_models, "discovery": "broken:discovery"})
    monkeypatch.setattr(s, "llm_fallbacks", ["fake:discovery"])
    llm.set_model_factory(fakes.fake_factory(failing={"broken:discovery"}))
    trace = Trace()
    service.run_plan(evening_request(), trace)
    assert trace.stats["llm_fallbacks"] > 0
    assert any(e["type"] == "llm_error" for e in trace.events)


def test_tool_failure_falls_back_to_openstreetmap(offline):
    offline.fail = {"geoapify.com/v2/places"}
    trace = Trace()
    result = service.run_plan(evening_request(), trace)
    calls = [e for e in trace.events if e["type"] == "tool_call" and e["content"] == "search_venues"]
    assert calls and calls[0]["data"]["result"]["source"] == "openstreetmap"
    assert result.status in ("ok", "partial")


def test_replan_after_rain_and_schedule_change():
    first = service.run_plan(evening_request(), Trace())
    ev = ReplanEvent(plan_id=first.plan_id, event="schedule_change", details="Meeting extended to 20:30",
                     new_busy_block=BusyBlock(start=datetime(2026, 10, 14, 19, 30), end=datetime(2026, 10, 14, 20, 30),
                                              title="Meeting extended"))
    trace = Trace(kind="replan")
    second = service.replan(ev, trace)
    assert second.plan_id == first.plan_id
    assert any(e["type"] == "message" and (e.get("data") or {}).get("kind") == "event" for e in trace.events)
    for d in second.days:
        for it in d.items:
            if it.kind in ("meal", "snack", "activity", "event"):
                assert it.start >= "2026-10-14T21:15"  # 20:30 + 45 min commute


def test_trip_request_requires_destination():
    try:
        service.run_plan(PlanRequest(query="Plan a vacation", mode="domestic_trip", location="Pune"), Trace())
    except service.PlanningError as exc:
        assert "destination" in str(exc)
    else:
        raise AssertionError("expected PlanningError")


def test_result_is_json_serialisable():
    result = service.run_plan(evening_request(), Trace())
    json.dumps(result.model_dump(mode="json"))
