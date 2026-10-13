# LifePlan AI — a multi-agent AI personal planner

**Five LLM agents work together to plan your time, activities and travel.** You can ask for anything from *"I have three free hours tonight"* to *"plan my 5-day Tokyo vacation"*. The agents check your calendar and routine, find real places and events near you, measure real travel times, build an optimised schedule, criticise each other's work, resolve conflicts and re-plan when something changes.

> CA3 project, Agentic AI (Autonomous AI Systems & Agent-Based Computing). Built with **LangGraph**, **LangChain**, **FastAPI** and real-world APIs.

![architecture](docs/architecture.png)

| | |
|---|---|
| **Synopsis** | [docs/SYNOPSIS.md](docs/SYNOPSIS.md) |
| **Design document** (architecture, agent roles, orchestration, tech choices) | [docs/DESIGN.md](docs/DESIGN.md) |
| **Input / output definitions** with real samples | [docs/IO_DEFINITIONS.md](docs/IO_DEFINITIONS.md) |
| **Execution traces** of real runs | [samples/traces/](samples/traces/) |

---

## The five agents

| Agent | Responsibility | Tools (real APIs / solvers) | Default model |
|---|---|---|---|
| 🤖 **Adaptive Coordinator** | Main decision-maker. Each turn it reads the shared blackboard and chooses one action: delegate a task, call a review round, or finalise. It resolves conflicts between agents and handles re-planning. | `delegate`, `request_reviews`, `finalize` (+ guardrails) | Gemini Flash |
| 🧠 **Personal Profile & Availability** | Learns the user's weekly routine from calendar history, or uses manually entered hours. Computes free windows including commute, applies explicit overrides, stores preferences, and objects to plans that clash with the schedule. | ICS parser (RRULE), routine learner, availability, Nager.Date holidays, preference memory | Gemini Flash-Lite |
| 📍 **Discovery & Location** | Finds real restaurants, cafés, attractions, parks, nightlife and events near the selected location. Owns the weather picture and acts as the grounding guard: events need a live source URL and venues need a map id. | Geoapify Places → OSM Overpass, Tavily / SerpApi events, Open-Meteo, Wikipedia | Gemini Flash-Lite |
| 🚗 **Travel & Logistics** | Picks travel modes, measures real road-network travel times, prices legs, checks feasibility and budget, and handles inter-city transport, flights, hotels and currency. | OSRM / Geoapify routing & matrix, SerpApi Google Flights & Hotels, Frankfurter FX | Gemini Flash-Lite |
| 📅 **Planner & Optimization** | Decides priorities and trade-offs, then builds and revises the itinerary, optimising time + distance + cost + preferences + opening hours + duration. Consults Logistics and Discovery directly when it needs to. | schedule solver, constraint checks, `consult_logistics`, `consult_discovery` | Gemini Flash |

Each specialist is a **LangGraph ReAct subgraph** (`think → act → think …`) with its own tools and model. The Coordinator is the supervisor node of the main `StateGraph`. All agents share one **blackboard** (the graph state). That state also carries the inter-agent message channel, the reviews and the conflict log.

### How they coordinate (not a fixed pipeline)

* **The LLM picks the order.** A "what can I do right now?" request may skip Logistics entirely. A trip needs inter-city legs and a hotel before planning. The Coordinator decides from what is on the blackboard.
* **Conflicts are detected and resolved.** After every draft the agents' constraint checks run (calendar → Profile, travel and budget → Logistics, opening hours, weather and grounding → Discovery). The Coordinator sees each conflict, names the facts behind it and sends a concrete fix to the right agent.
* **Agents critique each other.** In a review round, Profile, Discovery and Logistics review the draft *in parallel* (LangGraph `Send` fan-out). Each returns APPROVE or REVISE with specific swaps. Discovery can even find a replacement venue that is open at that time.
* **Agents talk peer to peer.** The Planner can ask Logistics ("is a cab or the metro faster from X to Y at 18:00?") or Discovery ("find an indoor dessert place open after 21:00") without going through the Coordinator.
* **Plans adapt to events.** When it rains, you're running late, a venue closes or a meeting is extended, the plan is re-planned with that change. The Coordinator changes only what the event affects.

**The worked conflict example** from the proposal is the *"After a meeting (conflict demo)"* scenario, and an automated test checks it end to end (`tests/test_graph_e2e.py::test_conflict_is_detected_and_resolved_by_replanning`). Discovery finds a comedy show at 19:00. Profile reports a meeting until 19:30 plus 45 minutes of commute. Logistics reports the venue is a 40-minute ride away. The Coordinator rejects the draft that contains the show and sends the Planner back with the conflicting facts. The Planner rebuilds the evening, for example with a later show or a venue near the office.

### Robustness

