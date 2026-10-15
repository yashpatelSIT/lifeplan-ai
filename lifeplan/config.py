"""Central configuration, read once from environment variables (and .env if present)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

try:  # python-dotenv is optional at import time (tests don't need it)
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover
    pass

ROOT_DIR = Path(__file__).resolve().parent.parent

# Default model per agent. Format: "<provider>:<model>".
# The coordinator and planner do the hardest reasoning, so they get the stronger
# model; the specialist agents mostly call tools and summarise, so a faster/cheaper
# model is enough (this keeps us inside free-tier rate limits).
DEFAULT_AGENT_MODELS = {
    "gemini": {
        "coordinator": "gemini:gemini-flash-latest",
        "planner": "gemini:gemini-flash-latest",
        "discovery": "gemini:gemini-flash-lite-latest",
        "logistics": "gemini:gemini-flash-lite-latest",
        "profile": "gemini:gemini-flash-lite-latest",
    },
    "groq": {
        "coordinator": "groq:openai/gpt-oss-120b",
        "planner": "groq:openai/gpt-oss-120b",
        "discovery": "groq:openai/gpt-oss-20b",
        "logistics": "groq:openai/gpt-oss-20b",
        "profile": "groq:openai/gpt-oss-20b",
    },
    "openai": {
        "coordinator": "openai:gpt-4.1-mini",
        "planner": "openai:gpt-4.1-mini",
        "discovery": "openai:gpt-4.1-mini",
        "logistics": "openai:gpt-4.1-mini",
        "profile": "openai:gpt-4.1-mini",
    },
}

AGENT_NAMES = ("coordinator", "profile", "discovery", "logistics", "planner")


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else default


@dataclass
class Settings:
    google_api_key: str = ""
    groq_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""
    geoapify_api_key: str = ""
    tavily_api_key: str = ""
    serpapi_api_key: str = ""
    default_timezone: str = "Asia/Kolkata"
    default_currency: str = "INR"
    max_coordinator_steps: int = 14
    max_revisions: int = 3
    max_agent_iterations: int = 6
    http_timeout: float = 15.0
    osm_contact: str = "lifeplan-ai-student-project"
    agent_models: dict = field(default_factory=dict)
    llm_fallbacks: list = field(default_factory=list)
    dev_mock_planning: bool = False
    local_calendar_path: str = ""
    calendar_feed_url: str = ""
    data_dir: Path = ROOT_DIR / "data"
    trace_dir: Path = ROOT_DIR / "traces"

    # ------------------------------------------------------------------
    @property
    def available_providers(self) -> list[str]:
        providers = []
        if self.google_api_key:
            providers.append("gemini")
        if self.groq_api_key:
            providers.append("groq")
        if self.openai_api_key:
            providers.append("openai")
        return providers

    def model_for(self, agent: str) -> str:
        return self.agent_models[agent]

    def public_summary(self) -> dict:
        """Safe-to-show configuration (no secrets) for /api/health and the UI."""
        return {
            "llm_providers": self.available_providers,
            "agent_models": self.agent_models,
            "llm_fallbacks": self.llm_fallbacks,
            "data_sources": {
                "geoapify": bool(self.geoapify_api_key),
                "tavily": bool(self.tavily_api_key),
                "serpapi": bool(self.serpapi_api_key),
                "open_meteo": True,
                "openstreetmap": True,
                "frankfurter": True,
            },
        }


def build_settings() -> Settings:
    s = Settings(
        google_api_key=_env("GOOGLE_API_KEY") or _env("GEMINI_API_KEY"),
        groq_api_key=_env("GROQ_API_KEY"),
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_base_url=_env("OPENAI_BASE_URL"),
        geoapify_api_key=_env("GEOAPIFY_API_KEY"),
        tavily_api_key=_env("TAVILY_API_KEY"),
        serpapi_api_key=_env("SERPAPI_API_KEY"),
        default_timezone=_env("DEFAULT_TIMEZONE", "Asia/Kolkata") or "Asia/Kolkata",
        default_currency=_env("DEFAULT_CURRENCY", "INR") or "INR",
        max_coordinator_steps=int(_env("MAX_COORDINATOR_STEPS", "14") or 14),
        max_revisions=int(_env("MAX_REVISIONS", "3") or 3),
        osm_contact=_env("OSM_CONTACT", "lifeplan-ai-student-project") or "lifeplan-ai-student-project",
    )
    if _env("DATA_DIR"):
        s.data_dir = Path(_env("DATA_DIR"))
    if _env("TRACE_DIR"):
        s.trace_dir = Path(_env("TRACE_DIR"))
    s.dev_mock_planning = _env("DEV_MOCK_PLANNING", "").lower() in ("true", "1", "yes")
    s.local_calendar_path = _env("LOCAL_CALENDAR_PATH")
    s.calendar_feed_url = _env("CALENDAR_FEED_URL") or _env("GOOGLE_CALENDAR_URL") or _env("APPLE_CALENDAR_URL")

    # Primary provider = PRIMARY_LLM_PROVIDER env var, or first one with a key (gemini > groq > openai).
    primary_pref = _env("PRIMARY_LLM_PROVIDER").lower()
    primary = primary_pref if primary_pref in s.available_providers else (s.available_providers or ["gemini"])[0]
    models = dict(DEFAULT_AGENT_MODELS[primary])
    for agent in AGENT_NAMES:
        override = _env(f"LLM_{agent.upper()}")
        if override:
            models[agent] = override
    s.agent_models = models

    # Explicit global fallback chain (optional). By default each agent falls back to its own default model on
    # every other configured provider — see lifeplan.llm.model_chain.
    s.llm_fallbacks = [f.strip() for f in _env("LLM_FALLBACKS").split(",") if f.strip()]
    return s


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return build_settings()


_SECRET_PARAM = re.compile(r"((?:api_?key|apiKey|key|token)=)[^&\s'\"]+", re.I)


def redact(text: str) -> str:
    """Remove API keys from any text that may be logged, traced or shown (error messages, URLs)."""
    if not isinstance(text, str) or not text:
        return text
    s = get_settings()
    for secret in (s.google_api_key, s.groq_api_key, s.openai_api_key, s.geoapify_api_key, s.tavily_api_key,
                   s.serpapi_api_key):
        if secret and len(secret) >= 8:
            text = text.replace(secret, "***")
    return _SECRET_PARAM.sub(r"\1***", text)
