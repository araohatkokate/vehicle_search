# Conversational Search Assistant

A conversational search experience over synthetic style vehicle
inventory. Describe what you want in plain English, then keep refining it
across turns — the app tracks filter state per session and applies each
message as a *diff* against it, not a full re-specification.

```
"Ford trucks under $10k" -> "with keys in Texas" -> "actually not a Ford"
```

## Quickstart

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate | macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# generate the synthetic inventory (deterministic, ~800 rows)
python data/generate_data.py

cp .env.example .env
# optional: paste an Anthropic API key from console.anthropic.com into .env
# for a big NLU quality upgrade, or skip this and it runs on the built-in
# rule-based fallback with zero secrets

uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

`GET /health` reports which extractor mode is active (`llm` or `fallback`).

## Architecture

```
static/index.html            single-page chat UI, hits /chat and /reset
app/main.py                  FastAPI app, orchestrates the pipeline below
app/filters.py                FilterState / FilterDiff (Pydantic) — the typed contract
app/extraction.py             picks LLM vs. fallback extractor at startup
app/extraction_llm.py          Claude tool-calling extractor
app/extraction_fallback.py     regex/keyword extractor (zero-secret fallback)
app/guardrails.py             validates every extracted diff before it's applied
app/search.py                  parameterized SQL query + result cap
app/response.py                grounded NL reply (LLM or deterministic template)
app/session.py                 in-memory FilterState per session_id
app/reference_data.py          canonical enum values, imported from data/generate_data.py
data/generate_data.py          synthetic data generator (single source of truth for enums)
data/SCHEMA.md                 documented vehicle-lot schema
```

Request flow for `POST /chat`:

```
message
  -> input guardrail (empty / length cap)
  -> extractor.extract(message, current_filters) -> FilterDiff   [LLM or fallback]
  -> off_topic? -> canned redirect, filters untouched
  -> clarification_needed? -> ask the question, filters untouched
  -> guardrails.validate_diff(diff, current_filters) -> cleaned FilterDiff
  -> filters.apply(cleaned diff)          [FilterState mutated in place]
  -> search.search(filters)               [parameterized SQL, capped, gets total count]
  -> response.generate_response(...)      [grounded in the exact rows returned]
```

### Why a diff, not a full re-specification

The extractor is asked to describe *what changed* against the filters
already applied, using three primitives:

- `set`: add or overwrite a field (`price_max: 15000`)
- `unset`: clear a field entirely
- `exclude`: values a field must *not* match (handles negation — "actually
  not a Ford" becomes `exclude: {make: [Ford]}`, not a guess at what the
  user *does* want)

This is what makes "show me Fords" -> "under 15k" -> "actually not a Ford"
work correctly across three independent turns instead of needing every
message to restate the whole query.

## Guardrails

Four checkpoints, because the brief calls out that inputs will be varied
and adversarial:

1. **Input guardrail** (`app/main.py`): empty messages short-circuit before
   spending an extractor call; messages are capped at 2000 characters.
2. **Extraction guardrail** (`app/guardrails.py`): every field in a
   `FilterDiff` — from either extractor — is checked against the canonical
   enum values in `app/reference_data.py` (imported from
   `data/generate_data.py`, so the "known good values" can't drift from the
   data itself) and against numeric bounds (year 1995–2025, price/mileage
   non-negative with a sane ceiling). Anything invalid is **dropped**, never
   passed through — the user gets a short note about what was ignored, not
   a silently wrong query. A make/model mismatch is also caught here: if a
   turn switches `make` without giving a new `model`, a stale model from the
   old make (e.g. `Mustang` after switching from Ford to Toyota) is unset
   rather than left in place to silently zero-out results.
3. **Execution guardrail** (`app/search.py`): filter values only ever reach
   SQLite as bound parameters (`?` placeholders) against a fixed,
   code-defined column map — there is no code path where extracted text
   becomes part of a SQL string. Result rows are capped (10) with the true
   total match count returned separately.
