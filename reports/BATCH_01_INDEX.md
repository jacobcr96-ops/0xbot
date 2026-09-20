# BATCH_01 Index — Anti-hindsight memecoin case files

Research batch for Historian (Jacob x-intel). Generated 2026-09-20 PT. Public web sources only (no X MCP).

## Pairs (5)

| # | Narrative family | Runner | Failure |
|---|------------------|--------|---------|
| 1 | Solana dog / animal-with-hat meme | WIF (`runner_wif_sol`) | SHAR (`fail_shar_sol`) |
| 2 | Solana cat meme (internet cat character) | POPCAT (`runner_popcat_sol`) | DORAE (`fail_dorae_sol`) |
| 3 | AI agent / Truth Terminal–adjacent | GOAT (`runner_goat_sol`) | GPT2 (`fail_gpt2_sol`) |
| 4 | Political / viral animal (Peanut) | PNUT (`runner_pnut_sol`) | JUSTICE (`fail_justice_sol`) |
| 5 | Viral zoo / real-world baby animal | MOODENG (`runner_moodeng_sol`) | PESTO (`fail_pesto_sol`) |

## Case table

| Ticker | Chain | Contract | Narrative family | Outcome | Paired with | File | Source quality |
|--------|-------|----------|------------------|---------|-------------|------|----------------|
| WIF | Solana | `EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm` | Solana dog / animal-with-hat | monster_runner | fail_shar_sol | `cases/runner_wif_sol.md` | high |
| SHAR | Solana | `9jZgvgS2bWtQiYzv48GcWzY4tnkeRSANbTm8Kp1LmSyS` | Solana dog / animal-with-hat | lookalike_death | runner_wif_sol | `cases/fail_shar_sol.md` | high |
| POPCAT | Solana | `7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr` | Solana cat meme | monster_runner | fail_dorae_sol | `cases/runner_popcat_sol.md` | high |
| DORAE | Solana | `F6s6hxSW6yWF4h5YBbW28JHLFEGXKYbEmungaTPtpump` | Solana cat meme | lookalike_death | runner_popcat_sol | `cases/fail_dorae_sol.md` | med |
| GOAT | Solana | `CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump` | AI agent meme | monster_runner | fail_gpt2_sol | `cases/runner_goat_sol.md` | high |
| GPT2 | Solana | `4B3NXEKgsT9hsadpCKNEwSXj6aDqwR7iqe5GzvgKpump` | AI agent meme | lookalike_death | runner_goat_sol | `cases/fail_gpt2_sol.md` | med |
| PNUT | Solana | `2qEHjDLDLbuBgRYvsxhc5D6uDWAivNFZGan56P1tpump` | Political / Peanut animal | monster_runner | fail_justice_sol | `cases/runner_pnut_sol.md` | high |
| JUSTICE | Solana | `DGagMywvLG3DwffZHX4eWWE6svnoJpiod3dSNBDwpump` | Political / Peanut animal | lookalike_death | runner_pnut_sol | `cases/fail_justice_sol.md` | high |
| MOODENG | Solana | `ED5nyyWEzpPPiWimP8vYm7sD7TD3LAt3Q3gRTWHzPJBY` | Viral zoo baby animal | monster_runner | fail_pesto_sol | `cases/runner_moodeng_sol.md` | high |
| PESTO | Solana | `34a8ALsPmbWxp7D3bQ6erERrCLz1ahr6u6o66Udmpump` | Viral zoo baby animal | lookalike_death | runner_moodeng_sol | `cases/fail_pesto_sol.md` | med |

## Source-quality notes
- **high**: Multiple reputable outlets + consistent CA/launch window.
- **med**: Core failure/runner facts solid, but thinner T0 metrics (exact open MC/liq/holders) or secondary aggregators for some timestamps (DORAE launch minute; GPT2 exact mint time; PESTO peak MC variance across outlets).
- **low**: none in this batch.

## Pairing caveats (fairness)
- WIF (Nov 2023) vs SHAR (Oct 2024): same dog-meme family, **not** same launch month—SHAR is a later dog lookalike death in a mature dog meta, not a Thanksgiving-week contemporary. Stronger contemporaneous WIF-era death (e.g. JUPDOG) lacked a reliably sourced CA in this pass.
- POPCAT (Dec 2023) vs DORAE (Jun 2024): both cat-character Solana memes; DORAE launched into an established cat-meta, not the same week as POPCAT.
- GOAT vs GPT2: tightest temporal pairing (Oct 2024 AI wave).
- PNUT vs JUSTICE: same news object; JUSTICE is late owner-affiliated challenger.
- MOODENG vs PESTO: same Sep 2024 zoo-animal wave; PESTO explicitly framed as post-MOODENG copycat.

## Related docs
- Signals: `BATCH_01_SIGNALS.md`
- Schema: `/workspace/x-intel/schemas/case_file_v1.md`
