"""Natural-language requirements extractor.

Extracts structured planning requirements from free-form user descriptions:
- Starting location (origin)
- Destination
- Dates, times, and duration
- Return destination and return arrival deadline
- Budget amount and currency
- Party size
- Hotel star rating and accommodation preferences
- Preferred transport modes
- Activity and dining preferences
- Exclusions / dislikes
- Pace
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from lifeplan.tools.datetime_parser import (
    extract_dates_from_query,
    extract_times_from_query,
    resolve_canonical_schedule,
)

# Common city synonyms for India & world destinations
COMMON_CITIES = {
    "pune", "mumbai", "bombay", "goa", "delhi", "new delhi", "bengaluru", "bangalore",
    "hyderabad", "chennai", "kolkata", "jaipur", "agra", "udaipur", "kochi", "cochin",
    "ahmedabad", "lonavala", "khandala", "mahabaleshwar", "alibaug", "panaji",
    "tokyo", "paris", "london", "singapore", "dubai", "new york", "san francisco",
}

STAR_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
}

TRANSPORT_SYNONYMS = {
    "cab": ["cab", "walk", "transit"],
    "taxi": ["cab", "walk", "transit"],
    "uber": ["cab", "walk", "transit"],
    "car": ["drive", "walk"],
    "driving": ["drive", "walk"],
    "drive": ["drive", "walk"],
    "train": ["train", "cab", "walk"],
    "rail": ["train", "cab", "walk"],
    "flight": ["flight", "cab", "walk"],
    "fly": ["flight", "cab", "walk"],
    "bus": ["transit", "walk", "cab"],
    "metro": ["transit", "walk"],
    "transit": ["transit", "walk"],
    "walk": ["walk"],
}


def _find_known_city(text: str) -> Optional[str]:
    text_lower = text.lower()
    for city in sorted(COMMON_CITIES, key=lambda c: -len(c)):
        pattern = r"\b" + re.escape(city) + r"\b"
        if re.search(pattern, text_lower):
            if city == "bombay":
                return "Mumbai"
            if city in ("bangalore", "bengaluru"):
                return "Bengaluru"
            return city.capitalize()
    return None


def extract_locations(query: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract (origin, destination, return_destination) from query."""
    origin = None
    dest = None
    return_dest = None

    q = query.strip()

    # Match "from <City> to <City>"
    m_from_to = re.search(r"\bfrom\s+([A-Za-z\s]+?)\s+to\s+([A-Za-z\s]+?)(?:\s+(?:from|on|next|for|with|in|by|at|\.|\,)|$)", q, re.IGNORECASE)
    if m_from_to:
        c1 = _find_known_city(m_from_to.group(1)) or m_from_to.group(1).strip().capitalize()
        c2 = _find_known_city(m_from_to.group(2)) or m_from_to.group(2).strip().capitalize()
        origin = c1
        dest = c2

    # Match "vacation/trip to <City>"
    if not dest:
        m_trip_to = re.search(r"\b(?:vacation|trip|journey|travel|visiting|visit)\s+to\s+([A-Za-z\s]+?)(?:\s+(?:from|on|next|for|with|in|by|at|\.|\,)|$)", q, re.IGNORECASE)
        if m_trip_to:
            dest = _find_known_city(m_trip_to.group(1)) or m_trip_to.group(1).strip().capitalize()

    # Match standalone "from <City>"
    if not origin:
        m_from = re.search(r"\b(?:from|starting\s+(?:from|at))\s+([A-Za-z\s]+?)(?:\s+(?:to|on|next|for|with|in|by|at|\.|\,)|$)", q, re.IGNORECASE)
        if m_from:
            origin = _find_known_city(m_from.group(1)) or m_from.group(1).strip().capitalize()

    # Match "in <City>" (e.g. "dinner in Pune")
    if not origin and not dest:
        m_in = re.search(r"\bin\s+([A-Za-z\s]+?)(?:\s+(?:from|on|next|for|with|by|at|\.|\,)|$)", q, re.IGNORECASE)
        if m_in:
            city = _find_known_city(m_in.group(1)) or m_in.group(1).strip().capitalize()
            origin = city

    # Match return destination (e.g. "back at my original location in Pune", "return to Pune")
    m_ret = re.search(r"\b(?:back\s+(?:at|to|in)(?:\s+my\s+original\s+location\s+in)?|return\s+to)\s+([A-Za-z\s]+?)(?:\s+(?:by|before|at|on|\.|\,)|$)", q, re.IGNORECASE)
    if m_ret:
        return_dest = _find_known_city(m_ret.group(1)) or m_ret.group(1).strip().capitalize()
    elif origin:
        # Default return destination for intercity trip is origin
        return_dest = origin

    return origin, dest, return_dest


