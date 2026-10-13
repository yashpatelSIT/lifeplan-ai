"""Run one real planning request from the command line and save the execution trace.

    python scripts/run_demo.py samples/request_1_evening_conflict.json
    python scripts/run_demo.py samples/request_5_goa_trip.json --out samples/traces
    python scripts/run_demo.py samples/request_1_evening_conflict.json --replan weather "Heavy rain started"

Writes <out>/<run_id>.json (full trace), <run_id>.md (readable transcript) and <run_id>.result.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lifeplan import service  # noqa: E402
from lifeplan.samples import resolve  # noqa: E402
from lifeplan.schemas import PlanRequest, ReplanEvent  # noqa: E402
from lifeplan.trace import Trace, to_markdown  # noqa: E402


def save(trace: Trace, result, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = trace.to_dict()
    (out / f"{trace.run_id}.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    (out / f"{trace.run_id}.md").write_text(to_markdown(data), encoding="utf-8")
    (out / f"{trace.run_id}.result.json").write_text(json.dumps(result.model_dump(mode="json"), indent=2),
                                                     encoding="utf-8")
    print(f"saved {out / trace.run_id}.json/.md/.result.json")


def live_print(trace: Trace) -> None:
    import threading

    q = trace.subscribe()

    def loop():
        while True:
            e = q.get()
            if e is None:
                return
            d = e.get("data") or {}
            if e["type"] == "message":
                print(f"[{e['t_ms'] / 1000:6.1f}s] {e['agent']:>11} → {d.get('to'):<11} [{d.get('kind')}] {e['content'][:160]}")
            elif e["type"] == "tool_call":
                print(f"[{e['t_ms'] / 1000:6.1f}s] {e['agent']:>11}   🔧 {e['content']} ok={d.get('ok')}")
            elif e["type"] in ("conflict", "decision", "guardrail", "llm_error", "degraded_mode", "final"):
                print(f"[{e['t_ms'] / 1000:6.1f}s] {e['agent']:>11}   {e['type'].upper()}: {e['content'][:160]}")

    threading.Thread(target=loop, daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("request", help="path to a PlanRequest JSON (relative dates allowed)")
    ap.add_argument("--out", default="traces")
    ap.add_argument("--replan", nargs=2, metavar=("EVENT", "DETAILS"),
                    help="after planning, send a change event: weather|delay|cancellation|closed|schedule_change")
    args = ap.parse_args()

    from datetime import date

    raw = json.loads(Path(args.request).read_text(encoding="utf-8"))
    req = PlanRequest(**resolve(raw, date.today()))
    trace = Trace()
    live_print(trace)
    result = service.run_plan(req, trace)
    print(f"\nSTATUS {result.status}: {result.summary}")
    for d in result.days:
        for it in d.items:
            print(f"  {it.start[5:]}–{it.end[11:]}  {it.kind:<8} {it.title}")
    save(trace, result, Path(args.out))

    if args.replan:
        ev = ReplanEvent(plan_id=result.plan_id, event=args.replan[0], details=args.replan[1])
        t2 = Trace(kind="replan")
        live_print(t2)
        r2 = service.replan(ev, t2)
        print(f"\nREPLAN STATUS {r2.status}: {r2.summary}")
        save(t2, r2, Path(args.out))


if __name__ == "__main__":
    main()
