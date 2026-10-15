from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("FAKE_RPM", "0")  # no client-side rate limiting for the fake provider

from lifeplan import llm  # noqa: E402
from lifeplan.config import AGENT_NAMES, get_settings  # noqa: E402
from lifeplan.tools import http  # noqa: E402

import fakes  # noqa: E402


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    """Every test runs offline: fake HTTP, fake LLMs, temp data dirs, demo keys."""
    s = get_settings()
    monkeypatch.setattr(s, "geoapify_api_key", "test-geo")
    monkeypatch.setattr(s, "tavily_api_key", "test-tavily")
    monkeypatch.setattr(s, "serpapi_api_key", "")
    monkeypatch.setattr(s, "agent_models", {a: f"fake:{a}" for a in AGENT_NAMES})
    monkeypatch.setattr(s, "llm_fallbacks", [])
    monkeypatch.setattr(s, "data_dir", tmp_path / "data")
    monkeypatch.setattr(s, "trace_dir", tmp_path / "traces")
    session = fakes.FakeSession()
    http.set_session(session)
    llm.set_model_factory(fakes.fake_factory())
    llm._down_until.clear()
    llm._next_slot.clear()
    from lifeplan import service
    from lifeplan.memory import PlanStore, ProfileStore

    monkeypatch.setattr(service, "profile_store", ProfileStore(tmp_path / "data"))
    monkeypatch.setattr(service, "plan_store", PlanStore(tmp_path / "data"))
    yield session
    http.set_session(None)
    llm.set_model_factory(None)
    llm._down_until.clear()
    llm._next_slot.clear()
