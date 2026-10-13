"""Input / output schemas (Pydantic). These are the contract documented in docs/IO_DEFINITIONS.md."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

PlanMode = Literal[
    "auto",
    "now",  # "What can I do right now?"
    "free_time",  # a few free hours
    "evening",
    "day",
    "weekend",
    "local_outing",
    "explore",  # explore near me
    "domestic_trip",
    "international_trip",
    "business_trip",
]

TravelMode = Literal["walk", "transit", "cab", "drive", "bike", "train", "flight"]


class Coordinates(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class Budget(BaseModel):
    amount: float = Field(..., gt=0, description="Total budget for the whole plan")
    currency: str = Field("INR", min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


class BusyBlock(BaseModel):
    start: datetime
    end: datetime
    title: str = "Busy"
    location: Optional[str] = None

    @model_validator(mode="after")
    def check_order(self):
        if self.end <= self.start:
            raise ValueError("busy block end must be after start")
        return self


class PlanRequest(BaseModel):
    """What the user asks LifePlan AI to do."""

    query: str = Field(..., min_length=3, max_length=1500, description="Natural-language request")
    mode: PlanMode = "auto"
    location: Optional[str] = Field(None, max_length=200, description="Where the user is / starts from")
    coordinates: Optional[Coordinates] = None
    destination: Optional[str] = Field(None, max_length=200, description="Trip destination (trips only)")
    start: Optional[datetime] = Field(None, description="Start of the planning window (local time)")
    end: Optional[datetime] = Field(None, description="End of the planning window (local time)")
    budget: Optional[Budget] = None
    interests: list[str] = Field(default_factory=list, max_length=20)
    travel_modes: list[TravelMode] = Field(default_factory=lambda: ["walk", "transit", "cab"])
    party_size: int = Field(1, ge=1, le=20)
    pace: Literal["relaxed", "balanced", "packed"] = "balanced"
    busy_blocks: list[BusyBlock] = Field(default_factory=list, max_length=200)
    calendar_ics: Optional[str] = Field(None, max_length=500_000, description="Raw .ics calendar text")
    use_learned_schedule: bool = Field(True, description="Let the Profile agent predict busy time from history")
    profile_id: str = Field("demo", max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    timezone: Optional[str] = None
    now: Optional[datetime] = Field(None, description="Override 'current time' (testing / demos)")

    @field_validator("interests")
    @classmethod
    def clean_interests(cls, v: list[str]) -> list[str]:
        return [i.strip().lower()[:40] for i in v if i and i.strip()]

    @model_validator(mode="after")
    def check_window(self):
        if self.start and self.end and self.end <= self.start:
            raise ValueError("end must be after start")
        if self.start and self.end and (self.end - self.start).days > 21:
            raise ValueError("planning window is limited to 21 days")
        if not self.location and not self.coordinates:
            raise ValueError("provide either 'location' (e.g. 'Pune') or 'coordinates'")
        return self


class ReplanEvent(BaseModel):
    """A real-world change that forces the agents to adapt an existing plan."""

    plan_id: str
    event: Literal["weather", "delay", "cancellation", "schedule_change", "user_feedback", "closed"]
    details: str = Field(..., min_length=3, max_length=1000)
    item_index: Optional[int] = Field(None, description="Which itinerary item is affected, if known")
    new_busy_block: Optional[BusyBlock] = None


class FeedbackRequest(BaseModel):
    plan_id: str
    rating: int = Field(..., ge=1, le=5)
    liked: list[str] = Field(default_factory=list)
    disliked: list[str] = Field(default_factory=list)
    comment: Optional[str] = Field(None, max_length=1000)


# ---------------------------------------------------------------- output ---


class PlaceRef(BaseModel):
    name: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    address: Optional[str] = None
    source: Optional[str] = None  # geoapify | openstreetmap | tavily | serpapi | user
    source_id: Optional[str] = None
    url: Optional[str] = None


class Money(BaseModel):
    amount: float
    currency: str
    estimate: bool = True


class TravelLeg(BaseModel):
    mode: str
    minutes: float
    km: Optional[float] = None
    source: Optional[str] = None


class ItineraryItem(BaseModel):
    start: str
    end: str
    kind: Literal["activity", "meal", "snack", "event", "travel", "hotel", "flight", "train", "buffer", "busy"]
    title: str
    place: Optional[PlaceRef] = None
    candidate_id: Optional[str] = None
    cost: Optional[Money] = None
    travel: Optional[TravelLeg] = None
    notes: Optional[str] = None


class DayPlan(BaseModel):
    date: str
    items: list[ItineraryItem]


class ConflictRecord(BaseModel):
    type: str
    description: str
    raised_by: list[str] = Field(default_factory=list)
    resolution: Optional[str] = None


class PlanResult(BaseModel):
    plan_id: str
    run_id: str
    status: Literal["ok", "partial", "failed"]
    mode: str
    summary: str
    timezone: str
    days: list[DayPlan] = Field(default_factory=list)
    totals: dict = Field(default_factory=dict)
    weather: Optional[dict] = None
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    alternatives: list[dict] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
