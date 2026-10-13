"""LLM resilience: retry-after parsing, circuit breaker, no Gemini continuation of foreign tool loops."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

import fakes
from lifeplan import llm
from lifeplan.config import get_settings
from lifeplan.trace import Trace


def test_retry_after_parsing():
    assert llm._retry_after("Rate limit reached... Please try again in 240ms. Need more") == 0.24
    assert llm._retry_after("Please try again in 3.5s") == 3.5
    assert llm._retry_after("503 UNAVAILABLE high demand") is None
    assert llm._is_transient("503 UNAVAILABLE. This model is currently experiencing high demand")
    assert not llm._is_transient("401 invalid api key")


def test_gemini_never_continues_another_providers_tool_loop():
    msg = AIMessage("", tool_calls=[{"name": "x", "args": {}, "id": "1"}], response_metadata={"lifeplan_provider": "groq"})
    assert llm._foreign_tool_history([HumanMessage("hi"), msg], "gemini")
    assert not llm._foreign_tool_history([HumanMessage("hi"), msg], "groq")


def test_waits_and_retries_same_model_on_short_rate_limit(monkeypatch):
    calls = {"n": 0}

    def responder(agent, tools, messages):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Error code: 429 - Rate limit reached ... Please try again in 10ms.")
        return AIMessage("ok")

    monkeypatch.setattr(get_settings(), "agent_models", {**get_settings().agent_models, "profile": "fake:profile"})
    llm.set_model_factory(fakes.fake_factory(responder))
    tr = Trace()
    out = llm.invoke("profile", [HumanMessage("hi")], tr)
    assert out.content == "ok" and calls["n"] == 2
    assert any(e["type"] == "llm_error" and e["data"]["will_retry"] for e in tr.events)
    assert out.response_metadata["lifeplan_provider"] == "fake"
