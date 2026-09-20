# BATCH_01 Signals — Provisional cross-case candidates

**n = 5 pairs (10 tokens). Small-n, provisional, needs out-of-sample validation from Outcome Desk. Not trading advice.**

Counts below are “how many pairs showed this pattern more on the runner side vs the death side,” based only on facts labeled observable before each runner’s explosive phase (or at T0 for same-day deaths).

## Signals that appeared more often on runners

| ID | Candidate signal | Runner hits | Death hits | Caveats | Confidence |
|----|------------------|------------|------------|---------|------------|
| S1 | **Pre-existing cultural object** (years-old meme, breaking news animal, or agent lore that existed *before* the mint) tightly matched to ticker | WIF, POPCAT, GOAT, PNUT, MOODENG (5/5) | JUSTICE inherits news but late; others weaker/generic (SHAR new brand art; GPT2 generic model name; DORAE anime IP without token-specific prior; PESTO real animal but follower-meta) | Survivorship: we selected famous runners that *had* priors. Many prior-backed tokens still die. | med |
| S2 | **First credible mint in the news/meta window** (not a week+ late copy) | PNUT, MOODENG, GOAT (category-defining), WIF/POPCAT as early in their lanes | JUSTICE late; GPT2/PESTO explicit copycats; SHAR paid-KOL launch into mature dog meta; DORAE late cat aspirant | “First” is relative to narrative family, hard to know live amid CA spam. | med |
| S3 | **Amplification from a non-generic attention node** (specific AI agent endorsement, election-week political amplifiers, multi-outlet animal virality) rather than only paid KOL blasts | GOAT (ToT), PNUT (Vance/Musk), MOODENG (global media animal) | SHAR = paid multi-KOL deck + false partnership claims; GPT2 = “supposedly” ChatGPT account | Amplifiers can also exit liquidity. Presence ≠ safety. | med |
| S4 | **Multi-day / multi-week survival before parabolic print** (grind or stair-step) vs minutes/hours lifetime | WIF (~weeks to Dec breakout coverage), POPCAT (months through early 2024), PNUT (days→weeks into listing), MOODENG (days→weeks), GOAT (days at large MC) | SHAR (~1h then rug), GPT2 (hours then >90% dump), DORAE (<6h dump), JUSTICE (~2 weeks to −99%), PESTO (sharp post-ATH crash) | Slow grind can still rug later; speed alone is not quality. | med |
| S5 | **Structural custody claims visible early** (LP burn, revoked mint, full float narrative, or successful CTO removing hostile deployer) | WIF (full float/revoked mint narrative), POPCAT (LP burn + Dec 2023 CTO) | SHAR (sybil-bundled 60%), DORAE (deployer-linked dump), JUSTICE (CA migration) | Many burned-LP tokens still go to zero on attention death. Need on-chain verify live. | low–med |
| S6 | **Absence of leaked paid-KOL “50+ tier1” pitch + denied partnerships at T0** | WIF early coverage emphasizes meme laugh / organic CT | SHAR textbook opposite | Absence of evidence ≠ evidence of absence; KOLs also touch runners. | low–med |

## Signals that appeared more often on deaths

| ID | Candidate signal | Death hits | Runner notes | Caveats | Confidence |
|----|------------------|------------|--------------|---------|------------|
| D1 | **Sybil-bundled launch / funnel concentration** (many wallets buy at launch then re-aggregate) | SHAR (Bubblemaps 60%/100+ wallets) | Not reported for runners in this batch | Requires cluster tooling at T0; invisible on naive holder UI. | med–high *when tooling available* |
| D2 | **Explicit copycat timing after a category leader already printed** | GPT2, PESTO, JUSTICE (challenger), DORAE (aspirational cat after POPCAT path known) | — | Some copycats still run (not in this batch). | med |
| D3 | **Same-day CEX-funded buy → dump path on deployer-linked wallets** | DORAE (Lookonchain) | — | Forensic usually posts *after* dump; live graph watch needed for prediction. | med (forensic), low (live predict) |
| D4 | **Contract migration / “new CA” within days of launch** | JUSTICE → JFP (~10 days) | PNUT main CA continuity in sources | Migrations sometimes honest; here coincided with collapse narrative. | med |
| D5 | **Hours-scale >90% drawdown after first parabolic print** | SHAR, GPT2, DORAE | — | Is outcome label more than predictor; useful as early death confirmation for desk labeling. | high *as death marker*, low *as pre-entry signal* |

## Provisional ranked shortlist for Outcome Desk (OOS)

1. **D1 bundled/funnel concentration at launch** (tooling-gated)
2. **S2 first-mint-in-window vs late challenger**
3. **S3 non-generic amplifier match (lore-native agent / news / political node)**
4. **S4 time-alive before first major expansion**
5. **D2 explicit post-leader copycat cohort membership**
6. **S5 verifiable LP burn / mint revoke / CTO**—weak alone
7. **D4 early CA migration**
8. **S1 cultural prior age/fit**—selection-biased; use as prior not as switch

## Explicit non-signals (avoid hindsight traps)
- “Had Ansem/Joji” without dated pre-explosion posts (often reconstructed later).
- Peak MC / CEX listing as if knowable at T0.
- “Community vibes” or “obvious quality.”
- Post-peak drawdowns of runners (POPCAT/PNUT/etc. later −95%)—irrelevant to launch-time runner vs death classification in this batch’s definition.

## Biggest source gaps (block stronger signal work)
1. Exact T0 MC/liq/holder/concentration tables from DexScreener historical snapshots (most cases UNKNOWN).
2. Contemporaneous X post archives (this batch skipped X MCP)—KOL timing often secondary.
3. WIF-era contemporaneous dog death with hardened CA (JUPDOG presale lacked clean mint in sources).
4. GPT2 exact mint timestamp and independent forensic beyond SolanaFloor.
5. DORAE / PESTO precise peak MC from primary chart pages (secondary variance).
6. POPCAT exact launch day (Dec 12 vs Dec 22 disagreement).

## Method note
Pair design matches narrative family, not calendar twins in all five pairs. Temporal tightness is highest for GOAT–GPT2 and MOODENG–PESTO; lowest for WIF–SHAR. Re-score signals after adding calendar-matched pairs in Batch 02.
