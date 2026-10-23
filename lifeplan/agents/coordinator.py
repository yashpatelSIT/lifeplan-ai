"""🤖 Adaptive Coordinator Agent — the main decision-maker.

Each turn it reads the shared blackboard (what every agent has reported, the current draft and its
conflicts, review critiques, real-world change events) and decides the next move with an LLM:
delegate a task to a specialist, call a review round in which agents critique the draft, or finalise.
It is the agent that resolves multi-agent conflicts, e.g.

    Discovery: "comedy show at 19:00"  ×  Profile: "meeting until 19:30"  ×  Logistics: "40 min to get there"
    -> Coordinator: "Planner, drop the 19:00 show; Discovery, find a later show or a venue near the office."
"""

from __future__ import annotations

import json
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from lifeplan import llm
from lifeplan.agents.base import RunContext, make_tool
from lifeplan.agents.shared import TRIP_MODES, board_summary, request_brief
from lifeplan.config import get_settings

NAME = "coordinator"
WORKERS = ("profile", "discovery", "logistics", "planner")

SYSTEM = """You are the Adaptive Coordinator Agent of LifePlan AI, a multi-agent personal planner.
You never plan the itinerary yourself — you lead four specialist agents and decide what happens next:
  • profile   – user preferences, calendar, learned routine, free windows, commute (Personal Profile & Availability)
  • discovery – real venues/events near the location, weather, alternatives for closed/rainy options (Discovery & Location)
  • logistics – travel modes & times, travel matrix, inter-city transport, flights, hotels, currency (Travel & Logistics)
  • planner   – builds and revises the optimised schedule from everything above (Planner & Optimization)
Each turn call EXACTLY ONE tool:
  delegate(agent, instruction, reason) – give one agent a specific, self-contained task.
  request_reviews(focus, reason)       – profile, logistics and discovery critique the current draft in parallel.
  finalize(summary, reason)            – accept the current draft and write the user-facing summary. The summary
                                         must describe ONLY the items in blackboard.draft_plan (exact names/times)
                                         plus the trade-offs made; never mention places that are not in it.
How to work:
  - Adapt to the request: a 2-hour "now" plan needs less work than a 5-day trip. Skip agents whose output is not needed.
  - Usually: availability (profile) and options (discovery) before logistics and planning; trips need logistics
    (inter-city + hotel) before planning.
  - After a draft exists, look at validation issues and review critiques. For every HIGH issue, decide a concrete
    resolution and delegate it (e.g. tell planner exactly what to move/drop, or ask discovery for an alternative
    first). Name the conflicting facts from each agent in your instruction.
  - Prefer finalize once the draft has no HIGH issues (a review round is recommended once before finalising).
  - If issues persist after several revisions, finalize anyway and explain the trade-offs honestly.
  - For a REPLAN EVENT, change only what the event affects and keep the rest of the plan.
Write instructions in plain English with ids/times; keep them short."""


def _decision_tools() -> list:
    def delegate(agent: str, instruction: str, reason: str) -> dict:
        """Hand a task to one specialist agent: profile | discovery | logistics | planner."""
        return {}

    def request_reviews(focus: str, reason: str) -> dict:
        """Ask profile, logistics and discovery to critique the current draft plan in parallel."""
        return {}

    def finalize(summary: str, reason: str) -> dict:
        """Accept the current draft. `summary` = 2-4 friendly sentences for the user explaining the plan and any
        trade-offs/conflicts that were resolved."""
        return {}

    return [make_tool(delegate), make_tool(request_reviews), make_tool(finalize)]


def _recent_messages(state: dict, n: int = 12) -> list[str]:
    out = []
    for m in (state.get("messages") or [])[-n:]:
        out.append(f"{m['from']} → {m['to']} [{m['kind']}]: {m['content'][:500]}")
    return out


def _latest_reviews(state: dict) -> list[dict]:
    r = state.get("review_round", 0)
    return [x for x in (state.get("reviews") or []) if x.get("round") == r]


def guardrail_notes(state: dict) -> list[str]:
    s = get_settings()
    notes = [f"Step {state.get('step', 0) + 1} of max {s.max_coordinator_steps}; planner revisions so far "
             f"{state.get('revisions', 0)} (max {s.max_revisions})."]
    if not state.get("draft"):
        notes.append("There is no draft plan yet — finalize/request_reviews are not possible until the planner builds one.")
    if state.get("mode") in TRIP_MODES and not (state.get("logistics") or {}).get("hotel"):
        notes.append("Trip without a selected hotel/base yet.")
    if state.get("events"):
        notes.append("REPLAN EVENT(S): " + "; ".join(f"{e['event']}: {e['details']}" for e in state["events"][-3:]))
    return notes


