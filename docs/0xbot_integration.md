# 0xbot handoff — Decision API / MCP / disk queue

## Principle
Intelligence emits **intents**. 0xbot **validates and executes**. Stale intents die.

**FOMO sidebar is lagging.** `x_intel` must source early X / on-chain signals for detection and sizing context — **not** the FOMO.family sidebar MC/tape. Sidebar may be used only as a late confirmation surface for the Tampermonkey bridge, never as the primary early-MC source.

## Tampermonkey CSP → disk queue

FOMO.family CSP blocks arbitrary remote POSTs from userscripts. The production handoff is therefore a **local disk decision queue**:

| Path | Role |
|------|------|
| `data/decisions/<decision_id>.json` | One intent file per decision (`xintel.decision.v1`) |
| `data/decisions/inbox.jsonl` | Optional append-only index (flushed on each emit) |
| `data/execution_reports/<report_id>.json` | Gateway return path (`xintel.execution_report.v1`) |
| `data/outcomes/exec_<decision_id>_<report_id>.json` | Ingested outcome overlays |

Override the data root with env **`XINTEL_DATA_DIR`** (default: repo `data/`).

### Atomic write contract
Writers **MUST**:
1. Write full JSON to `<decision_id>.json.tmp`
2. `os.replace` (atomic rename) to `<decision_id>.json`
3. Never leave partial JSON readable under the final name
4. Readers skip `*.tmp` and tolerate missing/corrupt files

Same contract applies to execution reports.

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
- `do_not_execute_until_armed` — see **XINTEL_ARMED** below

## Expiry (BUY / ADD TTL = 5 minutes)
Default **`expires_at = issued_at + 5 minutes` (300s)** for `BUY` and `ADD`.

Bridge RTT is typically **5–15 seconds**; a 5-minute TTL leaves room for queue poll + UI confirm without turning delayed delivery into a late chase. Other actions default to 15 minutes unless overridden. Consumers **MUST** still treat `expires_at` as a hard staleness cutoff.

## XINTEL_ARMED — intentional live-risk switch

| Env | Default | Behavior |
|-----|---------|----------|
| `XINTEL_ARMED` | `false` | Decisions still emit to disk/API for **paper**; `do_not_execute_until_armed` **MUST** be `true` on newly emitted intents |
| `XINTEL_ARMED=true` | — | Newly emitted BUY/ADD/HOLD/… set `do_not_execute_until_armed=false` |

Helper: `x_intel.config.is_armed()` / `do_not_execute_until_armed()`.

**Arming is an intentional live-risk switch.** Do not flip it casually. Intelligence never places orders regardless of this flag — only 0xbot / the gateway may execute, and only when the flag on the intent permits it.

## Pursue → BUY emitter
```bash
python -m x_intel.emit.pursue_buy --candidate <path-or-id>
make emit-buy CANDIDATE=<path-or-id>
```
Soft gates (v0): contract+chain, early MC (default max $500k, null warns), non-empty evidence, feature scores: hard reject `D1=true`; require `S1` or `S3` when those booleans are scored; `window_type=frenzy-lane` does not require S2. Size stub: `percent_equity=1.0`, urgency `normal`. Emit also hits the disk queue.

## execution_report.v1 — return path / reject feedback

Gateway drops reports at `data/execution_reports/<report_id>.json` (atomic write). Schema includes:
- `decision_id` linkage
- `fills[]`: `requested_usd` vs `filled_usd`, `price`, `mc_at_fill`, `slippage_bps`, `latency_ms` (from decision `issued_at` → fill)
- `positions[]`, `equity_usd`
- `closed_round`: `multiple`, `hold_time_s`, `mfe`, `mae`
- `reject_reason` enum: `stale` | `no_cash` | `chain_unsupported` | `ca_unresolved` | `disable_buying` | `duplicate` | `below_min_size` | `other`

Ingest:
```bash
python -m x_intel.ledger.execution_reports
# or
python scripts/ingest_execution_reports.py
make ingest-reports
```

**This reject stream is the primary feedback for confidence weights** — count and weight rejects by taxonomy before trusting raw hit rates.

## MCP tools
- `list_decisions(since, min_confidence, unexpired_only)`
- `get_decision(decision_id)`
- `ack_decision(decision_id, status)` — consumed / rejected_by_execution / expired
- `list_candidates(since, decision)`
- `get_signal_leaderboard()`

## REST
- `GET /v1/health`
- `GET /v1/decisions?unexpired=1`
- `GET /v1/candidates`
- `POST /v1/ack`

## Safety
- No private keys in intel layer
- No order placement here
- Default BUY/ADD expiry **5 minutes**
- Disarmed by default (`XINTEL_ARMED=false`)

## Implemented in this repo

| Surface | Entry |
|---------|-------|
| Disk queue | `data/decisions/` via `CandidateLedger.save_decision` (atomic) |
| Pursue→BUY | `python -m x_intel.emit.pursue_buy` |
| Execution reports | `data/execution_reports/` + `x_intel.ledger.execution_reports` |
| REST | `uvicorn x_intel.api.main:app` |
| MCP | `python -m x_intel.mcp_server.server` |
| Schemas | `x_intel/schemas/models.py` + `*.json` |
| Config | `x_intel.config` (`XINTEL_DATA_DIR`, `XINTEL_ARMED`) |

Execution remains **disarmed until `XINTEL_ARMED=true`**. 0xbot must refuse to act on any intent while `do_not_execute_until_armed` is true or `now > expires_at`.
