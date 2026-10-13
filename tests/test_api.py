"""HTTP API tests (FastAPI TestClient) incl. validation errors, streaming runs and re-planning."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from lifeplan.api import app
from lifeplan.config import get_settings

client = TestClient(app)

REQ = {"query": "Evening plan with dinner", "mode": "evening", "location": "Shivajinagar, Pune",
       "interests": ["food", "museum"], "budget": {"amount": 1500, "currency": "INR"}, "now": "2026-10-14T16:45:00"}


@pytest.fixture(autouse=True)
def llm_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "google_api_key", "test")  # pretend a provider is configured


def test_health_lists_sources_without_secrets():
    data = client.get("/api/health").json()
    assert data["status"] == "ok"
    assert "agent_models" in data and "geoapify" in data["data_sources"]
    assert "test-geo" not in json.dumps(data)


def test_samples_are_valid_requests():
    from lifeplan.schemas import PlanRequest

    samples = client.get("/api/samples").json()
    assert len(samples) >= 5
    for s in samples:
        PlanRequest(**s["request"])


@pytest.mark.parametrize("body,needle", [
    ({"query": "hi"}, "query"),
    ({"query": "plan my evening"}, "location"),
    ({"query": "plan", "location": "Pune", "budget": {"amount": -5}}, "budget"),
    ({"query": "plan it", "location": "Pune", "start": "2026-10-14T20:00", "end": "2026-10-14T18:00"}, "end must be after"),
])
def test_bad_input_returns_422_with_reason(body, needle):
    r = client.post("/api/plan", json=body)
    assert r.status_code == 422
    assert needle in json.dumps(r.json())


def test_unknown_location_is_a_clear_error():
    r = client.post("/api/plan", json={**REQ, "location": "Nowhere-ville 12345"})
    assert r.status_code == 422
    assert "Could not find the location" in r.json()["detail"]


def test_no_llm_configured_returns_503(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "google_api_key", "")
    monkeypatch.setattr(s, "groq_api_key", "")
    monkeypatch.setattr(s, "openai_api_key", "")
    assert client.post("/api/plan", json=REQ).status_code == 503


def test_sync_plan_and_feedback_learning():
    r = client.post("/api/plan", json=REQ)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["status"] in ("ok", "partial") and plan["days"]
    fb = client.post("/api/feedback", json={"plan_id": plan["plan_id"], "rating": 5, "liked": ["food"],
                                            "disliked": ["nightlife"]})
    assert fb.status_code == 200
    assert fb.json()["learned_weights"]["food"] > 0.5


def _wait(run_id: str, timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/runs/{run_id}").json()
        if st["status"] != "running":
            return st
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_streaming_run_events_trace_and_replan():
    run_id = client.post("/api/runs", json=REQ).json()["run_id"]
    st = _wait(run_id)
    assert st["status"] == "done", st
    with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
        lines = [ln for ln in stream.iter_lines() if ln.startswith("data:") or ln.startswith("event:")]
    events = [json.loads(ln[5:]) for ln in lines if ln.startswith("data:") and '"seq"' in ln]
    assert any(e["type"] == "message" for e in events)
    assert any(ln.startswith("event: done") for ln in lines)
    md = client.get(f"/api/runs/{run_id}/trace?format=md")
    assert md.status_code == 200 and "Execution trace" in md.text

    plan_id = st["result"]["plan_id"]
    rr = client.post("/api/runs/replan", json={"plan_id": plan_id, "event": "weather", "details": "It's pouring now"})
    assert rr.status_code == 200
    st2 = _wait(rr.json()["run_id"])
    assert st2["status"] == "done" and st2["result"]["plan_id"] == plan_id


def test_replan_unknown_plan_is_404():
    r = client.post("/api/runs/replan", json={"plan_id": "plan_nope", "event": "weather", "details": "rain"})
    assert r.status_code == 404


def test_index_serves_ui():
    r = client.get("/")
    assert r.status_code == 200 and "LifePlan AI" in r.text
