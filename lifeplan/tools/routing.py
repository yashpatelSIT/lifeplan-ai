"""Travel times between places.

Order of preference for each request:
  1. Geoapify Routing / Route Matrix (keyed, supports walk/drive/bicycle/approximated transit)
  2. OSRM public servers run by openstreetmap.de (free; car / foot / bike)
  3. A transparent distance-based estimate (always available, flagged `source="estimate"`)
"""

from __future__ import annotations

from typing import Optional

from lifeplan.config import get_settings
from lifeplan.tools.geo import haversine_km
from lifeplan.tools.http import ExternalAPIError, request_json

GEOAPIFY_ROUTING = "https://api.geoapify.com/v1/routing"
GEOAPIFY_MATRIX = "https://api.geoapify.com/v1/routematrix"
OSRM_BASE = "https://routing.openstreetmap.de/routed-{profile}/{service}/v1/driving/{coords}"

GEOAPIFY_MODE = {"walk": "walk", "drive": "drive", "cab": "drive", "bike": "bicycle", "transit": "approximated_transit"}
OSRM_PROFILE = {"walk": "foot", "drive": "car", "cab": "car", "bike": "bike", "transit": "car"}

# Average door-to-door speeds used for the estimate fallback (km/h) and fixed overheads (min)
SPEED_KMH = {"walk": 4.8, "bike": 13, "drive": 24, "cab": 24, "transit": 17}
OVERHEAD_MIN = {"walk": 0, "bike": 2, "drive": 5, "cab": 8, "transit": 10}
DETOUR = 1.35

# Indicative city cab fares (base, per km, per minute)
CAB_FARES = {
    "INR": (50.0, 14.0, 1.5),
    "USD": (3.0, 1.5, 0.35),
    "EUR": (3.5, 1.6, 0.35),
    "GBP": (3.5, 1.5, 0.3),
    "JPY": (500.0, 330.0, 50.0),
    "AED": (12.0, 2.2, 0.5),
    "SGD": (4.0, 0.8, 0.3),
    "THB": (35.0, 7.0, 2.0),
}
TRANSIT_FARE = {"INR": 30.0, "USD": 2.75, "EUR": 2.5, "GBP": 2.8, "JPY": 210.0, "AED": 5.0, "SGD": 1.8, "THB": 30.0}


def normalise_mode(mode: str) -> str:
    mode = (mode or "cab").lower()
    return {"driving": "drive", "car": "drive", "taxi": "cab", "uber": "cab", "foot": "walk", "walking": "walk",
            "bus": "transit", "metro": "transit", "public_transport": "transit", "bicycle": "bike"}.get(mode, mode)


def estimate_leg(a: tuple[float, float], b: tuple[float, float], mode: str) -> dict:
    km = haversine_km(a[0], a[1], b[0], b[1]) * DETOUR
    mode = normalise_mode(mode)
    speed = SPEED_KMH.get(mode, 20)
    minutes = km / speed * 60 + (OVERHEAD_MIN.get(mode, 5) if km > 0.05 else 0)
    return {"ok": True, "mode": mode, "minutes": round(minutes, 1), "km": round(km, 2), "source": "estimate"}


def _geoapify_leg(a, b, mode) -> Optional[dict]:
    key = get_settings().geoapify_api_key
    if not key or mode not in GEOAPIFY_MODE:
        return None
    params = {"waypoints": f"{a[0]},{a[1]}|{b[0]},{b[1]}", "mode": GEOAPIFY_MODE[mode], "apiKey": key}
    data = request_json("geoapify-routing", "GET", GEOAPIFY_ROUTING, params=params, cache_ttl=1800)
    feats = data.get("features") or []
    if not feats:
        return None
    p = feats[0]["properties"]
    minutes = p["time"] / 60 + (5 if mode == "cab" else 0)
    return {"ok": True, "mode": mode, "minutes": round(minutes, 1), "km": round(p["distance"] / 1000, 2),
            "source": "geoapify"}


def _osrm_leg(a, b, mode) -> Optional[dict]:
    profile = OSRM_PROFILE.get(mode)
    if not profile:
        return None
    url = OSRM_BASE.format(profile=profile, service="route", coords=f"{a[1]},{a[0]};{b[1]},{b[0]}")
    data = request_json("osrm", "GET", url, params={"overview": "false"}, cache_ttl=1800)
    routes = data.get("routes") or []
    if not routes:
        return None
    minutes = routes[0]["duration"] / 60
    if mode == "cab":
        minutes += 5
    if mode == "transit":  # OSRM has no transit: scale the car time and add waiting time
        minutes = minutes * 1.6 + 10
    return {"ok": True, "mode": mode, "minutes": round(minutes, 1), "km": round(routes[0]["distance"] / 1000, 2),
            "source": "osrm" if mode != "transit" else "osrm+transit-model"}