def extract_budget(query: str) -> tuple[Optional[float], str]:
    """Extract budget amount and currency."""
    # Match ₹10,000, Rs. 1500, INR 10000
    m_inr = re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", query, re.IGNORECASE)
    if m_inr:
        amt_str = m_inr.group(1).replace(",", "")
        try:
            return float(amt_str), "INR"
        except ValueError:
            pass

    # Match $500, USD 500
    m_usd = re.search(r"(?:\$|usd)\s*([\d,]+(?:\.\d{1,2})?)", query, re.IGNORECASE)
    if m_usd:
        amt_str = m_usd.group(1).replace(",", "")
        try:
            return float(amt_str), "USD"
        except ValueError:
            pass

    # Match €500, EUR 500
    m_eur = re.search(r"(?:€|eur)\s*([\d,]+(?:\.\d{1,2})?)", query, re.IGNORECASE)
    if m_eur:
        amt_str = m_eur.group(1).replace(",", "")
        try:
            return float(amt_str), "EUR"
        except ValueError:
            pass

    # Match "budget is 10,000", "budget of 5000"
    m_gen = re.search(r"\bbudget\s*(?:is|of)?\s*(?:₹|rs\.?|inr|\$|usd|€)?\s*([\d,]+(?:\.\d{1,2})?)", query, re.IGNORECASE)
    if m_gen:
        amt_str = m_gen.group(1).replace(",", "")
        try:
            return float(amt_str), "INR"
        except ValueError:
            pass

    return None, "INR"


def extract_return_deadline(query: str) -> Optional[dict]:
    """Extract return arrival deadline (e.g. 'by 9 PM Friday', 'before 21:00')."""
    m = re.search(r"\b(?:by|before|until)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM)?|\d{1,2}\s*(?:am|pm|AM|PM))(?:\s+(?:on\s+)?([A-Za-z]+))?", query, re.IGNORECASE)
    if m:
        time_str = m.group(1).strip()
        day_str = m.group(2).strip() if m.group(2) else None
        return {"time_str": time_str, "day_name": day_str}
    return None


def extract_hotel_requirements(query: str) -> dict:
    """Extract hotel star rating and requirements."""
    min_stars = None
    m_star = re.search(r"\b(?:rated\s+)?(?:at\s+least|minimum|min)?\s*(one|two|three|four|five|[1-5])\s*[- ]?star", query, re.IGNORECASE)
    if m_star:
        token = m_star.group(1).lower()
        min_stars = STAR_WORDS.get(token)

    features = []
    q_lower = query.lower()
    if "pool" in q_lower or "swimming" in q_lower:
        features.append("pool")
    if "resort" in q_lower:
        features.append("resort")
    if "breakfast" in q_lower:
        features.append("breakfast")
    if "beach" in q_lower or "sea view" in q_lower:
        features.append("beachfront")

    return {"min_stars": min_stars, "features": features}


def extract_preferred_transport(query: str) -> list[str]:
    """Extract preferred transport modes."""
    modes = []
    q_lower = query.lower()
    for keyword, mode_list in TRANSPORT_SYNONYMS.items():
        pattern = r"\b(?:by|prefer|using|via|take|in\s+a)\s+" + re.escape(keyword) + r"\b"
        if re.search(pattern, q_lower):
            for m in mode_list:
                if m not in modes:
                    modes.append(m)
    if not modes:
        for keyword, mode_list in TRANSPORT_SYNONYMS.items():
            if re.search(r"\b" + re.escape(keyword) + r"\b", q_lower):
                for m in mode_list:
                    if m not in modes:
                        modes.append(m)
                break
    return modes or ["cab", "transit", "walk"]


def extract_party_size(query: str) -> int:
    """Extract party size."""
    q_lower = query.lower()
    if "solo" in q_lower or "for myself" in q_lower or "just me" in q_lower:
        return 1
    if "couple" in q_lower or "for two" in q_lower or "for 2" in q_lower:
        return 2

    m = re.search(r"\b(?:for|with)\s+(\d+)\s*(?:people|persons|adults|friends|family)?\b", q_lower)
    if m:
        try:
            return max(1, min(20, int(m.group(1))))
        except ValueError:
            pass

    m_fam = re.search(r"\bfamily\s+of\s+(\d+)\b", q_lower)
    if m_fam:
        try:
            return max(1, min(20, int(m_fam.group(1))))
        except ValueError:
            pass

    return 1


