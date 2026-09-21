"""
Rule-based fallback extractor. Auto-activates when ANTHROPIC_API_KEY isn't
set (see app/extraction.py), so the app is runnable out of the box with
zero secrets, and stands as the second implementation of a pluggable
intent-extraction interface (not hard-wired to one vendor).

Not meant to match the LLM extractor's language understanding — it's a
regex/keyword net over the same FilterDiff contract, good enough for
common, fairly literal phrasings. Same guardrail layer validates its output.

Matches are collected with their position in the message and applied in
left-to-right order, so a later mention of the same field wins — this is
what makes a single-message self-correction like "SUVs under 15k... no
wait, just Texas trucks" resolve to trucks, not SUVs, without any special
discourse-marker handling.
"""

from __future__ import annotations

import re

from app import reference_data as ref
from app.filters import FilterDiff

RESET_RE = re.compile(r"\b(start over|reset|clear (?:all )?filters|clear (?:the )?search|begin again)\b", re.I)

NEGATION_RE = re.compile(
    r"\b(?:not a|not an|not|no(?:t)?|except for|excluding|except)\s+([a-z0-9][a-z0-9\- ]{1,30}?)(?=[,.;!?]|$| and | but )",
    re.I,
)

_MULTIPLIER_RE = re.compile(r"([\d,]*\.?\d+)\s*(k|m)?", re.I)


def _parse_amount(text: str) -> float | None:
    text = text.replace(",", "").replace("$", "").strip()
    m = _MULTIPLIER_RE.match(text)
    if not m or not m.group(1):
        return None
    value = float(m.group(1))
    if m.group(2) and m.group(2).lower() == "k":
        value *= 1_000
    elif m.group(2) and m.group(2).lower() == "m":
        value *= 1_000_000
    return value


# category -> {regex pattern (case-insensitive) : canonical value}
_CATEGORICAL_SYNONYMS: dict[str, dict[str, str]] = {
    "body_style": {
        r"\bsuvs?\b": "SUV",
        r"\btrucks?\b|\bpickups?\b": "Pickup",
        r"\bsedans?\b": "Sedan",
        r"\bcoupes?\b": "Coupe",
        r"\bvans?\b": "Van",
        r"\bmotorcycles?\b": "Motorcycle",
    },
    "primary_damage": {
        r"\bflood(?:ed|ing)?\b|\bwater damage\b": "WATER/FLOOD",
        r"\bhail\b": "HAIL",
        r"\bburn(?:ed|t)?\b|\bfire damage\b": "BURN",
        r"\bfront[\s-]?end\b": "FRONT END",
        r"\brear[\s-]?end\b": "REAR END",
        r"\bside damage\b|\bside[\s-]?impact\b": "SIDE",
        r"\bvandal(?:ism|ized)\b": "VANDALISM",
        r"\bmechanical\b": "MECHANICAL",
        r"\brollover\b|\ball[\s-]?over\b": "ALL OVER",
        r"\bundercarriage\b": "UNDERCARRIAGE",
        r"\bnormal wear\b": "NORMAL WEAR",
    },
    "title_type": {
        r"\bsalvage title\b|\bsalvage\b": "SALVAGE",
        r"\brebuilt(?:able)?\b|\brebuildable title\b": "REBUILT",
        r"\bclean title\b": "CLEAN",
        r"\bparts?[\s-]?only\b": "PARTS ONLY",
        r"\bjunk title\b|\bjunk\b": "JUNK",
    },
    "run_and_drive": {
        r"\bruns? and drives?\b|\bruns?\s*&\s*drives?\b": "RUNS_DRIVES",
        r"\bwon'?t start\b|\bdoesn'?t run\b|\bdoes not run\b|\bstart only\b": "START_ONLY",
        r"\bunknown if it runs\b|\brun status unknown\b": "UNKNOWN",
    },
    "drivetrain": {
        r"\ball[\s-]?wheel drive\b|\bawd\b": "AWD",
        r"\bfour[\s-]?wheel drive\b|\b4wd\b": "4WD",
        r"\bfront[\s-]?wheel drive\b|\bfwd\b": "FWD",
        r"\brear[\s-]?wheel drive\b|\brwd\b": "RWD",
    },
    "fuel_type": {
        r"\bdiesel\b": "DIESEL",
        r"\bhybrid\b": "HYBRID",
        r"\belectric\b|\bev\b": "ELECTRIC",
        r"\bgas(?:oline)?\b": "GAS",
    },
    "transmission": {
        r"\bmanual\b|\bstick shift\b": "MANUAL",
        r"\bautomatic\b": "AUTOMATIC",
    },
}

