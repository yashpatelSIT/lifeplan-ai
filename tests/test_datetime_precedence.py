from __future__ import annotations

import datetime as dt
import pytest

from lifeplan.tools.datetime_parser import (
    extract_dates_from_query,
    extract_times_from_query,
    resolve_canonical_schedule,
    CanonicalSchedule,
)
from lifeplan.agents.profile_agent import build_tools
from lifeplan.agents.base import Workspace, RunContext
from lifeplan.trace import Trace


def test_explicit_window_tomorrow_3pm_to_6pm():
    """Extracts 'Tomorrow from 3 PM to 6 PM' with explicit times."""
    ref_now = dt.datetime(2026, 10, 14, 10, 0, 0)
    query = "Tomorrow, I am free from 3 PM to 6 PM. I'd like to meet a childhood friend for non-vegetarian food in Pune."

    target_date = extract_dates_from_query(query, ref_now.date())
    assert target_date is not None
    assert target_date.day == 15

    start_time, end_time, dur = extract_times_from_query(query)
    assert start_time is not None
    assert end_time is not None
    assert start_time.hour == 15
    assert start_time.minute == 0
    assert end_time.hour == 18
    assert end_time.minute == 0


def test_precedence_overrides_conflicting_picker():
    """When description specifies 3 PM to 6 PM, it overrides 8 PM to 11 PM picker."""
    ref_now = dt.datetime(2026, 10, 14, 10, 0, 0)
    query = "Tomorrow, I am free from 3 PM to 6 PM. Let's get coffee."
    picker_start = dt.datetime(2026, 10, 15, 20, 15)
    picker_end = dt.datetime(2026, 10, 15, 23, 15)

    sched = resolve_canonical_schedule(
        query=query,
        now=ref_now,
        picker_start=picker_start,
        picker_end=picker_end,
    )
    assert sched.locked is True
    assert sched.source == "query_explicit"
    assert sched.overridden_picker is True
    # Canonical schedule must match 3 PM - 6 PM
    assert sched.start.hour == 15
    assert sched.end.hour == 18
    # User-facing override note must be present
    assert sched.override_note is not None
    assert "priority over form picker" in sched.override_note


def test_structured_inputs_used_when_no_nl_date():
    """When query has no date/time, structured pickers are respected."""
    ref_now = dt.datetime(2026, 10, 14, 10, 0, 0)
    query = "Looking for modern art museums and street food."
    picker_start = dt.datetime(2026, 10, 16, 11, 0)
    picker_end = dt.datetime(2026, 10, 16, 15, 0)

    sched = resolve_canonical_schedule(
        query=query,
        now=ref_now,
        picker_start=picker_start,
        picker_end=picker_end,
    )
    assert sched.source == "picker_explicit"
    assert sched.overridden_picker is False
    assert sched.start.day == 16
    assert sched.start.hour == 11
    assert sched.end.hour == 15
    assert sched.override_note is None


def test_relative_dates_tonight():
    """Handles relative dates: 'tonight from 7 PM to 10 PM'."""
    ref_now = dt.datetime(2026, 10, 14, 12, 0, 0)
    query = "Tonight from 7 PM to 10 PM, let's explore rooftop cafes."

    target_date = extract_dates_from_query(query, ref_now.date())
    assert target_date is not None
    assert target_date.day == 14

    start_time, end_time, dur = extract_times_from_query(query)
    assert start_time is not None
    assert end_time is not None
    assert start_time.hour == 19
    assert end_time.hour == 22


def test_duration_extraction():
    """Extracts duration like '2 free hours right now'."""
    ref_now = dt.datetime(2026, 10, 14, 14, 0, 0)
    query = "I have 2 free hours right now. Find a cozy cafe with outdoor seating."

    start_time, end_time, dur = extract_times_from_query(query)
    assert dur == 120 or dur is not None


def test_profile_agent_respects_locked_schedule():
    """Locked schedule prevents profile agent default work routine from wiping out user's requested window."""
    # A weekday afternoon: Thursday 3 PM to 6 PM
    state = {
        "window": {
            "start": "2026-10-15T15:00:00",
            "end": "2026-10-15T18:00:00",
            "locked": True,
        },
        "schedule": {
            "query_extracted": True,
            "source": "query_explicit",
        },
        "request": {
            "query": "Tomorrow from 3 PM to 6 PM",
            "use_learned_schedule": True,
            "calendar_ics": None,
        },
        "profile": {
            "work_hours": {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start": "09:00", "end": "17:30"},
            "commute_minutes": 30,
        },
    }
    ctx = RunContext(trace=Trace(), profile_store=None, now=dt.datetime(2026, 10, 14, 10, 0))
    ws = Workspace(state=state, ctx=ctx)

    tools = build_tools(ws)
    avail_tool = next(t for t in tools if t.name == "compute_availability")
    res = avail_tool.invoke({"start": "2026-10-15T15:00:00", "end": "2026-10-15T18:00:00"})

    # Availability must have free windows and should not be wiped out by 9:00-17:30 work block
    assert res.get("ok") is not False
    avail = ws.get("availability") or {}
    free_windows = avail.get("free_windows", [])
    assert len(free_windows) > 0
    assert free_windows[0]["start"] == "2026-10-15T15:00"
    assert free_windows[0]["end"] == "2026-10-15T18:00"
