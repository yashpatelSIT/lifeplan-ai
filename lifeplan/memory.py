"""Long-term memory: user profiles (preferences learned from feedback) and stored plans
(so the agents can re-plan an existing itinerary when something changes)."""

from __future__ import annotations

import copy
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from lifeplan.config import ROOT_DIR, get_settings

DEMO_PROFILE = {
    "profile_id": "demo",
    "name": "Demo user",
    "home": {"name": "Home (Shivajinagar, Pune)", "lat": 18.5308, "lon": 73.8475},
    "work": {"name": "College (Lavale, Pune)", "lat": 18.5404, "lon": 73.7262},
    "work_hours": {},
    "commute_minutes": 45,
    "interests": ["food", "cafe", "culture", "nature", "live music"],
    "dislikes": [],
    "dietary": None,
    "default_budget": {"amount": 1500, "currency": "INR"},
    "travel_modes": ["walk", "transit", "cab"],
    "pace": "balanced",
    "learned_weights": {},
    "feedback": [],
}

SAMPLE_CALENDAR = ROOT_DIR / "samples" / "sample_calendar.ics"


class ProfileStore:
    def __init__(self, directory: Optional[Path] = None):
        self.dir = (directory or get_settings().data_dir) / "profiles"
        self._lock = threading.Lock()

    def _path(self, profile_id: str) -> Path:
        return self.dir / f"{profile_id}.json"

    def get(self, profile_id: str) -> dict:
        with self._lock:
            p = self._path(profile_id)
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
            prof = copy.deepcopy(DEMO_PROFILE)
            prof["profile_id"] = profile_id
            return prof

    def save(self, profile: dict) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(profile["profile_id"]).write_text(json.dumps(profile, indent=2, default=str), encoding="utf-8")

    def update_preference(self, profile_id: str, key: str, value) -> dict:
        allowed = {"interests", "dislikes", "dietary", "pace", "travel_modes", "default_budget", "home", "work",
                   "work_hours", "commute_minutes", "name"}
        if key not in allowed:
            raise ValueError(f"'{key}' is not an editable profile field; allowed: {sorted(allowed)}")
        prof = self.get(profile_id)
        prof[key] = value
        self.save(prof)
        return prof

    def learn_from_feedback(self, profile_id: str, rating: int, liked: list[str], disliked: list[str],
                            comment: Optional[str]) -> dict:
        """Simple preference learning: categories in liked items gain weight, disliked lose weight."""
        prof = self.get(profile_id)
        weights = prof.setdefault("learned_weights", {})
        for cat in liked:
            weights[cat] = round(min(1.0, weights.get(cat, 0.5) + 0.1 * (rating / 5 + 0.5)), 2)
        for cat in disliked:
            weights[cat] = round(max(0.0, weights.get(cat, 0.5) - 0.15), 2)
            if weights[cat] <= 0.2 and cat not in prof.setdefault("dislikes", []):
                prof["dislikes"].append(cat)
        prof.setdefault("feedback", []).append({"at": datetime.now().isoformat(timespec="seconds"),
                                                "rating": rating, "liked": liked, "disliked": disliked,
                                                "comment": comment})
        prof["feedback"] = prof["feedback"][-50:]
        self.save(prof)
        return prof


class PlanStore:
    """Keeps finished plans (and the blackboard state that produced them) for re-planning."""

    def __init__(self, directory: Optional[Path] = None):
        self.dir = (directory or get_settings().data_dir) / "plans"
        self._mem: dict[str, dict] = {}
        self._lock = threading.Lock()

    def put(self, plan_id: str, record: dict) -> None:
        with self._lock:
            self._mem[plan_id] = record
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                (self.dir / f"{plan_id}.json").write_text(json.dumps(record, default=str), encoding="utf-8")
            except OSError:
                pass  # read-only disk: memory copy is enough

    def get(self, plan_id: str) -> Optional[dict]:
        with self._lock:
            if plan_id in self._mem:
                return self._mem[plan_id]
            p = self.dir / f"{plan_id}.json"
            if p.exists():
                rec = json.loads(p.read_text(encoding="utf-8"))
                self._mem[plan_id] = rec
                return rec
            return None
