# Case: SHAR (failure)
- chain: Solana
- contract_address: 9jZgvgS2bWtQiYzv48GcWzY4tnkeRSANbTm8Kp1LmSyS
- narrative_family: Solana dog / animal-with-hat meme
- paired_with: runner_wif_sol
- launch_window: 2024-10-23 / 2024-10-24 UTC (Decrypt; OAK incident note)
- outcome_label: lookalike_death
- peak_context: ONLY in Hindsight section below

## Observable timeline
- **T0 / 2024-10-23 (UTC launch window)** — Sharpei (SHAR) dog-themed Solana memecoin launches; branded with cartoon Shar-Pei art; heavy influencer/KOL promotion on X. Source: https://decrypt.co/288160/solana-meme-coin-sharpei-epic-rug-pull
- **T+minutes–<1 hour / 2024-10-23** — Market cap reportedly runs to ~$54M under influencer-attracted buying (Decrypt; OAK). Pitch deck leak circulates claiming “50+ tier 1 KOLs,” BONK partnership at $100M MC, named influencers. Source: https://decrypt.co/288160/solana-meme-coin-sharpei-epic-rug-pull ; https://onchainattack.org/document/examples/2024-10-sharpei-solana-funnel-collapse-rug/
- **T+same day / 2024-10-23** — Named parties push back: BONK contributors Kadense and Nom deny partnership; Joji denies involvement with Telegram screenshot; Yelotree tells Decrypt he was paid for promotion months earlier without disclosure in his tweet. Observable FUD/denial cascade on CT. Source: https://decrypt.co/288160/solana-meme-coin-sharpei-epic-rug-pull
- **T+same day / after denials** — Price retraces from ~$54M MC to ~$35.5M MC (Decrypt). Then coordinated sell: Bubblemaps reports 60% of supply bought at launch across 100+ addresses, funneled to one wallet, sold ~$3.4M in one clip; ~96.3% crash to ~$1.3M MC in ~two seconds. Bubblemaps confirms to Decrypt this meets rug-pull definition. Sources: https://decrypt.co/288160/solana-meme-coin-sharpei-epic-rug-pull ; https://onchainattack.org/document/examples/2024-10-sharpei-solana-funnel-collapse-rug/
- **T+hours / 2024-10-23–24** — Official SHAR X account claims project “no longer had the funds to continue operations” due to FUD; promises proof of influencer comms (Decrypt: still not delivered next day). Multiple outlets republish forensic accounts. Source: https://decrypt.co/288160/solana-meme-coin-sharpei-epic-rug-pull
- **Failure confirmation** — Not a brief dip: seconds-scale ~96% collapse with operator-cohort extraction ~$3.4M; project ops publicly abandoned per team post. Source: same Decrypt/OAK.

## T0 detection features
- **Age**: minutes-old at first public spike.
- **MC band**: UNKNOWN at first block; within first hour Decrypt/OAK cite peak >$54M (already parabolic).
- **Liquidity**: UNKNOWN exact LP USD at T0; AMM depth later insufficient for $3.4M clip (observable only at dump).
- **Volume**: UNKNOWN precise T0 figure.
- **Holders / concentration**: **Critical** — Bubblemaps: 60% supply acquired at launch across 100+ addresses (sybil-bundle fingerprint). Detectable near T0 with cluster tooling; naive top-10 holder UI may look “distributed.”
- **Social acceleration**: Extreme KOL blast at launch; leaked pitch deck naming many influencers.
- **KOL involvement**: Paid/undisclosed promotions alleged (Yelotree admission to Decrypt); several named KOLs deny.
- **CA propagation**: Influencer posts + deck; official account @SolanaKol cited by Decrypt.
- **Red flags at T0**: Bundled launch concentration; paid KOL funnel; partnership claims later denied; pitch-deck overclaim pattern.

## Features that looked similar to the pair mate
- Solana dog meme ticker/branding.
- CT-native promotion and dog-image humor as the product.
- Rapid Solana DEX price discovery with speculative retail flow.
- Narrative that “this dog meme can be the next big Solana dog” in a post-BONK/WIF dog meta.

## Distinguishing features (candidate signals — provisional)
- **Sybil-bundled 60% launch acquisition** (Bubblemaps) vs WIF’s reported full-float / no team allocation narrative. Confidence: **med–high** if cluster tools used at T0; **low** if only reading ticker narrative.
- **Paid multi-KOL launch package + leaked deck** vs WIF’s organic meme-image path emphasized in early Decrypt coverage. Confidence: **med**.
- **Minutes-to-$54M then denial cascade** vs WIF’s multi-week grind before Dec 2023 breakout coverage. Confidence: **med**.
- **False partnership claims (BONK) visible same day** when named parties deny. Confidence: **med** (denials are observable; falsity confirmed same day).

## Sources
- https://decrypt.co/288160/solana-meme-coin-sharpei-epic-rug-pull
- https://onchainattack.org/document/examples/2024-10-sharpei-solana-funnel-collapse-rug/
- https://coinboom.net/coin/sharpei (CA corroboration; treat as secondary)
- https://www.cryptotimes.io/2024/10/24/solana-memecoin-shar-plummets-95-post-launch-classic-rugpull/

## Hindsight only (do not use for signals)
- Peak MC: ~$54M within first hour (Decrypt/OAK).
- Death mode: funnel-collapse rug — 100+ wallets → one wallet → single-clip ~$3.4M sell; ~96% crash in seconds; team cites inability to continue ops.
- Multiple from launch: UNKNOWN (launch MC not given as a clean figure in Decrypt).
