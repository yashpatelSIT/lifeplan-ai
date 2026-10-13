"""Test doubles: a fake HTTP session returning realistic API payloads, and a scripted chat model
that behaves like a tool-calling LLM. They let the whole multi-agent graph run offline in CI."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

# --------------------------------------------------------------------------- HTTP

PUNE = (18.5308, 73.8475)

PLACES = {
    "catering.restaurant": [("Vaishali", 18.5206, 73.8411, "Mo-Su 07:00-23:00"),
                            ("Shabree Restaurant", 18.5236, 73.8418, "Mo-Su 11:00-15:30,19:00-23:00")],
    "catering.cafe": [("Cafe Goodluck", 18.5175, 73.8410, "Mo-Su 07:00-23:30")],
    "entertainment.museum": [("Raja Dinkar Kelkar Museum", 18.5100, 73.8560, "Mo-Su 10:00-17:30")],
    "entertainment.culture": [("Bal Gandharva Rang Mandir", 18.5236, 73.8490, None)],
    "leisure.park": [("Sambhaji Park", 18.5199, 73.8462, "Mo-Su 06:00-21:00")],
    "catering.bar": [("High Spirits", 18.5410, 73.8890, "Mo-Su 19:00-01:00")],
}


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Routes requests by URL to canned payloads. `fail` = set of service substrings that return HTTP 503."""

    def __init__(self, fail: Optional[set] = None):
        self.fail = fail or set()
        self.calls: list[tuple[str, str]] = []
        self.headers = {}

    def request(self, method, url, params=None, json=None, data=None, headers=None, timeout=None):  # noqa: A002
        self.calls.append((method, url))
        for f in self.fail:
            if f in url:
                return FakeResponse({"error": "down"}, 503)
        params = params or {}
        if "geoapify.com/v1/geocode" in url:
            text = params.get("text", "")
            if "nowhere" in text.lower():
                return FakeResponse({"results": []})
            return FakeResponse({"results": [{"formatted": f"{text}, Maharashtra, India", "lat": PUNE[0], "lon": PUNE[1],
                                              "city": "Pune", "country": "India", "country_code": "in",
                                              "timezone": {"name": "Asia/Kolkata"}}]})
        if "nominatim" in url:
            if "nowhere" in str(params.get("q", "")).lower():
                return FakeResponse([])
            return FakeResponse([{"display_name": "Pune, India", "lat": str(PUNE[0]), "lon": str(PUNE[1]),
                                  "address": {"city": "Pune", "country": "India", "country_code": "in"}}])
        if "geoapify.com/v2/places" in url:
            feats = []
            for cat in params["categories"].split(","):
                for i, (name, lat, lon, hours) in enumerate(PLACES.get(cat, [])):
                    feats.append({"properties": {"name": name, "lat": lat, "lon": lon, "place_id": f"{cat}-{i}",
                                                 "formatted": f"{name}, Pune", "opening_hours": hours,
                                                 "distance": 900 + 300 * i,
                                                 "datasource": {"raw": {"osm_type": "n", "osm_id": 1000 + i}}}})
            return FakeResponse({"features": feats})
        if "overpass" in url:
            return FakeResponse({"elements": [{"type": "node", "id": 1, "lat": 18.52, "lon": 73.85,
                                               "tags": {"amenity": "restaurant", "name": "OSM Diner",
                                                        "opening_hours": "Mo-Su 10:00-23:00"}}]})
        if "/table/v1/" in url:
            coords = url.split("/driving/")[1].split("?")[0].split(";")
            n = len(coords)
            return FakeResponse({"durations": [[0 if i == j else 600 + 60 * abs(i - j) for j in range(n)] for i in range(n)],
                                 "distances": [[0 if i == j else 3000 + 500 * abs(i - j) for j in range(n)] for i in range(n)]})
        if "/route/v1/" in url:
            return FakeResponse({"routes": [{"duration": 900, "distance": 5000}]})
        if "geoapify.com/v1/routing" in url:
            return FakeResponse({"features": [{"properties": {"time": 840, "distance": 4800}}]})
        if "geoapify.com/v1/routematrix" in url:
            raise AssertionError("tests expect OSRM table first")
        if "open-meteo.com" in url:
            day = params["start_date"]
            hours = [f"{day}T{h:02d}:00" for h in range(24)]
            return FakeResponse({
                "timezone": "Asia/Kolkata",
                "hourly": {"time": hours, "temperature_2m": [26] * 24, "precipitation_probability": [10] * 20 + [80] * 4,
                           "precipitation": [0] * 20 + [2] * 4, "weather_code": [1] * 20 + [63] * 4,
                           "wind_speed_10m": [5] * 24},
                "daily": {"time": [day], "weather_code": [63], "temperature_2m_max": [30], "temperature_2m_min": [21],
                          "precipitation_sum": [8], "precipitation_probability_max": [80],
                          "sunrise": [f"{day}T06:20"], "sunset": [f"{day}T18:05"]}})
        if "tavily" in url:
            return FakeResponse({"results": [{"title": "Stand-up comedy night - Pune", "url": "https://example.com/comedy",
                                              "content": "Stand-up comedy at The Box, Pune. Wed 19:00-20:30. Tickets 499."}]})
        if "frankfurter" in url:
            return FakeResponse({"date": "2026-10-13", "rates": {params.get("symbols"): 0.012}})
        if "date.nager.at" in url:
            return FakeResponse([{"date": "2026-10-02", "localName": "Gandhi Jayanti"}])
        if "wikipedia" in url:
            return FakeResponse({"title": "Pune", "extract": "Pune is a city in Maharashtra.",
                                 "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Pune"}}})
        if "restcountries" in url:
            return FakeResponse({"name": {"common": "India"}, "currencies": {"INR": {}}, "timezones": ["UTC+05:30"]})
        return FakeResponse({"error": f"unexpected url {url}"}, 404)


