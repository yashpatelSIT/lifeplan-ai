# LifePlan AI — Input / Output Definitions

The contract is defined by the Pydantic models in [`lifeplan/schemas.py`](../lifeplan/schemas.py) and validated on every request. The full JSON Schemas are generated into [`docs/schemas/`](schemas/) by `python scripts/export_schemas.py`. They are also served live at `/openapi.json`, and `/docs` gives an interactive version.

## 1. Input — `PlanRequest` (`POST /api/plan`, `POST /api/runs`)

| Field | Type | Required | Meaning / constraints |
|---|---|---|---|
| `query` | string (3–1500) | ✅ | Natural-language request, e.g. "I'm free after my meeting, want comedy + dinner" |
| `mode` | enum | – | `auto` (default; inferred from the query), `now`, `free_time`, `evening`, `day`, `weekend`, `local_outing`, `explore`, `domestic_trip`, `international_trip`, `business_trip` |
| `location` | string ≤200 | ✅ (or `coordinates`) | Where the user is / starts from (geocoded) |
| `coordinates` | `{lat, lon}` | – | Exact position (e.g. browser geolocation) |
| `destination` | string ≤200 | trips only | Trip destination (required for `*_trip` modes) |
| `start`, `end` | ISO local datetime | – | Planning window. Defaults depend on `mode`. `end` must be after `start`, max 21 days |
| `budget` | `{amount > 0, currency: ISO-4217}` | – | Total budget for the plan |
| `interests` | string[] ≤20 | – | Free text ("comedy", "parks", "street food"…), mapped to venue categories. Defaults to profile interests |
| `travel_modes` | enum[] | – | Allowed modes: `walk`, `transit`, `cab`, `drive`, `bike`, `train`, `flight` (default walk/transit/cab) |
| `party_size` | int 1–20 | – | Number of people (scales costs) |
| `pace` | `relaxed` \| `balanced` \| `packed` | – | Buffer between stops: 20 / 10 / 5 min |
| `busy_blocks` | `[{start, end, title, location?}]` ≤200 | – | Manual availability input (always overrides predictions) |
| `calendar_ics` | string (≤500 kB) | – | Raw `.ics` export (Google/Outlook), RRULEs supported |
| `use_learned_schedule` | bool | – | Let the Profile agent predict busy time from calendar history (default true) |
| `profile_id` | `[A-Za-z0-9_-]` ≤64 | – | Which stored profile/preferences to use (default `demo`) |
| `timezone` | IANA tz | – | Defaults to the location's time zone |
| `now` | ISO datetime | – | Override "current time" (tests/demos) |

### Real sample input

From [`samples/request_1_evening_conflict.json`](../samples/request_1_evening_conflict.json), with relative times resolved:

```json
{
  "query": "I'm stuck in a project meeting at the office in Hinjewadi for the next 90 minutes. After that I want to catch a live comedy or music show and have a good dinner. Budget ₹1500.",
  "mode": "free_time",
  "location": "Shivajinagar, Pune",
  "start": "2026-10-13T17:00",
  "end": "2026-10-13T22:30",
  "interests": ["comedy", "live music", "food"],
  "budget": {"amount": 1500, "currency": "INR"},
  "travel_modes": ["walk", "transit", "cab"],
  "busy_blocks": [
    {"start": "2026-10-13T17:00", "end": "2026-10-13T18:30", "title": "Project meeting (office)", "location": "Hinjewadi, Pune"}
  ],
  "pace": "balanced",
  "party_size": 1,
  "use_learned_schedule": true
}
```

Other real inputs: [right now](../samples/request_2_right_now.json) · [weekend](../samples/request_3_weekend.json) · [day in Mumbai](../samples/request_4_day_mumbai.json) · [Goa trip](../samples/request_5_goa_trip.json) · [business trip](../samples/request_6_business_bengaluru.json) · [Tokyo vacation](../samples/request_7_tokyo.json).

### Re-planning input — `ReplanEvent` (`POST /api/replan`, `POST /api/runs/replan`)

| Field | Type | Meaning |
|---|---|---|
| `plan_id` | string | Plan to adapt |
| `event` | `weather` \| `delay` \| `cancellation` \| `schedule_change` \| `user_feedback` \| `closed` | What happened |
| `details` | string 3–1000 | Description ("Heavy rain started", "meeting runs until 20:30") |
| `item_index` | int? | Affected itinerary item (index across all days) |
| `new_busy_block` | `BusyBlock?` | New commitment (for `delay` / `schedule_change`) |

```json
{"plan_id": "plan_3f9c0a1b2d", "event": "weather", "details": "Heavy rain just started", "item_index": 3}
```

### Feedback input — `FeedbackRequest` (`POST /api/feedback`)

`{"plan_id": "...", "rating": 1-5, "liked": ["food"], "disliked": ["nightlife"], "comment": "..."}`. This updates the profile's learned preference weights.

## 2. Output — `PlanResult`

