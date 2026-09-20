# Early-Mover Discovery Workstream

**Branch / experiment:** `feat/x-intel-early-discovery` / `xintel_v0`  
**Policy:** Live execution stays **DISARMED** (`do_not_execute_until_armed` / `XINTEL_ARMED` default false).

## Mandate (not an X-reactive scanner)

Earliest **practical** discovery across chains. Parallel ingest:

1. New pairs / launches (DexScreener)
2. Bonding-curve activity (pump.fun-style)
3. First-buyer / cluster hints (Flow Desk inbox)
4. FOMO / 0xbot sidebar dumps (**lagging confirmation only**)
5. X social (secondary)

Either **on-chain or X** can discover first and immediately trigger research fan-out. FOMO never sole-pursues.

## Architecture (prose diagram)

```
┌─────────────────────────────────────────────────────────────┐
│                     discovery.runner                         │
│              once (cron)  |  loop (≤30s default)              │
└──────────────┬──────────────────────────────────────────────┘
               │ poll() all adapters in parallel (same cycle)
     ┌─────────┼──────────┬──────────┬──────────┬────────────┐
     ▼         ▼          ▼          ▼          ▼            │
 dex_new   pump_curve   x_social  fomo_inbox  flow_inbox     │
     │         │          │          │          │            │
     └─────────┴──────────┴────┬─────┴──────────┘            │
                               ▼                             │
                     DiscoveryBus (dedupe)                    │
                   key=(chain, ca) sticky                     │
                   first_source + first_seen_at               │
                   sources[] append-only                      │
                               ▼                             │
                     pipeline.ingest_event                    │
         ledger write → enrich → gates.score → decision       │
                               │                             │
              ┌────────────────┼────────────────┐            │
              ▼                ▼                ▼            │
        agent_inbox      candidates/      decisions/         │
      narrative/market   pursue|watch     BUY if pursue      │
      flow/outcome       |reject          TTL 5m disarmed    │
─────────────────────────────────────────────────────────────┘
```

## Package map

| Path | Role |
|------|------|
| `x_intel/discovery/models.py` | `DiscoveryEvent`, `DiscoveryRecord` |
| `x_intel/discovery/bus.py` | Normalize + dedupe; sticky first_source |
| `x_intel/discovery/gates.py` | Threshold constants + pursue/watch/reject |
| `x_intel/discovery/pipeline.py` | ingest → ledger → score → optional BUY |
| `x_intel/discovery/fanout.py` | Agent work orders → `agent_inbox/{agent}/` |
| `x_intel/discovery/runner.py` | CLI `once` / `loop` |
| `x_intel/discovery/validate.py` | Historical + prospective earliness report |
| `x_intel/discovery/sources/*` | Parallel adapters (fixture/replay default) |

## Sources

| Adapter | Input | Notes |
|---------|-------|-------|
| `dexscreener_new` | Public DexScreener latest profiles (live opt-in) + fixtures | Solana+Base+ETH+BSC |
| `pumpfun_curve` | DexScreener pump search / fixtures | new curve + graduate/migrate |
| `x_social` | Wraps `FixtureIngest` + fixture posts | Secondary CA/launch cues |
| `fomo_sidebar` | `{XINTEL_DATA_DIR}/fomo_inbox/*.json` | **`lagging_universe=true` always** |
| `flow_hint` | `{XINTEL_DATA_DIR}/flow_inbox/*.json` | Optional first-buyer / sybil hints |

### FOMO inbox schema

```json
{
  "schema_version": "xintel.fomo_inbox.v0",
  "observed_at": "2026-09-20T12:00:00Z",
  "chain": "solana",
  "ca": "<contract>",
  "ticker": "EXAMPLE",
  "mc_usd": 1500000,
  "source_ui": "fomo_sidebar"
}
```

### Flow inbox schema

```json
{
  "schema_version": "xintel.flow_inbox.v0",
  "observed_at": "2026-09-20T12:00:00Z",
  "chain": "solana",
  "ca": "<contract>",
  "sybil": false,
  "cluster_score": 0.2,
  "first_buyer_count": 12
}
```

### Agent work-order schema

See `x_intel/discovery/fanout.py`. Path: `{XINTEL_DATA_DIR}/agent_inbox/{narrative|market|flow|outcome}/{event_id}.json`.

## Thresholds (from BATCH_01–03 evidence)

Encoded in `x_intel/discovery/gates.py`:

| Constant | Value | One-line rationale |
|----------|-------|--------------------|
| `AGE_MINUTES_PURSUE_MAX` | **30** | BATCH edge was minutes/hours from mint — past ~30m is usually not first size. |
| `AGE_MINUTES_WATCH_MAX` | **180** | Still interesting to watch through 3h; beyond → reject as late. |
| `MC_USD_PUMP_CURVE_MAX` | **250_000** | Pump-curve early band; PNUT Decrypt early <<$10k; $250k is practical ceiling before mid-print. |
| `MC_USD_DEX_NEW_MAX` | **1_000_000** | Already-listed dex-new with liq can remain early under ~$1M. |
| `MIN_LIQ_USD_AFTER_ENRICH` | **500** | Dead micro with ~0 liq after enrich → reject (untradable). |
| `MC_USD_HARD_REJECT` | **5_000_000** | Past $5M is not early-mover discovery. |
| `BUY_TTL_SECONDS` | **300** | Matches disk-queue BUY/ADD TTL (bridge RTT 5–15s). |
| FOMO-only | **hard reject** | Sidebar is lagging universe — never sole pursue trigger. |
| D1 / sybil | **hard reject** | BATCH death exemplar SHAR bundled concentration. |
| Known farm domains / airdrop_farm | **hard reject** | Explicit farm / claim funnels. |
| S1 or S3 when scored | **prefer pursue** | BATCH shortlist amplifiers / cultural prior when present. |
| S2 late_challenger (non-frenzy) | **reject** | Aligns with pursue_buy emitter; frenzy-lane exempt. |

### Soft prefer pursue when

- `age_minutes ≤ 30` from pair create / first seen
- MC under early band (pump ≤250k; dex-new ≤1M) or MC unknown
- S1 or S3 true when feature hints present (unscored → age+MC enough for v0)
- Not FOMO-only; not D1; resolvable CA

### Watch band

Interesting but missing flow/narrative boost, or age 30–180m.

### Reject

Parasites of known runners, airdrop farms, CA mismatch, dead micro zero liq, D1, FOMO-only, hard MC ceiling, S2 late_challenger.

## Disarmed policy

- `XINTEL_ARMED` default **false**
- Emitted BUY intents set `do_not_execute_until_armed: true` unless armed
- Discovery never places trades; only writes disk queue via existing `emit.pursue_buy`

## FOMO lag warning

Treat FOMO/0xbot sidebar as a **lagging confirmation universe**. Tokens that appear there are often already mid-print. Scoring **rejects FOMO-only** discoveries. When FOMO arrives after an on-chain/X first_source, it appends to `sources[]` without moving `first_seen_at`.

## How to run

```bash
make discovery-once          # one poll cycle (fixtures if not --live)
make discovery-validate      # write reports/early_discovery_validation_v0.md

python -m x_intel.discovery.runner once
python -m x_intel.discovery.runner loop --interval 30
python -m x_intel.discovery.runner once --live   # public HTTP; still disarmed
python -m x_intel.discovery.validate
```

## Validation

`x_intel/discovery/validate.py` simulates source ordering vs BATCH case T0/peak and is **explicit when minute data is missing**. Prospective runs log `discovery_latency_features` on every first sighting.