* Every tool call is wrapped. A failing API returns `{"ok": false, "error": ...}` to the agent, which can try another approach, and the trace records it.
* **API fallbacks:** Geoapify → OpenStreetMap (Nominatim/Overpass/OSRM) → a clearly labelled estimate.
* **LLM fallbacks:** if the agent's model is rate-limited or down, the call moves to the next provider (Gemini → Groq). If no LLM is reachable, each agent has a deterministic fallback, so you still get a plan (status `partial`, flagged in warnings).
* **Bad input** gets HTTP 422 with a readable reason (unknown place, end before start, trip without a destination, etc.).
* **Guardrails:** a step budget, a revision limit, no finalising without a draft, and at most two review rounds.

---

## Run it locally (Windows / macOS / Linux)

Requires Python 3.10+.

```bash
git clone https://github.com/yashpatelSIT/lifeplan-ai.git
cd lifeplan-ai
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env        # macOS/Linux: cp .env.example .env   — then paste your keys
python scripts/check_keys.py  # verifies every key with one tiny request
uvicorn lifeplan.api:app --reload
```

Open <http://127.0.0.1:8000>, pick a demo scenario and press **Plan it**. The **Agents (live)** tab streams every message, tool call and decision. API docs are at <http://127.0.0.1:8000/docs>.

Command line (saves the execution trace as JSON + Markdown):

```bash
python scripts/run_demo.py samples/request_1_evening_conflict.json --out samples/traces
python scripts/run_demo.py samples/request_1_evening_conflict.json --replan weather "Heavy rain just started"
```

### API keys

| Key | Needed? | Free tier | Get it |
|---|---|---|---|
| `GOOGLE_API_KEY` (Gemini) | one LLM key required | yes | <https://aistudio.google.com/apikey> |
| `GROQ_API_KEY` | recommended as fallback | yes | <https://console.groq.com/keys> |
| `GEOAPIFY_API_KEY` | recommended | 3,000 req/day | <https://myprojects.geoapify.com> |
| `TAVILY_API_KEY` | recommended (live events) | 1,000/month | <https://app.tavily.com> |
| `SERPAPI_API_KEY` | optional (live flight/hotel prices, events) | 250/month | <https://serpapi.com> |
| Open-Meteo, Nominatim, Overpass, OSRM, Frankfurter, Nager.Date, Wikipedia | no key | — | — |

You can choose a model for each agent with `LLM_COORDINATOR=gemini:gemini-flash-latest`, `LLM_DISCOVERY=groq:openai/gpt-oss-20b`, and so on. See `.env.example`.

## Deploy (live Project URL)

**Render (free):** push to GitHub → render.com → **New → Blueprint** → choose this repo. `render.yaml` sets up everything; paste your keys when prompted. Health check: `/api/health`. Free instances sleep after 15 minutes idle, so open the URL a minute before the demo.
Docker works anywhere: `docker build -t lifeplan . && docker run -p 8000:8000 --env-file .env lifeplan`.

## Tests & CI

```bash
pip install -r requirements-dev.txt
pytest          # 41 offline tests: scripted tool-calling LLMs + fake HTTP, no keys needed
ruff check .
```

The suite runs the **whole multi-agent graph** end to end. It covers the conflict-resolution loop, LLM outage (degraded mode), provider fallback, API fallback to OpenStreetMap, re-planning after a schedule change, API validation and streaming, plus unit tests for opening hours, calendar/routine learning, the scheduler and the validators.
GitHub Actions runs CI on every push. The manual **Live agent run** workflow executes the real agents with repository secrets and uploads the execution trace.

## Repository layout

```
lifeplan/
  agents/            coordinator.py, profile_agent.py, discovery_agent.py, logistics_agent.py,
                     planner_agent.py, base.py (ReAct subgraph runtime), shared.py
  tools/             geo, places, routing, weather, search, travel, calendar, holidays,
                     opening_hours, scheduler (solver), validator (constraint checks), http
  graph.py           LangGraph StateGraph wiring (supervisor, workers, parallel reviews, finalize)
  service.py         request set-up, run, re-plan, result packaging
  llm.py             per-agent models, rate limiting, cross-provider fallback
  trace.py           execution trace recorder (live stream + JSON/Markdown export)
  memory.py          profile store (learned preferences) and plan store (for re-planning)
  api.py             FastAPI app (REST + SSE) · schemas.py (Pydantic I/O contract)
web/                 single-page UI (live agent feed, timeline, map, re-plan, feedback)
samples/             demo requests, demo calendar (.ics), real execution traces
tests/               offline end-to-end + unit tests
docs/                synopsis, design document, I/O definitions, architecture diagram
scripts/             check_keys.py, run_demo.py, generate_sample_calendar.py
```

## Limitations (honest notes)

* Train and bus fares/times are distance-based estimates, because there is no free official rail API for most countries. Flight and hotel prices are live only with a SerpApi key; otherwise they are labelled estimates.
* Event times come from live web search. The Discovery agent only registers an event when its URL came from a search result, but always check the ticket page.
* Free-tier LLM rate limits can slow a run down (30–120 s is typical). The client-side rate limiter and provider fallback keep it working.
* Plans and profiles are stored on the server's local disk. On free hosting this resets on redeploy.

Data © OpenStreetMap contributors, Geoapify, Open-Meteo, Tavily, SerpApi, ECB (via Frankfurter).