def decide_with_llm(state: dict, ctx: RunContext) -> dict:
    prompt = {
        "request": json.loads(request_brief(state)),
        "blackboard": board_summary(state),
        "latest_reviews": [{"agent": r["agent"], "verdict": r["verdict"], "critique": r["text"][:600]}
                           for r in _latest_reviews(state)],
        "recent_messages": _recent_messages(state),
        "notes": guardrail_notes(state),
    }
    msgs = [SystemMessage(SYSTEM), HumanMessage("Current situation:\n" + json.dumps(prompt, default=str))]
    msg = llm.invoke(NAME, msgs, ctx.trace, tools=_decision_tools(), tool_choice="any")
    if not msg.tool_calls:
        text = llm.text_of(msg).lower()
        if "final" in text and state.get("draft"):
            return {"action": "finalize", "summary": llm.text_of(msg), "reason": "model replied without a tool call"}
        return fallback_policy(state, "model replied without choosing a tool")
    call = msg.tool_calls[0]
    args = call.get("args") or {}
    if call["name"] == "delegate":
        return {"action": "delegate", "agent": str(args.get("agent", "")).lower().strip(),
                "instruction": args.get("instruction", ""), "reason": args.get("reason", "")}
    if call["name"] == "request_reviews":
        return {"action": "review", "instruction": args.get("focus", "review the draft"), "reason": args.get("reason", "")}
    return {"action": "finalize", "summary": args.get("summary", ""), "reason": args.get("reason", "")}


def fallback_policy(state: dict, why: str) -> dict:
    """Deterministic policy used only if no LLM is reachable (degraded mode) or the model misbehaves."""
    trip = state.get("mode") in TRIP_MODES
    log = state.get("logistics") or {}
    val = state.get("validation") or {}
    if not state.get("availability"):
        return {"action": "delegate", "agent": "profile", "instruction": "Compute the user's free windows for the "
                "planning window (learn routine from calendar history first).", "reason": why, "degraded": True}
    if not state.get("candidates"):
        return {"action": "delegate", "agent": "discovery", "instruction": "Fetch the weather and find venues and "
                "events matching the user's interests.", "reason": why, "degraded": True}
    if (trip and not log.get("hotel")) or (not trip and not log.get("mode")):
        return {"action": "delegate", "agent": "logistics", "instruction": "Choose travel modes"
                + (", inter-city legs and a hotel" if trip else " and prepare a travel matrix for the best candidates"),
                "reason": why, "degraded": True}
    if not state.get("draft"):
        return {"action": "delegate", "agent": "planner", "instruction": "Build the best schedule from the pool.",
                "reason": why, "degraded": True}
    if val.get("high") and state.get("revisions", 0) < get_settings().max_revisions:
        issues = "; ".join(i["message"] for i in val.get("issues", []) if i["severity"] == "high")[:600]
        return {"action": "delegate", "agent": "planner", "instruction": f"Fix these conflicts: {issues}",
                "reason": why, "degraded": True}
    return {"action": "finalize", "summary": "", "reason": why, "degraded": True}


def sanitize(decision: dict, state: dict) -> tuple[dict, Optional[str]]:
    """Enforce hard guardrails on the LLM's decision. Returns (decision, guardrail_note)."""
    s = get_settings()
    note = None
    if state.get("step", 0) + 1 >= s.max_coordinator_steps:
        if decision["action"] != "finalize":
            note = "step budget exhausted — finalising with the best draft available"
        return {"action": "finalize", "summary": decision.get("summary", ""), "reason": note or decision.get("reason", "")}, note
    if decision["action"] == "delegate" and decision.get("agent") not in WORKERS:
        note = f"unknown agent '{decision.get('agent')}' — routed to planner"
        decision = {**decision, "agent": "planner"}
    if decision["action"] in ("finalize", "review") and not state.get("draft"):
        note = f"cannot {decision['action']} without a draft — asking planner to build one"
        decision = {"action": "delegate", "agent": "planner",
                    "instruction": "Build a draft schedule from the current candidate pool and free windows.",
                    "reason": note}
    val = state.get("validation") or {}
    has_high = bool(val.get("high"))
    if decision["action"] == "review" and state.get("review_round", 0) >= 1 and not has_high:
        note = "review round completed and no high issues remain — finalising"
        decision = {"action": "finalize", "summary": "", "reason": note}
    elif decision["action"] == "review" and state.get("review_round", 0) >= 2:
        note = "two review rounds already done — finalising"
        decision = {"action": "finalize", "summary": "", "reason": note}
    if (decision["action"] == "delegate" and decision.get("agent") == "planner" and state.get("draft")
            and (state.get("revisions", 0) >= s.max_revisions or (not has_high and state.get("review_round", 0) >= 1))):
        note = "revision limit reached or draft verified clean — finalising"
        decision = {"action": "finalize", "summary": "", "reason": note}
    return decision, note
