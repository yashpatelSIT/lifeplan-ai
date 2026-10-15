"""The LangGraph multi-agent graph.

Pattern: hierarchical orchestrator-worker (Coordinator = supervisor) over a shared blackboard,
with a parallel critique round and peer-to-peer consultation (Planner -> Logistics / Discovery).

                 ┌───────────── delegate ─────────────┐
                 │                                     ▼
   START ──► coordinator ◄──── report ──── profile | discovery | logistics | planner
                 │  ▲                                        (planner may consult logistics/discovery)
     request_    │  └──── critiques ──── review_profile ║ review_logistics ║ review_discovery   (parallel)
     reviews     ▼
              finalize ──► END
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from lifeplan import llm
from lifeplan.agents import coordinator, discovery_agent, logistics_agent, planner_agent, profile_agent
from lifeplan.agents.base import RunContext
from lifeplan.agents.shared import validate_state

AGENTS = {
    "profile": profile_agent,
    "discovery": discovery_agent,
    "logistics": logistics_agent,
    "planner": planner_agent,
}
REVIEWERS = ("profile", "logistics", "discovery")


def merge_dict(left: Optional[dict], right: Optional[dict]) -> dict:
    return {**(left or {}), **(right or {})}


class PlanState(TypedDict, total=False):
    request: dict
    profile: dict
    mode: str
    now: str
    timezone: str
    currency: str
    budget: Optional[float]
    origin: dict
    destination: Optional[dict]
    window: dict
    routine: Optional[dict]
    availability: Optional[dict]
    weather: Optional[dict]
    event_sources: list
    candidates: Annotated[dict, merge_dict]
    logistics: Annotated[dict, merge_dict]
    draft: Optional[dict]
    validation: Optional[dict]
    messages: Annotated[list, operator.add]  # inter-agent message channel of the blackboard
    reviews: Annotated[list, operator.add]
    conflicts: Annotated[list, operator.add]
    events: list  # real-world change events (re-planning)
    next: str
    task: str
    step: int
    revisions: int
    review_round: int
    final_summary: str
    degraded: bool
    status: str


def _msg(sender: str, receiver: str, content: str, kind: str, step: int) -> dict:
    return {"from": sender, "to": receiver, "content": content, "kind": kind, "step": step}


def build_graph(ctx: RunContext):
    trace = ctx.trace

    # ------------------------------------------------------------ coordinator
    def coordinator_node(state: PlanState) -> dict:
        step = state.get("step", 0)
        degraded = state.get("degraded", False)
        try:
            decision = coordinator.decide_with_llm(state, ctx)
        except llm.LLMUnavailable as exc:
            decision = coordinator.fallback_policy(state, f"LLM unavailable: {exc}")
        if decision.get("degraded"):
            degraded = True
            trace.emit("degraded_mode", coordinator.NAME, decision["reason"][:400])
        decision, note = coordinator.sanitize(decision, state)
        if note:
            trace.emit("guardrail", coordinator.NAME, note)

        update: dict[str, Any] = {"step": step + 1, "degraded": degraded}
        # record conflicts currently open so we can report how they were resolved
        val = state.get("validation") or {}
        known = {(c["type"], c["description"]) for c in state.get("conflicts") or []}
        new_conflicts = []
        for issue in val.get("issues", []):
            if issue["severity"] == "high" and (issue["type"], issue["message"]) not in known:
                new_conflicts.append({"type": issue["type"], "description": issue["message"],
                                      "raised_by": [issue["agent"]], "step": step + 1,
                                      "resolution_attempt": decision.get("instruction") or decision.get("summary")})
        if new_conflicts:
            update["conflicts"] = new_conflicts

        trace.emit("decision", coordinator.NAME, decision.get("reason", ""), action=decision["action"],
                   target=decision.get("agent"), instruction=decision.get("instruction"))
        if decision["action"] == "delegate":
            agent = decision["agent"]
            trace.message(coordinator.NAME, agent, decision["instruction"], kind="request", reason=decision.get("reason"))
            update.update({"next": agent, "task": decision["instruction"],
                           "messages": [_msg(coordinator.NAME, agent, decision["instruction"], "request", step + 1)]})
            if agent == "planner" and state.get("draft"):
                update["revisions"] = state.get("revisions", 0) + 1
        elif decision["action"] == "review":
            rnd = state.get("review_round", 0) + 1
            for a in REVIEWERS:
                trace.message(coordinator.NAME, a, decision["instruction"], kind="review_request")
            update.update({"next": "review", "task": decision["instruction"], "review_round": rnd,
                           "messages": [_msg(coordinator.NAME, "all", decision["instruction"], "review_request", step + 1)]})
        else:
            update.update({"next": "finalize", "final_summary": decision.get("summary") or ""})
        return update

    def route(state: PlanState):
        nxt = state.get("next")
        if nxt == "review":
            return [Send(f"review_{a}", state) for a in REVIEWERS]
        if nxt in AGENTS:
            return nxt
        return "finalize"

    # ---------------------------------------------------------------- workers
    def make_worker(name: str):
        module = AGENTS[name]

        def node(state: PlanState) -> dict:
            degraded = False
            try:
                updates, report = module.run(dict(state), ctx, state.get("task", ""))
            except llm.LLMUnavailable as exc:
                # graceful degradation: the agent's deterministic fallback still produces its output
                trace.emit("agent_error", name, f"LLM unavailable, using deterministic fallback: {str(exc)[:300]}")
                try:
                    updates, report = module.fallback(dict(state), ctx)
                except Exception as exc2:  # noqa: BLE001
                    updates, report = {}, f"FAILED: {type(exc2).__name__}: {exc2}"
                degraded = True
            trace.message(name, coordinator.NAME, report, kind="report")
            out = dict(updates)
            if degraded:
                out["degraded"] = True
            out["messages"] = [_msg(name, coordinator.NAME, report, "report", state.get("step", 0))]
            merged = {**state, **updates}
            if merged.get("draft"):
                # any new information (availability, weather, venues, travel) can invalidate the draft:
                # re-run the agents' constraint checks so the Coordinator always sees current conflicts
                val = validate_state(merged)
                out["validation"] = val
                before = {(i["type"], i["message"]) for i in (state.get("validation") or {}).get("issues", [])}
                for issue in val["issues"]:
                    if issue["severity"] in ("high", "medium") and (name == "planner" or
                                                                    (issue["type"], issue["message"]) not in before):
                        trace.emit("conflict", issue["agent"], issue["message"], type=issue["type"],
                                   severity=issue["severity"], detected_by="constraint-check tool")
            return out

        node.__name__ = f"{name}_node"
        return node

    def make_reviewer(name: str):
        module = AGENTS[name]

        def node(state: PlanState) -> dict:
            rnd = state.get("review_round", 1)
            try:
                updates, report = module.run(dict(state), ctx, state.get("task", "review the draft"), review=True)
            except llm.LLMUnavailable as exc:
                report, updates = f"(review skipped: {exc})", {}
            low = report.lower()
            verdict = "SKIPPED" if low.startswith("(review skipped") else ("REVISE" if "revise" in low else "APPROVE")
            trace.message(name, coordinator.NAME, report, kind="critique", verdict=verdict, round=rnd)
            allowed = {k: v for k, v in updates.items() if k in ("candidates", "logistics", "availability")}
            return {**allowed,
                    "reviews": [{"agent": name, "round": rnd, "verdict": verdict, "text": report}],
                    "messages": [_msg(name, coordinator.NAME, report, "critique", state.get("step", 0))]}

        node.__name__ = f"review_{name}_node"
        return node

    def finalize_node(state: PlanState) -> dict:
        val = validate_state(state) if state.get("draft") else None
        has_high_issue = val and any(i.get("severity") == "high" for i in val.get("issues", []))
        draft_stops = sum(
            1 for d in (state.get("draft") or {}).get("days", [])
            for it in d.get("items", [])
            if it.get("kind") not in ("travel", "busy")
        )
        if not state.get("draft") or has_high_issue or draft_stops == 0:
            status = "failed"
        elif val and val["ok"] and not state.get("degraded"):
            status = "ok"
        else:
            status = "partial"
        trace.emit("final", coordinator.NAME, state.get("final_summary") or "plan finalised", status=status)
        return {"validation": val, "status": status}

    g = StateGraph(PlanState)
    g.add_node("coordinator", coordinator_node)
    for name in AGENTS:
        g.add_node(name, make_worker(name))
        g.add_edge(name, "coordinator")
    for name in REVIEWERS:
        g.add_node(f"review_{name}", make_reviewer(name))
        g.add_edge(f"review_{name}", "coordinator")
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "coordinator")
    g.add_conditional_edges("coordinator", route,
                            list(AGENTS) + [f"review_{a}" for a in REVIEWERS] + ["finalize"])
    g.add_edge("finalize", END)
    return g.compile()