| Field | Type | Meaning |
|---|---|---|
| `plan_id`, `run_id` | string | Plan identifier (stable across re-plans) / this run's trace id |
| `status` | `ok` \| `partial` \| `failed` | `ok` = no high-severity conflicts; `partial` = trade-offs remain or degraded mode; `failed` = nothing feasible |
| `mode`, `timezone` | string | Resolved plan type and time zone |
| `summary` | string | Coordinator's user-facing explanation, including the trade-offs it made |
| `days[]` | `{date, items[]}` | The itinerary, see below |
| `totals` | object | `cost`, `currency`, `budget`, `travel_minutes`, `activity_minutes`, `stops` |
| `weather` | object? | `kind` (`forecast` / `historical-same-dates-last-year`), `daily[]`, `wet_hours[]` |
| `conflicts[]` | `{type, description, raised_by[], resolution}` | Every high-severity conflict the agents met and how it was resolved (or `UNRESOLVED`) |
| `warnings[]` | string[] | Remaining medium/low issues, estimate notices, degraded-mode notice |
| `alternatives[]` | `{id, name, category, distance_km, opening_hours, url}` | Good options that did not fit |
| `sources[]` | string[] | Data sources (OSM ids / URLs) of the scheduled places |
| `stats` | object | `llm_calls`, `tool_calls`, `messages`, tokens, `llm_fallbacks`, `tool_errors`, `coordinator_steps`, `planner_revisions`, `review_rounds`, `agents`, `duration_s` |

**Itinerary item** (`days[].items[]`):

| Field | Meaning |
|---|---|
| `start`, `end` | Local ISO datetime (`YYYY-MM-DDTHH:MM`) |
| `kind` | `activity`, `meal`, `snack`, `event`, `travel`, `hotel`, `flight`, `train`, `busy` (your commitments, for context) |
| `title` | Venue / event / leg name |
| `place` | `{name, lat, lon, address, source, source_id, url}`: where the place came from (grounding) |
| `candidate_id` | Id in the verified candidate pool (`geo:…`, `osm:node/…`, `evt:…`, `serp:…`) |
| `cost` | `{amount, currency, estimate}` |
| `travel` | For legs: `{mode, minutes, km, source}` (`osrm` / `geoapify` / `estimate`) |
| `notes` | Optional explanation |

### Real sample output

*Will be replaced with the output of a real live run.* The command `python scripts/run_demo.py samples/request_1_evening_conflict.json --out samples/traces` writes the full result to `samples/traces/<run_id>.result.json`. Shape (abridged):

```json
{
  "plan_id": "plan_…", "run_id": "run_…", "status": "ok", "mode": "free_time", "timezone": "Asia/Kolkata",
  "summary": "…",
  "days": [{"date": "2026-10-13", "items": [
    {"start": "2026-10-13T17:00", "end": "2026-10-13T18:30", "kind": "busy", "title": "Project meeting (office)"},
    {"start": "…", "end": "…", "kind": "travel", "title": "Cab to …", "travel": {"mode": "cab", "minutes": 16, "km": 3.5, "source": "osrm"}, "cost": {"amount": 123, "currency": "INR", "estimate": true}},
    {"start": "…", "end": "…", "kind": "meal", "title": "…", "candidate_id": "geo:…", "place": {"name": "…", "lat": 18.52, "lon": 73.84, "source": "geoapify", "source_id": "node/…"}, "cost": {"amount": 600, "currency": "INR", "estimate": true}}
  ]}],
  "totals": {"cost": 1163, "currency": "INR", "budget": 1500, "travel_minutes": 40, "activity_minutes": 120, "stops": 2},
  "conflicts": [{"type": "CALENDAR_CONFLICT", "description": "…", "raised_by": ["profile"], "resolution": "…"}],
  "stats": {"llm_calls": 31, "tool_calls": 24, "messages": 14, "coordinator_steps": 7, "planner_revisions": 1, "review_rounds": 1}
}
```

## 3. Execution trace (`GET /api/runs/{run_id}/trace`, `?format=md`)

```json
{"run_id": "run_…", "plan_id": "plan_…", "kind": "plan|replan", "duration_s": 74.2,
 "stats": {"llm_calls": 31, "tool_calls": 24, "messages": 14, "input_tokens": 0, "output_tokens": 0, "llm_fallbacks": 0, "tool_errors": 1},
 "events": [
   {"seq": 12, "ts": "2026-10-13T11:31:05.120+00:00", "t_ms": 8410, "type": "message", "agent": "coordinator",
    "content": "Compute the user's free windows…", "data": {"to": "profile", "kind": "request"}},
   {"seq": 15, "type": "tool_call", "agent": "profile", "content": "compute_availability",
    "data": {"args": {}, "result": {"free_windows": ["…"]}, "duration_ms": 12, "ok": true}},
   {"seq": 40, "type": "conflict", "agent": "profile", "content": "'Comedy night' (19:00–20:30) overlaps your 'Project meeting' …",
    "data": {"type": "CALENDAR_CONFLICT", "severity": "high"}}
 ]}
```

Event `type`s: `setup`, `message` (`data.kind`: request / report / review_request / critique / consult / consult_reply / event), `decision`, `agent_start`, `agent_end`, `llm_call`, `llm_error`, `tool_call`, `conflict`, `guardrail`, `degraded_mode`, `agent_error`, `final`, `result`, `error`.

## 4. Errors

| HTTP | When | Body |
|---|---|---|
| 422 | schema violation | `{"error": "invalid_request", "detail": ["end: Value error, end must be after start", …]}` |
| 422 | impossible request (unknown place, trip without destination, window in the past) | `{"error": "planning_error", "detail": "Could not find the location 'Nowhere-ville'…"}` |
| 404 | unknown `plan_id` / `run_id` | `{"detail": "unknown plan_id …"}` |
| 503 | no LLM key configured | `{"detail": "No LLM provider configured…"}` |
