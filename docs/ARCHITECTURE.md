# X-Intel — Intelligence & Decision Layer for 0xbot

## Roles
| Layer | Owner | Responsibility |
|-------|--------|----------------|
| Intelligence / Decision | Grok Bot + specialist agents | What deserves capital, when evidence changes, sizing *intent*, staleness |
| Execution | Claude / 0xbot | Wallets, orders, balances, sells, hard safety, slippage, nonce |

**Live trade commands are DISARMED** until Jacob arms the pipeline (`do_not_execute_until_armed: true`).

## Objective
Maximize long-run compounded equity: find major runners early, size into genuine winners, reject noise, recycle failures, keep tail exposure. **Not** win-rate or activity.

## Specialist agents (established)
| Agent | Role |
|-------|------|
| X Scout | Early X detection, CA propagation, shill/fake-engagement filters |
| Narrative Filter | Narrative quality, originality, KOL credibility |
| Market Check | MC/liq/vol/age/authority snapshots at detection time |
| Flow Desk | Wallet clustering, early buyers, funding paths, LP events |
| Outcome Desk | Prospective ledger: every candidate → returns/MDd; rank signals OOS |
| Historian | Anti-hindsight reconstruction of monster runners vs lookalike failures |

Coordinator: **Grok Bot** synthesizes channels → structured decisions → (later) 0xbot MCP/API.

## Data channels
1. **X social** — posts, acceleration, account graph, CA spread (testable, not proof)
2. **Market microstructure** — DexScreener/Birdeye: age, MC, liq, vol
3. **On-chain flow** — holders, early buyers, clusters, LP
4. **Narrative / web** — story analogs, launch context
5. **Prospective ledger** — our own alert→outcome database (ground truth for learning)
6. **Historical case files** — timestamp-frozen feature matrices

## Experiment discipline
- Every change ships under an `experiment_id` (e.g. `xintel_v0`)
- Promote rules only when full-sample / OOS evidence supports them
- Never invent a rule from one winner, rug, or miss

## Continuous loop
Detect → log candidate (incl. rejects) → multi-channel score → decision intent → outcome track → rank sources → (optional) versioned policy update

## Schemas
- `x_intel/schemas/candidate_v1.json` — every detection
- `x_intel/schemas/decision_v1.json` — BUY/ADD/REDUCE/EXIT/HOLD handoff to 0xbot

## Package map (0xbot repo)

| Path | Role |
|------|------|
| `x_intel/schemas` | Pydantic models + JSON Schema sources |
| `x_intel/api` | FastAPI decision/candidate surface |
| `x_intel/mcp_server` | MCP tools for execution-layer handoff |
| `x_intel/ledger` | Candidate store, acks, outcome math |
| `x_intel/scoring` | Feature freeze + inspectable provisional weights |
| `x_intel/research` | BATCH_01 / hypotheses loaders |
| `x_intel/ingest` | Fixture/replay X + market interfaces |
| `data/` | Prospective candidates, outcomes, decision fixtures |
| `reports/` | Historian BATCH_01 artifacts |
