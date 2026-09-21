"""
Picks the active intent/filter extractor: Claude tool-calling if
ANTHROPIC_API_KEY is set, otherwise the regex/keyword fallback. Both
implement the same `extract(message, current_filters) -> FilterDiff`
contract, so callers (app/main.py) don't need to know which is active.
"""

from __future__ import annotations

import os
from typing import Protocol

from app.filters import FilterDiff, FilterState


class Extractor(Protocol):
    def extract(self, message: str, current_filters: FilterState) -> FilterDiff: ...


class FallbackExtractor:
    """Thin wrapper so the module-level `extract` function matches the Extractor protocol."""

    def extract(self, message: str, current_filters: FilterState) -> FilterDiff:
        from app import extraction_fallback

        return extraction_fallback.extract(message, current_filters)


def get_extractor() -> tuple[Extractor, str]:
    """Returns (extractor, mode) where mode is "llm" or "fallback"."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key and api_key != "your_key_here":
        from app.extraction_llm import LLMExtractor

        return LLMExtractor(), "llm"
    return FallbackExtractor(), "fallback"
