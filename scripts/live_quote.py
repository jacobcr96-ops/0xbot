#!/usr/bin/env python3
"""Live MC/price quote — never answer Jacob from disk alone.

Usage:
  python scripts/live_quote.py --mint <CA>
  python scripts/live_quote.py --ticker SCAT
Prints JSON with mc_usd, source, fetched_at, age_sec=0 on success.
Exit 2 if all live sources fail (caller must say STALE).

Logic lives in x_intel.discovery.enrich (importable by the pipeline).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

# Allow running from repo without install
sys.path.insert(0, str(__file__).rsplit("/scripts", 1)[0] if "/scripts" in __file__ else ".")

from x_intel.discovery.enrich import quote_mint  # noqa: E402


def _get(url: str, timeout: float = 10.0) -> Any:
    req = Request(url, headers={"User-Agent": "xintel-live-quote/1.0", "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mint", default=None)
    ap.add_argument("--ticker", default=None, help="best-effort search; prefer --mint")
    args = ap.parse_args()
    if args.mint:
        out = quote_mint(args.mint)
    elif args.ticker:
        try:
            d = _get(f"https://api.dexscreener.com/latest/dex/search?q={quote(args.ticker)}")
            pairs = d.get("pairs") or []
            tick = args.ticker.lstrip("$").upper()
            scored = [
                p
                for p in pairs
                if ((p.get("baseToken") or {}).get("symbol") or "").upper() == tick
            ]
            use = scored or pairs
            if not use:
                print(json.dumps({"ok": False, "stale": True, "errors": ["search:empty"]}))
                return 2
            use = sorted(use, key=lambda p: p.get("marketCap") or p.get("fdv") or 0, reverse=True)
            mint = (use[0].get("baseToken") or {}).get("address")
            out = quote_mint(mint) if mint else {"ok": False, "stale": True, "errors": ["search:no_mint"]}
            out["search_ticker"] = args.ticker
            out["candidates_n"] = len(use)
        except Exception as e:  # noqa: BLE001
            out = {"ok": False, "stale": True, "errors": [f"search:{type(e).__name__}:{e}"]}
    else:
        print("need --mint or --ticker", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