def travel_time(a: tuple[float, float], b: tuple[float, float], mode: str = "cab") -> dict:
    """Door-to-door travel time between two (lat, lon) points."""
    mode = normalise_mode(mode)
    warnings = []
    for fn in (_geoapify_leg, _osrm_leg):
        try:
            leg = fn(a, b, mode)
            if leg:
                return leg
        except ExternalAPIError as exc:
            warnings.append(str(exc))
    leg = estimate_leg(a, b, mode)
    if warnings:
        leg["warnings"] = warnings
    return leg


def travel_matrix(points: list[tuple[float, float]], mode: str = "cab") -> dict:
    """Minutes between every pair of points with ONE API call: OSRM table (free, real road network) first,
    Geoapify Route Matrix second, distance estimate last."""
    mode = normalise_mode(mode)
    n = len(points)
    if n == 0:
        return {"ok": True, "mode": mode, "minutes": [], "km": [], "source": "empty"}
    profile = OSRM_PROFILE.get(mode)
    if profile and n <= 25:
        try:
            coords = ";".join(f"{p[1]},{p[0]}" for p in points)
            url = OSRM_BASE.format(profile=profile, service="table", coords=coords)
            data = request_json("osrm", "GET", url, params={"annotations": "duration,distance"}, cache_ttl=1800)
            factor, extra = (1.6, 10) if mode == "transit" else (1.0, 5 if mode == "cab" else 0)
            minutes = [[round((d or 0) / 60 * factor + (extra if i != j else 0), 1) for j, d in enumerate(r)]
                       for i, r in enumerate(data["durations"])]
            km = [[round((d or 0) / 1000, 2) for d in r] for r in data.get("distances", [])] or None
            return {"ok": True, "mode": mode, "minutes": minutes, "km": km, "source": "osrm"}
        except (ExternalAPIError, KeyError, TypeError):
            pass
    key = get_settings().geoapify_api_key
    if key and mode in GEOAPIFY_MODE and n <= 20:
        try:
            body = {"mode": GEOAPIFY_MODE[mode],
                    "sources": [{"location": [p[1], p[0]]} for p in points],
                    "targets": [{"location": [p[1], p[0]]} for p in points]}
            data = request_json("geoapify-matrix", "POST", GEOAPIFY_MATRIX, params={"apiKey": key},
                                json_body=body, cache_ttl=1800)
            rows = data["sources_to_targets"]
            minutes = [[round((c.get("time") or 0) / 60 + (5 if mode == "cab" and i != j else 0), 1)
                        for j, c in enumerate(r)] for i, r in enumerate(rows)]
            km = [[round((c.get("distance") or 0) / 1000, 2) for c in r] for r in rows]
            return {"ok": True, "mode": mode, "minutes": minutes, "km": km, "source": "geoapify"}
        except (ExternalAPIError, KeyError, TypeError):
            pass
    minutes, km = [], []
    for a in points:
        row_m, row_k = [], []
        for b in points:
            leg = estimate_leg(a, b, mode)
            row_m.append(leg["minutes"] if a != b else 0)
            row_k.append(leg["km"] if a != b else 0)
        minutes.append(row_m)
        km.append(row_k)
    return {"ok": True, "mode": mode, "minutes": minutes, "km": km, "source": "estimate"}


def cab_fare(km: float, minutes: float, currency: str = "INR") -> dict:
    base, per_km, per_min = CAB_FARES.get(currency.upper(), CAB_FARES["USD"])
    used_currency = currency.upper() if currency.upper() in CAB_FARES else "USD"
    return {"amount": round(base + per_km * km + per_min * minutes), "currency": used_currency, "estimate": True}


def leg_cost(mode: str, km: float, minutes: float, currency: str = "INR", party_size: int = 1) -> dict:
    mode = normalise_mode(mode)
    cur = currency.upper()
    if mode in ("walk", "bike"):
        return {"amount": 0, "currency": cur, "estimate": True}
    if mode in ("cab", "drive"):
        fare = cab_fare(km, minutes, cur)
        if mode == "drive":  # own vehicle: fuel only, ~40% of a cab fare
            fare["amount"] = round(fare["amount"] * 0.4)
        return fare
    if mode == "transit":
        return {"amount": round(TRANSIT_FARE.get(cur, 2.5) * party_size), "currency": cur, "estimate": True}
    return {"amount": 0, "currency": cur, "estimate": True}
