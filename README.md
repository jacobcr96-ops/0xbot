# 0xbot

Dual-role repo:

| Layer | Status | Responsibility |
|-------|--------|----------------|
| **x_intel** (this PR) | Active, **disarmed** | Detect candidates, score features, emit decision *intents* |
| **Execution** (future) | Not armed | Wallets, orders, balances, hard safety — Claude / 0xbot |

**No live trading until `XINTEL_ARMED=true`.** Disarmed decisions still emit for paper with `do_not_execute_until_armed: true`. Default experiment: **`xintel_v0`**.

Disk queue: `data/decisions/` (atomic). BUY/ADD TTL: **5 minutes**. See [docs/0xbot_integration.md](docs/0xbot_integration.md).

## Quick start

```bash
make install          # creates .venv, installs package + pytest
make test             # unit tests
make demo             # load fixtures, print findings paths + curl examples
make api              # uvicorn x_intel.api.main:app on :8080
make mcp              # list MCP handoff tools
make emit-buy CANDIDATE=<id>   # pursue→BUY → data/decisions/
make ingest-reports   # load execution_report.v1 → outcomes
```

Or manually:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
python scripts/demo.py
uvicorn x_intel.api.main:app --host 127.0.0.1 --port 8080
```

### Curl examples (API running)

```bash
curl -s http://127.0.0.1:8080/v1/health
curl -s 'http://127.0.0.1:8080/v1/candidates'
curl -s 'http://127.0.0.1:8080/v1/decisions?unexpired=1'
curl -s http://127.0.0.1:8080/v1/leaderboard
```

### MCP tools (0xbot handoff)

```bash
python -m x_intel.mcp_server.server tools
python -m x_intel.mcp_server.server call list_candidates '{}'
python -m x_intel.mcp_server.server call get_account_leaderboard '{}'
```

Tools: `list_decisions`, `get_decision`, `list_candidates`, `get_account_leaderboard`, `ack_decision`.

## Package layout

```
x_intel/
  schemas/      # pydantic + JSON schemas (candidate_v1, decision_v1, …)
  api/          # FastAPI: health, candidates, decisions, ack, leaderboard
  mcp_server/   # MCP tool surface for handoff
  ledger/       # candidate store + outcome return math
  scoring/      # feature freeze + inspectable weights (anti-hindsight)
  research/     # historical cases / hypotheses loaders
  ingest/       # X + market interfaces (fixture/replay mode)
  emit/         # pursue→BUY emitter (disk queue)
  config.py     # XINTEL_DATA_DIR, XINTEL_ARMED
docs/           # ARCHITECTURE, integration, outcome protocol, feature registry
reports/        # BATCH_01 case files, signal hypotheses, leaderboard
data/           # candidates, outcomes, decisions/ queue, execution_reports/
```

## Safety rules

- Intelligence **never** places orders, holds keys, or swaps.
- `expires_at` is a hard staleness cutoff — consumers MUST drop expired intents.
- Actions: `BUY` | `ADD` | `REDUCE` | `EXIT` | `HOLD` (intents only).
- Candidate ledger logs `pursue` | `watch` | `reject` with outcome horizons 5m/15m/1h/6h/24h.
- Promote/demote signals only with OOS evidence under `experiment_id=xintel_v0`.

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/0xbot_integration.md](docs/0xbot_integration.md)
- [docs/OUTCOME_PROTOCOL.md](docs/OUTCOME_PROTOCOL.md)
- [docs/FEATURE_REGISTRY_v0.md](docs/FEATURE_REGISTRY_v0.md)
- [reports/BATCH_01_INDEX.md](reports/BATCH_01_INDEX.md)
- [reports/signal_hypotheses_v0.md](reports/signal_hypotheses_v0.md)
