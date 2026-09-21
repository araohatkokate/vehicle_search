"""
Structured filter schema: the typed contract between the chat layer and the
search layer.

Two shapes:
- FilterDiff: what the extractor (LLM or fallback) produces for a single
  turn — a *diff* against the current state, not a full re-specification.
  This is what makes multi-turn refinement work: "show me Fords" then
  "under 15k" then "actually not a Ford" each only describe the change.
- FilterState: the accumulated, current filter set for a session, built by
  applying a sequence of validated diffs.

`distance` is intentionally not modeled: the dataset (data/SCHEMA.md) has no
zip code or lat/long, only yard_city/yard_state, so a real radius search
isn't possible without fabricating geo data. `location_state`/`location_city`
cover the "location" part of the original field list.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

# Fields a diff's `set` may contain, and the FilterState attribute they map to.
# Single source of truth for both models below and for the guardrail layer.
FILTER_FIELDS = [
    "make", "model", "year_min", "year_max", "price_min", "price_max",
    "mileage_min", "mileage_max", "primary_damage", "title_type",
    "body_style", "color", "has_keys", "run_and_drive", "drivetrain",
    "fuel_type", "transmission", "location_state", "location_city",
]


class FilterDiff(BaseModel):
    """One turn's worth of change, as extracted from a user message."""

    set: dict[str, Any] = Field(default_factory=dict)
    unset: list[str] = Field(default_factory=list)
    # field -> values that must NOT match, e.g. {"make": ["Ford"]} for
    # "actually not a Ford". Applied on top of `set`/`unset`.
    exclude: dict[str, list[Any]] = Field(default_factory=dict)

    reset: bool = False
    off_topic: bool = False
    clarification_needed: bool = False
    clarification_question: Optional[str] = None


class FilterState(BaseModel):
    """Accumulated filters for a session."""

    make: Optional[str] = None
    model: Optional[str] = None
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    mileage_min: Optional[int] = None
    mileage_max: Optional[int] = None
    primary_damage: Optional[str] = None
    title_type: Optional[str] = None
    body_style: Optional[str] = None
    color: Optional[str] = None
    has_keys: Optional[bool] = None
    run_and_drive: Optional[str] = None
    drivetrain: Optional[str] = None
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None
    location_state: Optional[str] = None
    location_city: Optional[str] = None

    excludes: dict[str, list[Any]] = Field(default_factory=dict)

    def apply(self, diff: FilterDiff) -> None:
        """Mutate self in place by applying a validated diff."""
        if diff.reset:
            for field in FILTER_FIELDS:
                setattr(self, field, None)
            self.excludes = {}
            return

        for field, value in diff.set.items():
            if field in FILTER_FIELDS:
                setattr(self, field, value)

        for field in diff.unset:
            if field in FILTER_FIELDS:
                setattr(self, field, None)

        for field, values in diff.exclude.items():
            if field not in FILTER_FIELDS:
                continue
            existing = set(self.excludes.get(field, [])) | set(values)
            self.excludes[field] = sorted(existing)
            # A newly excluded value contradicts a positive filter on the
            # same field ("make=Ford" then "not a Ford") -> clear it.
            if getattr(self, field, None) in values:
                setattr(self, field, None)

    def is_empty(self) -> bool:
        return (
            all(getattr(self, field) is None for field in FILTER_FIELDS)
            and not self.excludes
        )

    def as_display_dict(self) -> dict[str, Any]:
        """Non-null filters, for showing the user current search state."""
        data = {f: getattr(self, f) for f in FILTER_FIELDS if getattr(self, f) is not None}
        if self.excludes:
            data["excludes"] = self.excludes
        return data
