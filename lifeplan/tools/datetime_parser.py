"""Deterministic natural-language date and time extraction with strict precedence rules.

Guarantees that when a user explicitly writes a date/time in natural language
(e.g., "Tomorrow, I am free from 3 PM to 6 PM..."), that explicit specification
ALWAYS takes precedence over UI picker defaults or conflicting form fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional


@dataclass
class CanonicalSchedule:
    start: datetime
    end: datetime
    source: str  # "query_explicit", "picker_explicit", "query_picker_merged", "derived"
    query_extracted: bool
    overridden_picker: bool
    override_note: Optional[str] = None
    locked: bool = True


WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _parse_clock_time(h_str: str, m_str: Optional[str], meridiem: Optional[str]) -> Optional[time]:
    try:
        h = int(h_str)
        m = int(m_str) if m_str else 0
    except (ValueError, TypeError):
        return None

    if meridiem:
        meridiem = meridiem.lower()
        if meridiem == "pm" and h < 12:
            h += 12
        elif meridiem == "am" and h == 12:
            h = 0
    elif h < 7:  # default afternoon/evening for ambiguous 1-6 in social contexts
        h += 12

    if 0 <= h < 24 and 0 <= m < 60:
        return time(h, m)
    return None

def extract_date_range_from_query(query: str, ref_date: date) -> tuple[Optional[date], Optional[date]]:
    """Extract (start_date, end_date) when query specifies a multi-day span.
    e.g. 'Monday to Friday next week', 'from Monday to Friday', 'from 2026-11-20 to 2026-11-23', 'for 4 days'.
    """
    q = query.lower()

    # ISO date range: "from 2026-11-20 to 2026-11-23" or "2026-11-20 - 2026-11-23"
    m_iso = re.search(r"\b(?:from\s+)?(\d{4}-\d{2}-\d{2})\s+(?:to|-|until|till)\s+(\d{4}-\d{2}-\d{2})\b", q)
    if m_iso:
        try:
            return date.fromisoformat(m_iso.group(1)), date.fromisoformat(m_iso.group(2))
        except ValueError:
            pass

    m = re.search(
        r"\b(?:from\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)\s+to\s+"
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)"
        r"(?:\s+(next\s+week|this\s+week))?\b",
        q
    )
    if m:
        w1_str, w2_str, week_spec = m.group(1), m.group(2), m.group(3) or ""
        wd1 = WEEKDAYS.get(w1_str)
        wd2 = WEEKDAYS.get(w2_str)
        if wd1 is not None and wd2 is not None:
            is_next = "next" in week_spec or "next week" in q
            days_to_next_mon = (0 - ref_date.weekday()) % 7
            if days_to_next_mon == 0:
                days_to_next_mon = 7
            base_monday = ref_date + timedelta(days=days_to_next_mon) if is_next else ref_date - timedelta(days=ref_date.weekday())
            if not is_next:
                start_d = base_monday + timedelta(days=wd1)
                if start_d < ref_date:
                    start_d = ref_date
            else:
                start_d = base_monday + timedelta(days=wd1)
            end_d = base_monday + timedelta(days=wd2)
            if end_d < start_d:
                end_d += timedelta(days=7)
            return start_d, end_d

    m_days = re.search(r"\bfor\s+(\d+)\s+days?\b", q)
    if m_days:
        num_days = int(m_days.group(1))
        start_d = extract_dates_from_query(query, ref_date) or ref_date
        return start_d, start_d + timedelta(days=max(1, num_days - 1))

    return None, None


def extract_dates_from_query(query: str, ref_date: date) -> Optional[date]:
    """Extract explicit target date from natural language."""
    q = query.lower()

    m_iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", q)
    if m_iso:
        try:
            return date.fromisoformat(m_iso.group(1))
        except ValueError:
            pass

    if re.search(r"\b(day after tomorrow)\b", q):
        return ref_date + timedelta(days=2)
    if re.search(r"\b(tomorrow)\b", q):
        return ref_date + timedelta(days=1)
    if re.search(r"\b(today|tonight|this evening|this afternoon|this morning)\b", q):
        return ref_date

    # "this weekend" / "next weekend"
    if re.search(r"\b(this weekend)\b", q):
        days_ahead = (5 - ref_date.weekday()) % 7
        return ref_date + timedelta(days=days_ahead if days_ahead > 0 else 7)
    if re.search(r"\b(next weekend)\b", q):
        days_ahead = (5 - ref_date.weekday()) % 7 + 7
        return ref_date + timedelta(days=days_ahead)

    # Weekdays: "on friday", "this saturday", "next monday"
    weekday_match = re.search(
        r"\b(?:on\s+|this\s+|next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"mon|tue|wed|thu|fri|sat|sun)\b",
        q
    )
    if weekday_match:
        target_wd = WEEKDAYS.get(weekday_match.group(1))
        if target_wd is not None:
            is_next = "next" in weekday_match.group(0)
            delta = (target_wd - ref_date.weekday()) % 7
            if delta == 0 and is_next:
                delta = 7
            elif delta == 0:
                delta = 7  # treat upcoming instance
            elif is_next:
                delta += 7
            return ref_date + timedelta(days=delta)

    # Specific dates: "15th oct", "october 15", "15/10/2026"
    date_num = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+(\d{4}))?\b", q)
    if date_num:
        d = int(date_num.group(1))
        m = MONTHS.get(date_num.group(2)[:3])
        y = int(date_num.group(3)) if date_num.group(3) else ref_date.year
        if m:
            try:
                return date(y, m, d)
            except ValueError:
                pass

    date_month_first = re.search(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?\b", q)
    if date_month_first:
        m = MONTHS.get(date_month_first.group(1)[:3])
        d = int(date_month_first.group(2))
        y = int(date_month_first.group(3)) if date_month_first.group(3) else ref_date.year
        if m:
            try:
                return date(y, m, d)
            except ValueError:
                pass

    return None


def extract_times_from_query(query: str) -> tuple[Optional[time], Optional[time], Optional[int]]:
    """Extract start time, end time, and duration minutes from query."""
    q = query.lower()

    # Pattern A: "3 PM to 6 PM", "3:00pm - 6:30pm", "from 3 PM to 6 PM", "between 3 and 6 pm", "15:00 to 18:00"
    range_match = re.search(
        r"(?:from\s+|between\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to|-|until|till|and)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
        q
    )
    if range_match:
        h1, m1, mer1, h2, m2, mer2 = range_match.groups()
        # If first meridiem missing, inherit from second if reasonable
        if not mer1:
            mer1 = mer2
        t_start = _parse_clock_time(h1, m1, mer1)
        t_end = _parse_clock_time(h2, m2, mer2)
        if t_start and t_end:
            dur = int((datetime.combine(date.today(), t_end) - datetime.combine(date.today(), t_start)).total_seconds() // 60)
            if dur < 0:
                dur += 24 * 60
            return t_start, t_end, dur

    # Pattern B: 24-hour range "15:00 to 18:00", "15:00 - 18:30"
    range_24 = re.search(r"(\d{1,2}):(\d{2})\s*(?:to|-|until)\s*(\d{1,2}):(\d{2})", q)
    if range_24:
        h1, m1, h2, m2 = range_24.groups()
        t_start = _parse_clock_time(h1, m1, None)
        t_end = _parse_clock_time(h2, m2, None)
        if t_start and t_end:
            dur = int((datetime.combine(date.today(), t_end) - datetime.combine(date.today(), t_start)).total_seconds() // 60)
            if dur < 0:
                dur += 24 * 60
            return t_start, t_end, dur

    # Pattern C: "from 3 PM for 2 hours", "at 4:30 pm for 90 minutes", "free for 3 hours from 2pm"
    duration_match = re.search(
        r"(?:from|at)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:for\s+)?(?:about\s+)?(\d+(?:\.\d+)?)\s*(hours?|hrs?|mins?|minutes?)",
        q
    )
    if duration_match:
        h1, m1, mer1, dur_val, dur_unit = duration_match.groups()
        t_start = _parse_clock_time(h1, m1, mer1 or "pm")
        if t_start:
            num = float(dur_val)
            dur_mins = int(num * 60 if "h" in dur_unit else num)
            dt_end = datetime.combine(date.today(), t_start) + timedelta(minutes=dur_mins)
            return t_start, dt_end.time(), dur_mins

    # Pattern D: "free for 3 hours" / "have 2 free hours" / "have 2 hours" (duration only)
    dur_only = re.search(r"\b(?:free for|have|got)\s+(?:about\s+)?(\d+(?:\.\d+)?)\s*(?:free\s+)?(hours?|hrs?|mins?|minutes?)\b", q)
    duration_minutes = None
    if dur_only:
        num = float(dur_only.group(1))
        duration_minutes = int(num * 60 if "h" in dur_only.group(2) else num)

    # Pattern E: single start point: "after 7 PM", "from 6pm", "at 5 PM"
    single_start = re.search(r"\b(?:after|from|starting at|at)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", q)
    if single_start:
        h1, m1, mer1 = single_start.groups()
        t_start = _parse_clock_time(h1, m1, mer1)
        if t_start:
            dur = duration_minutes or 180  # default 3 hours
            dt_end = datetime.combine(date.today(), t_start) + timedelta(minutes=dur)
            return t_start, dt_end.time(), dur

    return None, None, duration_minutes


def resolve_canonical_schedule(
    query: str,
    now: datetime,
    picker_start: Optional[datetime] = None,
    picker_end: Optional[datetime] = None,
    mode: str = "auto",
    default_duration_hours: float = 3.0,
) -> CanonicalSchedule:
    """Resolve the definitive scheduling window, enforcing natural-language precedence over pickers.

    Rules:
    1. If query explicitly defines a date, that date overrides the picker date.
    2. If query explicitly defines a time window (start & end), that window overrides the picker times.
    3. If picker supplied conflicting values, flag them as overridden with a clear explanation.
    4. If query is partial (e.g., date specified but not time), fill remaining details from picker.
    5. Preserves exact dates and times without shifting or silent drifts.
    """
    ref_date = now.date()
    range_start, range_end = extract_date_range_from_query(query, ref_date)
    if range_start and range_end:
        picker_start_clean = picker_start.replace(tzinfo=None) if picker_start else None
        picker_end_clean = picker_end.replace(tzinfo=None) if picker_end else None
        overridden_picker = False
        override_notes = []
        if picker_start_clean and (picker_start_clean.date() != range_start or (picker_end_clean and picker_end_clean.date() != range_end)):
            overridden_picker = True
            override_notes.append(
                f"Requested dates ({range_start.strftime('%a, %d %b')} to {range_end.strftime('%a, %d %b')}) took priority over form picker."
            )
        start_time_val = time(8, 0)
        end_time_val = time(21, 0)
        extracted_start_time, extracted_end_time, _ = extract_times_from_query(query)
        if extracted_start_time:
            start_time_val = extracted_start_time
        if extracted_end_time:
            end_time_val = extracted_end_time
        final_start = datetime.combine(range_start, start_time_val)
        final_end = datetime.combine(range_end, end_time_val)
        return CanonicalSchedule(
            start=final_start,
            end=final_end,
            source="query_explicit",
            query_extracted=True,
            overridden_picker=overridden_picker,
            override_note="; ".join(override_notes) if override_notes else None,
            locked=True,
        )

    extracted_date = extract_dates_from_query(query, ref_date)
    extracted_start_time, extracted_end_time, extracted_dur = extract_times_from_query(query)

    query_has_date = extracted_date is not None
    query_has_times = extracted_start_time is not None and extracted_end_time is not None
    query_has_partial_time = extracted_start_time is not None and extracted_end_time is None

    picker_start_clean = picker_start.replace(tzinfo=None) if picker_start else None
    picker_end_clean = picker_end.replace(tzinfo=None) if picker_end else None

    overridden_picker = False
    override_notes: list[str] = []

    # 1. Resolve Target Date
    if query_has_date:
        target_date = extracted_date
        if picker_start_clean and picker_start_clean.date() != target_date:
            overridden_picker = True
            override_notes.append(
                f"Requested date '{target_date.strftime('%a, %d %b')}' took priority over picker date '{picker_start_clean.date().strftime('%a, %d %b')}'."
            )
    elif picker_start_clean:
        target_date = picker_start_clean.date()
    else:
        # Default date
        if mode == "evening" and now.time() > time(22, 30):
            target_date = ref_date + timedelta(days=1)
        elif mode == "weekend":
            days_ahead = (5 - ref_date.weekday()) % 7
            target_date = ref_date + timedelta(days=days_ahead if days_ahead > 0 else 7)
        elif mode in ("domestic_trip", "international_trip", "business_trip"):
            target_date = ref_date + timedelta(days=7)
        else:
            target_date = ref_date

    # 2. Resolve Start & End Times
    if query_has_times:
        final_start = datetime.combine(target_date, extracted_start_time)
        final_end = datetime.combine(target_date, extracted_end_time)
        if final_end <= final_start:
            # overnight window
            final_end += timedelta(days=1)

        if picker_start_clean and (picker_start_clean.time() != extracted_start_time or (picker_end_clean and picker_end_clean.time() != extracted_end_time)):
            overridden_picker = True
            override_notes.append(
                f"Natural-language window ({extracted_start_time.strftime('%I:%M %p').lstrip('0')} to {extracted_end_time.strftime('%I:%M %p').lstrip('0')}) took priority over form picker."
            )
        source = "query_explicit"

    elif query_has_partial_time:
        final_start = datetime.combine(target_date, extracted_start_time)
        dur = extracted_dur or int(default_duration_hours * 60)
        final_end = final_start + timedelta(minutes=dur)
        source = "query_explicit"

    elif picker_start_clean and picker_end_clean:
        final_start = picker_start_clean
        final_end = picker_end_clean
        if query_has_date:
            # Transfer picker clock times to target_date
            final_start = datetime.combine(target_date, picker_start_clean.time())
            final_end = datetime.combine(target_date, picker_end_clean.time())
            if final_end <= final_start:
                final_end += timedelta(days=1)
        source = "picker_explicit"

    elif picker_start_clean:
        final_start = picker_start_clean
        dur = extracted_dur or int(default_duration_hours * 60)
        final_end = final_start + timedelta(minutes=dur)
        source = "picker_explicit"

    else:
        # Derived times based on mode
        if mode == "evening":
            final_start = max(datetime.combine(target_date, time(17, 0)), now.replace(second=0, microsecond=0))
            final_end = datetime.combine(target_date, time(23, 0))
        elif mode in ("day", "local_outing"):
            final_start = max(datetime.combine(target_date, time(9, 0)), now.replace(second=0, microsecond=0))
            final_end = datetime.combine(target_date, time(21, 0))
        elif mode == "weekend":
            final_start = datetime.combine(target_date, time(9, 0))
            final_end = datetime.combine(target_date + timedelta(days=1), time(21, 0))
        elif mode in ("domestic_trip", "international_trip", "business_trip"):
            final_start = datetime.combine(target_date, time(8, 0))
            final_end = datetime.combine(target_date + timedelta(days=2), time(21, 0))
        else:
            base = (now + timedelta(minutes=(-now.minute) % 15)).replace(second=0, microsecond=0)
            if target_date > ref_date:
                final_start = datetime.combine(target_date, time(15, 0))
            else:
                final_start = base
            dur = extracted_dur or int(default_duration_hours * 60)
            final_end = final_start + timedelta(minutes=dur)
        source = "derived"

    return CanonicalSchedule(
        start=final_start,
        end=final_end,
        source=source,
        query_extracted=query_has_date or query_has_times,
        overridden_picker=overridden_picker,
        override_note=" ".join(override_notes) if override_notes else None,
        locked=True,
    )
