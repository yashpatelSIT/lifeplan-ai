"""LLM access for the agents.

* Each agent has its own model (``LLM_<AGENT>`` env vars) — e.g. the Coordinator and Planner use a
  stronger model while the tool-heavy specialists use a fast, cheap one.
* Client-side rate limiting keeps us inside free-tier RPM limits.
* If the primary model fails (rate limit, outage, bad key) the call automatically falls back to the
  next provider in ``LLM_FALLBACKS`` and the fallback is recorded in the trace.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from lifeplan.config import get_settings, redact
from lifeplan.trace import Trace


class LLMUnavailable(RuntimeError):
    """Raised when every configured model failed for a call."""


# ---------------------------------------------------------------- rate limiting
# Free-tier quotas are per model, so calls are spaced per model spec. Override with e.g. GEMINI_RPM=30
# (a paid key) or GEMINI_RPM=0 to disable client-side limiting.
_DEFAULT_RPM = {"gemini": 10, "gemini-lite": 15, "groq": 30, "openai": 0}
_limit_lock = threading.Lock()
_next_slot: dict[str, float] = {}


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
    gap = 60.0 / rpm
    with _limit_lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(spec, 0.0))
        _next_slot[spec] = slot + gap
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


# ---------------------------------------------------------------- model factory
def _build_model(spec: str) -> BaseChatModel:
    provider, _, model = spec.partition(":")
    s = get_settings()
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not s.google_api_key:
            raise LLMUnavailable("GOOGLE_API_KEY not configured")
        return ChatGoogleGenerativeAI(model=model, api_key=s.google_api_key, max_retries=1, timeout=90)
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
    s = get_settings()
    chain = [s.model_for(agent)]
    for fb in s.llm_fallbacks:
        if fb not in chain:
            chain.append(fb)
    return chain


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
    """Call the agent's model (with fallbacks), log it to the trace and return the AIMessage."""
    errors = []
    for i, spec in enumerate(model_chain(agent)):
        provider = spec.split(":", 1)[0]
        try:
            model = get_model(spec)
            runnable = model.bind_tools(tools, tool_choice=tool_choice) if tools else model
            _wait_for_slot(spec)
            t0 = time.time()
            msg = runnable.invoke(_portable(messages, provider))
            dt = int((time.time() - t0) * 1000)
            if not isinstance(msg, AIMessage):
                msg = AIMessage(content=str(getattr(msg, "content", msg)))
            trace.llm_call(agent, spec, dt, _usage(msg), _text(msg), msg.tool_calls, fallback_used=i > 0)
            return msg
        except Exception as exc:  # noqa: BLE001 - provider SDKs raise many different types
            err = redact(f"{type(exc).__name__}: {str(exc)[:300]}")
            errors.append(f"{spec} -> {err}")
            trace.emit("llm_error", agent, err, model=spec, will_fallback=i + 1 < len(model_chain(agent)))
    raise LLMUnavailable("; ".join(errors) or "no LLM configured")
