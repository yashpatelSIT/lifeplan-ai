"""Calendar + availability reasoning used by the Personal Profile & Availability agent.

* parse an .ics calendar (Google/Outlook export or secret iCal URL contents), incl. simple RRULEs
* learn the user's routine from history: recurring busy blocks per weekday with a confidence score
* compute free windows for a target period, combining explicit events + predicted routine
All datetimes are handled as naive *local* times in the planning timezone.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

SLOT_MIN = 30
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _to_local(value, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(tz)
        return value.replace(tzinfo=None)
    return datetime.combine(value, time(0, 0))  # all-day event (date)


def parse_ics(ics_text: str, tz_name: str, range_start: datetime, range_end: datetime) -> list[dict]:
    """Return events overlapping [range_start, range_end] as dicts with naive local datetimes."""
    from icalendar import Calendar  # local import keeps module import cheap

    tz = ZoneInfo(tz_name)
    cal = Calendar.from_ical(ics_text)
    events: list[dict] = []
    for comp in cal.walk("VEVENT"):
        try:
            dtstart = comp.decoded("DTSTART")
        except KeyError:
            continue
        all_day = not isinstance(dtstart, datetime)
        if "DTEND" in comp:
            dtend = comp.decoded("DTEND")
        elif "DURATION" in comp:
            dtend = dtstart + comp.decoded("DURATION")
        else:
            dtend = dtstart + (timedelta(days=1) if all_day else timedelta(hours=1))
        start, end = _to_local(dtstart, tz), _to_local(dtend, tz)
        title = str(comp.get("SUMMARY", "Busy"))
        location = str(comp.get("LOCATION", "")) or None
        transparent = str(comp.get("TRANSP", "")).upper() == "TRANSPARENT"
        duration = end - start
        starts: list[datetime] = [start]
        if "RRULE" in comp:
            rule_text = comp.get("RRULE").to_ical().decode()
            try:
                rule = rrulestr(rule_text, dtstart=start, ignoretz=True)
                starts = list(rule.between(range_start - duration, range_end, inc=True))
            except (ValueError, TypeError):
                starts = [start]
            exdates = set()
            for ex in comp.get("EXDATE", []) if isinstance(comp.get("EXDATE"), list) else [comp.get("EXDATE")]:
                if ex is None:
                    continue
                for d in getattr(ex, "dts", []):
                    exdates.add(_to_local(d.dt, tz))
            starts = [s for s in starts if s not in exdates]
        for s in starts:
            e = s + duration
            if e > range_start and s < range_end:
                events.append({"start": s, "end": e, "title": title, "location": location,
                               "all_day": all_day, "transparent": transparent})
    events.sort(key=lambda e: e["start"])
    return events


def merge_intervals(blocks: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[list[datetime]] = []
    for s, e in sorted(blocks):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def free_windows(busy: Iterable[tuple[datetime, datetime]], start: datetime, end: datetime,
                 min_minutes: int = 45) -> list[tuple[datetime, datetime]]:
    merged = merge_intervals((max(s, start), min(e, end)) for s, e in busy if e > start and s < end)
    windows, cursor = [], start
    for s, e in merged:
        if s > cursor and (s - cursor).total_seconds() / 60 >= min_minutes:
            windows.append((cursor, s))
        cursor = max(cursor, e)
    if end > cursor and (end - cursor).total_seconds() / 60 >= min_minutes:
        windows.append((cursor, end))
    return windows


def learn_routine(history: list[dict], min_confidence: float = 0.5) -> dict:
    """Learn recurring busy blocks per weekday from past (non all-day) events.

    For each weekday and 30-minute slot we compute the fraction of observed weeks in which the
    user was busy. Contiguous slots above `min_confidence` become predicted busy blocks.
    """
    timed = [e for e in history if not e.get("all_day") and not e.get("transparent")]
    if not timed:
        return {"weeks_observed": 0, "blocks": [], "frequent_locations": [], "typical_day_end": {}}
    first = min(e["start"].date() for e in timed)
    last = max(e["start"].date() for e in timed)
    weeks_per_weekday = Counter()
    d = first
    while d <= last:
        weeks_per_weekday[d.weekday()] += 1
        d += timedelta(days=1)

    slots_busy: dict[tuple[int, int], set] = defaultdict(set)
    titles: dict[tuple[int, int], Counter] = defaultdict(Counter)
    for e in timed:
        t = e["start"]
        while t < e["end"]:
            slot = (t.hour * 60 + t.minute) // SLOT_MIN
            slots_busy[(t.weekday(), slot)].add(t.date())
            titles[(t.weekday(), slot)][e["title"]] += 1
            t += timedelta(minutes=SLOT_MIN)

    blocks = []
    for wd in range(7):
        n = weeks_per_weekday.get(wd, 0)
        if not n:
            continue
        run_start, confs, title_counter = None, [], Counter()
        for slot in range(0, 24 * 60 // SLOT_MIN + 1):
            conf = len(slots_busy.get((wd, slot), ())) / n if slot < 48 else 0
            if conf >= min_confidence:
                if run_start is None:
                    run_start, confs, title_counter = slot, [], Counter()
                confs.append(conf)
                title_counter.update(titles[(wd, slot)])
            elif run_start is not None:
                blocks.append({
                    "weekday": wd, "weekday_name": WEEKDAYS[wd],
                    "start": f"{run_start * SLOT_MIN // 60:02d}:{run_start * SLOT_MIN % 60:02d}",
                    "end": f"{slot * SLOT_MIN // 60:02d}:{slot * SLOT_MIN % 60:02d}" if slot < 48 else "23:59",
                    "confidence": round(sum(confs) / len(confs), 2),
                    "label": title_counter.most_common(1)[0][0] if title_counter else "Busy",
                })
                run_start = None

    locs = Counter(e["location"] for e in timed if e.get("location"))
    day_end: dict[str, str] = {}
    for wd in range(7):
        ends = sorted(e["end"].time() for e in timed if e["start"].weekday() == wd)
        if ends:
            median = ends[len(ends) // 2]
            day_end[WEEKDAYS[wd]] = median.strftime("%H:%M")
    return {"weeks_observed": max(weeks_per_weekday.values()), "blocks": blocks,
            "frequent_locations": [{"location": l, "count": c} for l, c in locs.most_common(5)],
            "typical_day_end": day_end}


def predicted_busy_for(day: date, routine: dict) -> list[dict]:
    out = []
    for b in routine.get("blocks", []):
        if b["weekday"] != day.weekday():
            continue
        s = datetime.combine(day, time.fromisoformat(b["start"]))
        e = datetime.combine(day, time.fromisoformat(b["end"])) if b["end"] != "23:59" else \
            datetime.combine(day, time(23, 59))
        out.append({"start": s, "end": e, "title": f"{b['label']} (predicted)", "confidence": b["confidence"],
                    "predicted": True})
    return out


def availability(window_start: datetime, window_end: datetime, events: list[dict],
                 manual_busy: list[dict], routine: Optional[dict], min_minutes: int = 45) -> dict:
    """Combine explicit events, manual blocks and the learned routine into free windows."""
    explicit = [e for e in events if not e.get("transparent") and not e.get("all_day")]
    busy = [(e["start"], e["end"]) for e in explicit] + [(b["start"], b["end"]) for b in manual_busy]
    predicted = []
    if routine:
        d = window_start.date()
        while d <= window_end.date():
            for p in predicted_busy_for(d, routine):
                # only trust a prediction if the real calendar doesn't already cover that day/time
                overlaps_real = any(s < p["end"] and p["start"] < e for s, e in busy)
                if not overlaps_real and p["end"] > window_start and p["start"] < window_end:
                    predicted.append(p)
            d += timedelta(days=1)
    busy_all = busy + [(p["start"], p["end"]) for p in predicted]
    windows = free_windows(busy_all, window_start, window_end, min_minutes)

    def fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M")

    return {
        "window": {"start": fmt(window_start), "end": fmt(window_end)},
        "busy": [{"start": fmt(e["start"]), "end": fmt(e["end"]), "title": e["title"],
                  "location": e.get("location"), "source": "calendar"} for e in explicit
                 if e["end"] > window_start and e["start"] < window_end]
                + [{"start": fmt(b["start"]), "end": fmt(b["end"]), "title": b.get("title", "Busy"),
                    "source": "manual"} for b in manual_busy],
        "predicted_busy": [{"start": fmt(p["start"]), "end": fmt(p["end"]), "title": p["title"],
                            "confidence": p["confidence"]} for p in predicted],
        "free_windows": [{"start": fmt(s), "end": fmt(e), "minutes": int((e - s).total_seconds() // 60)}
                         for s, e in windows],
    }
