"""Sample requests use relative times ("today 17:30", "+7d 06:00", "next friday 06:00") so the demos
always plan for the near future. This resolves them to concrete local ISO datetimes."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from lifeplan.config import ROOT_DIR, get_settings

SAMPLES_DIR = ROOT_DIR / "samples"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_REL = re.compile(r"^(today|tomorrow|\+(\d+)d|next (\w+))\s+(\d{1,2}):(\d{2})$", re.I)
_NOW = re.compile(r"^now(?:\+(\d+)([mh]))?$", re.I)


def _now_local() -> datetime:
    return datetime.now(ZoneInfo(get_settings().default_timezone)).replace(tzinfo=None, second=0, microsecond=0)


def resolve_time(value: str, today: date, now: Optional[datetime] = None) -> str:
    n = _NOW.match(value.strip())
    if n:
        base = now or _now_local()
        base += timedelta(minutes=(-base.minute) % 5)
        amount = int(n.group(1) or 0) * (60 if (n.group(2) or "m").lower() == "h" else 1)
        return (base + timedelta(minutes=amount)).strftime("%Y-%m-%dT%H:%M")
    m = _REL.match(value.strip())
    if not m:
        return value
    word, plus, wd, hh, mm = m.groups()
    word = word.lower()
    if word == "today":
        d = today
    elif word == "tomorrow":
        d = today + timedelta(days=1)
    elif plus:
        d = today + timedelta(days=int(plus))
    else:
        target = WEEKDAYS.index(wd.lower()) if wd and wd.lower() in WEEKDAYS else 5
        delta = (target - today.weekday()) % 7 or 7
        d = today + timedelta(days=delta)
    return datetime.combine(d, datetime.min.time()).replace(hour=int(hh), minute=int(mm)).strftime("%Y-%m-%dT%H:%M")


def resolve(obj: Any, today: date, now: Optional[datetime] = None) -> Any:
    if isinstance(obj, dict):
        return {k: (resolve_time(v, today, now) if k in ("start", "end") and isinstance(v, str)
                    else resolve(v, today, now)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve(x, today, now) for x in obj]
    return obj


def load_samples(today: Optional[date] = None, now: Optional[datetime] = None) -> list[dict]:
    now = now or _now_local()
    today = today or now.date()
    out = []
    for p in sorted(SAMPLES_DIR.glob("request_*.json")):
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = re.sub(r"^request_\d*_?", "", p.stem)
        out.append({"name": name, "request": resolve(data, today, now)})
    return out
