# Signal Hypotheses v0 — Testable Claims from Historical Cases

**Parent study:** `historical_cases_v0.md`  
**Experiment:** `xintel_historian_v0`  
**Rule:** Promote nothing to policy without prospective / OOS evidence.  
**Compiled:** 2026-09-20 PT

---

## FACTS (observational from case file — not yet causal)

These are **documented patterns in the reconstructed set**, not proven predictors.

1. **FACT:** Monster runners in this set include both **stealth fair launches** (PEPE, WIF, POPCAT) and **distribution events** (BONK airdrop, DEGEN engagement airdrop).
2. **FACT:** At least two non-Solana monsters appear: **PEPE (Ethereum)**, **BRETT & DEGEN (Base)**.
3. **FACT:** Failures include (a) **clustered extractive dumps** (SHAR, LIBRA), (b) **narrative expiry** (BODEN), (c) **derivative lore fade** (ANDY vs PEPE).
4. **FACT:** Early **price + volume + KOL cheerleading** appeared in both SHAR (rug) and multiple runners — social heat alone did not separate classes in the first hours.
5. **FACT:** Pre-existing meme IP (Pepe frog, dogwifhat image, Popcat) co-occurred with several runners; invented tickers also run (BONK, DEGEN) when distribution/community fit was strong.
6. **FACT:** Celebrity / head-of-state endorsement (LIBRA) produced the fastest path to multi-$B MC and the fastest collapse in this set.
7. **FACT:** SLERF shows **ops failure + irrevocable burn** can create a temporary attention singularity; it is an outlier relative to presale base rates.
8. **FACT:** Minute-level MC/liq/holder series at T0+15m/+1h/+6h are **mostly missing** from open web reconstructions for WIF, POPCAT, DEGEN, ANDY.

---

## HYPOTHESES (testable — label clearly)

Each hypothesis should be logged prospectively on new candidates (`schemas/candidate_v1.json`) and scored by Outcome Desk.

### H1 — Flow cluster reject
**Claim:** Tokens where ≥X% of first N buyers share funding clusters (CEX→many wallets→consolidate) have drastically higher P(rug within 24h).  
**Motivated by:** SHAR (strong), LIBRA concentration (strong), contrast WIF/PEPE organic tellings.  
**Test:** Flow Desk cluster score at detection; outcome = −90% within 24h or LP drain.  
**Status:** PRIORITY — highest expected value for risk filter.

### H2 — Social-only heat is insufficient
**Claim:** Top-decile X mention velocity in first hour does **not** raise P(≥10x from detection) once conditioned on liq and cluster score.  
**Motivated by:** SHAR KOL loop vs quiet early WIF.  
**Test:** X Scout acceleration features ± Market/Flow covariates.  
**Status:** PRIORITY — prevents false positives.

### H3 — Meme prior helps, does not decide
**Claim:** Tokens with measurable pre-launch web/meme prior (search interest, meme age) have higher P(reach $50M+) than matched invented tickers, **but** prior alone has poor precision (Pepe clones).  
**Motivated by:** PEPE/WIF/POPCAT vs clone graveyard; ANDY as mid outcome.  
**Test:** Narrative Filter “prior_score” vs outcomes; need failure-matched sample.

### H4 — Chain/community vacancy
**Claim:** Launches that fill a vacant cultural niche (post-crisis chain revival, new L2 mascot, social-app channel coin) outperform same-meta launches on saturated venues.  
**Motivated by:** BONK (SOL despair), BRETT (Base mascot), DEGEN (`/degen` channel) vs POINTS/WOWOW/FARTS.  
**Test:** Encode `vacancy_score` at T0; compare Base/SOL/ETH cohorts.

### H5 — Safety checklist is necessary, not sufficient
**Claim:** LP burned + mint/freeze revoked raises survival vs soft rugs but **does not** predict monster multiples (BODEN had strong safety optics and still died on narrative).  
**Motivated by:** BODEN, PEPE, POPCAT vs endless burned-LP micro rugs that still dump via inventory.  
**Test:** Gate as hard filter; do not use as ranking alpha.

### H6 — Celebrity amplify = adverse selection
**Claim:** First-hour entries after Tier-0 political/celebrity CA posts have negative expected value after fees/slippage due to insider inventory.  
**Motivated by:** LIBRA.  
**Test:** Flag `endorsement_tier`; measure entry→+15m/+1h/+6h returns distribution.  
**Status:** Likely hard avoid or microscopic size until Flow clears.

### H7 — Derivative “friend of X” underperforms originals on same chain
**Claim:** On a chain that already has a dominant lore coin, “best friend / spinoff” tickers have lower median peak MC and faster decay.  
**Motivated by:** ANDY (ETH, PEPE exists) vs BRETT (Base, PEPE lore + new chain vacancy).  
**Test:** Interaction term `derivative_lore × chain_has_dominant_meme`.

### H8 — Slow organic > instant parabolic (conditional)
**Claim:** Among tokens that survive 72h, those with smoother holder growth and lower first-hour MC velocity have higher P(eventual ≥100x from T0) than instant $50M+ prints.  
**Motivated by:** WIF/POPCAT slow burn vs SHAR/LIBRA verticals.  
**Caveat:** Survivorship — must include all verticals, not only famous rugs.  
**Test:** Needs full failure sample, not hero cases only.

### H9 — Distribution quality predicts community persistence
**Claim:** Engagement-weighted or broad airdrops (BONK, DEGEN) show higher 7d holder retention than stealth sniper launches with equal day-1 volume.  
**Test:** Holder retention / Gini over 7d.

### H10 — Spectacle outliers must be isolated
**Claim:** “Dev apology / accidental burn / drama” launches have fat-tailed short-horizon returns but poor 30d expectancy; treating them as a positive rule overfits SLERF.  
**Test:** Tag `spectacle_event`; evaluate separately from core policy.

---

## Proposed feature matrix (for next tranche)

| Channel | Feature examples | Hypotheses |
|---------|------------------|------------|
| Flow | first-buyer cluster %, funding age, consolidate events | H1, H6 |
| Market | age, MC, liq, vol, MC/liq, 15m velocity | H2, H5, H8 |
| X Scout | CA mention rate, unique accounts, account age mix, bot score | H2, H6 |
| Narrative | meme prior, derivative flag, vacancy score, politifi flag | H3, H4, H7 |
| Structure | mint/freeze/LP burn, tax, authority | H5 |

---

## Explicit non-claims (do not ship)

- “High volume in first hour ⇒ winner”
- “KOL bought ⇒ safe”
- “Dev mistake ⇒ bullish”
- “Burned LP ⇒ moon”
- Any rule fit on **n≈11** hero/villain cases without matched failures

---

## Data gaps blocking hypothesis tests

1. Historical minute bars (DexScreener/Birdeye) for T0 windows.  
2. Matched failure cohort (same day/meta, died < $2M MC).  
3. Prospective X CA-propagation logs (Scout).  
4. Ground-truth cluster labels beyond a few postmortems (SHAR-quality).  
5. Pump.fun graduate base-rate table (2024–2026).

---

*End of signal_hypotheses_v0.md*