def extract_interests_and_activities(query: str) -> list[str]:
    """Extract interest categories."""
    interests = []
    q_lower = query.lower()
    interest_map = {
        "food": ["restaurant", "restaurants", "dining", "food", "eat", "lunch", "dinner", "breakfast", "cuisine"],
        "cafe": ["cafe", "cafes", "coffee", "tea", "bakery"],
        "attractions": ["attraction", "attractions", "sightseeing", "monument", "monuments", "heritage", "palace", "fort"],
        "museum": ["museum", "museums", "art gallery", "exhibition"],
        "nature": ["nature", "beach", "beaches", "park", "parks", "garden", "gardens", "lake", "viewpoint"],
        "nightlife": ["nightlife", "pub", "pubs", "club", "clubs", "bar", "bars", "lounge"],
        "shopping": ["shopping", "market", "bazaar", "mall"],
    }
    for cat, keywords in interest_map.items():
        if any(re.search(r"\b" + re.escape(k) + r"\b", q_lower) for k in keywords):
            interests.append(cat)
    return interests


def extract_exclusions(query: str) -> list[str]:
    """Extract mandatory exclusions / dislikes."""
    exclusions = []
    m = re.findall(r"\b(?:no|avoid|without|don't want|dont want)\s+([a-zA-Z\s]+?)(?:\.|\,|$|\band\b)", query, re.IGNORECASE)
    for match in m:
        cleaned = match.strip().lower()
        if len(cleaned) < 30 and cleaned not in ("the", "a", "an"):
            exclusions.append(cleaned)
    return exclusions


def extract_pace(query: str) -> str:
    """Extract pace."""
    q_lower = query.lower()
    if any(k in q_lower for k in ("relaxed", "leisurely", "slow pace", "chill", "easygoing")):
        return "relaxed"
    if any(k in q_lower for k in ("packed", "busy", "fast", "as much as possible", "see everything")):
        return "packed"
    return "balanced"


def parse_natural_language_requirements(
    query: str,
    now: Optional[datetime] = None,
    picker_start: Optional[datetime] = None,
    picker_end: Optional[datetime] = None,
    mode: Optional[str] = None,
) -> dict:
    """Parse complete structured requirements from query with precedence rules."""
    now = now or datetime.now()
    origin, dest, return_dest = extract_locations(query)
    budget_amt, currency = extract_budget(query)
    return_deadline = extract_return_deadline(query)
    hotel_reqs = extract_hotel_requirements(query)
    travel_modes = extract_preferred_transport(query)
    party_size = extract_party_size(query)
    interests = extract_interests_and_activities(query)
    exclusions = extract_exclusions(query)
    pace = extract_pace(query)

    is_trip = bool(dest and origin and dest.lower() != origin.lower())
    if mode and mode != "auto":
        resolved_mode = mode
    elif is_trip:
        resolved_mode = "domestic_trip"
    elif re.search(r"\b(tonight|evening|after work|after college)\b", query.lower()):
        resolved_mode = "evening"
    elif "weekend" in query.lower():
        resolved_mode = "weekend"
    else:
        resolved_mode = "day"

    canonical = resolve_canonical_schedule(
        query=query,
        now=now,
        picker_start=picker_start,
        picker_end=picker_end,
        mode=resolved_mode,
    )

    # If query specified a return deadline (e.g. by 9 PM Friday), align canonical.end
    end_dt = canonical.end
    if return_deadline and return_deadline.get("time_str"):
        t_str = return_deadline["time_str"]
        h_str, _, m_str = t_str.partition(":")
        am_pm = ""
        if "am" in t_str.lower():
            am_pm = "am"
            h_str = h_str.lower().replace("am", "").strip()
        elif "pm" in t_str.lower():
            am_pm = "pm"
            h_str = h_str.lower().replace("pm", "").strip()

        try:
            hour = int(h_str)
            minute = int(m_str.replace("am", "").replace("pm", "").strip()) if m_str else 0
            if am_pm == "pm" and hour < 12:
                hour += 12
            elif am_pm == "am" and hour == 12:
                hour = 0
            end_dt = datetime.combine(canonical.end.date(), time(hour, minute))
        except (ValueError, TypeError):
            pass

    return {
        "origin": origin,
        "destination": dest,
        "return_destination": return_dest,
        "start": canonical.start.isoformat(),
        "end": end_dt.isoformat(),
        "schedule_source": canonical.source,
        "override_note": canonical.override_note,
        "overridden_picker": canonical.overridden_picker,
        "return_deadline": return_deadline,
        "budget": budget_amt,
        "currency": currency,
        "hotel": hotel_reqs,
        "travel_modes": travel_modes,
        "party_size": party_size,
        "interests": interests,
        "exclusions": exclusions,
        "pace": pace,
        "is_trip": is_trip,
        "mode": mode,
    }
