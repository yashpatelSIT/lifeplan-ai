from __future__ import annotations

import datetime as dt
from fastapi.testclient import TestClient
import pytest

from lifeplan.api import app
from lifeplan.schemas import PlanRequest
from lifeplan.service import prepare_state, run_mock_plan
from lifeplan.trace import Trace


def test_service_run_mock_plan():
    """Unit test for run_mock_plan in service layer."""
    trace = Trace()
    req = PlanRequest(
        query="Tomorrow, I am free from 3 PM to 6 PM. Meet friend in Pune.",
        location="Pune",
        dev_mock=True,
    )

    result = run_mock_plan(req, trace)

    assert result is not None
    assert result.status == "ok"
    assert trace.stats["llm_calls"] == 0
    assert "demo" in result.summary.lower() or "demonstration" in str(result.warnings).lower()

    # Verify time window of items
    assert len(result.days) > 0
    items = result.days[0].items
    assert len(items) > 0
    # First item starts at 15:00, last item finishes within the 18:00 window
    assert "15:00" in items[0].start
    assert items[-1].end <= f"{items[-1].end[:11]}18:00"


def test_api_plan_endpoint_with_mock_mode():
    """TestClient calling /api/plan with dev_mock=True returns 200 without LLM checks."""
    client = TestClient(app)
    payload = {
        "query": "Tomorrow, I am free from 3 PM to 6 PM. Meet friend in Pune.",
        "location": "Pune",
        "dev_mock": True,
    }

    resp = client.post("/api/plan", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "ok"
    assert len(data["days"]) > 0
    assert any("15:00" in it["start"] for it in data["days"][0]["items"])


def test_api_runs_endpoint_with_mock_mode():
    """TestClient calling /api/runs with dev_mock=True creates an asynchronous run."""
    client = TestClient(app)
    payload = {
        "query": "I have 2 free hours right now. Find a cozy cafe in Pune.",
        "location": "Pune",
        "dev_mock": True,
    }

    resp = client.post("/api/runs", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data

    # Poll run status
    run_id = data["run_id"]
    status_resp = client.get(f"/api/runs/{run_id}")
    assert status_resp.status_code == 200
    run_data = status_resp.json()
    assert run_data["status"] in ("queued", "running", "done")
