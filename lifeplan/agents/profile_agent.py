"""🧠 Personal Profile & Availability Agent.

Knows the user: preferences, home/work, commute, budget habits, and — crucially — *when they are
actually free*. It learns the user's routine from calendar history (or uses manually entered
working hours), predicts likely-busy periods, lets explicit user input override predictions, and
critiques draft plans that collide with the user's schedule.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from lifeplan.agents.base import RunContext, Workspace, make_tool, run_react_agent
from lifeplan.agents.shared import request_brief, validate_state
from lifeplan.tools import calendar as cal
from lifeplan.tools import validator
from lifeplan.tools.holidays import public_holidays
from lifeplan.tools.scheduler import fmt, parse_dt

NAME = "profile"

SYSTEM = """You are the Personal Profile & Availability Agent in LifePlan AI, a multi-agent planner.
Your job: know the user and determine WHEN they are genuinely free.
- Use your tools; never invent calendar entries or preferences.
- Learn the routine from calendar history when it exists, then compute availability for the planning window.
- Explicit calendar events and user-entered busy blocks always override predictions.
- If the target day is a public holiday, consider ignoring the learned work/college routine.
- When reviewing a draft plan, check it against the user's schedule and say precisely what must move and why.
Finish with a short report (max 120 words) for the Coordinator: free windows, key constraints
(e.g. "meeting until 19:30, then 40 min commute"), and preferences the planner must respect."""

REVIEW = """You are the Personal Profile & Availability Agent reviewing a DRAFT plan.
Call check_calendar_conflicts, then reply with a verdict: APPROVE or REVISE, and for each problem a concrete fix
(e.g. "start the dinner after 20:10 because the meeting ends 19:30 + 40 min commute"). Max 100 words."""


def _ics_text(ws: Workspace) -> str | None:
    req = ws.get("request") or {}
    if req.get("calendar_ics"):
        return req["calendar_ics"]
    if req.get("use_learned_schedule", True) and req.get("profile_id", "demo") == "demo":
        return ws.ctx.sample_calendar  # demo user's historical calendar shipped in samples/
    return None


def _profile_work_blocks(profile: dict, start: datetime, end: datetime) -> list[dict]:
    wh = profile.get("work_hours") or {}
    if not wh:
        return []
    days = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
    wanted = {days[d] for d in wh.get("days", []) if d in days}
    out, d = [], start.date()
    while d <= end.date():
        if d.weekday() in wanted:
            s = datetime.combine(d, datetime.strptime(wh["start"], "%H:%M").time())
            e = datetime.combine(d, datetime.strptime(wh["end"], "%H:%M").time())
            out.append({"start": s, "end": e, "title": "Work/college hours (profile)", "confidence": 0.9,
                        "predicted": True})
        d += timedelta(days=1)
    return out


def build_tools(ws: Workspace, review: bool = False) -> list:
    ctx = ws.ctx
    req = ws.get("request") or {}
    tz = ws.get("timezone")

    def get_profile() -> dict:
        """Return the stored user profile: home/work locations, working hours, commute, interests, dislikes,
        dietary needs, default budget, preferred travel modes, learned preference weights."""
        p = dict(ws.get("profile") or {})
        p.pop("feedback", None)
        return {"ok": True, "profile": p}

    def learn_routine(weeks_back: int = 4) -> dict:
        """Learn the user's recurring weekly routine from their calendar history over the last `weeks_back`
        weeks. Returns predicted busy blocks per weekday with a confidence score and frequent locations."""
        ics = _ics_text(ws)
        if not ics:
            return {"ok": False, "error": "no calendar history available; rely on profile work_hours and user input"}
        now = ctx.now
        history = cal.parse_ics(ics, tz, now - timedelta(weeks=max(1, min(weeks_back, 12))), now)
        routine = cal.learn_routine(history)
        ws.set("routine", routine)
        return {"ok": True, "events_analysed": len(history), **routine}

    def compute_availability(start: str = "", end: str = "", min_minutes: int = 45,
                             use_prediction: bool = True) -> dict:
        """Compute the user's free windows between `start` and `end` (ISO local time, default = planning window)
        by combining calendar events, user-entered busy blocks, the learned routine (if use_prediction) and
        the commute time after work/college. Stores the result on the shared blackboard."""
        w = ws.get("window") or {}
        s = parse_dt(start or w["start"])
        e = parse_dt(end or w["end"])
        if e <= s:
            return {"ok": False, "error": "end must be after start"}
        ics = _ics_text(ws)
        events = cal.parse_ics(ics, tz, s - timedelta(days=1), e + timedelta(days=1)) if ics else []
        manual = [{"start": parse_dt(b["start"]), "end": parse_dt(b["end"]), "title": b.get("title", "Busy")}
                  for b in req.get("busy_blocks", [])]
        routine = ws.get("routine") if use_prediction and req.get("use_learned_schedule", True) else None
        profile = ws.get("profile") or {}
        # commute: after a work/college event the user still has to travel home
        commute = int(profile.get("commute_minutes") or 0)
        commute_blocks = []
        for ev in events + manual:
            text = f"{ev['title']} {ev.get('location') or ''}".lower()
            if commute and any(k in text for k in ("work", "office", "college", "class", "lecture", "meeting", "lab")):
                commute_blocks.append({"start": ev["end"], "end": ev["end"] + timedelta(minutes=commute),
                                       "title": f"Commute after {ev['title']}"})
        av = cal.availability(s, e, events, manual + commute_blocks, routine, min_minutes)
        if not ics and use_prediction:
            # manual mode: no calendar, so the profile's working hours are the prediction
            pred = _profile_work_blocks(profile, s, e)
            busy = [(parse_dt(b["start"]), parse_dt(b["end"])) for b in av["busy"]] + [(p["start"], p["end"]) for p in pred]
            av["predicted_busy"] = [{"start": fmt(p["start"]), "end": fmt(p["end"]), "title": p["title"],
                                     "confidence": p["confidence"]} for p in pred if p["end"] > s and p["start"] < e]
            av["free_windows"] = [{"start": fmt(a), "end": fmt(b), "minutes": int((b - a).total_seconds() // 60)}
                                  for a, b in cal.free_windows(busy, s, e, min_minutes)]
        ws.set("availability", av)
        return {"ok": True, **av}

    def check_holidays(country_code: str = "IN", year: int = 0) -> dict:
        """List public holidays for a country (ISO code) and year; holidays usually free the user from work."""
        y = year or ctx.now.year
        res = public_holidays(country_code, y)
        w = ws.get("window") or {}
        if res.get("ok") and w:
            s, e = w["start"][:10], w["end"][:10]
            res["in_window"] = [h for h in res["holidays"] if s <= h["date"] <= e]
            res["holidays"] = res["holidays"][:5]
        return res

    def remember_preference(key: str, value: str) -> dict:
        """Persist a preference the user stated (e.g. key='dietary', value='vegetarian'; key='dislikes',
        value='["nightlife"]'). Value may be JSON. Only use for things the user explicitly said."""
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed = value
        prof = ctx.profile_store.update_preference((ws.get("profile") or {}).get("profile_id", "demo"), key, parsed)
        ws.set("profile", prof)
        return {"ok": True, "saved": {key: parsed}}

    def check_calendar_conflicts() -> dict:
        """Review the current draft plan against the user's calendar, predicted routine and planning window."""
        av = ws.get("availability") or {}
        draft = ws.get("draft")
        if not draft:
            return {"ok": False, "error": "no draft plan to review"}
        issues = validator.check_calendar(draft, av.get("busy", []), av.get("predicted_busy", []), ws.get("window"))
        return {"ok": True, "issues": issues, "free_windows": av.get("free_windows")}

    if review:
        return [make_tool(check_calendar_conflicts), make_tool(compute_availability)]
    return [make_tool(f) for f in (get_profile, learn_routine, compute_availability, check_holidays,
                                   remember_preference, check_calendar_conflicts)]


def run(state: dict, ctx: RunContext, instruction: str, review: bool = False) -> tuple[dict, str]:
    ws = Workspace(dict(state), ctx)
    tools = build_tools(ws, review)
    prompt = (f"Request: {request_brief(state)}\nCurrent time: {state.get('now')}\n\n"
              f"Task from {('Coordinator (review round)' if review else 'Coordinator')}: {instruction}")
    if review:
        prompt += f"\nCurrent validation summary: {json.dumps((validate_state(state) or {}).get('high'))} high-severity issues"
    report = run_react_agent(NAME, REVIEW if review else SYSTEM, prompt, tools, ctx, max_iterations=3 if review else 5)
    return ws.updates(), report


def fallback(state: dict, ctx: RunContext) -> tuple[dict, str]:
    """Deterministic behaviour if no LLM is reachable: learn routine + compute availability."""
    ws = Workspace(dict(state), ctx)
    tools = {t.name: t for t in build_tools(ws)}
    tools["learn_routine"].func()
    av = tools["compute_availability"].func()
    windows = ", ".join(f"{w['start'][11:]}-{w['end'][11:]}" for w in av.get("free_windows", [])) or "none"
    return ws.updates(), f"[deterministic fallback, LLM unavailable] free windows: {windows}"
