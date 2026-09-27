"""Live market enrich — Jupiter datapi primary (Solana), pump.fun + DexScreener
fallbacks, DexScreener + GoPlus for EVM.

Solana per-mint MC: pump.fun ``GET /coins/{mint}`` started returning 404 (list
feed still works), so Jupiter's public asset endpoint
(``datapi.jup.ag/v1/assets/search?query=<mint>[,<mint>...]``) is the primary
source. It is keyless, batches up to 100 mints per call, and returns mcap,
usdPrice, liquidity and ``updatedAt`` (last price update) for pump.fun curve
and graduated coins alike.

Never leave enrich as a no-op stub. Prefer pump.fun fields to minimize X spend.
Respects XINTEL_SKIP_DEX for Solana only — EVM has no pump fallback so Dex/GoPlus
stay available. Never invent MC — callers print STALE when mc_usd is unknown.
Importable from pipeline + x_intel.tools.live_quote / scripts/live_quote.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from x_intel.config import (
    EVM_CHAINS,
    dex_chain_id,
    goplus_chain_id,
    is_evm_chain,
)

log = logging.getLogger(__name__)

UA = "x-intel-discovery/0.2"
PUMPFUN_COIN_URL = "https://frontend-api-v3.pump.fun/coins/{mint}"
JUP_ASSETS_URL = "https://datapi.jup.ag/v1/assets/search?query={q}"
JUP_BATCH_MAX = 50
# A quote whose last on-chain price update is older than this is not "live".
PRICE_LIVE_MAX_AGE_SEC = 24 * 3600
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
DEX_TOKEN_CHAIN_URL = "https://api.dexscreener.com/tokens/v1/{chain}/{mint}"
GOPLUS_SECURITY_URL = (
    "https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
    "?contract_addresses={ca}"
)

# Weak social URL must look like a real profile/status link (not empty junk).
_SOCIAL_URL_RE = re.compile(
    r"^https?://(www\.)?(x\.com|twitter\.com|t\.me|telegram\.me)/.+",
    re.I,
)
_CA_RE_EVM = re.compile(r"^0x[a-fA-F0-9]{40}$")

EARLY_MC_SECONDARY_USD = 2_000_000.0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def skip_dex() -> bool:
    return _env_bool("XINTEL_SKIP_DEX", default=False)


def skip_jup() -> bool:
    return _env_bool("XINTEL_SKIP_JUP", default=False)


def skip_enrich() -> bool:
    """Test/offline escape hatch — still stamps enriched_at so callers see a no-fetch."""
    return _env_bool("XINTEL_SKIP_ENRICH", default=False)


def looks_like_evm_ca(ca: str) -> bool:
    return bool(_CA_RE_EVM.match((ca or "").strip()))


def resolve_quote_chain(ca: str, chain: Optional[str] = None) -> str:
    """Normalize chain for quoting. EVM CAs are never treated as solana mints."""
    c = (chain or "").strip().lower()
    aliases = {
        "sol": "solana",
        "eth": "ethereum",
        "ether": "ethereum",
        "bnb": "bsc",
        "binance": "bsc",
    }
    c = aliases.get(c, c)
    if looks_like_evm_ca(ca):
        if c in EVM_CHAINS:
            return c
        # Mis-tagged or missing: empty string → Dex without chain filter (never pump.fun)
        return ""
    return c or "solana"


def _http_get_json(url: str, *, timeout: float = 10.0) -> Any:
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_iso(v: Any) -> Optional[datetime]:
    if not v or not isinstance(v, str):
        return None
    t = v.strip().replace("Z", "+00:00")
    # Jupiter emits nanosecond fractions; fromisoformat accepts at most 6 digits.
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", t)
    if m:
        frac = (m.group(2) or "")[:7]
        t = m.group(1) + frac + (m.group(3) or "")
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_jup_assets(
    mints: list[str],
    *,
    timeout: float = 10.0,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Batch Jupiter datapi asset lookup. Returns ({mint: asset}, errors).

    Only exact id matches are kept (the endpoint is a search API).
    """
    want = [m.strip() for m in mints if m and m.strip() and not looks_like_evm_ca(m)]
    out: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for i in range(0, len(want), JUP_BATCH_MAX):
        chunk = want[i : i + JUP_BATCH_MAX]
        url = JUP_ASSETS_URL.format(q=",".join(chunk))
        try:
            d = _http_get_json(url, timeout=timeout)
        except HTTPError as e:
            errors.append(f"jup:HTTP{e.code}")
            if e.code == 429:
                break
            continue
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
            errors.append(f"jup:{type(e).__name__}")
            continue
        if not isinstance(d, list):
            errors.append("jup:bad_payload")
            continue
        wanted = set(chunk)
        for a in d:
            if isinstance(a, dict) and a.get("id") in wanted:
                out[str(a["id"])] = a
    return out, errors