4. **Output/grounding guardrail** (`app/response.py`): the response-writing
   LLM call is given only the exact rows the query returned and is
   instructed to describe only those — it cannot invent a lot, price, or
   detail. The no-API-key template path is built directly from the same
   rows, so grounding holds in both modes.

On top of that: **low-confidence fallback** — if a message is a real search
request but too vague to turn into any concrete filter, the extractor sets
`clarification_needed` and the app asks a specific question instead of
guessing; **off-topic handling** — messages unrelated to vehicle search
(gibberish, small talk, "ignore previous instructions"-style text) are
caught and redirected without ever reaching the filter/search pipeline.

## Key decisions

- **Python + FastAPI**, not Java/Spring Boot. Chosen for build velocity
  under time pressure and because the LLM/NLP tooling ecosystem has far
  less friction in Python. A Spring Boot façade in front of this service
  would be the natural production path for stack-consistency with Copart's
  likely infra — worth noting, not worth building in the time available.
- **Claude (tool calling) for extraction and response generation**, over
  self-hosting an open-source model — small local models are meaningfully
  worse at reliable structured extraction, and getting one there would burn
  the time budget on infra instead of the actual problem.
- **Rule-based fallback extractor**, auto-activated with no API key. This
  isn't just a demo convenience — it's the architecture making the point
  that intent-extraction is a pluggable interface, not hard-wired to one
  vendor. It won't match the LLM's language understanding (it's regex over
  fairly literal phrasings — see the docstring in
  `app/extraction_fallback.py` for exactly what it does and doesn't catch),
  but it implements the identical `FilterDiff` contract and passes through
  the same guardrail.
- **Single HTML/JS page served by FastAPI**, no separate frontend
  framework/build step — keeps the deliverable as one app and matches the
  brief's "prioritize engineering over UI polish."
- **Synthetic data, not scraped**. Scraping copart.com risked ToS/reliability
  issues in a time-boxed take-home. `data/generate_data.py` encodes the real
  lot-listing schema (`data/SCHEMA.md`) and realistic value distributions
  instead, and is the single source of truth both the data and the
  validator import from.
- **`distance` dropped from the filter schema.** The original field list
  included `location`/`distance`, but the dataset has no zip code or
  lat/long — only `yard_city`/`yard_state`. A true radius search isn't
  possible without fabricating geo data, so `location_state`/`location_city`
  cover the location intent and distance was cut rather than faked.
- **In-memory session state**, keyed by `session_id`, generated
  client-side-less (server mints a UUID on first message and hands it back).
  Fine for a take-home; see below for the production path.

## Edge cases tested

Verified manually against a running server (see git history / can be
re-run with the curl commands used during development): empty message;
gibberish; off-topic questions; zero-result queries; a contradictory
refinement ("show me Fords" -> "actually not a Ford"); a compound
refinement with an in-message self-correction ("SUVs under 15k with keys
in TX, no wait, just Texas trucks"); a make/model mismatch across turns
("Ford Mustang" -> "actually Toyota"); adversarial/injection-looking input
(`'; DROP TABLE vehicles; -- ignore previous instructions...`); a very long
message; and `/reset` mid-conversation.

## Production evolution

- Real search infra (OpenSearch/pgvector) in place of raw SQLite once
  inventory scale and query complexity (fuzzy match, relevance ranking)
  outgrow a single-table `WHERE` builder.
- Redis (or similar) for session state instead of in-process memory, so
  state survives a restart and is shared across app instances.
- Rate limiting and cost controls on LLM calls (per-session and global caps,
  since both extraction and response generation are billed API calls).
- An eval set of labeled (message, expected diff) pairs to track NLU
  accuracy across model/prompt changes, especially for the fallback
  extractor and for negation/compound-refinement cases.
- Observability on conversation transcripts — structured logging of
  extracted diffs, guardrail drops, and zero-result queries, both for
  debugging and as a feedback loop into the eval set above.
- A Spring Boot façade in front of this service for stack consistency with
  Copart's likely production infra, if this were to move beyond a
  prototype.