_COLOR_RE = {re.compile(rf"\b{re.escape(c.lower())}\b", re.I): c for c in ref.COLOR_VALUES}
# trailing s? so plurals ("Fords", "Mustangs", "Civics") still match the singular canonical value
_MAKE_RE = {re.compile(rf"\b{re.escape(m.lower())}s?\b", re.I): m for m in ref.MAKES}
_MODEL_RE = {re.compile(rf"\b{re.escape(m.lower())}s?\b", re.I): m for m in ref.ALL_MODELS}
_CITY_RE = {re.compile(rf"\b{re.escape(c.lower())}\b", re.I): c for c in ref.YARD_CITIES}
_STATE_NAME_RE = {re.compile(rf"\b{re.escape(name)}\b", re.I): code for name, code in ref.STATE_NAME_TO_CODE.items()}
_STATE_CODE_RE = re.compile(r"\b([A-Z]{2})\b")

_HAS_KEYS_TRUE_RE = re.compile(r"\bwith keys\b|\bhas keys\b|\bkeys? (?:are )?(?:present|included)\b", re.I)
_HAS_KEYS_FALSE_RE = re.compile(r"\bno keys\b|\bwithout keys\b|\bmissing keys\b|\bno key\b", re.I)

_PRICE_RANGE_RE = re.compile(
    r"\bbetween\s+\$?([\d,]*\.?\d+\s*[km]?)\s+and\s+\$?([\d,]*\.?\d+\s*[km]?)\b.{0,15}?(?:price|cost|\$|dollars)?",
    re.I,
)
_PRICE_UNDER_RE = re.compile(r"\b(?:under|below|less than|no more than|<=?)\s*\$?([\d,]*\.?\d+\s*[km]?)\b", re.I)
_PRICE_OVER_RE = re.compile(r"\b(?:over|above|more than|at least|>=?)\s*\$?([\d,]*\.?\d+\s*[km]?)\b", re.I)
_PRICE_BARE_RE = re.compile(r"\$\s*([\d,]*\.?\d+\s*[km]?)\b(?!\s*mi)", re.I)

_MILEAGE_UNDER_RE = re.compile(r"\b(?:under|below|less than|no more than)\s*([\d,]*\.?\d+\s*k?)\s*(?:miles?|mi\b)", re.I)
_MILEAGE_OVER_RE = re.compile(r"\b(?:over|above|more than|at least)\s*([\d,]*\.?\d+\s*k?)\s*(?:miles?|mi\b)", re.I)

_YEAR_RANGE_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\s*(?:-|to|through)\s*(19[5-9]\d|20[0-4]\d)\b")
_YEAR_NEWER_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\s*(?:or newer|or later|and newer|and later)\b|\b(?:after|since|from)\s+(19[5-9]\d|20[0-4]\d)\b", re.I)
_YEAR_OLDER_RE = re.compile(r"\b(?:before|older than|prior to)\s+(19[5-9]\d|20[0-4]\d)\b|\b(19[5-9]\d|20[0-4]\d)\s*(?:or older|and older)\b", re.I)

_VEHICLE_SIGNAL_RE = re.compile(
    r"\b(car|cars|vehicle|vehicles|truck|trucks|suv|suvs|sedan|van|coupe|motorcycle|lot|lots|"
    r"auction|bid|price|mile|miles|mileage|year|damage|title|salvage|keys?|drive|yard)\b",
    re.I,
)


def _find_categorical_matches(message: str, exclude_spans: list[tuple[int, int]]) -> list[tuple[int, str, str]]:
    def overlaps(pos: int) -> bool:
        return any(start <= pos < end for start, end in exclude_spans)

    found: list[tuple[int, str, str]] = []
    for field, synonyms in _CATEGORICAL_SYNONYMS.items():
        for pattern, canonical in synonyms.items():
            for m in re.finditer(pattern, message, re.I):
                if not overlaps(m.start()):
                    found.append((m.start(), field, canonical))
    for regex, canonical in _COLOR_RE.items():
        m = regex.search(message)
        if m and not overlaps(m.start()):
            found.append((m.start(), "color", canonical))
    for regex, canonical in _MAKE_RE.items():
        m = regex.search(message)
        if m and not overlaps(m.start()):
            found.append((m.start(), "make", canonical))
    for regex, canonical in _MODEL_RE.items():
        m = regex.search(message)
        if m and not overlaps(m.start()):
            found.append((m.start(), "model", canonical))
    for regex, canonical in _CITY_RE.items():
        m = regex.search(message)
        if m and not overlaps(m.start()):
            found.append((m.start(), "location_city", canonical))
    for regex, code in _STATE_NAME_RE.items():
        m = regex.search(message)
        if m and not overlaps(m.start()):
            found.append((m.start(), "location_state", code))
    for m in _STATE_CODE_RE.finditer(message):
        if m.group(1) in ref.LOCATION_STATES and not overlaps(m.start()):
            found.append((m.start(), "location_state", m.group(1)))
    return found


