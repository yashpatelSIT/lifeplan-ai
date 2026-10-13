"""Discovery of REAL venues: Geoapify Places (keyed) -> OpenStreetMap Overpass (free) fallback.

Every candidate returned carries its data source and source id, so the Planner can only
schedule venues that verifiably exist (this is our answer to the hallucination/grounding
concern raised in the project review).
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from lifeplan.config import get_settings
from lifeplan.tools.geo import haversine_km
from lifeplan.tools.http import ExternalAPIError, request_json

log = logging.getLogger("lifeplan.places")

GEOAPIFY_PLACES = "https://api.geoapify.com/v2/places"
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

# interest keyword -> how to search for it + planning defaults
# cost is a rough per-person estimate in INR, converted for other currencies by the Logistics agent.
CATEGORIES: dict[str, dict] = {
    "food": {
        "geoapify": ["catering.restaurant"],
        "osm": [("amenity", "restaurant")],
        "duration": 75, "cost_inr": 600, "indoor": True, "kind": "meal",
    },
    "cafe": {
        "geoapify": ["catering.cafe"],
        "osm": [("amenity", "cafe")],
        "duration": 45, "cost_inr": 300, "indoor": True, "kind": "snack",
    },
    "street_food": {
        "geoapify": ["catering.fast_food", "catering.food_court"],
        "osm": [("amenity", "fast_food"), ("amenity", "food_court")],
        "duration": 40, "cost_inr": 200, "indoor": False, "kind": "snack",
    },
    "nightlife": {
        "geoapify": ["catering.bar", "catering.pub", "adult.nightclub"],
        "osm": [("amenity", "bar"), ("amenity", "pub"), ("amenity", "nightclub")],
        "duration": 90, "cost_inr": 1200, "indoor": True, "kind": "activity",
    },
    "museum": {
        "geoapify": ["entertainment.museum"],
        "osm": [("tourism", "museum")],
        "duration": 90, "cost_inr": 100, "indoor": True, "kind": "activity",
    },
    "culture": {
        "geoapify": ["entertainment.culture", "tourism.sights", "building.historic"],
        "osm": [("amenity", "theatre"), ("amenity", "arts_centre"), ("historic", "monument"), ("historic", "fort")],
        "duration": 75, "cost_inr": 150, "indoor": False, "kind": "activity",
    },
    "attractions": {
        "geoapify": ["tourism.attraction", "tourism.sights"],
        "osm": [("tourism", "attraction"), ("tourism", "viewpoint")],
        "duration": 60, "cost_inr": 100, "indoor": False, "kind": "activity",
    },
    "nature": {
        "geoapify": ["leisure.park", "natural"],
        "osm": [("leisure", "park"), ("leisure", "garden"), ("natural", "peak")],
        "duration": 60, "cost_inr": 0, "indoor": False, "kind": "activity",
    },
    "shopping": {
        "geoapify": ["commercial.shopping_mall", "commercial.marketplace"],
        "osm": [("shop", "mall"), ("amenity", "marketplace")],
        "duration": 75, "cost_inr": 800, "indoor": True, "kind": "activity",
    },
    "movies": {
        "geoapify": ["entertainment.cinema"],
        "osm": [("amenity", "cinema")],
        "duration": 150, "cost_inr": 350, "indoor": True, "kind": "activity",
    },
    "entertainment": {
        "geoapify": ["entertainment.bowling_alley", "entertainment.escape_game", "entertainment.activity_park",
                     "entertainment.amusement_arcade", "entertainment.theme_park"],
        "osm": [("leisure", "bowling_alley"), ("leisure", "escape_game"), ("leisure", "amusement_arcade")],
        "duration": 90, "cost_inr": 700, "indoor": True, "kind": "activity",
    },
    "sports": {
        "geoapify": ["sport.sports_centre", "sport.stadium", "sport.swimming_pool"],
        "osm": [("leisure", "sports_centre"), ("leisure", "stadium")],
        "duration": 90, "cost_inr": 300, "indoor": True, "kind": "activity",
    },
    "religious": {
        "geoapify": ["religion.place_of_worship"],
        "osm": [("amenity", "place_of_worship")],
        "duration": 40, "cost_inr": 0, "indoor": True, "kind": "activity",
    },
    "learning": {
        "geoapify": ["education.library", "entertainment.planetarium"],
        "osm": [("amenity", "library"), ("amenity", "planetarium")],
        "duration": 60, "cost_inr": 50, "indoor": True, "kind": "activity",
    },
    "hotel": {
        "geoapify": ["accommodation.hotel", "accommodation.guest_house"],
        "osm": [("tourism", "hotel"), ("tourism", "guest_house")],
        "duration": 0, "cost_inr": 3000, "indoor": True, "kind": "hotel",
    },
}

# Free-text interests users type -> our category keys
SYNONYMS = {
    "restaurant": "food", "dinner": "food", "lunch": "food", "eat": "food", "dining": "food", "food": "food",
    "breakfast": "cafe", "coffee": "cafe", "cafe": "cafe", "dessert": "cafe",
    "street food": "street_food", "snacks": "street_food", "fast food": "street_food",
    "bar": "nightlife", "pub": "nightlife", "club": "nightlife", "drinks": "nightlife", "nightlife": "nightlife",
    "live music": "nightlife", "music": "nightlife",
    "museum": "museum", "museums": "museum", "history": "culture", "heritage": "culture", "art": "culture",
    "theatre": "culture", "culture": "culture", "monument": "culture", "fort": "culture",
    "sightseeing": "attractions", "attractions": "attractions", "tourist": "attractions", "viewpoint": "attractions",
    "park": "nature", "parks": "nature", "nature": "nature", "garden": "nature", "outdoors": "nature", "trek": "nature",
    "hiking": "nature", "shopping": "shopping", "mall": "shopping", "market": "shopping",
    "movie": "movies", "movies": "movies", "cinema": "movies", "film": "movies",
    "games": "entertainment", "bowling": "entertainment", "arcade": "entertainment", "entertainment": "entertainment",
    "escape room": "entertainment", "fun": "entertainment",
    "sports": "sports", "gym": "sports", "swimming": "sports", "fitness": "sports",
    "temple": "religious", "spiritual": "religious", "church": "religious",
    "library": "learning", "books": "learning", "workshop": "learning", "learning": "learning",
    "hotel": "hotel", "stay": "hotel",
}


def normalise_interests(interests: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in interests:
        key = (raw or "").strip().lower()
        cat = key if key in CATEGORIES else SYNONYMS.get(key)
        if not cat:
            for word, mapped in SYNONYMS.items():
                if re.search(rf"\b{re.escape(word)}\b", key):
                    cat = mapped
                    break
        if cat and cat not in out:
            out.append(cat)
    return out


def _estimate_cost(cat: str, raw: dict) -> int:
    base = CATEGORIES[cat]["cost_inr"]
    tags = raw or {}
    if str(tags.get("fee", "")).lower() in ("no", "free"):
        return 0
    return base


def _geoapify_search(lat: float, lon: float, cats: list[str], radius_m: int, limit: int) -> list[dict]:
    key = get_settings().geoapify_api_key
    if not key:
        raise ExternalAPIError("geoapify-places", "no GEOAPIFY_API_KEY configured")
    out = []
    for cat in cats:
        params = {
            "categories": ",".join(CATEGORIES[cat]["geoapify"]),
            "filter": f"circle:{lon},{lat},{radius_m}",
            "bias": f"proximity:{lon},{lat}",
            "limit": limit,
            "apiKey": key,
        }
        data = request_json("geoapify-places", "GET", GEOAPIFY_PLACES, params=params, cache_ttl=3600)
        for f in data.get("features", []):
            p = f.get("properties", {})
            name = p.get("name")
            if not name:
                continue
            raw = (p.get("datasource") or {}).get("raw") or {}
            out.append({
                "id": f"geo:{p.get('place_id')}",
                "name": name,
                "category": cat,
                "kind": CATEGORIES[cat]["kind"],
                "lat": p.get("lat"),
                "lon": p.get("lon"),
                "address": p.get("formatted") or p.get("address_line2"),
                "opening_hours": p.get("opening_hours") or raw.get("opening_hours"),
                "website": p.get("website") or raw.get("website"),
                "cuisine": raw.get("cuisine"),
                "distance_km": round((p.get("distance") or 0) / 1000, 2),
                "source": "geoapify",
                "source_id": str(raw.get("osm_type", "")) + "/" + str(raw.get("osm_id", "")) if raw.get("osm_id") else p.get("place_id"),
                "duration_min": CATEGORIES[cat]["duration"],
                "est_cost_inr": _estimate_cost(cat, raw),
                "indoor": CATEGORIES[cat]["indoor"],
            })
    return out


def _overpass_search(lat: float, lon: float, cats: list[str], radius_m: int, limit: int) -> list[dict]:
    selectors = []
    for cat in cats:
        for k, v in CATEGORIES[cat]["osm"]:
            selectors.append(f'nwr["{k}"="{v}"]["name"](around:{radius_m},{lat},{lon});')
    query = f"[out:json][timeout:20];({''.join(selectors)});out center tags {max(limit * len(cats), 20)};"
    data = None
    last_exc: Optional[Exception] = None
    for url in OVERPASS_URLS:
        try:
            data = request_json("overpass", "POST", url, data={"data": query}, cache_ttl=3600, timeout=25)
            break
        except ExternalAPIError as exc:
            last_exc = exc
    if data is None:
        raise last_exc or ExternalAPIError("overpass", "no response")

    tag_to_cat = {}
    for cat in cats:
        for k, v in CATEGORIES[cat]["osm"]:
            tag_to_cat[(k, v)] = cat
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        cat = next((c for (k, v), c in tag_to_cat.items() if tags.get(k) == v), None)
        if not cat:
            continue
        elat = el.get("lat") or (el.get("center") or {}).get("lat")
        elon = el.get("lon") or (el.get("center") or {}).get("lon")
        if elat is None or elon is None:
            continue
        addr = ", ".join(x for x in [tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:city")] if x)
        out.append({
            "id": f"osm:{el['type']}/{el['id']}",
            "name": tags.get("name"),
            "category": cat,
            "kind": CATEGORIES[cat]["kind"],
            "lat": elat,
            "lon": elon,
            "address": addr or None,
            "opening_hours": tags.get("opening_hours"),
            "website": tags.get("website") or tags.get("contact:website"),
            "cuisine": tags.get("cuisine"),
            "distance_km": round(haversine_km(lat, lon, elat, elon), 2),
            "source": "openstreetmap",
            "source_id": f"{el['type']}/{el['id']}",
            "duration_min": CATEGORIES[cat]["duration"],
            "est_cost_inr": _estimate_cost(cat, tags),
            "indoor": CATEGORIES[cat]["indoor"],
        })
    return out


def _rank(items: list[dict], limit_per_cat: int) -> list[dict]:
    seen = set()
    by_cat: dict[str, list[dict]] = {}
    for it in sorted(items, key=lambda x: (x.get("opening_hours") is None, x.get("distance_km") or 99)):
        key = (it["name"].lower(), round(it["lat"], 3), round(it["lon"], 3))
        if key in seen:
            continue
        seen.add(key)
        by_cat.setdefault(it["category"], []).append(it)
    out = []
    for cat_items in by_cat.values():
        out.extend(cat_items[:limit_per_cat])
    return out


def search_places(
    lat: float,
    lon: float,
    interests: Iterable[str],
    radius_km: float = 5.0,
    limit_per_category: int = 6,
) -> dict:
    """Find real venues near (lat, lon). Returns {'ok', 'source', 'places': [...], 'warnings': [...]}."""
    cats = normalise_interests(interests) or ["food", "attractions"]
    radius_m = int(max(0.5, min(radius_km, 50)) * 1000)
    warnings: list[str] = []
    for name, fn in (("geoapify", _geoapify_search), ("openstreetmap", _overpass_search)):
        try:
            items = fn(lat, lon, cats, radius_m, limit_per_category * 2)
            if items:
                return {"ok": True, "source": name, "categories": cats,
                        "places": _rank(items, limit_per_category), "warnings": warnings}
            warnings.append(f"{name}: no results within {radius_km} km")
        except ExternalAPIError as exc:
            warnings.append(f"{name} unavailable ({exc}); trying fallback")
    return {"ok": False, "source": None, "categories": cats, "places": [], "warnings": warnings}
