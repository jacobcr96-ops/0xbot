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
| `MC_USD_PUMP_CURVE_MAX` | **1_000_000** | Pump-curve actionable early band; unique first-sights (SCAT/ZEBRA-class) under ~$1M may pursue/BUY when quality passes; ≥$4M never-bought stays no_chase. |
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
- MC under early band (pump/dex-new ≤1M) or MC unknown
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
make discovery-once          # one poll cycle (live when XINTEL_ARMED=true, else offline)
make discovery-offline       # deliberate offline/fixture cycle (--offline)
make discovery-validate      # write reports/early_discovery_validation_v0.md

python -m x_intel.discovery.runner once
python -m x_intel.discovery.runner loop --interval 30
python -m x_intel.discovery.runner once --live      # force live (default when armed)
python -m x_intel.discovery.runner once --offline   # force offline (alias --no-live)
python -m x_intel.discovery.validate
```

## Validation

`x_intel/discovery/validate.py` simulates source ordering vs BATCH case T0/peak and is **explicit when minute data is missing**. Prospective runs log `discovery_latency_features` on every first sighting.


## WATCH dip-buy escalate

Module: `x_intel/discovery/watch_escalate.py` (wired in `runner.run_cycle` after source ingest).

| Rule | Threshold |
|------|-----------|
| Dip BUY | `mc_now ≤ $2M` AND (`first_sight` null OR `mc_now ≤ 0.85 × first_sight`) |
| Reclaim BUY | `min_mc_seen < $2M` AND `mc_now < min(first_sight×1.1, $3.5M)` once |
| No chase | `mc_now ≥ $4M` and never bought |
| Stale watch | refresh age `> 600s` **and** buyable (last MC ≥ `XINTEL_WATCH_BUY_MIN_MC`, or unknown) — see below |
| Flag | `watch_dip_buy` on real BUY (no shadow/calibration) |

Solana preferred; skip hard-rugged / name-parasite clones. Persist `mc_usd_now`, `min_mc_usd_seen`, `refreshed_at`.

### Watch freshness: buyable-only stale alert

Sub-floor WATCHes (last known MC < `XINTEL_WATCH_BUY_MIN_MC`, default $25k) can
never trigger a BUY, so they never alert. They are still refreshed every cycle
(after the buyable ones) and reported separately.

`data/health/watch_freshness.json` (written by `refresh_open_watches`):

| Field | Meaning |
|-------|---------|
| `watch_stale_count` / `stale` / `stale_buyable` | buyable WATCHes with refresh age > 600s (**alerting count**) |
| `stale_subfloor` | sub-floor WATCHes with refresh age > 600s (report only, never alert) |
| `stale_total` | all WATCHes with refresh age > 600s |
| `stale_watch_alert` | `stale_buyable > 0` |
| `oldest_watch_refresh_age_sec` | oldest refresh age among **buyable** WATCHes |
| `oldest_watch_refresh_age_sec_all` | oldest refresh age among all open WATCHes |
| `watch_buyable_count` / `watch_subfloor_count` / `watch_buy_min_mc_usd` | split + floor used |
| `jup_errors` / `jup_stats` | unrecovered Jupiter errors; 429 retry stats (`http429`, `retries`, `wait_sec`, `recovered`, `gave_up`, `unanswered`) |
| `watches[].stale` | buyable-only stale flag (what the watchdog lists) |
| `watches[].refresh_stale` / `buyable` / `subfloor` | raw age flag + classification |

`data/health/heartbeat.json` mirrors: `watch_stale_count` (buyable-only),
`watch_stale_buyable`, `watch_stale_subfloor`, `stale_watch_alert`,
`oldest_watch_refresh_age_sec` (buyable-only), `oldest_watch_refresh_age_sec_all`,
`watch_jup_stats`, `watch_jup_errors`.

**Scan / watchdog routines:** decide the stale-watch alert from heartbeat
`stale_watch_alert` (or `watch_stale_buyable > 0`), and list offenders from
`watch_freshness.json` rows with `stale: true`. Never compute staleness from
`refresh_age_sec` alone or from `stale_total` / `stale_subfloor` /
`oldest_watch_refresh_age_sec_all` — those include dead sub-floor coins.

### Jupiter 429 handling (watch MC quotes)

`fetch_jup_assets` (datapi batch): on HTTP 429 it waits
`max(Retry-After, backoff)` — `Retry-After` is delta-seconds or HTTP-date and is
never undercut; backoff is exponential with jitter (0.75s, 1.5s + ≤0.5s jitter),
needed because datapi answers 429 with `Retry-After: 0`; at most 3 tries within a 10s budget; each retry
halves the failed batch (min 5). A `Retry-After` > 5s or beyond the budget gives
up immediately and sets a process-local cooldown so per-mint enrich quotes skip
Jupiter (they never retry — bounded ingest latency). Watch refresh orders mints
buyable-first (highest MC first) so STAMP/OURA/IOF/USOS-class coins get quoted
before dead ones; pump.fun → Dex emergency fallback covers the rest.

### Dead-coin close during a Jupiter outage

Buyable WATCHes: a Jupiter outage/429 never counts toward the dead streak.
Sub-floor WATCHes: when Jupiter did not answer **and** pump.fun returns no MC
**and** Dex answered with no usable pair, the refresh counts as
`no_mc_all_sources` (a Dex error/429 is an outage and does not count); 3 consecutive
refreshes (`WATCH_DEAD_NULL_STREAK`, runner refreshes twice per cycle) close
the WATCH as `expired_stale` (`dead_coin_no_mc_all_sources … mc_below_floor`).
Any successful quote resets the streak.

## Organic X (Jacob ping)

See [ORGANIC_X.md](ORGANIC_X.md). Scans should set `XINTEL_X_SOCIAL_LIVE=1`.
Agent writes `data/x_organic/<mint>.json` (≤5 CAs/cycle); enrich stamps `organic_x`.
`XINTEL_ORGANIC_X_REQUIRED_FOR_PING=1` → Jacob ping only on organic_x or strong unique curve+profile (no HELLO floods).

## Multi-chain (BSC / Base / ETH)

See [MULTI_CHAIN.md](MULTI_CHAIN.md).

## WATCH dip/reclaim BUY hard gates

Every WATCH dip/reclaim BUY (`watch_escalate.py`) must pass, in order (applied
even under `--no-emit`, so the escalate summary never shows `should_buy` for a
gated row):

| Gate | Env | Default | Reject reason |
|------|-----|---------|---------------|
| Live-MC floor | `XINTEL_WATCH_BUY_MIN_MC` | `25000` | `gate_reject:mc_below_watch_buy_floor` |
| Per-mint dedupe vs ANY prior watch BUY (incl. expired/cancelled) | `XINTEL_WATCH_BUY_DEDUPE_SEC` (cannot go below 7200) | 2h | `gate_reject:watch_buy_dedupe_2h` |
| Real organic X (`organic_x=True`, CA-scoped, not boost) | on whenever `XINTEL_ORGANIC_X_REQUIRED_FOR_PING=1`; else `XINTEL_WATCH_BUY_REQUIRE_ORGANIC_X` (default on) | on | `gate_reject:watch_buy_requires_organic_x` |

`watch_dip_quality_path` and `S1/S3 unset — soft pass` can no longer arm a watch
BUY without organic X; they are downgraded to a logged gate reject.
A drawdown-from-first-sight floor is intentionally NOT implemented (pending decision).

### Cancelling queued decisions
Set on the decision JSON: `status="cancelled"`, `cancelled=true`,
`do_not_publish=true`, `do_not_execute_until_armed=true`, `expires_at=<now>`
(original kept in `original_expires_at`), `cancel_reason`, plus an
`acks/<id>.json` sidecar with `status=expired`. `scripts/publish_decisions_git.sh`
skips any decision with `status` cancelled/expired, `cancelled|expired|do_not_publish`
true, and drops their lines from the published `inbox.jsonl`.
