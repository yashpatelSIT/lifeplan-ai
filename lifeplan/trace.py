"""Execution trace: a faithful, timestamped log of every agent message, LLM call, tool call,
hand-off, conflict and decision during a run. It is streamed live to the UI and saved to
`traces/<run_id>.json` (this is the 'Agent Execution Trace' CA3 artifact)."""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from lifeplan.config import redact

MAX_FIELD = 4000


def _clip(value: Any, limit: int = MAX_FIELD) -> Any:
    if isinstance(value, str):
        value = redact(value)
        return value if len(value) <= limit else value[:limit] + f"… [+{len(value) - limit} chars]"
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        clipped = [_clip(v, limit) for v in value[:60]]
        if len(value) > 60:
            clipped.append(f"… [+{len(value) - 60} items]")
        return clipped
    return value


class Trace:
    def __init__(self, run_id: Optional[str] = None, plan_id: Optional[str] = None, kind: str = "plan"):
        self.run_id = run_id or f"run_{uuid.uuid4().hex[:10]}"
        self.plan_id = plan_id
        self.kind = kind
        self.started = time.time()
        self.events: list[dict] = []
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self.stats = {"llm_calls": 0, "tool_calls": 0, "messages": 0, "input_tokens": 0, "output_tokens": 0,
                      "llm_fallbacks": 0, "tool_errors": 0}
        self.finished = False

    # ------------------------------------------------------------------ emit
    def emit(self, type_: str, agent: str, content: str = "", **data) -> dict:
        with self._lock:
            event = {
                "seq": len(self.events) + 1,
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "t_ms": int((time.time() - self.started) * 1000),
                "type": type_,
                "agent": agent,
                "content": _clip(content, 2000),
            }
            if data:
                event["data"] = _clip(data)
            self.events.append(event)
            subs = list(self._subscribers)
        for q in subs:
            q.put(event)
        return event

    def message(self, sender: str, receiver: str, content: str, kind: str = "message", **data) -> dict:
        """An inter-agent message (request, report, critique, conflict, resolution…)."""
        self.stats["messages"] += 1
        return self.emit("message", sender, content, to=receiver, kind=kind, **data)

    def llm_call(self, agent: str, model: str, duration_ms: int, usage: dict | None, text: str,
                 tool_calls: list | None, fallback_used: bool = False) -> None:
        self.stats["llm_calls"] += 1
        if usage:
            self.stats["input_tokens"] += int(usage.get("input_tokens") or 0)
            self.stats["output_tokens"] += int(usage.get("output_tokens") or 0)
        if fallback_used:
            self.stats["llm_fallbacks"] += 1
        self.emit("llm_call", agent, text or "", model=model, duration_ms=duration_ms, usage=usage or {},
                  tool_calls=[{"name": t["name"], "args": t.get("args")} for t in (tool_calls or [])],
                  fallback_used=fallback_used)

    def tool_call(self, agent: str, tool: str, args: dict, result: Any, duration_ms: int, ok: bool) -> None:
        self.stats["tool_calls"] += 1
        if not ok:
            self.stats["tool_errors"] += 1
        self.emit("tool_call", agent, tool, args=args, result=result, duration_ms=duration_ms, ok=ok)

    # ------------------------------------------------------------- streaming
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            for e in self.events:  # replay history for late subscribers
                q.put(e)
            self._subscribers.append(q)
            if self.finished:
                q.put(None)
        return q

    def close(self) -> None:
        with self._lock:
            self.finished = True
            subs = list(self._subscribers)
        for q in subs:
            q.put(None)

    # ---------------------------------------------------------------- export
    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "kind": self.kind,
            "started_at": datetime.fromtimestamp(self.started, timezone.utc).isoformat(),
            "duration_s": round(time.time() - self.started, 2),
            "stats": self.stats,
            "events": self.events,
        }

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        return path


def to_markdown(trace: dict) -> str:
    """Human-readable transcript of a trace (for the CA3 submission / README)."""
    lines = [f"# Execution trace `{trace['run_id']}`", "",
             f"* plan: `{trace.get('plan_id')}` · kind: {trace.get('kind')} · duration: {trace.get('duration_s')} s",
             f"* stats: {json.dumps(trace.get('stats'))}", "", "| # | t (s) | agent | event | detail |",
             "|---|---|---|---|---|"]
    for e in trace["events"]:
        d = e.get("data") or {}
        if e["type"] == "message":
            detail = f"→ **{d.get('to')}** [{d.get('kind')}] {e['content']}"
        elif e["type"] == "tool_call":
            detail = f"`{e['content']}({json.dumps(d.get('args'), default=str)[:160]})` ok={d.get('ok')} " \
                     f"{d.get('duration_ms')}ms"
        elif e["type"] == "llm_call":
            calls = ", ".join(t["name"] for t in d.get("tool_calls") or [])
            detail = f"{d.get('model')} {d.get('duration_ms')}ms" + (f" → tools: {calls}" if calls else "") + \
                     (f" — {e['content'][:200]}" if e["content"] else "")
        else:
            detail = e["content"][:300]
        detail = detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {e['seq']} | {e['t_ms'] / 1000:.1f} | {e['agent']} | {e['type']} | {detail} |")
    return "\n".join(lines) + "\n"
