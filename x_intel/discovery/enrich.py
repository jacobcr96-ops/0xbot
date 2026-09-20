"""Live market enrich — pump.fun primary, DexScreener fallback.

Never leave enrich as a no-op stub. Prefer pump.fun fields to minimize X spend.
Respects XINTEL_SKIP_DEX. Importable from pipeline + scripts/live_quote.py.
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

log = logging.getLogger(__name__)

UA = "x-intel-discovery/0.2"
PUMPFUN_COIN_URL = "https://frontend-api-v3.pump.fun/coins/{mint}"
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

# Weak social URL must look like a real profile/status link (not empty junk).
_SOCIAL_URL_RE = re.compile(
    r"^https?://(www\.)?(x\.com|twitter\.com|t\.me|telegram\.me)/.+",
    re.I,
)

EARLY_MC_SECONDARY_USD = 2_000_000.0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def skip_dex() -> bool:
    return _env_bool("XINTEL_SKIP_DEX", default=False)


def skip_enrich() -> bool:
    """Test/offline escape hatch — still stamps enriched_at so callers see a no-fetch."""
    return _env_bool("XINTEL_SKIP_ENRICH", default=False)


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


def is_real_social_url(url: Optional[str]) -> bool:
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if not u or u.lower() in {"n/a", "none", "null", "-", "na"}:
        return False
    return bool(_SOCIAL_URL_RE.match(u))


def quote_mint(
    mint: str,
    *,
    timeout: float = 10.0,
    allow_dex: Optional[bool] = None,
) -> dict[str, Any]:
    """Live quote for a mint/CA. pump.fun first, then Dex unless skipped.

    Returns dict with ok, mint, ticker, name, mc_usd, price_usd, liq_usd,
    source, fetched_at, age_sec, plus pump social/volume fields when available.
    """
    mint = (mint or "").strip()
    errors: list[str] = []
    use_dex = (not skip_dex()) if allow_dex is None else allow_dex
    now_iso = datetime.now(timezone.utc).isoformat()

    if not mint:
        return {"ok": False, "mint": mint, "errors": ["empty_mint"], "fetched_at": now_iso, "stale": True}

    # 1) pump.fun
    try:
        d = _http_get_json(PUMPFUN_COIN_URL.format(mint=mint), timeout=timeout)
        if isinstance(d, dict):
            # market_cap on pump is often SOL-denominated; prefer usd_* only for mc_usd
            mc = _f(d.get("usd_market_cap") or d.get("market_cap_usd"))
            out: dict[str, Any] = {
                "ok": mc is not None,
                "mint": mint,
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
                "virtual_sol_reserves": _f(d.get("virtual_sol_reserves") or d.get("real_sol_reserves")),
                "source": "pump.fun",
                "fetched_at": now_iso,
                "age_sec": 0,
                "raw_pump": {k: d.get(k) for k in ("symbol", "name", "complete", "nsfw")},
            }
            if mc is not None:
                return out
            errors.append("pump:no_mc")
            # Keep partial pump identity even if MC missing — fall through to dex
            pump_partial = out
        else:
            errors.append("pump:bad_payload")
            pump_partial = None
    except HTTPError as e:
        errors.append(f"pump:HTTP{e.code}")
        pump_partial = None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
        errors.append(f"pump:{type(e).__name__}")
        pump_partial = None

    # 2) dexscreener token
    if use_dex:
        try:
            d = _http_get_json(DEX_TOKEN_URL.format(mint=mint), timeout=timeout)
            pairs = d.get("pairs") or [] if isinstance(d, dict) else []
            pairs = sorted(
                pairs,
                key=lambda p: (p.get("liquidity") or {}).get("usd") or 0,
                reverse=True,
            )
            if pairs:
                p = pairs[0]
                mc = _f(p.get("marketCap") or p.get("fdv"))
                liq = _f((p.get("liquidity") or {}).get("usd"))
                price = _f(p.get("priceUsd"))
                vol = _f((p.get("volume") or {}).get("h24") or (p.get("volume") or {}).get("h1"))
                socials = (p.get("info") or {}).get("socials") or []
                boosts = p.get("boosts") or {}
                base = p.get("baseToken") or {}
                out = {
                    "ok": mc is not None or liq is not None,
                    "mint": mint,
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
                }
                # Prefer pump socials if we had them
                if pump_partial:
                    for k in ("twitter", "telegram", "website", "complete", "reply_count", "ath_market_cap"):
                        if pump_partial.get(k) not in (None, ""):
                            out[k] = pump_partial[k]
                    if out.get("mc_usd") is None and pump_partial.get("mc_usd") is not None:
                        out["mc_usd"] = pump_partial["mc_usd"]
                        out["source"] = "pump.fun+dexscreener"
                if out.get("ok"):
                    return out
                errors.append("dex:no_mc_liq")
            else:
                errors.append("dex:no_pairs")
        except HTTPError as e:
            errors.append(f"dex:HTTP{e.code}")
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
            errors.append(f"dex:{type(e).__name__}")
    else:
        errors.append("dex:skipped")

    # Return pump partial identity if we had it (even without MC)
    if pump_partial is not None:
        pump_partial["ok"] = pump_partial.get("mc_usd") is not None
        pump_partial["errors"] = errors
        return pump_partial

    return {
        "ok": False,
        "mint": mint,
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

    if quote.get("ok") or quote.get("mc_usd") is not None or quote.get("name"):
        if quote.get("mc_usd") is not None:
            rec.mc_usd = float(quote["mc_usd"])
        if quote.get("liq_usd") is not None:
            rec.liquidity_usd = float(quote["liq_usd"])
        if quote.get("ticker"):
            rec.ticker = quote["ticker"]
        # Identity from chain — keep name/symbol so Shielded Cat ≠ random SCAT clone
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

        # --- Cheap secondary quality when organic X is empty ---
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

        # Dex socials / boosts (weak)
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
            # boost alone is paid — do NOT mark organic
            hints["boost_only"] = hints.get("boost_only", True) and not hints.get("verified_social")

        # Volume → weak flow_hint (not enough alone for BUY)
        vol = _f(quote.get("volume_h24"))
        vsol = _f(quote.get("virtual_sol_reserves"))
        if vol is not None and vol > 0:
            hints["volume_h24"] = vol
            hints["volume_flow_hint"] = True
        if vsol is not None and vsol > 0:
            hints["virtual_sol_reserves"] = vsol
            # Non-trivial curve activity as weak flow
            if vsol >= 5.0:  # ~$500+ at ~$100 SOL — soft threshold
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
        # Live enrich with MC present counts as rising/fresh for first sight
        if mc is not None and hints.get("mc_rising") is not True:
            # First enrich: treat positive live MC under early band as fresh print
            if float(mc) < EARLY_MC_SECONDARY_USD:
                hints.setdefault("mc_live_early", True)

        if quote.get("complete") is True:
            hints["pump_complete"] = True

        # Prefer chain create time when record lacks pair_created_at
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

    rec.confidence_hints = hints
    rec.discovery_latency_features = feats
    return rec


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

    quote = quote_mint(ca, timeout=timeout, allow_dex=allow_dex)
    return apply_quote_to_record(rec, quote)


__all__ = [
    "quote_mint",
    "enrich_record",
    "apply_quote_to_record",
    "is_real_social_url",
    "skip_dex",
    "skip_enrich",
    "EARLY_MC_SECONDARY_USD",
]
