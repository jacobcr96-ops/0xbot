# Case: CIF (failure)
- chain: Solana
- contract_address: G3vWvAaXPHCnncnyAbq6yBRXqfRtEV3h7vExzasZeT6g
- narrative_family: Solana cat meme (internet cat / animal-with-hat cat)
- paired_with: runner_popcat_sol
- launch_window: 2023-12-12 UTC (Cointelegraph: minted Dec 12 2023; CoinGecko lists CatwifHat CIF at this CA)
- outcome_label: lookalike_death
- peak_context: ONLY in Hindsight section below
- batch: 02 (calendar-matched replacement for B01 DORAE pairing)

## Observable timeline
- **Context T- / Nov–early Dec 2023** — Dogwifhat (WIF) already the dominant “animal-with-hat” Solana meme; Cointelegraph later quotes CIF investor NFT_Sloth that Catwifhat was created to follow WIF’s footsteps. Observable meta: hat-animal copycats appearing as WIF ran. Source: https://cointelegraph.com/news/meet-the-solana-meme-coin-that-suffered-two-rug-pulls-but-still-survived
- **T0 / 2023-12-12 (UTC)** — Catwifhat (CIF) minted on Solana; Cointelegraph (citing Solscan): 1B tokens to deployer; 10% to wallet Fm1w…, 10% to AUKt…, 80% (800M) into Raydium LP paired with 1 SOL (initial price 0.00000000125 SOL/CIF). CA indexed by CoinGecko/Jupiter/Coinbase as `G3vWvAaXPHCnncnyAbq6yBRXqfRtEV3h7vExzasZeT6g`. Sources: https://cointelegraph.com/news/meet-the-solana-meme-coin-that-suffered-two-rug-pulls-but-still-survived ; https://www.coingecko.com/en/coins/catwifhat ; https://api.coingecko.com/api/v3/coins/catwifhat
- **T0 / same morning** — Cointelegraph: no marketing on launch day; token still attracted buyers on DEX Screener via name similarity to Dogwifhat.
- **T+hours / 2023-12-12 ~09:41–09:56 UTC** — First rug sequence (Cointelegraph on-chain reconstruction): Fm1w… sold 100M CIF for ~1.24 SOL; AUKt… sold 100M for ~2.62 SOL; deployer then removed LP share, receiving residual SOL only. Alleged exit scam ~$265 total. Price/liquidity effectively destroyed for remaining holders. Source: same Cointelegraph
- **T+days / Dec 12–23 2023** — Community CTO attempt: remaining investors form new promo team; Joji reportedly promotes; a large LP provider rebuilds pool (~55M CIF deposited; ~92% of TVL controlled by that LP). CIF recovers to >$4M MC by Dec 23 per Cointelegraph (CoinGecko ATH day Dec 23 2023). Source: Cointelegraph; https://www.coingecko.com/en/coins/catwifhat
- **T+12d / 2023-12-24 ~19:57 UTC** — Second liquidity rug: wallet CWSy… removes ~102M CIF + 1,630 SOL then ~55M CIF + 869 SOL from Raydium; Cointelegraph cites ~76% price slide into next-day CoinGecko print. Social/admin silence alleged. Source: Cointelegraph
- **Parallel same week (pairing context)** — POPCAT Solana cat meme launches in the same Dec 2023 window (Phemex Dec 12 / DexTools Dec 22 band) and undergoes its own CTO drama without dual LP rugs of this pattern. Source: B01 `runner_popcat_sol.md`
- **Failure confirmation (anti-hindsight label)** — Dual deployer/LP extraction events within ~12 days of mint; never becomes category-defining Solana cat vs POPCAT; residual micro-cap trading years later is narrative death, not temporary underperformance. (Cointelegraph Apr 2024 still cites ~$1.4M MC “survival”—that is post-rug residual, not monster-runner path.)

## T0 detection features
- **Age**: brand-new Dec 12 2023 SPL.
- **MC band at T0**: UNKNOWN exact open USD; LP seeded with 1 SOL + 800M CIF (Cointelegraph)—micro by design.
- **Liquidity**: 80% supply in Raydium LP but **not burned**; deployer retained remove-liquidity power (observable structure risk if checked).
- **Volume**: UNKNOWN at T0.
- **Holders / concentration**: 20% supply split to two wallets that dumped same morning (Cointelegraph)—concentration red flag if wallet graph watched at T0.
- **Social acceleration**: Name-jacking WIF “wif hat” meme; no sourced pre-existing multi-year *cat-with-hat* cultural object comparable to Popcat/Oatmeal.
- **KOL involvement**: Minimal at pure T0 (no marketing); Joji promotion cited *after* first rug during CTO phase—not a T0 organic amplifier for the mint itself.
- **CA propagation**: DEX Screener visibility via ticker similarity to WIF.
- **Red flags at T0**: Unburned LP; 20% non-LP wallets; anonymous deployer; copycat-of-WIF timing into already-running hat meta; thin seed liq (1 SOL).

## Features that looked similar to the pair mate
- Solana “cat” memecoin in December 2023 aiming at dog-dominated mindshare.
- Animal-character / meme branding; pure speculative SPL.
- Early drama + community-takeover language (both CIF and POPCAT had Dec 2023 custody fights).
- Aspiration to ride the post-WIF Solana meme wave.

## Distinguishing features (candidate signals — provisional)
- **Unburned LP + same-day deployer/cohort dump** vs POPCAT’s claimed majority-LP-burn + 6.9% multisig template (Phemex). Confidence: **med** (structure readable at T0 with explorer).
- **20% supply parked in dumpable wallets at mint** vs POPCAT’s disclosed LP-heavy distribution. Confidence: **med**.
- **Copycat-of-WIF naming (“catwifhat”) without years-old standalone meme object** vs POPCAT’s pre-2020 Popcat/Oatmeal prior. Confidence: **med**.
- **Hours-scale first death then second LP rug within 12 days** vs POPCAT’s multi-month survival through Jan 2024 lows. Confidence: **med** (pace)—second rug is post-T0 but still early.

## Sources
- https://cointelegraph.com/news/meet-the-solana-meme-coin-that-suffered-two-rug-pulls-but-still-survived
- https://www.coingecko.com/en/coins/catwifhat
- https://api.coingecko.com/api/v3/coins/catwifhat
- https://www.coinbase.com/price/catwifhat
- https://github.com/jup-ag/token-list/pull/667
- https://dexpaprika.com/solana/token/G3vWvAaXPHCnncnyAbq6yBRXqfRtEV3h7vExzasZeT6g

## Hindsight only (do not use for signals)
- CoinGecko ATH ~$0.006163 on Dec 23 2023 (~$4M+ MC class per Cointelegraph); then Dec 24 LP rug; Apr 2024 residual ~$1.4M MC (Cointelegraph); by 2026 CoinGecko MC ~$14k class with near-zero volume.
- Death mode: serial rug / LP extraction with CTO attempts that never restored category leadership vs POPCAT’s later billion-class path.
- Note: Do not confuse with separate CWIF / other “catwifhat” mints.
