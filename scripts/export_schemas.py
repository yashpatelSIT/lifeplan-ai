"""Write the JSON Schemas of the I/O contract to docs/schemas/ (referenced by docs/IO_DEFINITIONS.md)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lifeplan.schemas import FeedbackRequest, PlanRequest, PlanResult, ReplanEvent  # noqa: E402

out = ROOT / "docs" / "schemas"
out.mkdir(parents=True, exist_ok=True)
for name, model in (("plan_request", PlanRequest), ("plan_result", PlanResult), ("replan_event", ReplanEvent),
                    ("feedback_request", FeedbackRequest)):
    (out / f"{name}.schema.json").write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
    print("wrote", out / f"{name}.schema.json")
