"""
Search execution guardrail: turns a validated FilterState into a
parameterized SQL query against vehicles.db. LLM/user-derived values only
ever appear as bound parameters, never string-concatenated into SQL — the
set of columns and operators is fixed in code, so there's no injection
surface regardless of what a value contains (tested with
quotes/semicolons/"ignore previous instructions" style input).

Result set is capped; callers get both the capped rows and the true total
match count so the response layer can say "showing 10 of 47".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.filters import FilterState

DB_PATH = Path(__file__).parent.parent / "data" / "vehicles.db"
RESULT_CAP = 10

# FilterState field -> (column, comparison). Fixed, code-defined — never
# built from user/LLM input.
_RANGE_COLUMNS = {
    "year_min": ("year", ">="), "year_max": ("year", "<="),
    "price_min": ("current_bid", ">="), "price_max": ("current_bid", "<="),
    "mileage_min": ("odometer", ">="), "mileage_max": ("odometer", "<="),
}
_EQUALITY_COLUMNS = {
    "make": "make", "model": "model", "primary_damage": "primary_damage",
    "title_type": "title_type", "body_style": "body_style", "color": "color",
    "has_keys": "has_keys", "run_and_drive": "run_and_drive",
    "drivetrain": "drivetrain", "fuel_type": "fuel_type",
    "transmission": "transmission", "location_state": "yard_state",
    "location_city": "yard_city",
}


@dataclass
class SearchResult:
    rows: list[dict]
    total_matches: int


def search(filters: FilterState, limit: int = RESULT_CAP) -> SearchResult:
    clauses: list[str] = []
    params: list = []

    for field, (column, op) in _RANGE_COLUMNS.items():
        value = getattr(filters, field)
        if value is not None:
            clauses.append(f"{column} {op} ?")
            params.append(value)

    for field, column in _EQUALITY_COLUMNS.items():
        value = getattr(filters, field)
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)

    for field, values in filters.excludes.items():
        column = _EQUALITY_COLUMNS.get(field)
        if column and values:
            placeholders = ",".join("?" for _ in values)
            clauses.append(f"{column} NOT IN ({placeholders})")
            params.extend(values)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM vehicles {where}", params).fetchone()[0]
        cursor = conn.execute(
            f"SELECT * FROM vehicles {where} ORDER BY current_bid ASC LIMIT ?",
            [*params, limit],
        )
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

    return SearchResult(rows=rows, total_matches=total)