def quote_from_jup_asset(
    mint: str,
    a: dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Normalize a Jupiter asset row into the quote_mint dict shape."""
    now = now or datetime.now(timezone.utc)
    mc = _f(a.get("mcap"))
    if mc is None:
        mc = _f(a.get("fdv"))
    upd = _parse_iso(a.get("updatedAt"))
    price_age = max(0.0, (now - upd).total_seconds()) if upd else None
    live = mc is not None and price_age is not None and price_age <= PRICE_LIVE_MAX_AGE_SEC
    s24 = a.get("stats24h") or {}
    s1h = a.get("stats1h") or {}
    vol = None
    if isinstance(s24, dict) and (s24.get("buyVolume") is not None or s24.get("sellVolume") is not None):
        vol = (_f(s24.get("buyVolume")) or 0.0) + (_f(s24.get("sellVolume")) or 0.0)
    bc = _f(a.get("bondingCurve"))
    return {
        "ok": mc is not None,
        "mint": mint,
        "ca": mint,
        "chain": "solana",
        "ticker": a.get("symbol"),
        "name": a.get("name"),
        "mc_usd": mc,
        "price_usd": _f(a.get("usdPrice")),
        "liq_usd": _f(a.get("liquidity")),
        "volume_h24": vol,
        "chg_1h": s1h.get("priceChange") if isinstance(s1h, dict) else None,
        "holder_count": _f(a.get("holderCount")),
        "twitter": a.get("twitter") or "",
        "telegram": a.get("telegram") or "",
        "website": a.get("website") or "",
        "complete": (bc >= 100.0) if bc is not None else None,
        "launchpad": a.get("launchpad"),
        "price_updated_at": upd.isoformat() if upd else None,
        "price_age_sec": int(price_age) if price_age is not None else None,
        "price_live": live,
        "source": "jupiter",
        "fetched_at": now.isoformat(),
        "age_sec": 0,
        # MC known but no trade in 24h → report it, flagged stale (not live).
        "stale": not live,
    }


def _fetch_jup_quote(mint: str, *, timeout: float) -> tuple[Optional[dict[str, Any]], list[str]]:
    assets, errs = fetch_jup_assets([mint], timeout=timeout)
    a = assets.get(mint)
    if a is None:
        return None, errs or ["jup:not_found"]
    q = quote_from_jup_asset(mint, a)
    if q.get("mc_usd") is None:
        return None, errs + ["jup:no_mc"]
    return q, errs


def is_real_social_url(url: Optional[str]) -> bool:
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if not u or u.lower() in {"n/a", "none", "null", "-", "na"}:
        return False
    return bool(_SOCIAL_URL_RE.match(u))


def _pick_dex_pair(
    pairs: list[Any], *, chain: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """Prefer highest-liquidity pair; optionally filter to requested chain."""
    want = dex_chain_id(chain) if chain else None
    filtered: list[dict[str, Any]] = []
    for p in pairs:
        if not isinstance(p, dict):
            continue
        if want:
            cid = str(p.get("chainId") or "").lower()
            if cid != want:
                continue
        filtered.append(p)
    use = filtered or [p for p in pairs if isinstance(p, dict)]
    if not use:
        return None
    return sorted(
        use,
        key=lambda p: (p.get("liquidity") or {}).get("usd") or 0,
        reverse=True,
    )[0]


def _quote_from_dex_pair(
    mint: str,
    p: dict[str, Any],
    *,
    now_iso: str,
    pump_partial: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    mc = _f(p.get("marketCap") or p.get("fdv"))
    liq = _f((p.get("liquidity") or {}).get("usd"))
    price = _f(p.get("priceUsd"))
    vol = _f((p.get("volume") or {}).get("h24") or (p.get("volume") or {}).get("h1"))
    socials = (p.get("info") or {}).get("socials") or []
    boosts = p.get("boosts") or {}
    base = p.get("baseToken") or {}
    chain_id = str(p.get("chainId") or "").lower() or None
    out: dict[str, Any] = {
        "ok": mc is not None,  # never invent MC — liq alone is not ok for live MC
        "mint": mint,
        "ca": mint,
        "chain": chain_id,
        "ticker": base.get("symbol") or (pump_partial or {}).get("ticker"),
        "name": base.get("name") or (pump_partial or {}).get("name"),
        "mc_usd": mc if mc is not None else (pump_partial or {}).get("mc_usd"),
        "price_usd": price,
        "liq_usd": liq,
        "volume_h24": vol,
        "chg_1h": (p.get("priceChange") or {}).get("h1"),
        "dex_socials": socials,
        "dex_boosts": boosts,
        "twitter": (pump_partial or {}).get("twitter") or "",
        "telegram": (pump_partial or {}).get("telegram") or "",
        "source": "dexscreener",
        "fetched_at": now_iso,
        "age_sec": 0,
        "stale": mc is None,
    }
    if pump_partial:
        for k in ("twitter", "telegram", "website", "complete", "reply_count", "ath_market_cap"):
            if pump_partial.get(k) not in (None, ""):
                out[k] = pump_partial[k]
        if out.get("mc_usd") is None and pump_partial.get("mc_usd") is not None:
            out["mc_usd"] = pump_partial["mc_usd"]
            out["source"] = "pump.fun+dexscreener"
            out["ok"] = True
            out["stale"] = False
    return out


def _fetch_dex_quote(
    mint: str,
    *,
    chain: Optional[str],
    timeout: float,
) -> tuple[Optional[dict[str, Any]], list[str]]:
    errors: list[str] = []
    # Prefer chain-scoped endpoint when we know the EVM/solana chain id
    dex_id = dex_chain_id(chain) if chain else None
    urls: list[str] = []
    if dex_id and dex_id != "solana":
        urls.append(DEX_TOKEN_CHAIN_URL.format(chain=dex_id, mint=mint))
    urls.append(DEX_TOKEN_URL.format(mint=mint))

    for url in urls:
        try:
            d = _http_get_json(url, timeout=timeout)
            if isinstance(d, list):
                pairs = d
            elif isinstance(d, dict):
                pairs = d.get("pairs") or d.get("pair") or []
                if isinstance(pairs, dict):
                    pairs = [pairs]
            else:
                pairs = []
            if not isinstance(pairs, list):
                pairs = []
            p = _pick_dex_pair(pairs, chain=chain if chain and chain != "solana" else None)
            # If chain filter emptied results but we have pairs, retry without filter
            # only when chain was unknown; when chain specified keep filter strict.
            if p is None and pairs and chain and is_evm_chain(chain):
                errors.append(f"dex:no_pairs_on_{chain}")
                continue
            if p is None and pairs:
                p = _pick_dex_pair(pairs, chain=None)
            if p is None:
                errors.append("dex:no_pairs")
                continue
            return _quote_from_dex_pair(mint, p, now_iso=datetime.now(timezone.utc).isoformat()), errors
        except HTTPError as e:
            errors.append(f"dex:HTTP{e.code}")
            if e.code == 429:
                break  # don't hammer alternate URL
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
            errors.append(f"dex:{type(e).__name__}")
    return None, errors


def _fetch_goplus_meta(
    mint: str,
    *,
    chain: str,
    timeout: float,
) -> tuple[Optional[dict[str, Any]], list[str]]:
    """GoPlus token_security — metadata + is_in_dex only; never invents MC."""
    errors: list[str] = []
    cid = goplus_chain_id(chain)
    if not cid:
        return None, [f"goplus:unsupported_chain:{chain}"]
    url = GOPLUS_SECURITY_URL.format(chain_id=cid, ca=mint.lower())
    try:
        d = _http_get_json(url, timeout=timeout)
    except HTTPError as e:
        return None, [f"goplus:HTTP{e.code}"]
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
        return None, [f"goplus:{type(e).__name__}"]

    if not isinstance(d, dict) or int(d.get("code") or 0) != 1:
        return None, [f"goplus:bad_payload:{d.get('message') if isinstance(d, dict) else type(d).__name__}"]

    result = d.get("result") or {}
    if not isinstance(result, dict) or not result:
        return None, ["goplus:empty_result"]

    # Key may be lowercased CA
    tok = result.get(mint.lower()) or result.get(mint) or next(iter(result.values()), None)
    if not isinstance(tok, dict):
        return None, ["goplus:no_token"]

    is_in_dex = str(tok.get("is_in_dex") or "").strip() in {"1", "true", "True"}
    now_iso = datetime.now(timezone.utc).isoformat()
    out: dict[str, Any] = {
        "ok": False,  # no MC from GoPlus
        "mint": mint,
        "ca": mint,
        "chain": chain,
        "ticker": tok.get("token_symbol"),
        "name": tok.get("token_name"),
        "mc_usd": None,
        "price_usd": None,
        "liq_usd": None,
        "is_in_dex": is_in_dex,
        "holder_count": _f(tok.get("holder_count")),
        "total_supply": tok.get("total_supply"),
        "is_honeypot": str(tok.get("is_honeypot") or "") in {"1", "true"},
        "buy_tax": tok.get("buy_tax"),
        "sell_tax": tok.get("sell_tax"),
        "source": "goplus",
        "fetched_at": now_iso,
        "age_sec": 0,
        "stale": True,
        "goplus": {
            "is_in_dex": is_in_dex,
            "holder_count": tok.get("holder_count"),
            "dex_n": len(tok.get("dex") or []) if isinstance(tok.get("dex"), list) else 0,
        },
    }
    return out, errors


def quote_mint(
    mint: str,
    *,
    chain: Optional[str] = None,
    timeout: float = 10.0,
    allow_dex: Optional[bool] = None,
) -> dict[str, Any]:
    """Live quote for a mint/CA.

    Solana: Jupiter datapi first (unless XINTEL_SKIP_JUP), then pump.fun coin
    detail, then Dex unless XINTEL_SKIP_DEX (Dex also used as an emergency
    fallback when both fail).
    EVM (bsc/base/ethereum/… or 0x CA): DexScreener (chain-filtered), then GoPlus
    metadata fallback. Never invents mc_usd — sets stale=True when unknown.

    Returns dict with ok, mint/ca, chain, ticker, name, mc_usd, price_usd, liq_usd,
    source, fetched_at, age_sec, stale.
    """
    mint = (mint or "").strip()
    errors: list[str] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    if not mint:
        return {
            "ok": False,
            "mint": mint,
            "ca": mint,
            "errors": ["empty_mint"],
            "fetched_at": now_iso,
            "stale": True,
        }

    chain_n = resolve_quote_chain(mint, chain)
    evm = is_evm_chain(chain_n) or looks_like_evm_ca(mint)

    # EVM: Dex always available by default (no pump). Solana: respect SKIP_DEX.
    if allow_dex is None:
        use_dex = True if evm else (not skip_dex())
    else:
        use_dex = allow_dex

    pump_partial: Optional[dict[str, Any]] = None

    # --- Solana: Jupiter datapi primary (pump.fun coin detail 404s) ---
    if not evm and not skip_jup():
        jq, jerrs = _fetch_jup_quote(mint, timeout=timeout)
        errors.extend(jerrs)
        if jq is not None:
            if jerrs:
                jq["errors"] = list(jerrs)
            return jq

    # --- Solana: pump.fun coin detail (second path) ---
    if not evm:
        try:
            d = _http_get_json(PUMPFUN_COIN_URL.format(mint=mint), timeout=timeout)
            if isinstance(d, dict):
                mc = _f(d.get("usd_market_cap") or d.get("market_cap_usd"))
                out: dict[str, Any] = {
                    "ok": mc is not None,
                    "mint": mint,
                    "ca": mint,
                    "chain": "solana",
                    "ticker": d.get("symbol"),
                    "name": d.get("name"),
                    "mc_usd": mc,
                    "price_usd": None,
                    "liq_usd": None,
                    "complete": d.get("complete"),
                    "twitter": d.get("twitter") or "",
                    "telegram": d.get("telegram") or "",
                    "website": d.get("website") or "",
                    "reply_count": d.get("reply_count"),
                    "created_timestamp": d.get("created_timestamp"),
                    "ath_market_cap": _f(d.get("ath_market_cap")),
                    "virtual_sol_reserves": _f(
                        d.get("virtual_sol_reserves") or d.get("real_sol_reserves")
                    ),
                    "source": "pump.fun",
                    "fetched_at": now_iso,
                    "age_sec": 0,
                    "stale": mc is None,
                    "raw_pump": {k: d.get(k) for k in ("symbol", "name", "complete", "nsfw")},
                }
                if mc is not None:
                    return out
                errors.append("pump:no_mc")
                pump_partial = out
            else:
                errors.append("pump:bad_payload")
        except HTTPError as e:
            errors.append(f"pump:HTTP{e.code}")
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
            errors.append(f"pump:{type(e).__name__}")

    # --- DexScreener ---
    # Emergency fallback: pump /coins/{mint} has been returning HTTP 404 while the
    # list endpoint still works. When SKIP_DEX would leave Solana blind after a
    # pump failure, try Dex once so live MC is not permanently STALE.
    if (
        not use_dex
        and not evm
        and any(e.startswith("pump:") for e in errors)
    ):
        log.warning(
            "quote_mint: pump failed (%s); emergency Dex fallback ca=%s…",
            ",".join(errors),
            mint[:12],
        )
        use_dex = True

    if use_dex:
        dex_out, dex_errs = _fetch_dex_quote(mint, chain=chain_n if evm else "solana", timeout=timeout)
        errors.extend(dex_errs)
        if dex_out is not None:
            if pump_partial:
                for k in ("twitter", "telegram", "website", "complete", "reply_count", "ath_market_cap"):
                    if pump_partial.get(k) not in (None, ""):
                        dex_out[k] = pump_partial[k]
                if dex_out.get("mc_usd") is None and pump_partial.get("mc_usd") is not None:
                    dex_out["mc_usd"] = pump_partial["mc_usd"]
                    dex_out["source"] = "pump.fun+dexscreener"
                    dex_out["ok"] = True
                    dex_out["stale"] = False
            if dex_out.get("chain") is None:
                dex_out["chain"] = chain_n
            if dex_out.get("ok") or dex_out.get("mc_usd") is not None:
                return dex_out
            # Keep partial dex identity for GoPlus merge
            dex_partial = dex_out
        else:
            dex_partial = None
    else:
        errors.append("dex:skipped")
        dex_partial = None

    # --- GoPlus fallback (EVM metadata; never invents MC) ---
    if evm:
        gp_chain = chain_n if is_evm_chain(chain_n) else (
            str((dex_partial or {}).get("chain") or "") if dex_partial else ""
        )
        if not is_evm_chain(gp_chain):
            # Without a known EVM chain GoPlus cannot run; leave metadata empty
            gp, gp_errs = None, ["goplus:need_chain"]
        else:
            gp, gp_errs = _fetch_goplus_meta(mint, chain=gp_chain, timeout=timeout)
        errors.extend(gp_errs)
        if gp is not None:
            if dex_partial:
                # Prefer dex identity/price/liq; keep goplus meta + stale MC
                for k in ("ticker", "name", "price_usd", "liq_usd", "volume_h24", "dex_socials"):
                    if dex_partial.get(k) not in (None, "", []):
                        gp[k] = dex_partial[k]
                if dex_partial.get("mc_usd") is not None:
                    gp["mc_usd"] = dex_partial["mc_usd"]
                    gp["ok"] = True
                    gp["stale"] = False
                    gp["source"] = "dexscreener+goplus"
                else:
                    gp["source"] = "goplus"
                    gp["stale"] = True
                    gp["ok"] = False
            gp["errors"] = errors
            return gp

    if pump_partial is not None:
        pump_partial["ok"] = pump_partial.get("mc_usd") is not None
        pump_partial["errors"] = errors
        pump_partial["stale"] = pump_partial.get("mc_usd") is None
        return pump_partial

    if dex_partial is not None:
        dex_partial["errors"] = errors
        dex_partial["stale"] = dex_partial.get("mc_usd") is None
        dex_partial["ok"] = dex_partial.get("mc_usd") is not None
        return dex_partial

    return {
        "ok": False,
        "mint": mint,
        "ca": mint,
        "chain": chain_n,
        "errors": errors,
        "fetched_at": now_iso,
        "stale": True,
    }


def apply_quote_to_record(rec: Any, quote: dict[str, Any]) -> Any:
    """Mutate DiscoveryRecord (or duck-typed) with live quote fields + quality hints."""
    now = datetime.now(timezone.utc)
    hints = dict(getattr(rec, "confidence_hints", None) or {})
    feats = dict(getattr(rec, "discovery_latency_features", None) or {})

    feats["enriched_at"] = now.isoformat()
    feats["source"] = getattr(rec, "first_source", None) or feats.get("source")
    feats["first_seen_at"] = feats.get("first_seen_at") or (
        rec.first_seen_at.isoformat() if getattr(rec, "first_seen_at", None) else None
    )
    if "mc_at_first_seen" not in feats:
        feats["mc_at_first_seen"] = getattr(rec, "mc_usd", None)

    if quote.get("ok") or quote.get("mc_usd") is not None or quote.get("name") or quote.get("ticker"):
        if quote.get("mc_usd") is not None:
            rec.mc_usd = float(quote["mc_usd"])
        if quote.get("liq_usd") is not None:
            rec.liquidity_usd = float(quote["liq_usd"])
        if quote.get("ticker"):
            rec.ticker = quote["ticker"]
        name = quote.get("name")
        symbol = quote.get("ticker") or quote.get("symbol")
        if name:
            hints["name"] = name
            try:
                rec.name = name  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        if symbol:
            hints["symbol"] = symbol
            try:
                rec.symbol = symbol  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        if quote.get("price_usd") is not None:
            hints["price_usd"] = quote["price_usd"]
            try:
                rec.price_usd = float(quote["price_usd"])  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        if quote.get("is_in_dex") is not None:
            hints["is_in_dex"] = quote["is_in_dex"]
        if quote.get("stale"):
            hints["mc_stale"] = True

        # Never overwrite EVM chain with solana from a bad quote
        q_chain = quote.get("chain")
        rec_chain = (getattr(rec, "chain", None) or "").lower()
        if q_chain and is_evm_chain(str(q_chain)) and rec_chain == "solana":
            try:
                rec.chain = str(q_chain)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

        mc_source = quote.get("source") or "unknown"
        hints["mc_source"] = mc_source
        try:
            rec.mc_source = mc_source  # type: ignore[attr-defined]
            rec.enriched_at = now  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        feats["mc_source"] = mc_source
        feats["liquidity_usd"] = getattr(rec, "liquidity_usd", None)
        feats["enrich_ok"] = bool(quote.get("ok") or quote.get("mc_usd") is not None)
        feats["quote_stale"] = bool(quote.get("stale"))

        twitter = quote.get("twitter") or ""
        telegram = quote.get("telegram") or ""
        if is_real_social_url(twitter):
            hints["pump_twitter"] = twitter.strip()
            hints["weak_narrative"] = True
            hints["verified_social"] = True
        if is_real_social_url(telegram):
            hints["pump_telegram"] = telegram.strip()
            hints["weak_narrative"] = True
            hints["verified_social"] = True

        for s in quote.get("dex_socials") or []:
            if not isinstance(s, dict):
                continue
            st = str(s.get("type") or "").lower()
            su = s.get("url") or ""
            if st in {"twitter", "telegram"} and is_real_social_url(su):
                hints.setdefault(f"dex_{st}", su)
                hints["weak_narrative"] = True
                hints["verified_social"] = True
        boosts = quote.get("dex_boosts") or {}
        if isinstance(boosts, dict) and (boosts.get("active") or 0):
            hints["dex_boost_active"] = boosts.get("active")
            hints["boost_only"] = hints.get("boost_only", True) and not hints.get("verified_social")

        vol = _f(quote.get("volume_h24"))
        vsol = _f(quote.get("virtual_sol_reserves"))
        if vol is not None and vol > 0:
            hints["volume_h24"] = vol
            hints["volume_flow_hint"] = True
        if vsol is not None and vsol > 0:
            hints["virtual_sol_reserves"] = vsol
            if vsol >= 5.0:
                hints["volume_flow_hint"] = True

        ath = _f(quote.get("ath_market_cap"))
        mc = getattr(rec, "mc_usd", None)
        if ath is not None and mc is not None and ath > 0 and mc >= 0.5 * ath:
            hints["mc_near_ath"] = True
        if mc is not None and feats.get("mc_at_first_seen") is not None:
            try:
                first_mc = float(feats["mc_at_first_seen"])
                if first_mc > 0 and float(mc) > first_mc * 1.05:
                    hints["mc_rising"] = True
            except (TypeError, ValueError):
                pass
        if mc is not None and hints.get("mc_rising") is not True:
            if float(mc) < EARLY_MC_SECONDARY_USD:
                hints.setdefault("mc_live_early", True)

        if quote.get("complete") is True:
            hints["pump_complete"] = True

        if getattr(rec, "pair_created_at", None) is None and quote.get("created_timestamp") is not None:
            try:
                ts = float(quote["created_timestamp"])
                if ts > 1e12:
                    ts /= 1000.0
                rec.pair_created_at = datetime.fromtimestamp(ts, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                pass
    else:
        feats["enrich_ok"] = False
        feats["enrich_errors"] = quote.get("errors") or ["unknown"]
        hints["mc_source"] = "enrich_failed"
        if quote.get("stale"):
            hints["mc_stale"] = True

    rec.confidence_hints = hints
    rec.discovery_latency_features = feats
    return rec


LIST_MC_MAX_AGE_SEC = 600.0


def _same_cycle_list_quote(rec: Any, ca: str, *, now: datetime) -> Optional[dict[str, Any]]:
    feats = getattr(rec, "discovery_latency_features", None) or {}
    mc = _f(feats.get("last_event_mc_usd"))
    src = str(feats.get("last_event_mc_source") or "").lower()
    at_raw = feats.get("last_event_mc_at")
    if mc is None or "pumpfun" not in src or not at_raw:
        return None
    try:
        at = datetime.fromisoformat(str(at_raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    age = (now - at).total_seconds()
    if age < 0 or age > LIST_MC_MAX_AGE_SEC:
        return None
    return {
        "ok": True,
        "mint": ca,
        "ca": ca,
        "chain": getattr(rec, "chain", None) or "solana",
        "mc_usd": mc,
        "ticker": getattr(rec, "ticker", None) or getattr(rec, "symbol", None),
        "name": getattr(rec, "name", None),
        "source": "pump.fun_list",
        "fetched_at": at.isoformat(),
        "age_sec": int(age),
        "stale": False,
    }


def enrich_record(
    rec: Any,
    *,
    timeout: float = 10.0,
    allow_dex: Optional[bool] = None,
    force: bool = False,
) -> Any:
    """Live-enrich a DiscoveryRecord by mint/CA. Never a silent no-op."""
    now = datetime.now(timezone.utc)
    ca = (getattr(rec, "ca", None) or "").strip()
    chain = getattr(rec, "chain", None)
    feats = dict(getattr(rec, "discovery_latency_features", None) or {})
    feats["enriched_at"] = now.isoformat()
    feats["source"] = getattr(rec, "first_source", None) or feats.get("source")

    if skip_enrich() and not force:
        feats["enrich_ok"] = False
        feats["enrich_errors"] = ["skipped (XINTEL_SKIP_ENRICH)"]
        feats["mc_at_first_seen"] = feats.get("mc_at_first_seen", getattr(rec, "mc_usd", None))
        feats["liquidity_usd"] = getattr(rec, "liquidity_usd", None)
        rec.discovery_latency_features = feats
        return rec

    if not ca:
        feats["enrich_ok"] = False
        feats["enrich_errors"] = ["no_ca"]
        rec.discovery_latency_features = feats
        return rec

    # Guard: EVM CA must not be quoted via pump.fun as a solana mint
    if looks_like_evm_ca(ca) and (chain or "").lower() == "solana":
        log.warning(
            "enrich: EVM ca tagged solana — skipping pump path ca=%s…", ca[:12]
        )
        chain = None  # quote_mint treats 0x as EVM; Dex may reveal real chainId

    quote = quote_mint(ca, chain=chain, timeout=timeout, allow_dex=allow_dex)
    # Same-cycle pump.fun list poll already carried MC; coin detail route may 404.
    # Only trust the list MC stamped by this sighting (bus.upsert) and only while
    # fresh — rec.mc_usd itself may be an older quote from a previous cycle.
    if not (quote.get("ok") or quote.get("mc_usd") is not None):
        list_q = _same_cycle_list_quote(rec, ca, now=now)
        if list_q is not None:
            list_q["errors"] = list(quote.get("errors") or []) + ["used_list_mc_after_coin_fail"]
            quote = list_q
    # If Dex/GoPlus revealed an EVM chain and record was mis-tagged solana, fix identity
    q_chain = (quote or {}).get("chain")
    if (
        looks_like_evm_ca(ca)
        and q_chain
        and is_evm_chain(str(q_chain))
        and (getattr(rec, "chain", None) or "").lower() == "solana"
    ):
        try:
            rec.chain = str(q_chain)
        except Exception:  # noqa: BLE001
            pass
    return apply_quote_to_record(rec, quote)


__all__ = [
    "quote_mint",
    "enrich_record",
    "apply_quote_to_record",
    "is_real_social_url",
    "looks_like_evm_ca",
    "resolve_quote_chain",
    "skip_dex",
    "skip_enrich",
    "skip_jup",
    "fetch_jup_assets",
    "quote_from_jup_asset",
    "PRICE_LIVE_MAX_AGE_SEC",
    "EARLY_MC_SECONDARY_USD",
]
