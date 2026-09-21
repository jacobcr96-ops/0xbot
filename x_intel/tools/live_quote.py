"""Live MC/price quote CLI — never answer from disk alone.

Usage::

    python -m x_intel.tools.live_quote --mint <solana_ca>
    python -m x_intel.tools.live_quote --chain bsc --ca 0x...
    python -m x_intel.tools.live_quote --chain base --ca 0x...
    python -m x_intel.tools.live_quote --ticker CROW --chain bsc

Prints JSON with mc_usd, source, fetched_at, age_sec=0 on success.
Exit 2 if live sources fail or MC unknown (caller must say STALE).
Never invents market cap.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from x_intel.discovery.enrich import looks_like_evm_ca, quote_mint


def _get(url: str, timeout: float = 10.0) -> Any:
    req = Request(url, headers={"User-Agent": "xintel-live-quote/1.0", "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Live token quote (Dex / pump / GoPlus)")
    ap.add_argument("--mint", default=None, help="Solana mint (or any CA)")
    ap.add_argument("--ca", default=None, help="Contract address (alias of --mint; prefer with --chain)")
    ap.add_argument(
        "--chain",
        default=None,
        choices=["solana", "bsc", "base", "ethereum", "arbitrum", "polygon"],
        help="Chain id — required for reliable EVM quotes",
    )
    ap.add_argument("--ticker", default=None, help="best-effort Dex search; prefer --ca/--mint")
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args(argv)

    ca = (args.ca or args.mint or "").strip() or None
    chain = args.chain

    if ca:
        if looks_like_evm_ca(ca) and not chain:
            print(
                "EVM ca requires --chain bsc|base|ethereum (refusing to guess)",
                file=sys.stderr,
            )
            return 1
        # Live quote always attempts Dex for the requested chain (ignore SKIP_DEX)
        out = quote_mint(ca, chain=chain or "solana", timeout=args.timeout, allow_dex=True)
    elif args.ticker:
        try:
            q = quote(args.ticker)
            d = _get(f"https://api.dexscreener.com/latest/dex/search?q={q}", timeout=args.timeout)
            pairs = d.get("pairs") or []
            tick = args.ticker.lstrip("$").upper()
            scored = [
                p
                for p in pairs
                if ((p.get("baseToken") or {}).get("symbol") or "").upper() == tick
            ]
            if chain:
                scored = [
                    p
                    for p in scored
                    if str(p.get("chainId") or "").lower() == chain.lower()
                ] or [
                    p
                    for p in pairs
                    if str(p.get("chainId") or "").lower() == chain.lower()
                    and ((p.get("baseToken") or {}).get("symbol") or "").upper() == tick
                ]
            use = scored or pairs
            if chain:
                use = [p for p in use if str(p.get("chainId") or "").lower() == chain.lower()] or use
            if not use:
                out = {"ok": False, "stale": True, "errors": ["search:empty"]}
                print(json.dumps(out, indent=2))
                return 2
            use = sorted(use, key=lambda p: p.get("marketCap") or p.get("fdv") or 0, reverse=True)
            mint = (use[0].get("baseToken") or {}).get("address")
            pair_chain = str(use[0].get("chainId") or chain or "").lower() or None
            out = (
                quote_mint(mint, chain=pair_chain, timeout=args.timeout, allow_dex=True)
                if mint
                else {"ok": False, "stale": True, "errors": ["search:no_mint"]}
            )
            out["search_ticker"] = args.ticker
            out["candidates_n"] = len(use)
        except Exception as e:  # noqa: BLE001
            out = {"ok": False, "stale": True, "errors": [f"search:{type(e).__name__}:{e}"]}
    else:
        print("need --mint/--ca or --ticker", file=sys.stderr)
        return 1

    # Surface STALE clearly when MC unknown
    if out.get("mc_usd") is None:
        out["stale"] = True
        out.setdefault("ok", False)
        out["mc_status"] = "STALE"
    else:
        out["mc_status"] = "LIVE"
        out["stale"] = False

    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") and out.get("mc_usd") is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
