"""Check which API keys work before running the agents.

    python scripts/check_keys.py

Makes one tiny request per configured service and prints a status table. For Gemini it also lists the
Flash models your key can use, so you can pick LLM_<AGENT> values if a default name ever changes.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from lifeplan.config import get_settings  # noqa: E402

s = get_settings()
rows: list[tuple[str, str, str]] = []


def check(name: str, fn) -> None:
    t0 = time.time()
    try:
        detail = fn()
        rows.append((name, "OK", f"{detail} ({int((time.time() - t0) * 1000)} ms)"))
    except Exception as exc:  # noqa: BLE001
        rows.append((name, "FAIL", f"{type(exc).__name__}: {str(exc)[:160]}"))


def gemini():
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": s.google_api_key,
                                                                                         "pageSize": 200}, timeout=20)
    r.raise_for_status()
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in m["name"]]
    from langchain_core.messages import HumanMessage

    from lifeplan import llm

    configured = [m for m in s.agent_models.values() if m.startswith("gemini:")]
    replies = []
    for spec in sorted(set(configured)):
        msg = llm.get_model(spec).invoke([HumanMessage("Reply with the single word: ready")])
        replies.append(f"{spec.split(':', 1)[1]} -> '{llm.text_of(msg).strip()[:12]}'")
    return f"{'; '.join(replies) or 'not used as primary'} | flash models available: {', '.join(sorted(names)[:8])}"


def groq():
    from langchain_core.messages import HumanMessage
    from langchain_groq import ChatGroq

    model = next((m for m in [s.agent_models["coordinator"], *s.llm_fallbacks] if m.startswith("groq:")),
                 "groq:openai/gpt-oss-120b").split(":", 1)[1]
    msg = ChatGroq(model=model, api_key=s.groq_api_key, max_retries=0).invoke([HumanMessage("Reply: ready")])
    return f"{model} replied '{str(msg.content).strip()[:20]}'"


def geoapify():
    r = requests.get("https://api.geoapify.com/v1/geocode/search",
                     params={"text": "Shivajinagar, Pune", "format": "json", "limit": 1, "apiKey": s.geoapify_api_key},
                     timeout=15)
    r.raise_for_status()
    res = r.json()["results"][0]
    return f"geocoded Pune -> {res['lat']:.3f},{res['lon']:.3f}"


def tavily():
    r = requests.post("https://api.tavily.com/search", json={"query": "events in Pune this week", "max_results": 1},
                      headers={"Authorization": f"Bearer {s.tavily_api_key}"}, timeout=20)
    r.raise_for_status()
    return f"{len(r.json().get('results', []))} result(s)"


def serpapi():
    r = requests.get("https://serpapi.com/account.json", params={"api_key": s.serpapi_api_key}, timeout=15)
    r.raise_for_status()
    d = r.json()
    return f"plan={d.get('plan_name')}, searches left this month={d.get('total_searches_left')}"


def open_meteo():
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={"latitude": 18.52, "longitude": 73.85,
                                                                       "current": "temperature_2m"}, timeout=15)
    r.raise_for_status()
    return f"{r.json()['current']['temperature_2m']} °C in Pune now"


def osrm():
    r = requests.get("https://routing.openstreetmap.de/routed-car/route/v1/driving/73.8475,18.5308;73.7262,18.5404",
                     params={"overview": "false"}, timeout=15, headers={"User-Agent": "LifePlanAI-keycheck"})
    r.raise_for_status()
    return f"Shivajinagar -> Lavale {r.json()['routes'][0]['duration'] / 60:.0f} min by car"


if s.google_api_key:
    check("Gemini (LLM)", gemini)
else:
    rows.append(("Gemini (LLM)", "missing", "set GOOGLE_API_KEY"))
if s.groq_api_key:
    check("Groq (LLM)", groq)
else:
    rows.append(("Groq (LLM)", "missing", "set GROQ_API_KEY"))
for name, key, fn in (("Geoapify", s.geoapify_api_key, geoapify), ("Tavily", s.tavily_api_key, tavily),
                      ("SerpApi (optional)", s.serpapi_api_key, serpapi)):
    if key:
        check(name, fn)
    else:
        rows.append((name, "missing", "optional fallback will be used" if "optional" in name else "recommended"))
check("Open-Meteo (no key)", open_meteo)
check("OSRM routing (no key)", osrm)

width = max(len(r[0]) for r in rows)
print(f"\nPrimary models: {s.agent_models}\nFallbacks: {s.llm_fallbacks}\n")
for name, status, detail in rows:
    print(f"{name:<{width}}  {status:<7}  {detail}")
ok_llm = any(r[1] == "OK" for r in rows if "(LLM)" in r[0])
print("\n" + ("Ready: at least one LLM works." if ok_llm else "No working LLM key — the agents cannot run."))
sys.exit(0 if ok_llm else 1)
