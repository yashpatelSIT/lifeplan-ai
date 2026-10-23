"""LLM access for the agents.

* Each agent has its own model (``LLM_<AGENT>`` env vars) — e.g. the Coordinator and Planner use a
  stronger model while the tool-heavy specialists use a fast, cheap one.
* Client-side rate limiting keeps us inside free-tier RPM limits.
* If the primary model fails (rate limit, outage, bad key) the call automatically falls back to the
  next provider in ``LLM_FALLBACKS`` and the fallback is recorded in the trace.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from lifeplan.config import DEFAULT_AGENT_MODELS, get_settings, redact
from lifeplan.trace import Trace


class LLMUnavailable(RuntimeError):
    """Raised when every configured model failed for a call."""


# ---------------------------------------------------------------- rate limiting
# Token-bucket rate limiting with burst capacity allows rapid agent execution
# without artificial pauses on every step, while still respecting API RPM caps.
_DEFAULT_RPM = {"gemini": 15, "gemini-lite": 25, "groq": 45, "openai": 0}
_limit_lock = threading.Lock()
_next_slot: dict[str, float] = {}
_buckets: dict[str, tuple[float, float]] = {}  # spec -> (tokens, last_time)


def _rpm_for(spec: str) -> float:
    provider, _, model = spec.partition(":")
    env = os.getenv(f"{provider.upper()}_RPM")
    if env is not None and env.strip() != "":
        return float(env)
    if provider == "gemini" and "lite" in model:
        return _DEFAULT_RPM["gemini-lite"]
    return float(_DEFAULT_RPM.get(provider, 30))


def _wait_for_slot(spec: str) -> None:
    rpm = _rpm_for(spec)
    if rpm <= 0:
        return
    rate_per_sec = rpm / 60.0
    burst_capacity = min(5.0, max(3.0, rpm / 5.0))
    wait_time = 0.0
    with _limit_lock:
        now = time.monotonic()
        tokens, last_time = _buckets.get(spec, (burst_capacity, now))
        elapsed = max(0.0, now - last_time)
        tokens = min(burst_capacity, tokens + elapsed * rate_per_sec)
        if tokens >= 1.0:
            _buckets[spec] = (tokens - 1.0, now)
            return
        wait_time = (1.0 - tokens) / rate_per_sec
        _buckets[spec] = (0.0, now + wait_time)
    if wait_time > 0:
        time.sleep(min(wait_time, 10.0))


# ---------------------------------------------------------------- model factory
def _build_model(spec: str) -> BaseChatModel:
    provider, _, model = spec.partition(":")
    s = get_settings()
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not s.google_api_key:
            raise LLMUnavailable("GOOGLE_API_KEY not configured")
        return ChatGoogleGenerativeAI(model=model, api_key=s.google_api_key, max_retries=1, timeout=45)
    if provider == "groq":
        from langchain_groq import ChatGroq

        if not s.groq_api_key:
            raise LLMUnavailable("GROQ_API_KEY not configured")
        kwargs: dict[str, Any] = {"model": model, "api_key": s.groq_api_key, "temperature": 0.2, "max_retries": 1,
                                  "request_timeout": 90}
        if "gpt-oss" in model:
            kwargs["reasoning_effort"] = "low"
        return ChatGroq(**kwargs)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        if not s.openai_api_key:
            raise LLMUnavailable("OPENAI_API_KEY not configured")
        kwargs = {"model": model, "api_key": s.openai_api_key, "temperature": 0.2, "max_retries": 1, "timeout": 90}
        if s.openai_base_url:
            kwargs["base_url"] = s.openai_base_url
        return ChatOpenAI(**kwargs)
    raise LLMUnavailable(f"unknown provider in model spec '{spec}'")


ModelFactory = Callable[[str], BaseChatModel]
_factory: ModelFactory = _build_model
_cache: dict[str, BaseChatModel] = {}
_cache_lock = threading.Lock()


def set_model_factory(factory: Optional[ModelFactory]) -> None:
    """Tests inject a fake model factory here."""
    global _factory
    _factory = factory or _build_model
    with _cache_lock:
        _cache.clear()


def get_model(spec: str) -> BaseChatModel:
    with _cache_lock:
        if spec not in _cache:
            _cache[spec] = _factory(spec)
        return _cache[spec]


def model_chain(agent: str) -> list[str]:
    """Primary model for the agent, then the *same agent's* default model on every other configured
    provider (so a light agent falls back to a light model and spreads load across rate-limit buckets),
    unless LLM_FALLBACKS overrides the chain explicitly."""
    s = get_settings()
    chain = [s.model_for(agent)]
    if os.getenv("LLM_FALLBACKS", "").strip():
        fallbacks = list(s.llm_fallbacks)
    else:
        primary_provider = chain[0].split(":", 1)[0]
        if primary_provider in DEFAULT_AGENT_MODELS:
            fallbacks = [DEFAULT_AGENT_MODELS[p][agent] for p in s.available_providers if p != primary_provider]
            fallbacks += list(s.llm_fallbacks)
        else:
            fallbacks = list(s.llm_fallbacks)
    for fb in fallbacks:
        if fb not in chain:
            chain.append(fb)
    return chain


# ---------------------------------------------------------------- transient-error handling
_TRANSIENT = ("429", "rate limit", "resource_exhausted", "503", "unavailable", "overloaded", "504",
              "deadline", "high demand", "timeout", "timed out", "quota", "too many requests")
_OVERLOAD = ("503", "unavailable", "overloaded", "high demand", "504", "deadline", "429", "resource_exhausted", "quota", "too many requests")
_PERMANENT = ("401", "unauthorized", "invalid_api_key", "forbidden", "403")
_down_until: dict[str, float] = {}  # simple circuit breaker per model spec
BREAKER_SECONDS = 300
MAX_INLINE_WAIT = 20.0


def _retry_after(err: str) -> Optional[float]:
    """Parse 'Please try again in 240ms' / 'in 3.5s' / 'retry in 12s' from provider errors."""
    m = re.search(r"try again in ([0-9.]+)\s*(ms|s)", err, re.I) or re.search(r"retry[^0-9]{0,20}([0-9.]+)\s*(ms|s)", err, re.I)
    if not m:
        return None
    value = float(m.group(1))
    return value / 1000 if m.group(2).lower() == "ms" else value


def _is_transient(err: str) -> bool:
    low = err.lower()
    return any(k in low for k in _TRANSIENT)


def _foreign_tool_history(messages: Sequence[BaseMessage], provider: str) -> bool:
    """Gemini 3 requires its own 'thought signatures' on earlier tool calls in the same turn, so it cannot
    continue a tool-calling loop that another provider started. Detect that case."""
    for m in messages:
        if isinstance(m, AIMessage) and m.tool_calls:
            origin = (m.response_metadata or {}).get("lifeplan_provider")
            if origin and origin != provider:
                return True
    return False


def _usage(msg: AIMessage) -> dict:
    meta = getattr(msg, "usage_metadata", None) or {}
    return {"input_tokens": meta.get("input_tokens"), "output_tokens": meta.get("output_tokens")}


def _text(msg: AIMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def text_of(msg: BaseMessage) -> str:
    return _text(msg)  # type: ignore[arg-type]


def _portable(messages: Sequence[BaseMessage], provider: str) -> list[BaseMessage]:
    """Provider-specific content blocks (e.g. Gemini 'thinking' parts) are flattened to plain text
    when a conversation is continued by a *different* provider after a fallback."""
    if provider == "gemini":
        return list(messages)
    out: list[BaseMessage] = []
    for m in messages:
        if isinstance(m, AIMessage) and not isinstance(m.content, str):
            out.append(AIMessage(content=_text(m), tool_calls=m.tool_calls, id=m.id))
        else:
            out.append(m)
    return out


def invoke(agent: str, messages: Sequence[BaseMessage], trace: Trace, tools: Optional[list] = None,
           tool_choice: Optional[str] = None) -> AIMessage:
    """Call the agent's model with resilience, log it to the trace and return the AIMessage.

    * waits briefly and retries when a provider says "try again in Xs" (free-tier TPM limits)
    * skips a model for BREAKER_SECONDS after it reports overload (503/504) — a circuit breaker
    * never sends a Gemini model a tool-calling history produced by another provider
    * falls back along the agent's model chain; if everything failed transiently, waits and tries once more
    """
    chain = model_chain(agent)
    errors: list[str] = []
    for round_no in range(2):
        transient_only = True
        for i, spec in enumerate(chain):
            provider = spec.split(":", 1)[0]
            is_last = i == len(chain) - 1
            if provider == "gemini" and _foreign_tool_history(messages, provider):
                continue
            if _down_until.get(spec, 0) > time.time() and not (is_last and round_no == 1):
                continue
            for attempt in range(3):
                try:
                    model = get_model(spec)
                    runnable = model.bind_tools(tools, tool_choice=tool_choice) if tools else model
                    _wait_for_slot(spec)
                    t0 = time.time()
                    msg = runnable.invoke(_portable(messages, provider))
                    dt = int((time.time() - t0) * 1000)
                    if not isinstance(msg, AIMessage):
                        msg = AIMessage(content=str(getattr(msg, "content", msg)))
                    msg.response_metadata = {**(msg.response_metadata or {}), "lifeplan_provider": provider}
                    trace.llm_call(agent, spec, dt, _usage(msg), _text(msg), msg.tool_calls,
                                   fallback_used=spec != chain[0])
                    _down_until.pop(spec, None)
                    return msg
                except Exception as exc:  # noqa: BLE001 - provider SDKs raise many different types
                    err = redact(f"{type(exc).__name__}: {str(exc)[:300]}")
                    errors.append(f"{spec} -> {err}")
                    low = err.lower()
                    wait = _retry_after(err)
                    if any(k in low for k in _PERMANENT):
                        _down_until[spec] = time.time() + 3600
                        transient_only = False
                    elif any(k in low for k in _OVERLOAD):
                        _down_until[spec] = time.time() + BREAKER_SECONDS
                        transient_only = False
                    retry_here = wait is not None and wait <= MAX_INLINE_WAIT and attempt < 2
                    trace.emit("llm_error", agent, err, model=spec, will_retry=retry_here,
                               will_fallback=not retry_here and not is_last)
                    if retry_here:
                        time.sleep(wait + 0.5)
                        continue
                    break
        is_test = os.getenv("FAKE_RPM") == "0" or os.getenv("PYTEST_CURRENT_TEST") is not None
        if is_test or not transient_only or round_no == 1 or not errors:
            break
        trace.emit("llm_wait", agent, "all models busy — waiting 10 s before one more attempt")
        time.sleep(10)
    raise LLMUnavailable("; ".join(errors[-4:]) or "no LLM configured")
