"""
Canonical "known good values" for the guardrail/validation layer.

Deliberately imports from data/generate_data.py rather than redeclaring
these lists, so the data and the validator can never drift apart (see
data/SCHEMA.md). The generator's lists are weighted (e.g. PRIMARY_DAMAGE
has "FRONT END" repeated for sampling probability) so we dedupe here.
"""

from data.generate_data import (
    MAKES_MODELS,
    BODY_STYLES,
    COLORS,
    PRIMARY_DAMAGE,
    SECONDARY_DAMAGE_POOL,
    TITLE_TYPES,
    LOSS_TYPES,
    RUN_AND_DRIVE,
    DRIVETRAIN,
    FUEL_TYPE,
    TRANSMISSION,
    TITLE_STATES,
    YARDS,
)

MAKES: set[str] = set(MAKES_MODELS.keys())
MODELS_BY_MAKE: dict[str, set[str]] = {make: set(models) for make, models in MAKES_MODELS.items()}
ALL_MODELS: set[str] = {model for models in MAKES_MODELS.values() for model in models}
BODY_STYLE_VALUES: set[str] = set(BODY_STYLES.values())
COLOR_VALUES: set[str] = set(COLORS)
PRIMARY_DAMAGE_VALUES: set[str] = set(PRIMARY_DAMAGE)
SECONDARY_DAMAGE_VALUES: set[str] = {d for d in SECONDARY_DAMAGE_POOL if d is not None}
TITLE_TYPE_VALUES: set[str] = set(TITLE_TYPES)
LOSS_TYPE_VALUES: set[str] = set(LOSS_TYPES)
RUN_AND_DRIVE_VALUES: set[str] = set(RUN_AND_DRIVE)
DRIVETRAIN_VALUES: set[str] = set(DRIVETRAIN)
FUEL_TYPE_VALUES: set[str] = set(FUEL_TYPE)
TRANSMISSION_VALUES: set[str] = set(TRANSMISSION)

YARD_STATES: set[str] = {state for _, _, state in YARDS}
YARD_CITIES: set[str] = {city for _, city, _ in YARDS}
YARD_NAMES: set[str] = {name for name, _, _ in YARDS}
TITLE_STATE_VALUES: set[str] = set(TITLE_STATES)
LOCATION_STATES: set[str] = YARD_STATES | TITLE_STATE_VALUES

# Full state name -> USPS code, for the handful of states this dataset covers
# (used by both the LLM prompt and the regex fallback to normalize "Texas" -> "TX").
STATE_NAME_TO_CODE: dict[str, str] = {
    "texas": "TX", "california": "CA", "florida": "FL", "georgia": "GA",
    "ohio": "OH", "pennsylvania": "PA", "illinois": "IL",
    "north carolina": "NC", "arizona": "AZ", "colorado": "CO",
}

YEAR_MIN = 1995
YEAR_MAX = 2025
PRICE_MAX = 1_000_000.0
MILEAGE_MAX = 500_000
