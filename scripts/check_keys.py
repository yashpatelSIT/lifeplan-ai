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

from lifeplan.config import get_settings, redact  # noqa: E402

s = get_settings()
rows: list[tuple[str, str, str]] = []


def shape(value: str) -> str:
    """Describe a key without revealing it (helps spot copy-paste mistakes)."""
    issues = []
    if value != value.strip():
        issues.append("leading/trailing whitespace")
    if value[:1] in "\"'" or value[-1:] in "\"'":
        issues.append("wrapped in quotes")
    return f"len={len(value)}" + (f", PROBLEM: {', '.join(issues)}" if issues else "")


def check(name: str, fn) -> None:
    t0 = time.time()
    try:
        detail = fn()
        rows.append((name, "OK", redact(f"{detail} ({int((time.time() - t0) * 1000)} ms)")))
    except Exception as exc:  # noqa: BLE001
        body = getattr(getattr(exc, "response", None), "text", "") or ""
        rows.append((name, "FAIL", redact(f"{type(exc).__name__}: {str(exc)[:300]} {body[:300]}")))


def gemini():
    r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 200},
                     headers={"x-goog-api-key": s.google_api_key}, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"model list HTTP {r.status_code}: {r.text[:300]}")
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in m["name"]]
    from langchain_core.messages import HumanMessage

    from lifeplan import llm

    configured = [m for m in s.agent_models.values() if m.startswith("gemini:")]
    replies, failed = [], []
    for spec in sorted(set(configured)):
        try:
            msg = llm.get_model(spec).invoke([HumanMessage("Reply with the single word: ready")])
            replies.append(f"{spec.split(':', 1)[1]} -> '{llm.text_of(msg).strip()[:12]}'")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{spec.split(':', 1)[1]} FAILED {type(exc).__name__}: {str(exc)[:200]}")
    info = f"{'; '.join(replies + failed) or 'not used as primary'} | flash models available: {', '.join(sorted(names))}"
    if failed and not replies:
        raise RuntimeError(info)
    return info


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


print("Key shapes:", {k: shape(v) for k, v in {"GOOGLE_API_KEY": s.google_api_key, "GROQ_API_KEY": s.groq_api_key,
                                              "GEOAPIFY_API_KEY": s.geoapify_api_key, "TAVILY_API_KEY": s.tavily_api_key,
                                              "SERPAPI_API_KEY": s.serpapi_api_key}.items() if v})
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
lines = [f"Primary models: {s.agent_models}", f"Fallbacks: {s.llm_fallbacks}", ""]
lines += [f"{name:<{width}}  {status:<7}  {detail}" for name, status, detail in rows]
ok_llm = any(r[1] == "OK" for r in rows if "(LLM)" in r[0])
lines.append("\n" + ("Ready: at least one LLM works." if ok_llm else "No working LLM key — the agents cannot run."))
report = redact("\n".join(lines))
print(report)
if len(sys.argv) > 1:  # optional: write the report to a file (used by the GitHub workflow)
    Path(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)
    Path(sys.argv[1]).write_text(report + "\n", encoding="utf-8")
sys.exit(0 if ok_llm else 1)
