"""FastAPI app: REST + Server-Sent-Events API and the web UI.

Run locally:  uvicorn lifeplan.api:app --reload      (then open http://127.0.0.1:8000)
Interactive API docs: /docs
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import queue
from typing import Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from lifeplan import __version__, service
from lifeplan.config import ROOT_DIR, get_settings
from lifeplan.samples import load_samples
from lifeplan.schemas import FeedbackRequest, PlanRequest, PlanResult, ReplanEvent
from lifeplan.trace import to_markdown

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
WEB_DIR = ROOT_DIR / "web"

app = FastAPI(
    title="LifePlan AI",
    version=__version__,
    description="A multi-agent AI personal planner for time, activities and travel. Five LangGraph agents "
                "(Coordinator, Profile & Availability, Discovery & Location, Travel & Logistics, Planner & "
                "Optimization) collaborate over a shared blackboard using real-world APIs.",
)


@app.exception_handler(service.PlanningError)
async def planning_error(_: Request, exc: service.PlanningError):
    return JSONResponse(status_code=422, content={"error": "planning_error", "detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    problems = [f"{'.'.join(str(p) for p in e['loc'][1:]) or 'body'}: {e['msg']}" for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": "invalid_request", "detail": problems})


def _require_llm(req: Optional[PlanRequest] = None) -> None:
    if req and (req.dev_mock or get_settings().dev_mock_planning):
        return
    if not get_settings().available_providers:
        raise HTTPException(503, "No LLM provider configured. Set GOOGLE_API_KEY and/or GROQ_API_KEY.")


# ------------------------------------------------------------------ meta
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, **get_settings().public_summary()}


@app.get("/api/samples")
def samples() -> list[dict]:
    """Ready-made demo requests (relative dates resolved to the coming days)."""
    return load_samples()


@app.post("/api/extract-requirements")
def extract_requirements(payload: dict) -> dict:
    """Extract structured planning requirements and canonical schedule from natural language."""
    from lifeplan.tools.nl_extractor import parse_natural_language_requirements
    query = payload.get("query", "")
    now_str = payload.get("now")
    now = datetime.fromisoformat(now_str) if now_str else datetime.now()
    picker_start = datetime.fromisoformat(payload["start"]) if payload.get("start") else None
    picker_end = datetime.fromisoformat(payload["end"]) if payload.get("end") else None
    return parse_natural_language_requirements(
        query=query,
        now=now,
        picker_start=picker_start,
        picker_end=picker_end,
    )


@app.get("/api/calendar/detect")
def detect_calendar(tz: str = "Asia/Kolkata") -> dict:
    """Automatically detect whether a local calendar file or synced feed is active."""
    from lifeplan.tools import calendar as cal

    ics_text, src_type, src_name = cal.detect_local_calendar()
    if not ics_text:
        return {
            "detected": False,
            "source_type": None,
            "source_name": None,
            "events_count": 0,
            "upcoming_events": [],
        }
    now = datetime.now()
    events = cal.parse_ics(ics_text, tz, now - timedelta(days=1), now + timedelta(days=7))
    return {
        "detected": True,
        "source_type": src_type,
        "source_name": src_name,
        "events_count": len(events),
        "upcoming_events": [
            {
                "title": e.get("title", "Busy"),
                "start": e["start"].strftime("%Y-%m-%dT%H:%M"),
                "end": e["end"].strftime("%Y-%m-%dT%H:%M"),
                "location": e.get("location"),
            }
            for e in events[:12]
        ],
    }


@app.post("/api/calendar/connect")
async def connect_calendar(request: Request) -> dict:
    """Connect a Google Calendar, Apple iCloud Calendar, or WebCal feed URL."""
    from lifeplan.tools import calendar as cal

    body = await request.json()
    url = body.get("feed_url", "").strip()
    ok, msg = cal.save_calendar_feed(url)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


# ------------------------------------------------------------------ synchronous API
@app.post("/api/plan", response_model=PlanResult)
def plan(req: PlanRequest) -> PlanResult:
    """Run the full multi-agent planning process and return the final plan (may take 30–120 s)."""
    _require_llm(req)
    return service.run_plan(req)


@app.post("/api/replan", response_model=PlanResult)
def replan(ev: ReplanEvent) -> PlanResult:
    """Adapt an existing plan to a real-world change (rain, delay, cancellation, schedule change...)."""
    _require_llm()
    return service.replan(ev)


# ------------------------------------------------------------------ streaming API (used by the UI)
@app.post("/api/runs")
def start_run(req: PlanRequest) -> dict:
    _require_llm(req)
    trace = service.runs.start(lambda tr: service.run_plan(req, tr))
    return {"run_id": trace.run_id}


@app.post("/api/runs/replan")
def start_replan(ev: ReplanEvent) -> dict:
    _require_llm()
    if not service.plan_store.get(ev.plan_id):
        raise HTTPException(404, f"unknown plan_id {ev.plan_id}")
    trace = service.runs.start(lambda tr: service.replan(ev, tr), kind="replan", plan_id=ev.plan_id)
    return {"run_id": trace.run_id}


@app.get("/api/runs/{run_id}")
def run_status(run_id: str) -> dict:
    rec = service.runs.get(run_id)
    if not rec:
        raise HTTPException(404, "unknown run")
    result = rec["result"].model_dump(mode="json") if rec["result"] else None
    return {"run_id": run_id, "status": rec["status"], "error": rec["error"], "result": result,
            "stats": rec["trace"].stats}


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str) -> StreamingResponse:
    """Server-Sent Events: every trace event (agent messages, tool calls, LLM calls, conflicts) as it happens."""
    rec = service.runs.get(run_id)
    if not rec:
        raise HTTPException(404, "unknown run")
    q = rec["trace"].subscribe()

    def stream() -> Iterator[str]:
        while True:
            try:
                ev = q.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if ev is None:
                yield f"event: done\ndata: {json.dumps({'status': rec['status'], 'error': rec['error']})}\n\n"
                return
            yield f"data: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}/trace")
def run_trace(run_id: str, format: str = "json"):  # noqa: A002
    rec = service.runs.get(run_id)
    if rec:
        data = rec["trace"].to_dict()
    else:
        path = get_settings().trace_dir / f"{run_id}.json"
        if not path.exists() or "/" in run_id or ".." in run_id:
            raise HTTPException(404, "unknown run")
        data = json.loads(path.read_text(encoding="utf-8"))
    if format == "md":
        return PlainTextResponse(to_markdown(data), media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="{run_id}.md"'})
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'})


# ------------------------------------------------------------------ memory
@app.post("/api/feedback")
def feedback(fb: FeedbackRequest) -> dict:
    """Rate a plan; liked/disliked categories update the learned preference weights of the profile."""
    rec = service.plan_store.get(fb.plan_id)
    if not rec:
        raise HTTPException(404, "unknown plan_id")
    profile_id = rec["state"]["request"].get("profile_id", "demo")
    prof = service.profile_store.learn_from_feedback(profile_id, fb.rating, fb.liked, fb.disliked, fb.comment)
    return {"ok": True, "learned_weights": prof.get("learned_weights"), "dislikes": prof.get("dislikes")}


@app.get("/api/profile/{profile_id}")
def get_profile(profile_id: str) -> dict:
    return service.profile_store.get(profile_id)


# ------------------------------------------------------------------ web UI
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    page = WEB_DIR / "index.html"
    if page.exists():
        return FileResponse(page)
    return {"message": "LifePlan AI API — see /docs"}
