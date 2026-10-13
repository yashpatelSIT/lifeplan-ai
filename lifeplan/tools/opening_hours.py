"""A small, defensive parser for the OpenStreetMap `opening_hours` format.

Handles the patterns that cover the vast majority of real venues:
  "24/7", "10:00-22:00", "Mo-Fr 09:00-18:00; Sa 10:00-14:00; Su off",
  "Mo,We,Fr 09:00-12:00,14:00-18:00", "Tu-Su 18:00-02:00" (past midnight),
  "sunrise-sunset".
Anything it cannot understand returns None ("unknown") instead of guessing.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Optional

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_TIME_RANGE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\+?$")


def _expand_days(spec: str) -> Optional[set[int]]:
    days: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in DAYS or b not in DAYS:
                return None
            i, j = DAYS.index(a), DAYS.index(b)
            k = i
            while True:
                days.add(k)
                if k == j:
                    break
                k = (k + 1) % 7
        elif part in DAYS:
            days.add(DAYS.index(part))
        elif part == "PH":
            continue  # public holidays: ignored
        else:
            return None
    return days


def _parse_times(spec: str) -> Optional[list[tuple[time, time, bool]]]:
    spec = spec.strip()
    if spec in ("off", "closed"):
        return []
    if spec == "sunrise-sunset":
        return [(time(6, 0), time(18, 30), False)]
    out = []
    for chunk in spec.split(","):
        m = _TIME_RANGE.match(chunk.strip())
        if not m:
            return None
        h1, m1, h2, m2 = map(int, m.groups())
        overnight = False
        if h2 >= 24:
            h2 -= 24
            overnight = True
        if h1 > 23 or m1 > 59 or h2 > 23 or m2 > 59:
            return None
        t1, t2 = time(h1, m1), time(h2, m2)
        if t2 <= t1:
            overnight = True
        out.append((t1, t2, overnight))
    return out


def parse(spec: Optional[str]) -> Optional[dict[int, list[tuple[time, time, bool]]]]:
    """Return {weekday: [(open, close, overnight)]} or None if the spec is unknown/unparseable."""
    if not spec or not isinstance(spec, str):
        return None
    spec = spec.strip().strip(";")
    if spec in ("24/7", "Mo-Su 00:00-24:00", "00:00-24:00"):
        return {d: [(time(0, 0), time(23, 59), False)] for d in range(7)}
    table: dict[int, list] = {}
    for rule in [r.strip() for r in spec.split(";") if r.strip()]:
        if re.match(r"^\d", rule) or rule.startswith("sunrise"):
            days, times = set(range(7)), rule
        else:
            bits = rule.split(None, 1)
            if len(bits) == 1:
                return None
            days = _expand_days(bits[0])
            times = bits[1]
            if days is None:
                return None
        parsed = _parse_times(times.replace(" ", "") if times not in ("off", "closed") else times)
        if parsed is None:
            return None
        for d in days:
            table[d] = parsed  # later rules override earlier ones (OSM semantics)
    return table or None


def intervals_on(spec: Optional[str], day: date) -> Optional[list[tuple[datetime, datetime]]]:
    """Concrete open intervals (naive local datetimes) that touch `day`. None = unknown."""
    table = parse(spec)
    if table is None:
        return None
    out = []
    for offset in (-1, 0):  # yesterday's overnight slot may spill into today
        d = day + timedelta(days=offset)
        for t1, t2, overnight in table.get(d.weekday(), []):
            start = datetime.combine(d, t1)
            end = datetime.combine(d + timedelta(days=1) if overnight else d, t2)
            if t2 == time(23, 59):
                end += timedelta(minutes=1)
            if end.date() >= day and start.date() <= day:
                out.append((start, end))
    return sorted(out)


def is_open_between(spec: Optional[str], start: datetime, end: datetime) -> Optional[bool]:
    """True if the venue is open for the whole [start, end] interval; None if unknown."""
    start = start.replace(tzinfo=None)
    end = end.replace(tzinfo=None)
    intervals = intervals_on(spec, start.date())
    if intervals is None:
        return None
    return any(a <= start and end <= b for a, b in intervals)
