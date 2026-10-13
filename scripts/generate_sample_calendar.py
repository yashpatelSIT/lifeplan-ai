"""Generate samples/sample_calendar.ics — the demo user's (synthetic) calendar.

Weekly routine (RRULE) + one-off events, starting 2026-08-03, so the Profile agent has several
weeks of history to learn from and real upcoming commitments to plan around.
Run:  python scripts/generate_sample_calendar.py
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "samples" / "sample_calendar.ics"
COLLEGE = "Symbiosis Institute of Technology, Lavale, Pune"

RECURRING = [
    # (uid, summary, location, first date, start, end, BYDAY)
    ("lectures", "Lectures", COLLEGE, "20260803", "090000", "163000", "MO,TU,WE,TH,FR"),
    ("gym", "Gym", "Shivajinagar, Pune", "20260804", "180000", "190000", "TU,TH"),
    ("team-meeting", "CA3 team meeting", COLLEGE, "20260805", "173000", "193000", "WE"),
    ("badminton", "Badminton", "PYC Hindu Gymkhana, Pune", "20260808", "073000", "090000", "SA"),
]

ONE_OFF = [
    ("dentist", "Dentist appointment", "Deccan, Pune", "20260918T110000", "20260918T120000"),
    ("family-dinner", "Family dinner", "Home", "20260920T200000", "20260920T220000"),
    ("hackathon", "Hackathon", COLLEGE, "20261010T090000", "20261010T210000"),
    ("guest-lecture", "Guest lecture: Agentic AI", COLLEGE, "20261016T163000", "20261016T183000"),
    ("cousin-wedding", "Cousin's wedding reception", "Koregaon Park, Pune", "20261025T190000", "20261025T230000"),
]


def main() -> None:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//LifePlan AI//sample calendar//EN", "CALSCALE:GREGORIAN"]
    for uid, summary, loc, day, s, e, byday in RECURRING:
        lines += ["BEGIN:VEVENT", f"UID:{uid}@lifeplan-ai.sample", "DTSTAMP:20260801T000000Z",
                  f"DTSTART;TZID=Asia/Kolkata:{day}T{s}", f"DTEND;TZID=Asia/Kolkata:{day}T{e}",
                  f"RRULE:FREQ=WEEKLY;BYDAY={byday}", f"SUMMARY:{summary}", f"LOCATION:{loc}", "END:VEVENT"]
    for uid, summary, loc, s, e in ONE_OFF:
        lines += ["BEGIN:VEVENT", f"UID:{uid}@lifeplan-ai.sample", "DTSTAMP:20260801T000000Z",
                  f"DTSTART;TZID=Asia/Kolkata:{s}", f"DTEND;TZID=Asia/Kolkata:{e}",
                  f"SUMMARY:{summary}", f"LOCATION:{loc}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
