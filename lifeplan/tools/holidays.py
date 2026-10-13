"""Public holidays from Nager.Date (free, no key). Used by the Profile agent: a public holiday
usually means the user's work/college routine does not apply that day."""

from __future__ import annotations

from lifeplan.tools.http import ExternalAPIError, request_json

NAGER = "https://date.nager.at/api/v3/PublicHolidays/{year}/{cc}"


def public_holidays(country_code: str, year: int) -> dict:
    try:
        data = request_json("nager-date", "GET", NAGER.format(year=year, cc=country_code.upper()),
                            cache_ttl=86400 * 7)
    except ExternalAPIError as exc:
        # Nager.Date doesn't cover every country (404). The agent is told this explicitly.
        return {"ok": False, "error": str(exc), "holidays": []}
    return {"ok": True, "source": "nager.date",
            "holidays": [{"date": h.get("date"), "name": h.get("localName") or h.get("name")} for h in data or []]}