# --------------------------------------------------------------------------- LLM

Responder = Callable[[str, list[str], list[BaseMessage]], AIMessage]
_counter = {"n": 0}


def call(name: str, **args) -> dict:
    _counter["n"] += 1
    return {"name": name, "args": args, "id": f"call_{_counter['n']}", "type": "tool_call"}


class ScriptedChatModel(BaseChatModel):
    """A chat model whose 'reasoning' is a Python function of (agent, tool names, messages)."""

    agent: str
    responder: Any
    tool_names: list = []
    fail: bool = False

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, tool_choice=None, **kwargs):
        names = [getattr(t, "name", None) or t.get("name") for t in tools]
        return self.model_copy(update={"tool_names": names})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.fail:
            raise RuntimeError("429 rate limit (simulated)")
        msg = self.responder(self.agent, list(self.tool_names), messages)
        msg.usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _tool_results(messages: list[BaseMessage]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for m in messages:
        if isinstance(m, ToolMessage):
            try:
                out.setdefault(m.name, []).append(json.loads(m.content))
            except json.JSONDecodeError:
                out.setdefault(m.name, []).append({"raw": m.content})
    return out


def _human(messages) -> str:
    return "\n".join(m.content for m in messages if isinstance(m, HumanMessage) and isinstance(m.content, str))


def default_responder(agent: str, tools: list[str], messages: list[BaseMessage]) -> AIMessage:
    done = _tool_results(messages)
    text = _human(messages)
    if agent == "coordinator":
        situation = json.loads(text.split("Current situation:\n", 1)[1])
        board = situation["blackboard"]
        reviews = situation["latest_reviews"]
        if board["availability"] == "not computed":
            return AIMessage("", tool_calls=[call("delegate", agent="profile", reason="need free time",
                                                  instruction="Learn routine and compute free windows.")])
        if board["candidates"] == "none yet":
            return AIMessage("", tool_calls=[call("delegate", agent="discovery", reason="need options",
                                                  instruction="Weather + venues + events matching interests.")])
        if board["logistics"] == "none" or not board["logistics"].get("matrix"):
            return AIMessage("", tool_calls=[call("delegate", agent="logistics", reason="need travel times",
                                                  instruction="Pick mode and prepare travel matrix.")])
        if board["draft_plan"] == "none":
            return AIMessage("", tool_calls=[call("delegate", agent="planner", reason="ready to plan",
                                                  instruction="Build the schedule.")])
        val = board["validation"]
        if val.get("high") and board["revisions"] < 2:
            return AIMessage("", tool_calls=[call("delegate", agent="planner", reason="conflicts",
                                                  instruction="Fix: " + "; ".join(val["issues"])[:300])])
        if not reviews and not any("review_request" in m for m in situation["recent_messages"]):
            return AIMessage("", tool_calls=[call("request_reviews", focus="check the draft", reason="verify")])
        return AIMessage("", tool_calls=[call("finalize", summary="Here is your evening plan.", reason="no high issues")])

    if "check_calendar_conflicts" in tools and "learn_routine" not in tools:  # profile review
        if "check_calendar_conflicts" not in done:
            return AIMessage("", tool_calls=[call("check_calendar_conflicts")])
        return AIMessage("APPROVE - no calendar conflicts.")
    if "check_travel_and_budget" in tools:
        if "check_travel_and_budget" not in done:
            return AIMessage("", tool_calls=[call("check_travel_and_budget")])
        return AIMessage("APPROVE - travel feasible.")
    if "check_venues" in tools:
        if "check_venues" not in done:
            return AIMessage("", tool_calls=[call("check_venues")])
        return AIMessage("APPROVE - venues verified.")

    if agent == "profile":
        if "learn_routine" not in done:
            return AIMessage("", tool_calls=[call("learn_routine", weeks_back=4)])
        if "compute_availability" not in done:
            return AIMessage("", tool_calls=[call("compute_availability")])
        return AIMessage("Free after the meeting + commute.")
    if agent == "discovery":
        if "get_weather" not in done:
            return AIMessage("", tool_calls=[call("get_weather")])
        if "search_venues" not in done:
            return AIMessage("", tool_calls=[call("search_venues", interests=["food", "cafe", "museum"], radius_km=5)])
        if "find_events" not in done:
            return AIMessage("", tool_calls=[call("find_events", when="today", topic="comedy")])
        if "register_event" not in done:
            d = re.search(r'"now": "(\d{4}-\d{2}-\d{2})', text)
            day = d.group(1) if d else "2026-10-14"
            return AIMessage("", tool_calls=[call("register_event", title="Stand-up comedy night", venue="The Box",
                                                  start=f"{day}T19:00", end=f"{day}T20:30",
                                                  url="https://example.com/comedy", est_cost=499)])
        return AIMessage("Found restaurants, a cafe, a museum and a comedy show at 19:00.")
    if agent == "logistics":
        if "choose_travel_mode" not in done:
            return AIMessage("", tool_calls=[call("choose_travel_mode", mode="cab", reason="evening, short on time")])
        if "prepare_travel_matrix" not in done:
            ids = re.findall(r'"id": ?"((?:geo|osm|evt):[^"]+)"', text)
            return AIMessage("", tool_calls=[call("prepare_travel_matrix", candidate_ids=ids[:15])])
        return AIMessage("Cab chosen; matrix ready.")
    if agent == "planner":
        if "list_candidates" not in done:
            return AIMessage("", tool_calls=[call("list_candidates")])
        if "build_schedule" not in done or len(done["build_schedule"]) < 1:
            ids = [c["id"] for c in done["list_candidates"][0]["candidates"]]
            pri = [f"{i}:{0.9 if i.startswith('evt:') else 0.6}" for i in ids]
            must = [i for i in ids if i.startswith("evt:")]
            return AIMessage("", tool_calls=[call("build_schedule", priorities=pri, must_include=must)])
        return AIMessage("Draft built.")
    return AIMessage("ok")


def fake_factory(responder: Responder = default_responder, failing: Optional[set] = None):
    failing = failing or set()

    def factory(spec: str) -> ScriptedChatModel:
        provider, _, agent = spec.partition(":")
        return ScriptedChatModel(agent=agent.split("/")[0], responder=responder, fail=spec in failing)

    return factory
