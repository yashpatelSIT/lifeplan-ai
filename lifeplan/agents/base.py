"""Shared agent runtime.

Every specialist agent is a small LangGraph *subgraph* implementing a ReAct loop:

    think (LLM decides: call tools or answer) --tool calls--> act (run tools) --> think ...
                                              +--no tool calls / limit--> END

The agent's tools are plain Python functions bound to a `Workspace` — the agent's view of the
shared blackboard. Whatever the tools write to the workspace is merged back into the global
graph state when the agent finishes, and the agent's final answer is posted as a message.
"""

from __future__ import annotations

import concurrent.futures
import json
import operator
import threading
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from lifeplan import llm
from lifeplan.config import get_settings
from lifeplan.trace import Trace

TOOL_RESULT_CHARS = 3500


@dataclass
class RunContext:
    """Per-run services that are not part of the (serialisable) graph state."""

    trace: Trace
    profile_store: Any
    now: Any  # naive local datetime
    sample_calendar: Optional[str] = None


@dataclass
class Workspace:
    """Mutable copy of the blackboard fields an agent may read/write during its turn."""

    state: dict
    ctx: RunContext
    changed: set = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, key: str, default=None):
        with self._lock:
            return self.state.get(key, default)

    def set(self, key: str, value) -> None:
        with self._lock:
            self.state[key] = value
            self.changed.add(key)

    def add_candidate(self, cand: dict) -> None:
        with self._lock:
            cands = dict(self.state.get("candidates") or {})
            existing = cands.get(cand["id"], {})
            cands[cand["id"]] = {**existing, **cand}
            self.state["candidates"] = cands
            self.changed.add("candidates")

    def set_logistics(self, key: str, value) -> None:
        with self._lock:
            log = dict(self.state.get("logistics") or {})
            log[key] = value
            self.state["logistics"] = log
            self.changed.add("logistics")

    def updates(self) -> dict:
        with self._lock:
            return {k: self.state[k] for k in self.changed}


def to_json(value: Any, limit: int = TOOL_RESULT_CHARS) -> str:
    text = json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))
    if len(text) > limit:
        text = text[:limit] + '…"[truncated]"'
    return text


def make_tool(fn: Callable, name: Optional[str] = None, description: Optional[str] = None) -> StructuredTool:
    return StructuredTool.from_function(func=fn, name=name or fn.__name__,
                                        description=description or (fn.__doc__ or name or fn.__name__).strip())


class _AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    iterations: Annotated[int, operator.add]


def run_react_agent(agent: str, system_prompt: str, instruction: str, tools: list[StructuredTool],
                    ctx: RunContext, max_iterations: Optional[int] = None) -> str:
    """Run one agent turn as a LangGraph ReAct subgraph and return its final report text."""
    trace = ctx.trace
    max_iterations = max_iterations or get_settings().max_agent_iterations
    by_name = {t.name: t for t in tools}

    def think(state: _AgentState) -> dict:
        must_wrap_up = state["iterations"] >= max_iterations
        msgs = list(state["messages"])
        if must_wrap_up:
            msgs.append(HumanMessage("Tool budget used up. Reply now with your final report (no more tool calls)."))
        msg = llm.invoke(agent, msgs, trace, tools=None if must_wrap_up else tools)
        if must_wrap_up and msg.tool_calls:  # model ignored the instruction
            msg = AIMessage(content=llm.text_of(msg) or "(no report)")
        return {"messages": [msg], "iterations": 1}

    def act(state: _AgentState) -> dict:
        last = state["messages"][-1]
        calls = last.tool_calls or []
        if not calls:
            return {"messages": []}

        def _exec_call(call):
            tool = by_name.get(call["name"])
            t0 = time.time()
            if tool is None:
                result, ok = {"ok": False, "error": f"unknown tool '{call['name']}'"}, False
            else:
                try:
                    result = tool.func(**(call.get("args") or {}))
                    ok = not (isinstance(result, dict) and result.get("ok") is False)
                except TypeError as exc:
                    result, ok = {"ok": False, "error": f"bad arguments: {exc}"}, False
                except Exception as exc:  # noqa: BLE001 - a failing tool must never crash the agent
                    result, ok = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, False
            trace.tool_call(agent, call["name"], call.get("args") or {}, result, int((time.time() - t0) * 1000), ok)
            return ToolMessage(content=to_json(result), tool_call_id=call["id"], name=call["name"])

        if len(calls) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(calls), 4)) as pool:
                out = list(pool.map(_exec_call, calls))
        else:
            out = [_exec_call(calls[0])]
        return {"messages": out}

    def route(state: _AgentState) -> str:
        last = state["messages"][-1]
        return "act" if isinstance(last, AIMessage) and last.tool_calls else END

    g = StateGraph(_AgentState)
    g.add_node("think", think)
    g.add_node("act", act)
    g.add_edge(START, "think")
    g.add_conditional_edges("think", route, ["act", END])
    g.add_edge("act", "think")
    graph = g.compile()

    trace.emit("agent_start", agent, instruction[:600])
    final = graph.invoke(
        {"messages": [SystemMessage(system_prompt), HumanMessage(instruction)], "iterations": 0},
        {"recursion_limit": 4 * max_iterations + 6},
    )
    report = llm.text_of(final["messages"][-1]).strip() or "(no report)"
    trace.emit("agent_end", agent, report[:1500])
    return report
