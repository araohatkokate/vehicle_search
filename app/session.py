"""
In-memory session state keyed by session_id. Fine for a take-home; a real
deployment would move this to Redis (see README) so state survives a
restart and is shared across app instances.
"""

from __future__ import annotations

import threading

from app.filters import FilterState

_sessions: dict[str, FilterState] = {}
_lock = threading.Lock()


def get_filters(session_id: str) -> FilterState:
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = FilterState()
        return _sessions[session_id]


def reset(session_id: str) -> None:
    with _lock:
        _sessions[session_id] = FilterState()
