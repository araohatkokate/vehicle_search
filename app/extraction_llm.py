"""
LLM-based intent/filter extraction via Claude tool calling.

Takes the user's message plus the current FilterState and returns a
FilterDiff — a *diff*, not a full re-specification — which is what makes
multi-turn refinement work ("show me Fords" -> "under 15k" -> "actually not
a Ford" each only describes the change against what's already applied).

Every value this produces still passes through app.guardrails before it
touches the database; this module is trusted to be well-intentioned but not
trusted to be correct.
"""

from __future__ import annotations

import json
import os

from anthropic import Anthropic

from app import reference_data as ref
from app.filters import FilterDiff, FilterState

MODEL = "claude-sonnet-4-5"

_TOOL = {
    "name": "update_filters",
    "description": (
        "Record how the user's latest message changes the vehicle search filters, "
        "as a diff against the CURRENT filters already shown to you. Only include "
        "fields that actually change. Use `exclude` for negation (e.g. 'not a Ford'), "
        "`unset` to remove a filter entirely, and `reset` when the user wants to "
        "start over."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "set": {
                "type": "object",
                "description": (
                    "Fields to add or overwrite. Keys must be from: make, model, "
                    "year_min, year_max, price_min, price_max, mileage_min, "
                    "mileage_max, primary_damage, title_type, body_style, color, "
                    "has_keys, run_and_drive, drivetrain, fuel_type, transmission, "
                    "location_state, location_city."
                ),
                "additionalProperties": True,
            },
            "unset": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Field names to clear entirely.",
            },
            "exclude": {
                "type": "object",
                "description": "Field name -> list of values the user explicitly does NOT want.",
                "additionalProperties": {"type": "array"},
            },
            "reset": {
                "type": "boolean",
                "description": "True if the user wants to clear all filters and start over.",
            },
            "off_topic": {
                "type": "boolean",
                "description": "True if the message has nothing to do with searching vehicle inventory.",
            },
            "clarification_needed": {
                "type": "boolean",
                "description": "True if the message is too ambiguous to safely turn into filters.",
            },
            "clarification_question": {
                "type": "string",
                "description": "A short question to ask the user, only set when clarification_needed is true.",
            },
        },
        "required": ["set", "unset", "exclude", "reset", "off_topic", "clarification_needed"],
    },
}


def _system_prompt() -> str:
    makes_models = {make: sorted(models) for make, models in sorted(ref.MODELS_BY_MAKE.items())}
    return f"""You extract structured search filters for a used/salvage vehicle
inventory search (Copart-style lot listings). You will be shown the CURRENT
filters already applied in this conversation and the user's newest message.
Call `update_filters` exactly once with only what changes.

Rules:
- This is a DIFF, not a full restatement. Never repeat fields that don't change.
- If the user contradicts or negates an earlier filter (e.g. "actually not a
  Ford", "no wait, just trucks"), express it precisely: use `exclude` for
  negation, `set` to replace a value, `unset` to remove a filter entirely.
- If the message bundles multiple constraints ("SUVs under 15k with keys in
  TX"), extract all of them in one call.
- Only use these valid values (anything else, ask for clarification instead
  of guessing):
  - make/model pairs: {json.dumps(makes_models)}
  - primary_damage: {sorted(ref.PRIMARY_DAMAGE_VALUES)}
  - title_type: {sorted(ref.TITLE_TYPE_VALUES)}
  - body_style: {sorted(ref.BODY_STYLE_VALUES)}
  - color: {sorted(ref.COLOR_VALUES)}
  - run_and_drive: {sorted(ref.RUN_AND_DRIVE_VALUES)}
  - drivetrain: {sorted(ref.DRIVETRAIN_VALUES)}
  - fuel_type: {sorted(ref.FUEL_TYPE_VALUES)}
  - transmission: {sorted(ref.TRANSMISSION_VALUES)}
  - location_state (2-letter): {sorted(ref.LOCATION_STATES)}
  - location_city: {sorted(ref.YARD_CITIES)}
  - price_min/price_max are USD against the current bid; mileage_min/max are
    odometer miles; year_min/max are model years.
- If the message is unrelated to searching vehicles (e.g. small talk, a
  question about your capabilities, something nonsensical), set `off_topic`
  true and leave `set`/`unset`/`exclude` empty.
- If the message is a real search request but too vague to turn into any
  concrete filter (e.g. "show me something good"), set `clarification_needed`
  true and write a short, specific `clarification_question`.
- Never invent a make, model, or location that the user didn't say or clearly imply.
"""


class LLMExtractor:
    def __init__(self) -> None:
        self._client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def extract(self, message: str, current_filters: FilterState) -> FilterDiff:
        current = current_filters.as_display_dict()
        user_content = (
            f"CURRENT FILTERS: {json.dumps(current) if current else '(none yet)'}\n\n"
            f"USER MESSAGE: {message}"
        )
        response = self._client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_system_prompt(),
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "update_filters"},
            messages=[{"role": "user", "content": user_content}],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "update_filters":
                data = dict(block.input)
                data.setdefault("set", {})
                data.setdefault("unset", [])
                data.setdefault("exclude", {})
                return FilterDiff(**data)

        # Model didn't call the tool (shouldn't happen with tool_choice forced) —
        # fail safe into a clarification rather than silently doing nothing.
        return FilterDiff(
            clarification_needed=True,
            clarification_question="Sorry, I didn't quite catch that — could you rephrase what you're looking for?",
        )
