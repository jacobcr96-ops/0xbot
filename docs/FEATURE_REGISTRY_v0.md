# Feature Registry v0 — BATCH_01 provisional OOS shortlist

**experiment_id:** `xintel_v0`  
**batch:** `BATCH_01`  
**provisional:** true — score on **every** new candidate; promote/demote only with completed OOS outcomes  
**schema:** `x_intel/schemas/feature_scores_v1.json`  
**source:** `reports/BATCH_01_SIGNALS.md` (Historian)  
**rule:** Observable-at-T only. Prefer `null` over guessing. Never invent historical OOS stats.

Outcome Desk aggregates `feature_scores` onto candidates for OOS ranking. Specialist desks fill individual features at detection time; Outcome Desk does not invent values.

## Desk ownership (usual filler)

| Desk | Features |
|------|----------|
| **Flow** | D1, S5 |
| **Narrative** | S2, D2, S1 |
| **Market** | S4, D4 |
| **X Scout** | S3 |
| **Outcome Desk** | Aggregates onto candidates; ranks after fills complete; never trades |

---

## Shortlist (rank order from SIGNALS.md)

### 1. D1 — bundled concentration
- **Direction:** bearish  
- **Type:** boolean (`true` / `false` / `null`)  
- **Desk:** Flow  
- **Definition:** Sybil-bundled launch / funnel concentration — many wallets buy at launch then re-aggregate (cluster tooling).  
- **Rubric:** `true` if tooling shows material funnel/sybil concentration at T; `false` if checked clean; `null` if tooling unavailable.  
- **Notes:** Tooling-gated; invisible on naive holder UI. BATCH_01 death exemplar: SHAR.

### 2. S2 — first-mint-in-window
- **Direction:** bullish  
- **Type:** enum (`first` / `early` / `late_challenger` / `unknown` / `null`)  
- **Desk:** Narrative  
- **Definition:** First credible mint in the news/meta window vs late challenger (not week+ late copy).  
- **Rubric:** Relative to narrative family knowable at T; use `late_challenger` when explicit post-leader timing; `null`/`unknown` if family window unclear amid CA spam.  
- **Notes:** “First” is hard live. med confidence in batch.

### 3. S3 — lore-native amplifier
- **Direction:** bullish  
- **Type:** boolean  
- **Desk:** X Scout  
- **Definition:** Amplification from a non-generic attention node (lore-native agent, news/political node, multi-outlet animal virality) rather than only paid KOL blasts.  
- **Rubric:** `true` if such a node is dated at/before T; `false` if only generic/paid blasts; `null` unknown.  
- **Notes:** Presence ≠ safety; amplifiers can exit liquidity.

### 4. S4 — time-alive before parabola
- **Direction:** context  
- **Type:** ordinal (`minutes` / `hours` / `days` / `weeks_plus` / `null`)  
- **Desk:** Market  
- **Definition:** Elapsed life / pair age at first_seen — proxy for grind vs instant-parabolic path **so far**.  
- **Rubric:** Band from mint/pair age at T only; do not encode future runup.  
- **Notes:** Speed alone is not quality; slow grind can still rug later.

### 5. D2 — post-leader copycat
- **Direction:** bearish  
- **Type:** boolean  
- **Desk:** Narrative  
- **Definition:** Explicit copycat timing / cohort membership after a category leader already printed.  
- **Rubric:** `true` if clearly late challenger in same family; `false` if not a post-leader copycat (e.g. unrelated meta hitch); `null` unclear.  
- **Notes:** Some copycats still run — OOS will tell.

### 6. S5 — LP / mint / CTO (weak alone)
- **Direction:** bullish  
- **Type:** boolean  
- **Desk:** Flow  
- **Definition:** Verifiable structural custody claim at T: LP burn, mint revoke, full-float with on-chain support, or successful CTO.  
- **Rubric:** `true` if ≥1 claim verified on-chain at T; `false` if checked none; `null` unverified. **Weak alone** — never a sole pursue switch.  
- **Notes:** Many burned-LP tokens still go to zero on attention death.

### 7. D4 — early CA migration
- **Direction:** bearish  
- **Type:** boolean  
- **Desk:** Market  
- **Definition:** Contract migration / “new CA” within days of launch.  
- **Rubric:** `true` if migration observed/announced at T; `false` if main CA continuity confirmed; `null` unknown.  
- **Notes:** Migrations sometimes honest; batch death coincident with collapse narrative.

### 8. S1 — cultural prior (biased)
- **Direction:** context  
- **Type:** boolean  
- **Desk:** Narrative  
- **Definition:** Pre-existing cultural object (years-old meme, breaking-news animal, agent lore) before mint, tightly matched to ticker.  
- **Rubric:** `true` prior fit; `false` new/generic brand; `null` unknown. Use as **prior not switch** — selection-biased in Historian batch.  
- **Notes:** Many prior-backed tokens still die.

---

## Explicit non-signals (do not score as features)
From BATCH_01_SIGNALS — avoid hindsight traps:
- Undated KOL name-drops reconstructed after explosion  
- Peak MC / CEX listing as if knowable at T0  
- “Community vibes” / “obvious quality”  
- Post-peak drawdowns of eventual runners  

## Logging
Embed under optional `feature_scores` on `candidate_v1` (see `x_intel/schemas/candidate_v1.json` and `data/candidates/_TEMPLATE.json`). Outcome Desk ranks after horizons fill; until then leaderboard shows n=0 completed outcomes for these features.
