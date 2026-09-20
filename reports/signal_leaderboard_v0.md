# Signal Leaderboard v0

**experiment_id:** `xintel_v0`  
**regenerated_from:** full sample of filled candidates  
**rule:** demote/promote only with OOS evidence — never one-off anecdotes  
**n=0 — awaiting first candidates**

## By source account

| source_account | n | n_filled | mean_ret_1h | mean_ret_24h | median_max_runup | median_max_drawdown | notes |
|----------------|---|----------|-------------|--------------|------------------|---------------------|-------|
| — | 0 | 0 | — | — | — | — | awaiting data |

## By signal channel

| channel | n | n_filled | mean_ret_1h | mean_ret_24h | median_max_runup | median_max_drawdown | notes |
|---------|---|----------|-------------|--------------|------------------|---------------------|-------|
| — | 0 | 0 | — | — | — | — | awaiting data |

## By decision bucket

| decision | n | n_filled | mean_ret_1h | mean_ret_24h | hit_rate_ret_1h_gt_0 | notes |
|----------|---|----------|-------------|----------------------|----------------------|-------|
| pursue | 0 | 0 | — | — | — | awaiting data |
| watch | 0 | 0 | — | — | — | awaiting data |
| reject | 0 | 0 | — | — | — | awaiting data |

## Coverage

| fill_status | count |
|-------------|-------|
| pending | 0 |
| partial | 0 |
| complete | 0 |
| unavailable | 0 |

_Last regenerated: never (stub)._

## BATCH_01 provisional features (OOS)

**Source:** Historian `BATCH_01_SIGNALS.md` → registry `docs/FEATURE_REGISTRY_v0.md`  
**experiment_id:** `xintel_v0` · **provisional:** true · **completed outcomes n=0**  
**rule:** No historical OOS stats invented. Rank only after candidate horizons fill. Score every new candidate at T.

| feature_id | name | direction | desk | n_scored_nonnull | n_completed_outcomes | mean_ret_1h | mean_ret_24h | notes |
|------------|------|-----------|------|------------------|----------------------|-------------|--------------|-------|
| D1 | bundled_concentration | bearish | Flow | — | 0 | — | — | awaiting completed outcomes |
| S2 | first_mint_in_window | bullish | Narrative | — | 0 | — | — | awaiting completed outcomes |
| S3 | lore_native_amplifier | bullish | X Scout | — | 0 | — | — | awaiting completed outcomes |
| S4 | time_alive_before_parabola | context | Market | — | 0 | — | — | awaiting completed outcomes |
| D2 | post_leader_copycat | bearish | Narrative | — | 0 | — | — | awaiting completed outcomes |
| S5 | lp_mint_cto_custody | bullish | Flow | — | 0 | — | — | weak alone; awaiting completed outcomes |
| D4 | early_ca_migration | bearish | Market | — | 0 | — | — | awaiting completed outcomes |
| S1 | cultural_prior | context | Narrative | — | 0 | — | — | biased prior not switch; awaiting completed outcomes |

_Candidate stubs may already carry null/partial `feature_scores`; leaderboard metrics stay empty until `fill_status=complete`._
