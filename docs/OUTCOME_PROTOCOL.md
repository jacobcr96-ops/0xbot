# Outcome Protocol — Prospective Measurement Store

**Measurement only. NEVER trade.** Outcome Desk fills returns and ranks signals; it does not place orders or size positions.

Default `experiment_id`: `xintel_v0`. Path root: ``.

## Ownership

| Actor | Writes | Owns |
|-------|--------|------|
| **Grok Bot / specialist desks** | Candidate records at detection time (incl. rejects) | Logging every detection under `data/candidates/` |
| **Outcome Desk** | Outcome overlays, fill status, leaderboard, reviews | Filling horizons, ranking sources/signals, post-trade (measurement) review |

Desks may write either:
- one JSON file per candidate: `data/candidates/{candidate_id}.json` (full `candidate_v1`), **and**
- one append-only index line: `data/candidates/_index.jsonl`

Outcome Desk never invents candidates; it only fills and ranks what was logged.

## Horizons

Returns are measured from `first_seen_at` (price at first sight → price at horizon):

| Field | Horizon |
|-------|---------|
| `ret_5m` | +5 minutes |
| `ret_15m` | +15 minutes |
| `ret_1h` | +1 hour |
| `ret_6h` | +6 hours |
| `ret_24h` | +24 hours |
| `max_runup` | Peak return over the window through 24h (or until LP rug if detected) |
| `max_drawdown` | Worst drawdown from first-sight price over the same window |

If an LP rug / liquidity removal is detected before 24h, freeze the remaining unfilled horizons as `null`, set `fill_status` appropriately, and record the rug timestamp in notes. `max_runup` / `max_drawdown` still cover the observed window up to that event.

Return convention: fractional (e.g. `0.25` = +25%, `-0.40` = −40%). Same units as nested `outcomes` on `candidate_v1`.

## File conventions

```
data/candidates/{candidate_id}.json   # full candidate_v1 (every detection, incl rejects)
data/candidates/_index.jsonl          # append-only; one JSON object per new candidate (fast scan)
data/candidates/_TEMPLATE.json        # TEMPLATE only — not a real candidate
data/outcomes/{candidate_id}.json     # outcome_fill_v1 overlay + fill timestamps
data/outcomes/_pending.jsonl          # optional queue of candidate_ids awaiting fills
reports/signal_leaderboard_v0.md      # regenerated from full sample
reports/reviews/                      # missed-runner & post-trade review writeups
```

### Candidate file
Must validate against `x_intel/schemas/candidate_v1.json`. Nested `outcomes` may be omitted or null until Outcome Desk fills; prefer writing fills to `data/outcomes/{id}.json` and optionally mirroring into the candidate’s `outcomes` object when complete. Optional `feature_scores` (BATCH_01+ provisional) may be omitted on legacy rows; new candidates SHOULD include stubs with nulls for unknown features (see Feature scoring below).

### Index line (`_index.jsonl`)
Minimal scan row, one object per line, appended only (never rewrite history). Suggested fields:

```json
{"candidate_id":"<uuid>","first_seen_at":"<iso>","contract_address":"...","chain":"...","decision":"pursue|watch|reject","experiment_id":"xintel_v0","logged_at":"<iso>"}
```

### Outcome overlay
Standalone fill record per `x_intel/schemas/outcome_fill_v1.json`. Outcome Desk owns create/update. `fill_status`: `pending` | `partial` | `complete` | `unavailable`.

## Source / signal ranking

- Regenerate `reports/signal_leaderboard_v0.md` from the **full sample** of logged candidates with fills (by `experiment_id`, source account, signal channel, decision bucket).
- **Demote or promote only with OOS evidence** across the cohort — never one-off rules from a single winner, rug, or miss.
- Leaderboard columns should include n, hit rates at each horizon, mean/median returns, max-runup / max-drawdown summaries, and coverage (fill completeness).
- Until candidates exist: keep the stub header and `n=0 — awaiting first candidates`.

## Missed-runner and post-trade review

Path: `reports/reviews/`.

Use for:
- **Missed runners** — assets that later ran hard but were never logged or were rejected; reconstruct what was knowable at `first_seen`-equivalent time (Historian-friendly).
- **Post-trade / post-decision reviews** — measurement-only writeups of pursue/watch/reject vs realized path (no execution advice).

Naming suggestion: `reports/reviews/{YYYYMMDD}_{short_slug}.md` or `{candidate_id}.md`.

## `experiment_id` versioning

1. Default for this stand-up: **`xintel_v0`**.
2. Every candidate and every fill must carry the `experiment_id` active when the candidate was logged.
3. Bump version (`xintel_v1`, …) when detection rules, feature set, or decision policy change enough that mixing samples would confound ranking.
4. Leaderboards and expectancy stats are **partitioned by `experiment_id`**; do not pool across versions without an explicit cross-version study.
5. Never silently rewrite past candidates to a new experiment id — leave historical rows under the id they were logged with.
6. Policy demotions/promotions require OOS evidence **within** the relevant experiment cohort (or a declared holdout), not anecdotes.

## Helper

`scripts/outcome_ledger.py` — filesystem ledger ops only (list pending, status). Price pulls from DexScreener are stubbed with TODOs; no live trading.

## Feature scoring (BATCH_01 provisional)

Prospective OOS features from Historian BATCH_01. **Score on every new candidate** under `experiment_id=xintel_v0`. All features are `provisional: true` until completed outcomes support promotion/demotion.

- **Registry:** `docs/FEATURE_REGISTRY_v0.md`
- **Schema:** `x_intel/schemas/feature_scores_v1.json`
- **Embed:** optional `feature_scores` object on `candidate_v1` (do not break required fields). Prefer embedding over sidecars.
- **Rule:** Observable-at-T only. Use `null` when unknown — never guess, never hindsight. Never invent historical OOS stats.
- **Who fills:** Flow (D1, S5), Narrative (S2, D2, S1), Market (S4, D4), X Scout (S3). Outcome Desk aggregates onto candidates for OOS ranking after horizons fill.
- **Shortlist IDs:** D1, S2, S3, S4, D2, S5, D4, S1 (see registry for rubrics).
- **Helper:** `scripts/outcome_ledger.py status` reports how many candidates have `feature_scores` present vs missing (absence is OK for legacy rows).

When detection rules / feature set change enough to confound ranking, bump `experiment_id` (see versioning above).
