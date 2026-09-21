"""
Extraction guardrail: validates every extracted FilterDiff (LLM or
rule-based, doesn't matter which) against the known schema/enum values
before it's allowed to touch FilterState or the database.

Invalid values are dropped, not passed through — a bad value never
silently becomes a bad query. Every drop is recorded in `notes` so the
caller can decide whether to mention it to the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app import reference_data as ref
from app.filters import FilterDiff, FilterState

# make/model/damage/etc. are free-text from the extractor; case/whitespace
# varies ("ford" vs "Ford", "suv" vs "SUV"), so every enum check normalizes
# against a lowercased lookup and rewrites to the canonical stored value.
_BODY_STYLE_LOOKUP = {v.lower(): v for v in ref.BODY_STYLE_VALUES}
_COLOR_LOOKUP = {v.lower(): v for v in ref.COLOR_VALUES}
_DAMAGE_LOOKUP = {v.lower(): v for v in ref.PRIMARY_DAMAGE_VALUES}
_TITLE_TYPE_LOOKUP = {v.lower(): v for v in ref.TITLE_TYPE_VALUES}
_RUN_AND_DRIVE_LOOKUP = {v.lower(): v for v in ref.RUN_AND_DRIVE_VALUES}
_DRIVETRAIN_LOOKUP = {v.lower(): v for v in ref.DRIVETRAIN_VALUES}
_FUEL_TYPE_LOOKUP = {v.lower(): v for v in ref.FUEL_TYPE_VALUES}
_TRANSMISSION_LOOKUP = {v.lower(): v for v in ref.TRANSMISSION_VALUES}
_MAKE_LOOKUP = {m.lower(): m for m in ref.MAKES}
_MODEL_LOOKUP = {m.lower(): m for m in ref.ALL_MODELS}
_YARD_CITY_LOOKUP = {c.lower(): c for c in ref.YARD_CITIES}


@dataclass
class ValidationResult:
    diff: FilterDiff
    dropped: list[str] = field(default_factory=list)  # human-readable notes


def _validate_enum(value, lookup: dict[str, str]) -> tuple[str | None, bool]:
    if not isinstance(value, str):
        return None, False
    canonical = lookup.get(value.strip().lower())
    return (canonical, True) if canonical else (None, False)


def _validate_year(value) -> tuple[int | None, bool]:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None, False
    if ref.YEAR_MIN <= year <= ref.YEAR_MAX:
        return year, True
    return None, False


def _validate_price(value) -> tuple[float | None, bool]:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None, False
    if 0 <= price <= ref.PRICE_MAX:
        return price, True
    return None, False


def _validate_mileage(value) -> tuple[int | None, bool]:
    try:
        miles = int(value)
    except (TypeError, ValueError):
        return None, False
    if 0 <= miles <= ref.MILEAGE_MAX:
        return miles, True
    return None, False


def _validate_bool(value) -> tuple[bool | None, bool]:
    if isinstance(value, bool):
        return value, True
    return None, False


def _validate_location_state(value) -> tuple[str | None, bool]:
    if not isinstance(value, str):
        return None, False
    v = value.strip()
    code = ref.STATE_NAME_TO_CODE.get(v.lower(), v.upper())
    if code in ref.LOCATION_STATES:
        return code, True
    return None, False


def _validate_model(value, make: str | None) -> tuple[str | None, bool]:
    if not isinstance(value, str):
        return None, False
    canonical = _MODEL_LOOKUP.get(value.strip().lower())
    if not canonical:
        return None, False
    if make and canonical not in ref.MODELS_BY_MAKE.get(make, set()):
        # model doesn't belong to the currently-set make -> reject rather
        # than silently searching for a mismatched make/model pair
        return None, False
    return canonical, True


_FIELD_VALIDATORS = {
    "year_min": _validate_year,
    "year_max": _validate_year,
    "price_min": _validate_price,
    "price_max": _validate_price,
    "mileage_min": _validate_mileage,
    "mileage_max": _validate_mileage,
    "has_keys": _validate_bool,
    "body_style": lambda v: _validate_enum(v, _BODY_STYLE_LOOKUP),
    "color": lambda v: _validate_enum(v, _COLOR_LOOKUP),
    "primary_damage": lambda v: _validate_enum(v, _DAMAGE_LOOKUP),
    "title_type": lambda v: _validate_enum(v, _TITLE_TYPE_LOOKUP),
    "run_and_drive": lambda v: _validate_enum(v, _RUN_AND_DRIVE_LOOKUP),
    "drivetrain": lambda v: _validate_enum(v, _DRIVETRAIN_LOOKUP),
    "fuel_type": lambda v: _validate_enum(v, _FUEL_TYPE_LOOKUP),
    "transmission": lambda v: _validate_enum(v, _TRANSMISSION_LOOKUP),
    "location_state": _validate_location_state,
    "location_city": lambda v: _validate_enum(v, _YARD_CITY_LOOKUP),
    "make": lambda v: _validate_enum(v, _MAKE_LOOKUP),
}


def validate_diff(diff: FilterDiff, current_filters: FilterState) -> ValidationResult:
    dropped: list[str] = []
    clean_set: dict = {}
    current_make = current_filters.make

    effective_make = diff.set.get("make", current_make)
    if isinstance(effective_make, str):
        effective_make = _MAKE_LOOKUP.get(effective_make.strip().lower())

    for field_name, raw_value in diff.set.items():
        if field_name == "model":
            value, ok = _validate_model(raw_value, effective_make)
        else:
            validator = _FIELD_VALIDATORS.get(field_name)
            if validator is None:
                ok = False
                value = None
            else:
                value, ok = validator(raw_value)
        if ok:
            clean_set[field_name] = value
        else:
            dropped.append(f"couldn't understand {field_name}={raw_value!r}, ignoring it")

    # range sanity: min shouldn't exceed max once both are known post-validation
    for lo_key, hi_key in (("year_min", "year_max"), ("price_min", "price_max"), ("mileage_min", "mileage_max")):
        lo = clean_set.get(lo_key)
        hi = clean_set.get(hi_key)
        if lo is not None and hi is not None and lo > hi:
            dropped.append(f"{lo_key} was greater than {hi_key}, dropping both")
            clean_set.pop(lo_key, None)
            clean_set.pop(hi_key, None)

    clean_exclude: dict[str, list] = {}
    for field_name, raw_values in diff.exclude.items():
        validator = _FIELD_VALIDATORS.get(field_name)
        if field_name == "model" or validator is None:
            dropped.append(f"couldn't understand exclude on {field_name}, ignoring it")
            continue
        clean_values = []
        for raw_value in raw_values:
            value, ok = validator(raw_value)
            if ok:
                clean_values.append(value)
            else:
                dropped.append(f"couldn't understand excluded {field_name}={raw_value!r}, ignoring it")
        if clean_values:
            clean_exclude[field_name] = clean_values

    clean_unset = [f for f in diff.unset if f in _FIELD_VALIDATORS or f == "model"]

    # switching make without an explicit new model leaves a stale
    # model from the old make in place (e.g. "show Fords" -> "actually
    # Toyotas" would otherwise keep model=Mustang and silently zero results)
    new_make = clean_set.get("make")
    if (
        new_make
        and new_make != current_make
        and "model" not in clean_set
        and current_filters.model
        and current_filters.model not in ref.MODELS_BY_MAKE.get(new_make, set())
        and "model" not in clean_unset
    ):
        clean_unset.append("model")

    clean_diff = FilterDiff(
        set=clean_set,
        unset=clean_unset,
        exclude=clean_exclude,
        reset=diff.reset,
        off_topic=diff.off_topic,
        clarification_needed=diff.clarification_needed,
        clarification_question=diff.clarification_question,
    )
    return ValidationResult(diff=clean_diff, dropped=dropped)
