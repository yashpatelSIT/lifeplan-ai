"""Geocoding: Geoapify (keyed) -> OpenStreetMap Nominatim (free) fallback."""

from __future__ import annotations

import math
from typing import Optional

from lifeplan.config import get_settings
from lifeplan.tools.http import ExternalAPIError, request_json

GEOAPIFY_GEOCODE = "https://api.geoapify.com/v1/geocode/search"
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _geoapify(text: str, near: Optional[tuple[float, float]] = None) -> Optional[dict]:
    key = get_settings().geoapify_api_key
    if not key:
        return None
    params = {"text": text, "format": "json", "limit": 1, "apiKey": key}
    if near:
        params["bias"] = f"proximity:{near[1]},{near[0]}"
    data = request_json("geoapify-geocode", "GET", GEOAPIFY_GEOCODE, params=params, cache_ttl=86400)
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    return {
        "name": r.get("formatted") or text,
        "lat": r["lat"],
        "lon": r["lon"],
        "city": r.get("city") or r.get("county") or r.get("state"),
        "country": r.get("country"),
        "country_code": (r.get("country_code") or "").upper() or None,
        "timezone": (r.get("timezone") or {}).get("name"),
        "source": "geoapify",
    }


def _nominatim(text: str) -> Optional[dict]:
    params = {"q": text, "format": "jsonv2", "limit": 1, "addressdetails": 1}
    data = request_json("nominatim", "GET", NOMINATIM_SEARCH, params=params, cache_ttl=86400)
    if not data:
        return None
    r = data[0]
    addr = r.get("address") or {}
    return {
        "name": r.get("display_name") or text,
        "lat": float(r["lat"]),
        "lon": float(r["lon"]),
        "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("state"),
        "country": addr.get("country"),
        "country_code": (addr.get("country_code") or "").upper() or None,
        "timezone": None,
        "source": "openstreetmap",
    }


def geocode(text: str, near: Optional[tuple[float, float]] = None) -> dict:
    """Resolve a place name/address to coordinates. Returns {'ok': False, ...} if nothing works."""
    errors = []
    for fn in (lambda: _geoapify(text, near), lambda: _nominatim(text)):
        try:
            result = fn()
            if result:
                return {"ok": True, **result}
        except ExternalAPIError as exc:
            errors.append(str(exc))
    return {"ok": False, "error": f"could not geocode '{text}'", "details": errors}
