"""
Output/grounding guardrail: turns search results into a natural-language
reply. The LLM path is only ever given the exact rows that matched the
query and is explicitly instructed to describe only those — it cannot
invent a vehicle, price, or detail that isn't in the passed rows. The
template path (no API key) is fully deterministic, built directly from the
same rows, so grounding holds in both modes.
"""

from __future__ import annotations

import json
import os

from app.filters import FilterState
from app.search import SearchResult

MODEL = "claude-sonnet-4-5"


def _row_summary(row: dict) -> str:
    price = f"${row['current_bid']:,.0f}"
    keys = "with keys" if row["has_keys"] else "no keys"
    return (
        f"lot {row['lot_id']}: {row['year']} {row['make']} {row['model']} — {price}, "
        f"{row['odometer']:,} mi, {row['primary_damage']} damage, {row['title_type']} title, "
        f"{keys}, {row['yard_city']}, {row['yard_state']}"
    )


def _template_response(filters: FilterState, result: SearchResult) -> str:
    if result.total_matches == 0:
        active = filters.as_display_dict()
        if active:
            return (
                "No lots match that combination of filters. "
                f"Currently applied: {json.dumps(active)}. Try loosening one, "
                "e.g. widening the price or mileage range, or dropping a location filter."
            )
        return "No lots match right now - try describing what you're looking for, e.g. \"Toyota SUVs under $10k\"."

    lines = [_row_summary(row) for row in result.rows]
    header = f"Found {result.total_matches} matching lot{'s' if result.total_matches != 1 else ''}"
    if result.total_matches > len(result.rows):
        header += f" (showing the {len(result.rows)} lowest current bids)"
    return header + ":\n" + "\n".join(f"- {line}" for line in lines)


def _llm_response(message: str, filters: FilterState, result: SearchResult) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system = """You are a search assistant for a vehicle salvage-auction inventory.
You will be given the ACTIVE FILTERS and the exact QUERY RESULT ROWS that
matched them. Write a short, natural reply summarizing the results.

Hard rule: only describe vehicles that appear in QUERY RESULT ROWS. Never
invent a lot, price, make/model, or detail not present in those rows. If
ROWS is empty, say so plainly and suggest loosening a specific active
filter — don't pretend to have found something.
If TOTAL_MATCHES is greater than the number of rows shown, mention that
only the top matches (by lowest current bid) are shown."""
    user = (
        f"USER'S LATEST MESSAGE: {message}\n\n"
        f"ACTIVE FILTERS: {json.dumps(filters.as_display_dict())}\n\n"
        f"TOTAL_MATCHES: {result.total_matches}\n\n"
        f"QUERY RESULT ROWS: {json.dumps(result.rows)}"
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def generate_response(message: str, filters: FilterState, result: SearchResult, mode: str) -> str:
    if mode == "llm":
        try:
            return _llm_response(message, filters, result)
        except Exception:
            # response generation is a nicety on top of a query that already
            # succeeded — never let an LLM hiccup turn into a 500
            return _template_response(filters, result)
    return _template_response(filters, result)
