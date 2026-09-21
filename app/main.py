"""
FastAPI app: /chat and /reset endpoints, in-memory session state, and the
static chat UI. This is the orchestration layer — it doesn't contain any
extraction, validation, or query logic itself, just wires the pipeline:

    input guardrail -> extract (LLM or fallback) -> validate (guardrail)
    -> apply diff -> search (parameterized) -> grounded response
"""

from __future__ import annotations

import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from app import guardrails, response, search, session
from app.extraction import FallbackExtractor, get_extractor

app = FastAPI(title="Copart Conversational Search")

STATIC_DIR = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_extractor, _extractor_mode = get_extractor()
_fallback_extractor = FallbackExtractor()

MAX_MESSAGE_LEN = 2000


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    filters: dict
    results: list[dict]
    total_matches: int


class ResetRequest(BaseModel):
    session_id: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "extractor_mode": _extractor_mode}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    session_id = req.session_id or str(uuid.uuid4())
    filters = session.get_filters(session_id)

    message = req.message.strip()
    if len(message) > MAX_MESSAGE_LEN:
        message = message[:MAX_MESSAGE_LEN]

    # input guardrail: nothing to extract from an empty message, and no
    # need to spend an LLM call finding that out
    if not message:
        return ChatResponse(
            session_id=session_id,
            reply="I didn't catch a message — what kind of vehicle are you looking for?",
            filters=filters.as_display_dict(),
            results=[],
            total_matches=0,
        )

    # extraction guardrail continued: an LLM call can fail for reasons that
    # have nothing to do with the user's message (rate limit, billing,
    # network blip). Degrade to the rule-based extractor for this turn
    # rather than 500ing the whole request.
    turn_mode = _extractor_mode
    try:
        diff = _extractor.extract(message, filters)
    except Exception:
        diff = _fallback_extractor.extract(message, filters)
        turn_mode = "fallback"

    if diff.off_topic:
        return ChatResponse(
            session_id=session_id,
            reply=(
                "I can only help with searching the vehicle inventory — makes/models, "
                "price, mileage, year, damage type, title status, location, and similar. "
                "What are you looking for?"
            ),
            filters=filters.as_display_dict(),
            results=[],
            total_matches=0,
        )

    if diff.clarification_needed:
        return ChatResponse(
            session_id=session_id,
            reply=diff.clarification_question or "Could you say a bit more about what you're looking for?",
            filters=filters.as_display_dict(),
            results=[],
            total_matches=0,
        )

    validated = guardrails.validate_diff(diff, filters)
    filters.apply(validated.diff)

    result = search.search(filters)
    reply = response.generate_response(message, filters, result, turn_mode)
    if turn_mode != _extractor_mode:
        reply += "\n\n(Note: the AI assistant was temporarily unavailable, so this used basic keyword matching instead.)"
    if validated.dropped:
        reply += "\n\n(Note: " + "; ".join(validated.dropped) + ".)"

    return ChatResponse(
        session_id=session_id,
        reply=reply,
        filters=filters.as_display_dict(),
        results=result.rows,
        total_matches=result.total_matches,
    )


@app.post("/reset")
def reset(req: ResetRequest) -> dict:
    session.reset(req.session_id)
    return {"session_id": req.session_id, "filters": {}}
