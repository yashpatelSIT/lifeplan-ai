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


def validate_coordinates(coord: tuple[float, float]) -> tuple[float, float]:
    """Validate and normalize (lat, lon) coordinates, detecting swapped order."""
    lat, lon = float(coord[0]), float(coord[1])
    # Detect swapped coordinates: for Indian sub-continent (lat 8..37, lon 68..97),
    # if lat > 50 and lon < 40, they are inverted.
    if lat > 50.0 and lon < 40.0:
        lat, lon = lon, lat
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"Invalid geographical coordinates: ({lat}, {lon})")
    return lat, lon


def is_plausible_route(a: tuple[float, float], b: tuple[float, float], km: float, minutes: float, mode: str) -> tuple[bool, Optional[str]]:
    """Verify that a calculated route between two points is geographically and physically plausible."""
    direct_km = haversine_km(a[0], a[1], b[0], b[1])
    if km < direct_km * 0.9 and direct_km > 0.1:
        return False, f"Route distance ({km:.1f} km) is shorter than straight-line distance ({direct_km:.1f} km)"
    if km > direct_km * 4.0 and direct_km > 5.0:
        return False, f"Route distance ({km:.1f} km) indicates excessive detour from straight-line ({direct_km:.1f} km)"
    speed = (km / (minutes / 60.0)) if minutes > 0 else 0
    if mode in ("walk", "foot") and speed > 10.0:
        return False, f"Walking speed of {speed:.1f} km/h is implausibly high"
    if mode in ("drive", "cab", "car") and speed > 160.0:
        return False, f"Driving speed of {speed:.1f} km/h is implausibly high"
    # Reject implausibly slow speeds (e.g. 1,204 minutes for 5 km = 0.25 km/h driving)
    if km > 0.5 and minutes > 60 and speed < 2.0:
        return False, f"Travel speed of {speed:.2f} km/h is implausibly low (e.g. {round(minutes)} min for {km:.1f} km)"
    if km < 20.0 and minutes > 360:
        return False, f"Travel duration of {round(minutes)} min is excessive for local distance of {km:.1f} km"
    return True, None


def estimate_leg(a: tuple[float, float], b: tuple[float, float], mode: str) -> dict:
    a = validate_coordinates(a)
    b = validate_coordinates(b)
    km = haversine_km(a[0], a[1], b[0], b[1]) * DETOUR
    mode = normalise_mode(mode)
    speed = SPEED_KMH.get(mode, 20)
    minutes = km / speed * 60 + (OVERHEAD_MIN.get(mode, 5) if km > 0.05 else 0)
    return {"ok": True, "mode": mode, "minutes": round(minutes, 1), "km": round(km, 2),
            "source": "estimate", "status": "estimate"}


def _geoapify_leg(a, b, mode) -> Optional[dict]:
    key = get_settings().geoapify_api_key
    if not key or mode not in GEOAPIFY_MODE:
        return None
    a = validate_coordinates(a)
    b = validate_coordinates(b)
    params = {"waypoints": f"{a[0]},{a[1]}|{b[0]},{b[1]}", "mode": GEOAPIFY_MODE[mode], "apiKey": key}
    data = request_json("geoapify-routing", "GET", GEOAPIFY_ROUTING, params=params, cache_ttl=1800)
    feats = data.get("features") or []
    if not feats:
        return None
    p = feats[0]["properties"]
    minutes = p["time"] / 60 + (5 if mode == "cab" else 0)
    km = p["distance"] / 1000
    plausible, _ = is_plausible_route(a, b, km, minutes, mode)
    if not plausible:
        return None
    return {"ok": True, "mode": mode, "minutes": round(minutes, 1), "km": round(km, 2),
            "source": "geoapify", "status": "verified"}


def _osrm_leg(a, b, mode) -> Optional[dict]:
    profile = OSRM_PROFILE.get(mode)
    if not profile:
        return None
    a = validate_coordinates(a)
    b = validate_coordinates(b)
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
    km = routes[0]["distance"] / 1000
    plausible, _ = is_plausible_route(a, b, km, minutes, mode)
    if not plausible:
        return None
    return {"ok": True, "mode": mode, "minutes": round(minutes, 1), "km": round(km, 2),
            "source": "osrm" if mode != "transit" else "osrm+transit-model", "status": "verified"}


def travel_time(a: tuple[float, float], b: tuple[float, float], mode: str = "cab") -> dict:
    """Door-to-door travel time between two (lat, lon) points."""
    a = validate_coordinates(a)
    b = validate_coordinates(b)
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
    import math
    mode = normalise_mode(mode)
    cur = currency.upper()
    if mode in ("walk", "bike"):
        return {"amount": 0, "currency": cur, "estimate": True}
    if mode in ("cab", "drive"):
        fare = cab_fare(km, minutes, cur)
        if mode == "drive":  # own vehicle: fuel only, ~40% of a cab fare
            fare["amount"] = round(fare["amount"] * 0.4)
        elif party_size > 4:
            cabs = max(1, math.ceil(party_size / 4))
            fare["amount"] = round(fare["amount"] * cabs)
        return fare
    if mode == "transit":
        return {"amount": round(TRANSIT_FARE.get(cur, 2.5) * party_size), "currency": cur, "estimate": True}
    return {"amount": 0, "currency": cur, "estimate": True}
