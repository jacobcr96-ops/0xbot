# Case File Schema v1 (Historian / x-intel)

One markdown file per token under `reports/cases/<slug>.md`.

## Filename
- Runners: `runner_<ticker_lower>_<chain>.md` (e.g. `runner_wif_sol.md`)
- Failures: `fail_<shortname>_<chain>.md` (e.g. `fail_shar_sol.md`)

## Required front matter (as markdown bullets under H1)
| Field | Type | Notes |
|-------|------|-------|
| chain | string | e.g. Solana, Ethereum |
| contract_address | string | Full mint/CA or `UNKNOWN` |
| narrative_family | string | Shared family used for pairing |
| paired_with | slug | Exact sibling case slug |
| launch_window | string | Approx UTC date range + source note |
| outcome_label | enum | `monster_runner` \| `lookalike_death` |

## Required sections
1. **Observable timeline** — Chronological bullets. Each bullet: `T+… / approx datetime (UTC)` — fact knowable *then*; cite URL per bullet or cluster. No post-peak knowledge.
2. **T0 detection features** — Age, MC band, liq, volume, holders/concentration, social proxies, KOL, CA propagation, red flags visible near first public appearance. Use `UNKNOWN` when unsourced.
3. **Features that looked similar to the pair mate** — Early narrative/structure overlap.
4. **Distinguishing features (candidate signals — provisional)** — Only pre-explosive-phase diffs; confidence `low`/`med`; no hindsight quality labels.
5. **Sources** — URL list.
6. **Hindsight only (do not use for signals)** — Peak MC/multiple/death mode; clearly separated.

## Hard rules
- Never invent numbers, quotes, CAs, dates, or metrics.
- Label when a fact became publicly observable.
- No trading advice.
