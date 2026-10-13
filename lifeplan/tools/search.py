"""Live web information: events and general look-ups.

* SerpApi Google Events (optional key) — structured event listings
* Tavily web search (key) — AI-oriented search used for events, ticket pages, travel info
* Wikipedia REST summary (free) — short destination briefings for trips
Every result keeps its URL so the final plan can cite where each event came from.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from lifeplan.config import get_settings
from lifeplan.tools.http import ExternalAPIError, request_json

TAVILY_SEARCH = "https://api.tavily.com/search"
SERPAPI = "https://serpapi.com/search.json"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"


def web_search(query: str, max_results: int = 6, days: Optional[int] = None) -> dict:
    key = get_settings().tavily_api_key
    if not key:
        return {"ok": False, "error": "TAVILY_API_KEY not configured", "results": []}
    body = {"query": query[:380], "max_results": max(1, min(max_results, 10)), "search_depth": "basic",
            "include_answer": False}
    if days:
        body["topic"] = "news"
        body["days"] = days
    try:
        data = request_json("tavily", "POST", TAVILY_SEARCH, json_body=body,
                            headers={"Authorization": f"Bearer {key}"}, cache_ttl=1800)
    except ExternalAPIError as exc:
        return {"ok": False, "error": str(exc), "results": []}
    results = [{"title": r.get("title"), "url": r.get("url"), "snippet": (r.get("content") or "")[:400],
                "published": r.get("published_date")} for r in data.get("results", [])]
    return {"ok": True, "source": "tavily", "query": query, "results": results}


def _serpapi_events(city: str, when: str) -> Optional[dict]:
    key = get_settings().serpapi_api_key
    if not key:
        return None
    chip = {"today": "date:today", "tomorrow": "date:tomorrow", "weekend": "date:weekend",
            "week": "date:week", "next_week": "date:next_week", "month": "date:month"}.get(when, "")
    params = {"engine": "google_events", "q": f"Events in {city}", "hl": "en", "api_key": key}
    if chip:
        params["htichips"] = chip
    data = request_json("serpapi-events", "GET", SERPAPI, params=params, cache_ttl=3600)
    events = []
    for e in data.get("events_results", [])[:12]:
        venue = e.get("venue") or {}
        events.append({
            "title": e.get("title"),
            "when": (e.get("date") or {}).get("when"),
            "start_date": (e.get("date") or {}).get("start_date"),
            "venue": venue.get("name"),
            "address": ", ".join(e.get("address") or []),
            "url": e.get("link"),
            "snippet": (e.get("description") or "")[:300],
        })
    return {"ok": True, "source": "serpapi-google-events", "events": events}


def find_events(city: str, when: str = "today", topic: str = "") -> dict:
    """Search for real upcoming events. `when` in today|tomorrow|weekend|week|next_week|month or a date."""
    warnings = []
    try:
        res = _serpapi_events(city, when)
        if res and res["events"]:
            if topic:
                t = topic.lower()
                filtered = [e for e in res["events"] if t in (e["title"] or "").lower() or t in e["snippet"].lower()]
                res["events"] = filtered or res["events"]
            return res
    except ExternalAPIError as exc:
        warnings.append(str(exc))
    q = f"{topic + ' ' if topic else ''}events in {city} {when.replace('_', ' ')} tickets venue time"
    web = web_search(q, max_results=8)
    if web.get("ok"):
        return {"ok": True, "source": "tavily", "events": [
            {"title": r["title"], "url": r["url"], "snippet": r["snippet"], "when": None, "venue": None}
            for r in web["results"]], "warnings": warnings,
            "note": "Web results: extract the event name, venue and time ONLY if stated in the snippet."}
    warnings.append(web.get("error", "web search failed"))
    return {"ok": False, "events": [], "warnings": warnings}


def destination_brief(name: str) -> dict:
    """Short Wikipedia summary for a destination (used for trips)."""
    try:
        data = request_json("wikipedia", "GET", WIKI_SUMMARY.format(title=quote(name.replace(" ", "_"))),
                            cache_ttl=86400)
    except ExternalAPIError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "title": data.get("title"), "summary": (data.get("extract") or "")[:700],
            "url": ((data.get("content_urls") or {}).get("desktop") or {}).get("page"), "source": "wikipedia"}
