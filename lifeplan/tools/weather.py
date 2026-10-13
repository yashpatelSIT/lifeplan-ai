"""Weather from Open-Meteo (free, no key). Forecast up to 16 days; beyond that we look at the
same dates last year from the Open-Meteo archive and clearly label it as 'historical'."""

from __future__ import annotations

from datetime import date, timedelta

from lifeplan.tools.http import ExternalAPIError, request_json

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "violent showers", 85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


def _is_wet(code: int | None, prob: float | None, mm: float | None) -> bool:
    if code is not None and code >= 51 and code not in (71, 73, 75, 77, 85, 86):
        return (prob is None or prob >= 40) or (mm or 0) >= 0.5
    return (prob or 0) >= 60 and (mm or 0) >= 0.3


def get_weather(lat: float, lon: float, start: date, end: date | None = None) -> dict:
    end = end or start
    today = date.today()
    historical = start > today + timedelta(days=15)
    try:
        if not historical:
            params = {
                "latitude": lat, "longitude": lon, "timezone": "auto",
                "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                         "precipitation_probability_max,sunrise,sunset",
                "start_date": max(start, today).isoformat(), "end_date": min(end, today + timedelta(days=15)).isoformat(),
            }
            data = request_json("open-meteo", "GET", FORECAST, params=params, cache_ttl=1800)
        else:
            ly_start, ly_end = start.replace(year=start.year - 1), end.replace(year=end.year - 1)
            params = {
                "latitude": lat, "longitude": lon, "timezone": "auto",
                "hourly": "temperature_2m,precipitation,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,sunrise,sunset",
                "start_date": ly_start.isoformat(), "end_date": ly_end.isoformat(),
            }
            data = request_json("open-meteo-archive", "GET", ARCHIVE, params=params, cache_ttl=86400)
    except ExternalAPIError as exc:
        return {"ok": False, "error": str(exc), "note": "weather unavailable; plan assumes fair weather"}

    h = data.get("hourly", {})
    hourly = []
    for i, t in enumerate(h.get("time", [])):
        code = (h.get("weather_code") or [None] * (i + 1))[i]
        prob = (h.get("precipitation_probability") or [None] * (i + 1))[i]
        mm = (h.get("precipitation") or [None] * (i + 1))[i]
        hourly.append({
            "time": t, "temp_c": (h.get("temperature_2m") or [None] * (i + 1))[i],
            "precip_prob": prob, "precip_mm": mm, "code": code, "desc": WMO.get(code, "unknown"),
            "wet": _is_wet(code, prob, mm),
        })
    d = data.get("daily", {})
    daily = []
    for i, t in enumerate(d.get("time", [])):
        code = d.get("weather_code", [None])[i]
        daily.append({
            "date": t, "desc": WMO.get(code, "unknown"),
            "t_max": d.get("temperature_2m_max", [None])[i], "t_min": d.get("temperature_2m_min", [None])[i],
            "precip_mm": d.get("precipitation_sum", [None])[i],
            "precip_prob_max": (d.get("precipitation_probability_max") or [None] * (i + 1))[i],
            "sunrise": (d.get("sunrise") or [None] * (i + 1))[i], "sunset": (d.get("sunset") or [None] * (i + 1))[i],
        })
    wet_hours = [x["time"] for x in hourly if x["wet"]]
    return {
        "ok": True,
        "kind": "historical-same-dates-last-year" if historical else "forecast",
        "timezone": data.get("timezone"),
        "daily": daily,
        "hourly": hourly,
        "wet_hours": wet_hours,
        "source": "open-meteo",
    }


def summarise(weather: dict, max_hours: int = 0) -> dict:
    """Compact form for LLM prompts (keeps token use low)."""
    if not weather or not weather.get("ok"):
        return {"available": False, "note": (weather or {}).get("note", "no data")}
    out = {"kind": weather["kind"], "daily": weather["daily"],
           "wet_hours": weather["wet_hours"][:24]}
    if max_hours:
        out["hourly"] = weather["hourly"][:max_hours]
    return out
