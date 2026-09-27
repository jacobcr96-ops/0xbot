# Multi-chain discovery + live quotes

**Branch:** `feat/x-intel-early-discovery`  
Solana (pump.fun) stays the **primary** early path. BSC / Base / Ethereum ride a
**lower-frequency DexScreener poll** so rugs like CROW still get first-sight + live
MC without pasting a CA.

## Live quote

```bash
# Solana
python -m x_intel.tools.live_quote --mint <SOLANA_MINT>

# EVM (chain required — never invent MC)
python -m x_intel.tools.live_quote --chain bsc --ca 0x35abc2ec5d2b36cfedba65563443201618d6f1bf
python -m x_intel.tools.live_quote --chain base --ca 0x...
python -m x_intel.tools.live_quote --chain ethereum --ca 0x...
```

Sources (in order):

| Chain | Primary | Fallback |
|-------|---------|----------|
| solana | pump.fun | DexScreener (unless `XINTEL_SKIP_DEX`) |
| bsc / base / ethereum | DexScreener token API (chain-filtered) | GoPlus `token_security` for name/symbol/`is_in_dex` |

If `mc_usd` is unknown the tool prints `"mc_status": "STALE"` / `"stale": true` and
exits **2**. **Never invent market cap.**

Shim: `python scripts/live_quote.py` → same CLI.

## Discovery env

| Env | Default | Meaning |
|-----|---------|---------|
| `XINTEL_CHAINS` | `solana,base,ethereum,bsc` | Which chains Dex emits |
| `XINTEL_SKIP_DEX` | false | Skip **Solana** Dex profiles/boosts only (pump.fun primary). **EVM Dex still polls.** |
| `XINTEL_DEX_EVM_INTERVAL_SEC` | `300` | Min seconds between EVM Dex polls |
| `XINTEL_DEX_EVM_COOLDOWN_SEC` | `2700` | Post-429 cooldown for EVM path |

Cooldown health files under `{XINTEL_DATA_DIR}/health/`:

- `dex_solana_cooldown_until` / legacy `dex_cooldown_until`
- `dex_evm_cooldown_until`
- `dex_evm_last_poll`

Optional BSC new-pair probe: newest pairs involving WBNB (capped), same EVM cooldown.

## Candidate identity

Dedupe key is always `(chain, ca)` via `DiscoveryBus.record_key`.

- EVM `0x…` addresses **never** key as `solana::0x…` (mis-tags become `unresolved_evm` until enrich sees Dex `chainId`).
- Solana base58 never emits under EVM chains from DexScreener adapters.
- Watch refresh (`fetch_mc_usd` / `refresh_open_watches`) uses Dex for bsc/base/ethereum even when `XINTEL_SKIP_DEX` (no pump fallback).

## Organic X

CA search is a string query — `0x` addresses already work (`organic_x.ca_search_query`).

## Example: CROW on BSC

```bash
export XINTEL_CHAINS=solana,bsc,base
python -m x_intel.tools.live_quote --chain bsc --ca 0x35abc2ec5d2b36cfedba65563443201618d6f1bf
# → LIVE mc_usd when Dex answers; else GoPlus meta + STALE (no invented MC)
```

Scan picks EVM when `XINTEL_CHAINS` includes `bsc`/`base`/`ethereum` and the EVM
interval/cooldown allow a Dex profiles (+ optional BSC WBNB) poll — independent of
`XINTEL_SKIP_DEX`.
