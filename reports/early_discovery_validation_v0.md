# Early Discovery Validation v0

**Generated:** 2026-09-20T20:15:28.855967+00:00 (UTC)
**Experiment:** `xintel_v0` / discovery workstream

## Honesty preamble

BATCH_01 case files mostly lack minute-level MC/liq/X series at T0. Where minute data is missing, source fire times below are **labeled policy assumptions** (ordering: pump/dex → flow → X → FOMO), not backtested archive hits. Do not treat lead-time hours as measured alpha.

## Threshold constants (gates.py)

| Constant | Value | Rationale |
|----------|-------|-----------|
| `AGE_MINUTES_PURSUE_MAX` | 30 | Early edge dies after first half-hour of pair life in BATCH pump cohort. |
| `MC_USD_PUMP_CURVE_MAX` | 250_000 | Curve tokens past ~$250k are usually mid-print; PNUT early was <<$10k. |
| `MC_USD_DEX_NEW_MAX` | 1_000_000 | Listed dex-new with liq can still be early under $1M. |
| `MIN_LIQ_USD_AFTER_ENRICH` | 500 | Dead micros with ~0 liq are untradable rejects. |
| `MC_USD_HARD_REJECT` | 5_000_000 | Past $5M is not early-mover discovery. |
| FOMO-only | hard reject | Sidebar is lagging confirmation — never sole pursue. |
| D1/sybil | hard reject | BATCH death exemplar SHAR. |

## Historical simulations

### runner_pnut_sol (PNUT)

- **T0:** 2024-10-31T14:21:40+00:00
- **Peak approx:** 2024-11-20T00:00:00+00:00
- **Minute data:** False
- **Honesty:** MINUTE-LEVEL HISTORICAL DATA MISSING — fire times are policy assumptions for source ordering, not measured DexScreener/X archives.
- **Earliest assumed source:** `pumpfun_curve`
- **FOMO lag baseline:** 48.0h after T0
- **Notes:** Pump.fun T0 known; peak from Decrypt Binance window — day-granularity.

| Source | Lag vs T0 (min) | Lead vs FOMO (h) | Lead vs peak (h) |
|--------|-----------------|------------------|------------------|
| `pumpfun_curve` | 2.0 | 47.97 | 465.6 |
| `dexscreener_new` | 5.0 | 47.92 | 465.6 |
| `flow_hint` | 10.0 | 47.83 | 465.5 |
| `x_social` | 30.0 | 47.50 | 465.1 |
| `fomo_sidebar` | 2880.0 | 0.00 | 417.6 |

### runner_moodeng_sol (MOODENG)

- **T0:** 2024-09-10T00:00:00+00:00
- **Peak approx:** 2024-09-20T00:00:00+00:00
- **Minute data:** False
- **Honesty:** MINUTE-LEVEL HISTORICAL DATA MISSING — fire times are policy assumptions for source ordering, not measured DexScreener/X archives.
- **Earliest assumed source:** `pumpfun_curve`
- **FOMO lag baseline:** 36.0h after T0
- **Notes:** T0 day-level only; exact mint minute UNKNOWN in open sources.

| Source | Lag vs T0 (min) | Lead vs FOMO (h) | Lead vs peak (h) |
|--------|-----------------|------------------|------------------|
| `pumpfun_curve` | 2.0 | 35.97 | 240.0 |
| `dexscreener_new` | 5.0 | 35.92 | 239.9 |
| `flow_hint` | 10.0 | 35.83 | 239.8 |
| `x_social` | 30.0 | 35.50 | 239.5 |
| `fomo_sidebar` | 2160.0 | 0.00 | 204.0 |

### runner_goat_sol (GOAT)

- **T0:** 2024-10-10T00:00:00+00:00
- **Peak approx:** 2024-10-24T00:00:00+00:00
- **Minute data:** False
- **Honesty:** MINUTE-LEVEL HISTORICAL DATA MISSING — fire times are policy assumptions for source ordering, not measured DexScreener/X archives.
- **Earliest assumed source:** `pumpfun_curve`
- **FOMO lag baseline:** 24.0h after T0
- **Notes:** Truth Terminal amplifier window — minute MC series missing.

| Source | Lag vs T0 (min) | Lead vs FOMO (h) | Lead vs peak (h) |
|--------|-----------------|------------------|------------------|
| `pumpfun_curve` | 2.0 | 23.97 | 336.0 |
| `dexscreener_new` | 5.0 | 23.92 | 335.9 |
| `flow_hint` | 10.0 | 23.83 | 335.8 |
| `x_social` | 30.0 | 23.50 | 335.5 |
| `fomo_sidebar` | 1440.0 | 0.00 | 312.0 |

### fail_shar_sol (SHAR)

- **T0:** 2024-03-01T00:00:00+00:00
- **Peak approx:** 2024-03-01T01:00:00+00:00
- **Minute data:** False
- **Honesty:** MINUTE-LEVEL HISTORICAL DATA MISSING — fire times are policy assumptions for source ordering, not measured DexScreener/X archives.
- **Earliest assumed source:** `pumpfun_curve`
- **FOMO lag baseline:** 0.5h after T0
- **Notes:** Death exemplar D1 sybil — early social heat ≠ quality.

| Source | Lag vs T0 (min) | Lead vs FOMO (h) | Lead vs peak (h) |
|--------|-----------------|------------------|------------------|
| `pumpfun_curve` | 2.0 | 0.47 | 1.0 |
| `dexscreener_new` | 5.0 | 0.42 | 0.9 |
| `flow_hint` | 10.0 | 0.33 | 0.8 |
| `x_social` | 30.0 | 0.00 | 0.5 |
| `fomo_sidebar` | 30.0 | 0.00 | 0.5 |

## Prospective metrics

Every live `ingest_event` writes `discovery_latency_features` onto the bus record and candidate extras: `{source, first_seen_at, mc_at_first_seen, liquidity_usd, enriched_at}`.

Prospective ledger path: `data/candidates/*.json` → `discovery.discovery_latency_features`.

## Method

```bash
make discovery-validate
# or
python -m x_intel.discovery.validate
```

