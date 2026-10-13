"""Inter-city travel, flights, hotels and currency.

* Flights / hotels: SerpApi Google Flights & Google Hotels (real prices) when SERPAPI_API_KEY is set.
  Without it we return clearly-labelled estimates so the agents can still reason about feasibility.
* Trains / buses: distance-based estimates (no free official API exists for most rail networks).
* Currency: Frankfurter (ECB reference rates, free).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from lifeplan.config import get_settings
from lifeplan.tools.geo import geocode, haversine_km
from lifeplan.tools.http import ExternalAPIError, request_json
from lifeplan.tools.places import search_places
from lifeplan.tools.routing import travel_time

SERPAPI = "https://serpapi.com/search.json"
FRANKFURTER = "https://api.frankfurter.dev/v1/latest"
RESTCOUNTRIES = "https://restcountries.com/v3.1/alpha/{code}"


# ------------------------------------------------------------------ currency
def convert_currency(amount: float, from_cur: str, to_cur: str) -> dict:
    f, t = from_cur.upper(), to_cur.upper()
    if f == t:
        return {"ok": True, "amount": round(amount, 2), "currency": t, "rate": 1.0}
    try:
        data = request_json("frankfurter", "GET", FRANKFURTER, params={"base": f, "symbols": t}, cache_ttl=21600)
        rate = data["rates"][t]
        return {"ok": True, "amount": round(amount * rate, 2), "currency": t, "rate": rate,
                "date": data.get("date"), "source": "frankfurter"}
    except (ExternalAPIError, KeyError) as exc:
        return {"ok": False, "error": f"no FX rate {f}->{t}: {exc}"}


def country_info(country_code: str) -> dict:
    try:
        data = request_json("restcountries", "GET", RESTCOUNTRIES.format(code=country_code),
                            params={"fields": "name,currencies,timezones,languages,capital"}, cache_ttl=86400)
    except ExternalAPIError as exc:
        return {"ok": False, "error": str(exc)}
    if isinstance(data, list):
        data = data[0] if data else {}
    return {"ok": True, "name": (data.get("name") or {}).get("common"),
            "currencies": list((data.get("currencies") or {}).keys()),
            "timezones": data.get("timezones"), "languages": list((data.get("languages") or {}).values()),
            "capital": (data.get("capital") or [None])[0], "source": "restcountries"}


# ------------------------------------------------------------------ flights
def _flight_estimate(km: float, currency: str, international: bool) -> dict:
    hours = km / 750 + 0.6
    inr = (3500 + 4.2 * km) if not international else (9000 + 5.5 * km)
    conv = convert_currency(inr, "INR", currency) if currency != "INR" else {"ok": True, "amount": inr}
    price = round(conv["amount"]) if conv.get("ok") else round(inr)
    return {"airline": "typical economy fare (estimate)", "duration_min": round(hours * 60),
            "price": price, "currency": currency if conv.get("ok") else "INR", "stops": 0 if km < 2500 else 1,
            "source": "estimate"}


def search_flights(origin_city: str, dest_city: str, outbound_date: str, return_date: Optional[str] = None,
                   origin_iata: Optional[str] = None, dest_iata: Optional[str] = None,
                   currency: str = "INR", adults: int = 1) -> dict:
    """Real flight offers via SerpApi Google Flights when possible, otherwise an estimate."""
    key = get_settings().serpapi_api_key
    warnings = []
    if key and origin_iata and dest_iata:
        params = {"engine": "google_flights", "departure_id": origin_iata.upper(), "arrival_id": dest_iata.upper(),
                  "outbound_date": outbound_date, "currency": currency, "hl": "en", "adults": adults,
                  "type": "1" if return_date else "2", "api_key": key}
        if return_date:
            params["return_date"] = return_date
        try:
            data = request_json("serpapi-flights", "GET", SERPAPI, params=params, cache_ttl=3600, timeout=30)
            offers = []
            for group in (data.get("best_flights") or []) + (data.get("other_flights") or []):
                legs = group.get("flights") or []
                if not legs:
                    continue
                offers.append({
                    "airline": " + ".join(sorted({l.get("airline", "?") for l in legs})),
                    "flight_numbers": [l.get("flight_number") for l in legs],
                    "depart": legs[0].get("departure_airport", {}).get("time"),
                    "arrive": legs[-1].get("arrival_airport", {}).get("time"),
                    "duration_min": group.get("total_duration"),
                    "stops": len(legs) - 1,
                    "price": group.get("price"),
                    "currency": currency,
                    "source": "serpapi-google-flights",
                })
            if offers:
                offers.sort(key=lambda o: (o["price"] is None, o["price"] or 0))
                return {"ok": True, "source": "serpapi-google-flights", "offers": offers[:6]}
            warnings.append("no flights returned for these airports/dates")
        except ExternalAPIError as exc:
            warnings.append(str(exc))
    elif not key:
        warnings.append("SERPAPI_API_KEY not set: live flight prices unavailable, using estimate")
    else:
        warnings.append("IATA codes not provided: using estimate")

    o, d = geocode(origin_city), geocode(dest_city)
    if not (o.get("ok") and d.get("ok")):
        return {"ok": False, "error": "could not locate origin/destination", "warnings": warnings}
    km = haversine_km(o["lat"], o["lon"], d["lat"], d["lon"])
    intl = bool(o.get("country_code") and d.get("country_code") and o["country_code"] != d["country_code"])
    est = _flight_estimate(km, currency, intl)
    return {"ok": True, "source": "estimate", "distance_km": round(km), "offers": [est], "warnings": warnings}


# ------------------------------------------------------------------ hotels
def search_hotels(destination: str, check_in: str, check_out: str, adults: int = 1, currency: str = "INR",
                  max_price_per_night: Optional[float] = None) -> dict:
    key = get_settings().serpapi_api_key
    warnings = []
    if key:
        params = {"engine": "google_hotels", "q": f"hotels in {destination}", "check_in_date": check_in,
                  "check_out_date": check_out, "adults": adults, "currency": currency, "hl": "en", "api_key": key}
        if max_price_per_night:
            params["max_price"] = int(max_price_per_night)
        try:
            data = request_json("serpapi-hotels", "GET", SERPAPI, params=params, cache_ttl=3600, timeout=30)
            hotels = []
            for p in (data.get("properties") or [])[:10]:
                gps = p.get("gps_coordinates") or {}
                hotels.append({
                    "id": f"serp:{p.get('property_token') or p.get('name')}",
                    "name": p.get("name"),
                    "price_per_night": (p.get("rate_per_night") or {}).get("extracted_lowest"),
                    "currency": currency, "rating": p.get("overall_rating"),
                    "lat": gps.get("latitude"), "lon": gps.get("longitude"),
                    "url": p.get("link"), "source": "serpapi-google-hotels",
                })
            hotels = [h for h in hotels if h["name"]]
            if hotels:
                return {"ok": True, "source": "serpapi-google-hotels", "hotels": hotels}
        except ExternalAPIError as exc:
            warnings.append(str(exc))
    else:
        warnings.append("SERPAPI_API_KEY not set: hotel prices are estimates; hotels are real OSM/Geoapify listings")

    loc = geocode(destination)
    if not loc.get("ok"):
        return {"ok": False, "error": f"cannot locate {destination}", "warnings": warnings}
    found = search_places(loc["lat"], loc["lon"], ["hotel"], radius_km=6, limit_per_category=8)
    est_inr = 3000
    conv = convert_currency(est_inr, "INR", currency) if currency != "INR" else {"ok": True, "amount": est_inr}
    hotels = [{
        "id": p["id"], "name": p["name"], "price_per_night": round(conv["amount"]) if conv.get("ok") else est_inr,
        "currency": currency if conv.get("ok") else "INR", "rating": None, "lat": p["lat"], "lon": p["lon"],
        "url": p.get("website"), "source": p["source"], "price_is_estimate": True,
    } for p in found.get("places", [])]
    return {"ok": bool(hotels), "source": found.get("source") or "none", "hotels": hotels,
            "warnings": warnings + found.get("warnings", [])}


# ------------------------------------------------------------------ inter-city
def intercity_options(origin_city: str, dest_city: str, travel_date: Optional[str] = None,
                      currency: str = "INR") -> dict:
    """Compare road / rail / bus / air between two cities with time and indicative cost."""
    o, d = geocode(origin_city), geocode(dest_city)
    if not (o.get("ok") and d.get("ok")):
        return {"ok": False, "error": "could not locate origin or destination"}
    straight = haversine_km(o["lat"], o["lon"], d["lat"], d["lon"])
    intl = bool(o.get("country_code") and d.get("country_code") and o["country_code"] != d["country_code"])
    options = []

    def money(inr: float) -> tuple[float, str]:
        if currency == "INR":
            return round(inr), "INR"
        c = convert_currency(inr, "INR", currency)
        return (round(c["amount"]), currency) if c.get("ok") else (round(inr), "INR")

    if not intl and straight < 1500:
        drive = travel_time((o["lat"], o["lon"]), (d["lat"], d["lon"]), "drive")
        amt, cur = money(drive["km"] * 9)  # fuel + tolls approx
        options.append({"mode": "drive", "minutes": drive["minutes"], "km": drive["km"], "cost": amt,
                        "currency": cur, "source": drive["source"]})
        amt, cur = money(drive["km"] * 18 + 300)
        options.append({"mode": "cab (outstation)", "minutes": drive["minutes"] + 10, "km": drive["km"],
                        "cost": amt, "currency": cur, "source": "estimate"})
        rail_km = straight * 1.25
        amt, cur = money(150 + rail_km * 1.9)
        options.append({"mode": "train", "minutes": round(rail_km / 55 * 60 + 30), "km": round(rail_km),
                        "cost": amt, "currency": cur, "source": "estimate",
                        "note": "check exact trains & seats on IRCTC / national rail site"})
        amt, cur = money(100 + drive["km"] * 1.7)
        options.append({"mode": "bus", "minutes": round(drive["minutes"] * 1.3 + 20), "km": drive["km"],
                        "cost": amt, "currency": cur, "source": "estimate"})
    if straight > 300 or intl:
        est = _flight_estimate(straight, currency, intl)
        options.append({"mode": "flight", "minutes": est["duration_min"] + 150, "km": round(straight),
                        "cost": est["price"], "currency": est["currency"], "source": "estimate",
                        "note": "includes ~2.5h airport overhead; use search_flights for live fares"})
    options.sort(key=lambda x: x["minutes"])
    return {"ok": True, "origin": o["name"], "destination": d["name"], "straight_km": round(straight),
            "international": intl, "destination_country": d.get("country_code"), "options": options,
            "travel_date": travel_date or date.today().isoformat()}