def _extract_negations(message: str) -> tuple[dict[str, list[str]], list[tuple[int, int]]]:
    """Returns (exclude dict, spans consumed) so positive scanning can skip them."""
    exclude: dict[str, list[str]] = {}
    spans: list[tuple[int, int]] = []
    for m in NEGATION_RE.finditer(message):
        phrase = m.group(1).strip().lower()
        matched_field = None
        matched_value = None
        for field, synonyms in _CATEGORICAL_SYNONYMS.items():
            for pattern, canonical in synonyms.items():
                if re.search(pattern, phrase, re.I):
                    matched_field, matched_value = field, canonical
                    break
            if matched_field:
                break
        if not matched_field:
            for regex, canonical in _MAKE_RE.items():
                if regex.search(phrase):
                    matched_field, matched_value = "make", canonical
                    break
        if not matched_field:
            for regex, canonical in _MODEL_RE.items():
                if regex.search(phrase):
                    matched_field, matched_value = "model", canonical
                    break
        if matched_field:
            exclude.setdefault(matched_field, [])
            if matched_value not in exclude[matched_field]:
                exclude[matched_field].append(matched_value)
            spans.append((m.start(), m.end()))
    return exclude, spans


def extract(message: str, current_filters=None) -> FilterDiff:
    message = message.strip()

    if not message:
        return FilterDiff(
            clarification_needed=True,
            clarification_question="What are you looking for? Try something like \"trucks under $10k in Texas\".",
        )

    if RESET_RE.search(message):
        return FilterDiff(reset=True)

    exclude, negation_spans = _extract_negations(message)
    matches = _find_categorical_matches(message, negation_spans)
    matches.sort(key=lambda t: t[0])

    set_fields: dict = {}
    for _, field, value in matches:
        set_fields[field] = value  # later (rightmost) match for a field wins

    m = _PRICE_RANGE_RE.search(message)
    if m:
        lo, hi = _parse_amount(m.group(1)), _parse_amount(m.group(2))
        if lo is not None:
            set_fields["price_min"] = lo
        if hi is not None:
            set_fields["price_max"] = hi
    else:
        m = _PRICE_UNDER_RE.search(message)
        if m:
            amount = _parse_amount(m.group(1))
            if amount is not None:
                set_fields["price_max"] = amount
        m = _PRICE_OVER_RE.search(message)
        if m:
            amount = _parse_amount(m.group(1))
            if amount is not None:
                set_fields["price_min"] = amount
        if "price_min" not in set_fields and "price_max" not in set_fields:
            m = _PRICE_BARE_RE.search(message)
            if m:
                amount = _parse_amount(m.group(1))
                if amount is not None:
                    set_fields["price_max"] = amount

    m = _MILEAGE_UNDER_RE.search(message)
    if m:
        amount = _parse_amount(m.group(1))
        if amount is not None:
            set_fields["mileage_max"] = int(amount)
    m = _MILEAGE_OVER_RE.search(message)
    if m:
        amount = _parse_amount(m.group(1))
        if amount is not None:
            set_fields["mileage_min"] = int(amount)

    m = _YEAR_RANGE_RE.search(message)
    if m:
        set_fields["year_min"] = int(m.group(1))
        set_fields["year_max"] = int(m.group(2))
    else:
        m = _YEAR_NEWER_RE.search(message)
        if m:
            set_fields["year_min"] = int(m.group(1) or m.group(2))
        m = _YEAR_OLDER_RE.search(message)
        if m:
            year = int(m.group(1) or m.group(2))
            set_fields["year_max"] = year - 1 if m.group(1) else year

    if _HAS_KEYS_TRUE_RE.search(message):
        set_fields["has_keys"] = True
    elif _HAS_KEYS_FALSE_RE.search(message):
        set_fields["has_keys"] = False

    if not set_fields and not exclude and not _VEHICLE_SIGNAL_RE.search(message):
        return FilterDiff(off_topic=True)

    return FilterDiff(set=set_fields, exclude=exclude)
