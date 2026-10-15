"""Smart Decision Engine.

A deterministic, high-efficiency evaluation and filtering engine that:
1. Rejects candidates violating hard constraints (total budget, closed hours, dietary/dislikes).
2. Rejects irrelevant venues (educational institutions, campuses, hospitals, banks, corporate parks)
   that OSM/Geoapify queries occasionally pull into dining and leisure results.
3. Scores valid candidates deterministically against user preferences, proximity, hours, and weather.
4. Provides a shortlist-first strategy (selecting the top 3-5 most promising candidates)
   to eliminate wasteful 18x18 distance matrices and excessive solver complexity.
5. Progressively expands the shortlist only when initial candidates prove insufficient.
6. Enforces hard constraint validation on the final itinerary before display.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta
from typing import Any, Iterable, Optional

from lifeplan.tools import opening_hours
from lifeplan.tools.geo import haversine_km
from lifeplan.tools.scheduler import parse_dt

log = logging.getLogger("lifeplan.decision_engine")

# Patterns indicating irrelevant institutional, medical, administrative, or residential venues
# that should never appear in activity, dining, cafe, or leisure itineraries.
IRRELEVANT_PATTERNS = [
    # Educational institutions & campuses
    r"\b(?:college|university|institute of technology|iit|nit|polytechnic|engineering college|medical college)\b",
    r"\b(?:high school|secondary school|vidyalaya|vidyamandir|gurukul|preschool|kindergarten|primary school)\b",
    r"\b(?:coaching|tuition|academy of education|hostel|mess|dormitory|campus)\b",
    # Healthcare & medical facilities
    r"\b(?:hospital|dispensary|clinic|nursing home|maternity home|pathology|diagnostics|dialysis centre)\b",
    # Financial & administrative offices
    r"\b(?:bank|atm|post office|lic office|tax office|rto office|police station|chowki|court|tehsil)\b",
    # Residential & industrial complexes
    r"\b(?:residential society|apartments|housing society|co-op hsg soc|residency|industrial estate)\b",
]

_IRRELEVANT_REGEX = re.compile("|".join(IRRELEVANT_PATTERNS), re.IGNORECASE)

# Keywords in user query that legitimize educational or learning venues
LEARNING_INTENT_KEYWORDS = {"college", "university", "campus", "study", "library", "learning", "workshop", "lecture", "heritage", "monument"}


def is_irrelevant_venue(candidate: dict, user_query: str = "") -> tuple[bool, Optional[str]]:
    """Determine whether a candidate is an irrelevant venue (e.g. college, hospital, bank).
    
    Returns (is_irrelevant, reason).
    """
    name = candidate.get("name", "").strip()
    address = candidate.get("address") or ""
    full_text = f"{name} {address}".lower()

    # If the user explicitly requested visiting a college / university campus / learning venue, don't filter it out
    q_lower = user_query.lower()
    if any(k in q_lower for k in LEARNING_INTENT_KEYWORDS):
        return False, None

    # Check for irrelevant patterns
    m = _IRRELEVANT_REGEX.search(full_text)
    if m:
        matched_term = m.group(0)
        return True, f"Irrelevant institution/facility matched '{matched_term}'"

    return False, None


def filter_candidates(
    candidates: list[dict],
    request: dict,
    profile: Optional[dict] = None,
    window: Optional[dict] = None,
    availability: Optional[dict] = None,
) -> tuple[list[dict], list[dict]]:
    """Apply cheap deterministic filters to remove candidates that violate hard constraints.
    
    Returns:
        (valid_candidates, rejected_candidates_with_reasons)
    """
    profile = profile or {}
    dislikes = set(profile.get("dislikes") or [])
    budget_total = float((request.get("budget") or {}).get("amount") or 0)
    query = request.get("query", "")
    w_start = parse_dt(window["start"]) if window and window.get("start") else None
    w_end = parse_dt(window["end"]) if window and window.get("end") else None

    valid: list[dict] = []
    rejected: list[dict] = []

    for cand in candidates:
        cand_id = cand.get("id") or cand.get("name", "unknown")
        name = cand.get("name", "")

        # 1. Irrelevant venue check (e.g. colleges, schools, hospitals)
        is_irrel, irrel_reason = is_irrelevant_venue(cand, user_query=query)
        if is_irrel:
            rejected.append({"id": cand_id, "name": name, "reason": irrel_reason})
            continue

        # 2. Category dislike check
        category = cand.get("category", "")
        if category in dislikes:
            rejected.append({"id": cand_id, "name": name, "reason": f"Matches user profile dislike '{category}'"})
            continue

        # 3. Hard Budget check: a single candidate cost cannot exceed total plan budget
        cost = float(cand.get("est_cost") or 0)
        if budget_total > 0 and cost > budget_total and cand.get("kind") != "hotel":
            rejected.append({
                "id": cand_id, "name": name,
                "reason": f"Estimated cost ({round(cost)}) exceeds user's total budget ({round(budget_total)})"
            })
            continue

        # 4. Opening hours check: if the venue is definitively closed throughout the entire window
        if w_start and w_end and not cand.get("fixed_start"):
            spec = cand.get("opening_hours")
            if spec:
                open_check = opening_hours.is_open_between(spec, w_start, w_end)
                if open_check is False:
                    rejected.append({
                        "id": cand_id, "name": name,
                        "reason": f"Venue is closed during the entire planning window ({spec})"
                    })
                    continue

        valid.append(cand)

    return valid, rejected


def score_candidate(
    candidate: dict,
    request: dict,
    profile: Optional[dict] = None,
    weather: Optional[dict] = None,
    anchor_lat: Optional[float] = None,
    anchor_lon: Optional[float] = None,
    max_radius_km: float = 10.0,
) -> float:
    """Compute a deterministic suitability score between 0.0 and 1.0.
    
    A candidate that fails a hard constraint must never be accepted simply
    because it has a high suitability score.
    """
    profile = profile or {}
    requested_interests = set(request.get("interests") or [])
    learned_weights = profile.get("learned_weights") or {}
    category = candidate.get("category", "")
    
    # 1. Interest Alignment (weight 0.35)
    if category in requested_interests:
        interest_score = 1.0
    elif any(req_int in category for req_int in requested_interests):
        interest_score = 0.8
    else:
        interest_score = 0.4

    # 2. Proximity Score (weight 0.25)
    dist = candidate.get("distance_km")
    if dist is None and anchor_lat is not None and anchor_lon is not None and candidate.get("lat") is not None:
        dist = haversine_km(anchor_lat, anchor_lon, candidate["lat"], candidate["lon"])
    
    if dist is not None:
        prox_score = max(0.1, 1.0 - (dist / max(max_radius_km, 5.0)))
    else:
        prox_score = 0.5

    # 3. Learned Profile Weights (weight 0.15)
    lw = learned_weights.get(category)
    if lw is not None:
        learned_score = max(0.1, min(1.0, float(lw)))
    else:
        learned_score = 0.5

    # 4. Operating Hours Certainty (weight 0.15)
    if candidate.get("opening_hours"):
        hours_score = 1.0
    elif candidate.get("default_hours"):
        hours_score = 0.7
    else:
        hours_score = 0.4

    # 5. Weather Alignment (weight 0.10)
    wet_hours = set((weather or {}).get("wet_hours") or [])
    if wet_hours:
        # Rain forecast: reward indoor venues, penalize outdoor
        weather_score = 1.0 if candidate.get("indoor", True) else 0.2
    else:
        weather_score = 0.9

    total_score = (
        0.35 * interest_score
        + 0.25 * prox_score
        + 0.15 * learned_score
        + 0.15 * hours_score
        + 0.10 * weather_score
    )

    # Budget proportionality adjustment: if budget is tight, mildly favor cost-efficient spots
    budget_total = float((request.get("budget") or {}).get("amount") or 0)
    cost = float(candidate.get("est_cost") or 0)
    if budget_total > 0:
        cost_ratio = cost / budget_total
        if cost_ratio > 0.7:
            total_score -= 0.15
        elif cost_ratio < 0.3:
            total_score += 0.05

    return round(max(0.05, min(1.0, total_score)), 3)


def build_shortlist(
    candidates: list[dict],
    request: dict,
    profile: Optional[dict] = None,
    weather: Optional[dict] = None,
    shortlist_size: int = 5,
    anchor_lat: Optional[float] = None,
    anchor_lon: Optional[float] = None,
) -> tuple[list[dict], list[dict]]:
    """Select the top K most promising candidates with category diversity.
    
    Returns:
        (shortlist, reserve_pool)
    """
    if not candidates:
        return [], []

    # Score all valid candidates
    scored = []
    for c in candidates:
        sc = score_candidate(
            c, request, profile, weather,
            anchor_lat=anchor_lat, anchor_lon=anchor_lon
        )
        item = dict(c)
        item["suitability_score"] = sc
        scored.append(item)

    # Sort descending by suitability score
    scored.sort(key=lambda x: -x["suitability_score"])

    # Ensure diversity across requested categories
    shortlist: list[dict] = []
    reserve: list[dict] = []
    seen_categories: set[str] = set()

    # Pass 1: Add top candidate for each unique category
    for c in scored:
        cat = c.get("category", "")
        if cat not in seen_categories and len(shortlist) < shortlist_size:
            shortlist.append(c)
            seen_categories.add(cat)
        else:
            reserve.append(c)

    # Pass 2: Fill remaining shortlist slots with the highest scoring reserve candidates
    while len(shortlist) < shortlist_size and reserve:
        shortlist.append(reserve.pop(0))

    return shortlist, reserve


def expand_shortlist(
    current_shortlist: list[dict],
    reserve_pool: list[dict],
    count: int = 2,
) -> tuple[list[dict], list[dict]]:
    """Progressively pulls the next highest-scoring candidates from reserve into the shortlist."""
    new_shortlist = list(current_shortlist)
    new_reserve = list(reserve_pool)

    for _ in range(min(count, len(new_reserve))):
        new_shortlist.append(new_reserve.pop(0))

    return new_shortlist, new_reserve


def validate_itinerary_budget_and_constraints(
    draft: dict,
    request: dict,
    window: Optional[dict] = None,
    availability: Optional[dict] = None,
) -> dict:
    """Strictly validates the final itinerary against user budget, time bounds, and commitments.
    
    Guarantees that a plan exceeding the budget or failing mandatory constraints
    is never approved or accepted.
    """
    issues = []
    totals = draft.get("totals") or {}
    total_cost = float(totals.get("cost") or 0)
    budget = float((request.get("budget") or {}).get("amount") or 0)

    # 1. Total Budget Enforcement
    if budget > 0 and total_cost > budget:
        over_by = total_cost - budget
        issues.append({
            "type": "OVER_BUDGET",
            "severity": "high",
            "message": f"Itinerary total cost ({round(total_cost)}) strictly exceeds user budget ({round(budget)}) by {round(over_by)}.",
            "over_by": over_by,
        })

    # 2. Window Bound Enforcement
    w_start = parse_dt(window["start"]) if window and window.get("start") else None
    w_end = parse_dt(window["end"]) if window and window.get("end") else None
    if w_start and w_end:
        for d in draft.get("days", []):
            for idx, it in enumerate(d.get("items", [])):
                if it.get("kind") == "busy":
                    continue
                s, e = parse_dt(it["start"]), parse_dt(it["end"])
                if s < w_start or e > w_end:
                    issues.append({
                        "type": "OUTSIDE_WINDOW",
                        "severity": "high",
                        "message": f"Item '{it.get('title')}' ({it['start']} to {it['end']}) extends outside requested window.",
                        "item_index": idx,
                    })

    # 3. Commitment Clashes
    busy_blocks = (availability or {}).get("busy") or []
    for b in busy_blocks:
        b_s, b_e = parse_dt(b["start"]), parse_dt(b["end"])
        for d in draft.get("days", []):
            for idx, it in enumerate(d.get("items", [])):
                if it.get("kind") == "busy":
                    continue
                s, e = parse_dt(it["start"]), parse_dt(it["end"])
                # Check overlap
                overlap = max(0, (min(e, b_e) - max(s, b_s)).total_seconds() // 60)
                if overlap > 0:
                    issues.append({
                        "type": "CALENDAR_CONFLICT",
                        "severity": "high",
                        "message": f"Item '{it.get('title')}' overlaps commitment '{b.get('title')}' by {int(overlap)} minutes.",
                        "item_index": idx,
                    })

    return {
        "ok": len([i for i in issues if i.get("severity") == "high"]) == 0,
        "issues": issues,
    }


def extract_min_star_rating(query: str, request: Optional[dict] = None) -> Optional[int]:
    """Extract minimum hotel star class if requested (e.g. '3-star hotel', 'minimum 4 star')."""
    if request and request.get("min_star_rating"):
        try:
            return int(request["min_star_rating"])
        except (ValueError, TypeError):
            pass
    m = re.search(r"\b(\d+)\s*[- ]?star\b", query or "", re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def filter_hotels_by_star_rating(
    hotels: list[dict], min_stars: Optional[int] = None
) -> tuple[list[dict], list[dict]]:
    """Filter hotel candidates based on user's star class requirement.
    
    Returns (accepted, rejected).
    """
    if not min_stars:
        return hotels, []

    accepted = []
    rejected = []
    for h in hotels:
        star = h.get("star_rating")
        if star is not None and star < min_stars:
            rejected.append({
                "id": h.get("id"), "name": h.get("name"),
                "reason": f"Hotel has {star} stars, below required minimum of {min_stars} stars."
            })
        else:
            accepted.append(h)
    return accepted, rejected


def ensure_intercity_legs(state: dict) -> list[dict]:
    """Ensure that for trips between different cities, both outbound and return travel legs are firmly established.
    
    Guarantees that a Pune-to-Mumbai trip has:
    1. Outbound journey from Pune to Mumbai.
    2. Return journey from Mumbai to Pune.
    3. Return arrival before the user's return deadline.
    4. Intercity costs factored into total trip cost.
    """
    mode = state.get("mode")
    origin = state.get("origin") or {}
    dest = state.get("destination") or {}

    origin_city = (origin.get("city") or origin.get("name") or "").strip().lower()
    dest_city = (dest.get("city") or dest.get("name") or "").strip().lower()

    is_intercity = (
        mode in ("domestic_trip", "international_trip", "business_trip")
        or (origin_city and dest_city and origin_city != dest_city)
    )
    if not is_intercity or not origin_city or not dest_city:
        return (state.get("logistics") or {}).get("fixed_items") or []

    logistics = dict(state.get("logistics") or {})
    fixed = list(logistics.get("fixed_items") or [])

    w = state.get("window") or {}
    w_start = parse_dt(w["start"]) if w.get("start") else None
    w_end = parse_dt(w["end"]) if w.get("end") else None
    if not w_start or not w_end:
        return fixed

    cur = state.get("currency") or "INR"

    # Check if outbound leg already exists
    has_outbound = any(
        f.get("kind") in ("flight", "train", "travel")
        and parse_dt(f["start"]) <= w_start + timedelta(hours=36)
        and ("to " in f.get("title", "").lower() or dest_city in f.get("title", "").lower() or "outbound" in f.get("title", "").lower())
        for f in fixed
    )

    # Check if return leg already exists
    has_return = any(
        f.get("kind") in ("flight", "train", "travel")
        and parse_dt(f["end"]) >= w_end - timedelta(hours=36)
        and ("return" in f.get("title", "").lower() or origin_city in f.get("title", "").lower())
        for f in fixed
    )

    if has_outbound and has_return:
        return fixed

    from lifeplan.tools import travel as tv
    opts = tv.intercity_options(origin.get("name") or origin_city, dest.get("name") or dest_city, w_start.strftime("%Y-%m-%d"), cur)
    preferred_opt = None
    if opts.get("options"):
        preferred_mode = logistics.get("mode") or "cab"
        preferred_opt = next((o for o in opts["options"] if preferred_mode in o.get("mode", "")), opts["options"][0])

    duration_min = float(preferred_opt.get("minutes", 210)) if preferred_opt else 210.0
    cost_amt = float(preferred_opt.get("cost_inr", 2000)) if preferred_opt else 2000.0
    opt_mode = preferred_opt.get("mode", "cab") if preferred_opt else "cab"

    o_name = origin.get("name") or origin_city.capitalize()
    d_name = dest.get("name") or dest_city.capitalize()

    if not has_outbound:
        out_start = w_start
        out_end = w_start + timedelta(minutes=duration_min)
        fixed.append({
            "start": out_start.strftime("%Y-%m-%dT%H:%M"),
            "end": out_end.strftime("%Y-%m-%dT%H:%M"),
            "kind": "travel",
            "title": f"Intercity travel from {o_name} to {d_name} ({opt_mode})",
            "cost": {"amount": round(cost_amt), "currency": cur, "estimate": True},
            "travel": {"mode": opt_mode, "minutes": round(duration_min), "km": preferred_opt.get("distance_km") if preferred_opt else 150},
            "notes": f"Intercity outbound connection established between {o_name} and {d_name}",
        })

    if not has_return:
        ret_end = w_end
        ret_start = max(w_start + timedelta(minutes=duration_min + 60), w_end - timedelta(minutes=duration_min))
        fixed.append({
            "start": ret_start.strftime("%Y-%m-%dT%H:%M"),
            "end": ret_end.strftime("%Y-%m-%dT%H:%M"),
            "kind": "travel",
            "title": f"Return journey from {d_name} to {o_name} ({opt_mode})",
            "cost": {"amount": round(cost_amt), "currency": cur, "estimate": True},
            "travel": {"mode": opt_mode, "minutes": round(duration_min), "km": preferred_opt.get("distance_km") if preferred_opt else 150},
            "notes": f"Return journey ensuring arrival in {o_name} before deadline {w_end.strftime('%I:%M %p')}",
        })

    fixed.sort(key=lambda x: x["start"])
    logistics["fixed_items"] = fixed
    state["logistics"] = logistics
    return fixed


def validate_return_deadline(
    draft: dict,
    window: dict,
    origin: dict,
    destination: dict,
    return_duration_min: float = 210.0,
) -> list[dict]:
    """Check if the itinerary allows returning to the origin by the required deadline."""
    issues = []
    w_end = parse_dt(window["end"]) if window and window.get("end") else None
    if not w_end:
        return issues

    o_city = (origin.get("city") or origin.get("name") or "").strip().lower()
    d_city = (destination.get("city") or destination.get("name") or "").strip().lower()
    if not o_city or not d_city or o_city == d_city:
        return issues

    latest_departure = w_end - timedelta(minutes=return_duration_min)

    # Check all destination activities on the last day
    for d in draft.get("days", []):
        for idx, it in enumerate(d.get("items", [])):
            if it.get("kind") in ("travel", "busy"):
                continue
            e = parse_dt(it["end"])
            # If an activity in destination finishes after the latest permissible departure
            if e > latest_departure and parse_dt(it["start"]).date() == w_end.date():
                issues.append({
                    "type": "RETURN_DEADLINE_VIOLATED",
                    "severity": "high",
                    "agent": "logistics",
                    "item_index": idx,
                    "message": f"Activity '{it.get('title')}' ends at {it['end'][11:]}, which is too late to return to {origin.get('name', o_city.capitalize())} by the {w_end.strftime('%I:%M %p')} deadline (return journey takes {round(return_duration_min)} min). Must depart by {latest_departure.strftime('%I:%M %p')}.",
                })

    return issues


def compute_trip_ledger(plan: dict, state: dict) -> dict:
    """Calculate the transparent, itemized trip cost ledger across all categories."""
    req = state.get("request") or {}
    budget = float(state.get("budget") or (req.get("budget") or {}).get("amount") or 0) or None
    currency = state.get("currency") or "INR"

    accommodation = 0.0
    intercity_transport = 0.0
    local_transport = 0.0
    food_and_dining = 0.0
    activities_and_entries = 0.0
    verified_count = 0
    estimated_count = 0
    breakdown_notes = []

    days = plan.get("days", [])
    for d in days:
        for it in d.get("items", []):
            cost_info = it.get("cost") or {}
            amt = float(cost_info.get("amount") or 0)
            is_est = bool(cost_info.get("estimate", True))
            if is_est:
                estimated_count += 1
            else:
                verified_count += 1

            kind = it.get("kind", "")
            title = it.get("title", "").lower()

            if kind == "hotel":
                accommodation += amt
            elif kind in ("flight", "train") or (kind == "travel" and ("intercity" in title or "return journey" in title or "to " in title and any(c in title for c in ("pune", "mumbai", "delhi", "goa", "bangalore")))):
                intercity_transport += amt
            elif kind == "travel":
                local_transport += amt
            elif kind in ("meal", "snack"):
                food_and_dining += amt
            elif kind in ("activity", "event"):
                activities_and_entries += amt
            else:
                activities_and_entries += amt

    # Contingency buffer: 5% of direct expenses
    subtotal = accommodation + intercity_transport + local_transport + food_and_dining + activities_and_entries
    contingency = round(subtotal * 0.05) if subtotal > 0 else 0.0
    total_cost = round(subtotal + contingency)

    remaining_budget = round(budget - total_cost) if budget is not None else None
    is_within = (total_cost <= budget) if budget is not None else True

    if accommodation > 0:
        breakdown_notes.append(f"Accommodation: {round(accommodation)} {currency}")
    if intercity_transport > 0:
        breakdown_notes.append(f"Intercity Travel: {round(intercity_transport)} {currency}")
    if local_transport > 0:
        breakdown_notes.append(f"Local Travel: {round(local_transport)} {currency}")
    if food_and_dining > 0:
        breakdown_notes.append(f"Dining: {round(food_and_dining)} {currency}")
    if activities_and_entries > 0:
        breakdown_notes.append(f"Activities & Tickets: {round(activities_and_entries)} {currency}")
    if contingency > 0:
        breakdown_notes.append(f"Contingency (5%): {round(contingency)} {currency}")

    return {
        "accommodation": round(accommodation),
        "intercity_transport": round(intercity_transport),
        "local_transport": round(local_transport),
        "food_and_dining": round(food_and_dining),
        "activities_and_entries": round(activities_and_entries),
        "contingency": round(contingency),
        "total_cost": total_cost,
        "budget": budget,
        "remaining_budget": remaining_budget,
        "currency": currency,
        "is_within_budget": is_within,
        "verified_items_count": verified_count,
        "estimated_items_count": estimated_count,
        "breakdown_notes": breakdown_notes,
    }
