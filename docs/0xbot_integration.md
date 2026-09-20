# 0xbot handoff — Decision API / MCP

## Principle
Intelligence emits **intents**. 0xbot **validates and executes**. Stale intents die.

## Actions
`BUY` | `ADD` | `REDUCE` | `EXIT` | `HOLD`

## Required fields (xintel.decision.v1)
- `decision_id` (uuid)
- `action`
- `issued_at`, `expires_at` — **0xbot MUST drop if now > expires_at**
- `contract_address`, `chain`
- `confidence` 0..1
- `sizing_intent` `{ mode, value?, max_slippage_bps?, urgency }`
- `evidence[]` multi-channel
- `market_snapshot` at issue time
- `experiment_id`
- `do_not_execute_until_armed: true` until Jacob enables live mode

## MCP tools (planned)
- `list_decisions(since, min_confidence, unexpired_only)`
- `get_decision(decision_id)`
- `ack_decision(decision_id, status)` — consumed / rejected_by_execution / expired
- `list_candidates(since, decision)`
- `get_signal_leaderboard()`

## REST (planned)
- `GET /v1/health`
- `GET /v1/decisions?unexpired=1`
- `GET /v1/candidates`
- `POST /v1/ack`

## Safety
- No private keys in intel layer
- No order placement here
- Default expiry short (e.g. 2–5 minutes for entries) so delayed delivery cannot become a late chase

## Implemented in this repo

| Surface | Entry |
|---------|-------|
| REST | `uvicorn x_intel.api.main:app` — `/v1/health`, `/v1/candidates`, `/v1/decisions`, `/v1/ack`, `/v1/leaderboard` |
| MCP | `python -m x_intel.mcp_server.server` — tools listed above |
| Schemas | `x_intel/schemas/models.py` + `x_intel/schemas/*.json` |
| Ledger | `x_intel.ledger.store.CandidateLedger` over `data/candidates` + `data/fixtures/decisions` |

Execution remains **disarmed**. 0xbot must refuse to act on any intent while `do_not_execute_until_armed` is true or `now > expires_at`.
