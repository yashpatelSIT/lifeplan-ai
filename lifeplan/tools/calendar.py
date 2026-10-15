"""Calendar + availability reasoning used by the Personal Profile & Availability agent.

* parse an .ics calendar (Google/Outlook export or secret iCal URL contents), incl. simple RRULEs
* learn the user's routine from history: recurring busy blocks per weekday with a confidence score
* compute free windows for a target period, combining explicit events + predicted routine
All datetimes are handled as naive *local* times in the planning timezone.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
import logging
from pathlib import Path
import re
from typing import Iterable, Optional
import urllib.request
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


def parse_ics(ics_text: str, tz_name: str | None, range_start: datetime, range_end: datetime) -> list[dict]:
    """Return events overlapping [range_start, range_end] as dicts with naive local datetimes."""
    from icalendar import Calendar  # local import keeps module import cheap

    tz = ZoneInfo(tz_name or "UTC")
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
        status = str(comp.get("STATUS", "CONFIRMED")).upper()
        transparent = str(comp.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT"
        is_unconfirmed = (
            status in ("TENTATIVE", "CANCELLED")
            or transparent
            or bool(re.search(r"\b(tentative|unconfirmed|hold|maybe|tbd)\b", title, re.IGNORECASE))
        )
        is_confirmed = not is_unconfirmed
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
                events.append({
                    "start": s, "end": e, "title": title, "location": location,
                    "all_day": all_day, "transparent": transparent,
                    "status": status, "confirmed": is_confirmed,
                })
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
            "frequent_locations": [{"location": loc, "count": c} for loc, c in locs.most_common(5)],
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
    """Combine explicit events, manual blocks and the learned routine into free windows.
    Strict accuracy rule: Unconfirmed / tentative calendar events must NOT become mandatory scheduling constraints.
    """
    explicit_confirmed = [e for e in events if e.get("confirmed", True) and not e.get("transparent") and not e.get("all_day")]
    unconfirmed = [e for e in events if not e.get("confirmed", True) and not e.get("transparent") and not e.get("all_day")]

    busy = [(e["start"], e["end"]) for e in explicit_confirmed] + [(b["start"], b["end"]) for b in manual_busy]
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
                  "location": e.get("location"), "source": "calendar"} for e in explicit_confirmed
                 if e["end"] > window_start and e["start"] < window_end]
                + [{"start": fmt(b["start"]), "end": fmt(b["end"]), "title": b.get("title", "Busy"),
                    "source": "manual"} for b in manual_busy if b["end"] > window_start and b["start"] < window_end],
        "unconfirmed_events": [{"start": fmt(u["start"]), "end": fmt(u["end"]), "title": u["title"],
                                "location": u.get("location"), "status": u.get("status", "TENTATIVE")} for u in unconfirmed
                               if u["end"] > window_start and u["start"] < window_end],
        "predicted_busy": [{"start": fmt(p["start"]), "end": fmt(p["end"]), "title": p["title"],
                            "confidence": p["confidence"]} for p in predicted],
        "free_windows": [{"start": fmt(s), "end": fmt(e), "minutes": int((e - s).total_seconds() // 60)}
                         for s, e in windows],
    }


# =====================================================================
# Universal Local / Google / Apple Calendar Auto-Detection & Sync
# =====================================================================

log = logging.getLogger("lifeplan.calendar")

CALENDAR_CONFIG_FILE = Path("data/calendar_feed_url.txt")
CALENDAR_CACHE_FILE = Path("data/calendar_cache.ics")


def fetch_calendar_feed(url: str, cache_path: Optional[Path] = None, cache_ttl_seconds: int = 900) -> tuple[bool, str, Optional[str]]:
    """Download an iCal / WebCal calendar feed (Google, Apple iCloud, Outlook 365) with local caching."""
    if not url:
        return False, "", "Empty URL"

    clean_url = url.strip()
    if clean_url.startswith("webcal://"):
        clean_url = "https://" + clean_url[9:]
    elif clean_url.startswith("webcals://"):
        clean_url = "https://" + clean_url[10:]

    cache_path = cache_path or CALENDAR_CACHE_FILE

    # Check cache freshness
    if cache_path.exists():
        try:
            mtime = cache_path.stat().st_mtime
            if (datetime.now().timestamp() - mtime) < cache_ttl_seconds:
                content = cache_path.read_text(encoding="utf-8")
                if "BEGIN:VCALENDAR" in content:
                    return True, content, None
        except Exception as e:
            log.warning("Failed reading calendar cache: %s", e)

    # Fetch live feed
    try:
        req = urllib.request.Request(
            clean_url,
            headers={"User-Agent": "LifePlan-AI/1.0 (Calendar-Sync; +https://github.com/yashpatelSIT/lifeplan-ai)"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            if "BEGIN:VCALENDAR" in content:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(content, encoding="utf-8")
                return True, content, None
            return False, "", "URL did not return a valid iCalendar feed (missing BEGIN:VCALENDAR)"
    except Exception as exc:
        log.warning("Failed fetching calendar feed from %s: %s", clean_url, exc)
        if cache_path.exists():
            try:
                stale = cache_path.read_text(encoding="utf-8")
                if "BEGIN:VCALENDAR" in stale:
                    return True, stale, f"Using cached calendar (feed error: {exc})"
            except Exception:
                pass
        return False, "", str(exc)


def detect_local_calendar() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Automatically detect and load a calendar from local disk or linked Google/Apple feed.

    Returns:
        (ics_content, source_type, source_name)
        source_type is one of: "google_calendar", "apple_calendar", "webcal_feed", "local_file", None
    """
    from lifeplan.config import ROOT_DIR, get_settings
    settings = get_settings()

    # 1. Check configured calendar feed URL in environment or data file
    feed_url = getattr(settings, "calendar_feed_url", "") or ""
    if not feed_url and CALENDAR_CONFIG_FILE.exists():
        try:
            feed_url = CALENDAR_CONFIG_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    if feed_url:
        ok, content, _ = fetch_calendar_feed(feed_url)
        if ok and content:
            low_url = feed_url.lower()
            if "google.com" in low_url:
                src_type = "google_calendar"
                src_name = "Google Calendar"
            elif "icloud.com" in low_url or "apple.com" in low_url:
                src_type = "apple_calendar"
                src_name = "Apple iCloud Calendar"
            else:
                src_type = "webcal_feed"
                src_name = "Subscribed iCal Feed"
            return content, src_type, src_name

    # 2. Check explicitly configured local calendar path
    custom_path = getattr(settings, "local_calendar_path", "") or ""
    if custom_path:
        p = Path(custom_path).expanduser().resolve()
        if p.exists() and p.is_file():
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                if "BEGIN:VCALENDAR" in content:
                    return content, "local_file", p.name
            except Exception as e:
                log.warning("Could not read LOCAL_CALENDAR_PATH %s: %s", p, e)

    # 3. Check standard local paths in order of precedence
    candidate_paths = [
        ROOT_DIR / "calendar.ics",
        ROOT_DIR / "my_calendar.ics",
        ROOT_DIR / "personal.ics",
        ROOT_DIR / "data" / "calendar.ics",
        Path.home() / ".lifeplan" / "calendar.ics",
        Path.home() / "Documents" / "calendar.ics",
        Path.home() / "Desktop" / "calendar.ics",
    ]
    for p in candidate_paths:
        if p.exists() and p.is_file():
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                if "BEGIN:VCALENDAR" in content:
                    return content, "local_file", f"{p.name}"
            except Exception as e:
                log.warning("Could not read candidate calendar %s: %s", p, e)

    return None, None, None


def save_calendar_feed(url: str) -> tuple[bool, str]:
    """Persist a live calendar feed URL (Google/Apple) and verify it immediately."""
    clean = url.strip()
    if not clean:
        if CALENDAR_CONFIG_FILE.exists():
            CALENDAR_CONFIG_FILE.unlink(missing_ok=True)
        if CALENDAR_CACHE_FILE.exists():
            CALENDAR_CACHE_FILE.unlink(missing_ok=True)
        return True, "Calendar feed disconnected."

    ok, content, err = fetch_calendar_feed(clean, cache_ttl_seconds=0)
    if not ok:
        return False, f"Could not sync calendar feed: {err}"

    CALENDAR_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CALENDAR_CONFIG_FILE.write_text(clean, encoding="utf-8")
    return True, "Calendar successfully connected and verified!"

