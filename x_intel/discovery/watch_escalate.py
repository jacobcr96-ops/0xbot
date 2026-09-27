"""WATCH → dip-BUY escalation + anti-stale MC refresh.

After each discovery cycle refreshes open WATCH market caps (Jupiter datapi
batch primary; pump.fun coin detail second; DexScreener optional unless
XINTEL_SKIP_DEX, and as an emergency fallback), tracks min_mc_usd_seen, and may
emit one real BUY via emit_pursue_buy with risk_flags including watch_dip_buy.
Never emits calibration_shadow / shadow intents.

Hard gates on every WATCH dip/reclaim BUY (applied even with --no-emit so the
escalate summary is truthful):
  * live-MC floor ``XINTEL_WATCH_BUY_MIN_MC`` (default $25k) — dead coins never BUY
  * 2h per-mint dedupe vs any prior watch BUY (incl. cancelled ones)
  * real organic X required (same bar as ``XINTEL_ORGANIC_X_REQUIRED_FOR_PING``);
    watch_dip_quality_path / "S1/S3 unset — soft pass" never arm a BUY without it.

Stale WATCHes are closed (never deleted): a WATCH with no live price from any
source whose last successful MC refresh is >24h old — or whose only price is a
print older than 24h (no trades) — gets decision=reject,
reject_reason=expired_stale, watch_status=expired_stale. The open-watch set,
watch_freshness.json and heartbeat only count WATCHes that remain open.

Dead-coin early close (before the 24h expiry): a WATCH is also closed as
expired_stale when the live source repeatedly reports a dead coin —
LAST_PRINT (Jupiter print >24h old, no trades) for WATCH_DEAD_LAST_PRINT_STREAK
consecutive refreshes, or no MC at all for WATCH_DEAD_NULL_STREAK consecutive
refreshes — AND a corroborating dead signal holds: holders <= 1, liquidity ~0,
or MC below the ``XINTEL_WATCH_BUY_MIN_MC`` floor. A single failed fetch never
counts, and coins with real MC/liquidity/holders are never closed by this path.
A Jupiter batch outage (e.g. HTTP 429) never counts for a buyable WATCH (last
known MC >= floor); for a sub-floor WATCH, "no MC from every source" (Jupiter
unanswered + pump.fun empty + Dex *answered* with no usable pair) counts as
``no_mc_all_sources`` and closes after WATCH_DEAD_NULL_STREAK consecutive
refreshes. If Dex also errors (429/network) it is an outage and never counts.

Staleness scope: only buyable WATCHes (last known MC >= floor, or MC unknown)
count toward ``watch_stale_count`` / ``stale_buyable`` / ``stale_watch_alert``
and per-row ``stale``. Sub-floor WATCHes are still refreshed (buyable ones are
quoted first) but reported separately as ``stale_subfloor`` and never alert.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen

from x_intel.config import (
    BUY_ADD_TTL_SECONDS,
    MIN_BUY_PERCENT_EQUITY,
    data_dir,
    do_not_execute_until_armed,
    is_armed,
    watch_buy_dedupe_sec,
    watch_buy_min_mc_usd,
    watch_buy_requires_organic_x,
)
from x_intel.discovery import enrich as _enrich
from x_intel.discovery.enrich import (
    PRICE_LIVE_MAX_AGE_SEC,
    fetch_jup_assets,
    quote_from_jup_asset,
    skip_jup,
)
from x_intel.discovery.gates import has_real_organic_x
from x_intel.discovery.organic_x import annotate_organic_x_hints
from x_intel.emit.pursue_buy import GateReject, emit_pursue_buy
from x_intel.io_atomic import atomic_read_json, atomic_write_json
from x_intel.ledger.store import CandidateLedger, RepoPaths
from x_intel.schemas.models import (
    CandidateDecision,
    CandidateV1,
    DecisionAction,
    DecisionV1,
    is_decision_stale,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dip-buy thresholds
# ---------------------------------------------------------------------------
DIP_MC_USD_MAX = 2_000_000.0
DIP_VS_FIRST_SIGHT_MAX = 0.85  # mc_now <= 0.85 * first_sight
RECLAIM_VS_FIRST_SIGHT_MAX = 1.10
RECLAIM_MC_USD_CAP = 3_500_000.0
NO_CHASE_MC_USD = 4_000_000.0
WATCH_STALE_SEC = 600  # 10 minutes
WATCH_EXPIRE_SEC = 24 * 3600  # close WATCH after 24h without a live price
WATCH_EXPIRED_STATUS = "expired_stale"
# Dead-coin early close (see module docstring)
WATCH_DEAD_LAST_PRINT_STREAK = 2  # runner refreshes twice per cycle
WATCH_DEAD_NULL_STREAK = 3
WATCH_DEAD_MAX_HOLDERS = 1
WATCH_DEAD_MAX_LIQ_USD = 100.0
DIP_BUY_PERCENT_EQUITY = max(0.75, float(MIN_BUY_PERCENT_EQUITY))
DIP_BUY_TTL_SECONDS = BUY_ADD_TTL_SECONDS  # 1200
DIP_BUY_CONFIDENCE = 0.55

PUMPFUN_COIN_URL = "https://frontend-api-v3.pump.fun/coins/{mint}"
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{ca}"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def skip_dex() -> bool:
    return _env_bool("XINTEL_SKIP_DEX", default=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _cand_extra(cand: CandidateV1) -> dict[str, Any]:
    return dict(getattr(cand, "__pydantic_extra__", None) or {})


def _outcomes_dict(cand: CandidateV1) -> dict[str, Any]:
    if cand.outcomes is None:
        return {}
    if hasattr(cand.outcomes, "model_dump"):
        return cand.outcomes.model_dump(mode="json")
    if isinstance(cand.outcomes, dict):
        return dict(cand.outcomes)
    return {}


def get_mc_now(cand: CandidateV1) -> Optional[float]:
    """Prefer live mc_usd_now extras/outcomes, else first-sight."""
    extra = _cand_extra(cand)
    for src in (
        extra.get("mc_usd_now"),
        _outcomes_dict(cand).get("mc_usd_now"),
    ):
        if src is not None:
            try:
                return float(src)
            except (TypeError, ValueError):
                pass
    if cand.mc_usd_at_first_sight is not None:
        try:
            return float(cand.mc_usd_at_first_sight)
        except (TypeError, ValueError):
            return None
    return None


def get_refreshed_mc_now(cand: CandidateV1) -> Optional[float]:
    """MC from refresh only — never fall back to first-sight (avoids fixture false buys)."""
    extra = _cand_extra(cand)
    for src in (
        extra.get("mc_usd_now"),
        _outcomes_dict(cand).get("mc_usd_now"),
    ):
        if src is not None:
            try:
                return float(src)
            except (TypeError, ValueError):
                pass
    return None


def get_min_mc_seen(cand: CandidateV1) -> Optional[float]:
    extra = _cand_extra(cand)
    for src in (
        extra.get("min_mc_usd_seen"),
        _outcomes_dict(cand).get("min_mc_usd_seen"),
    ):
        if src is not None:
            try:
                return float(src)
            except (TypeError, ValueError):
                pass
    return get_mc_now(cand)


def get_refreshed_at(cand: CandidateV1) -> Optional[datetime]:
    extra = _cand_extra(cand)
    return _parse_dt(extra.get("refreshed_at")) or _parse_dt(
        _outcomes_dict(cand).get("refreshed_at")
    )


def _is_hard_rugged(cand: CandidateV1) -> bool:
    extra = _cand_extra(cand)
    disc = extra.get("discovery") if isinstance(extra.get("discovery"), dict) else {}
    hints = {}
    if isinstance(disc, dict):
        hints.update(disc.get("confidence_hints") or {})
    for blob in (extra, hints, _outcomes_dict(cand)):
        if not isinstance(blob, dict):
            continue
        if blob.get("hard_rugged") is True or blob.get("rugged") is True:
            return True
        if blob.get("lp_rug_detected") is True:
            return True
    # Near-zero liq + collapsed MC after refresh
    out = _outcomes_dict(cand)
    liq = out.get("liq_usd_top") or extra.get("liq_usd_top")
    mc = get_mc_now(cand)
    try:
        if liq is not None and float(liq) < 50 and mc is not None and float(mc) < 5_000:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _is_name_parasite(cand: CandidateV1) -> bool:
    rr = (cand.reject_reason or "").lower()
    if "name_parasite" in rr or "parasite" in rr:
        return True
    extra = _cand_extra(cand)
    disc = extra.get("discovery") if isinstance(extra.get("discovery"), dict) else {}
    reasons = disc.get("gate_reasons") or []
    if isinstance(reasons, list) and any("name_parasite" in str(r) for r in reasons):
        return True
    for key in ("name_parasite", "name_parasite_heuristic", "parasite_of_runner", "parasite"):
        if extra.get(key) is True:
            return True
        if isinstance(disc, dict) and disc.get(key) is True:
            return True
    # feature D2 parasite
    fs = cand.feature_scores
    if fs is not None and fs.scores:
        d2 = fs.scores.get("D2")
        if d2 is not None and d2.value is True:
            notes = (d2.notes or "").lower()
            if "parasite" in notes or "copycat" in notes:
                return True
    return False


def _solana_preferred(cand: CandidateV1) -> bool:
    return (cand.chain or "").strip().lower() == "solana"


# ---------------------------------------------------------------------------
# Pure threshold evaluation (unit-tested)
# ---------------------------------------------------------------------------


def evaluate_watch_dip_buy(
    *,
    mc_now: Optional[float],
    first_sight: Optional[float],
    min_mc_seen: Optional[float],
    already_bought: bool,
    hard_rugged: bool = False,
    name_parasite: bool = False,
    solana: bool = True,
) -> tuple[bool, str]:
    """Return (should_buy, reason_code).

    DIP: mc_now <= 2M AND (first_sight is None OR mc_now <= 0.85*first_sight)
    RECLAIM: min_mc_seen < 2M AND mc_now < min(first_sight*1.1, 3.5M) and not already_bought
    NO CHASE: mc_now >= 4M and never bought → False
    """
    if not solana:
        return False, "non_solana"
    if hard_rugged:
        return False, "hard_rugged"
    if name_parasite:
        return False, "name_parasite"
    if mc_now is None:
        return False, "mc_unknown"
    try:
        mc = float(mc_now)
    except (TypeError, ValueError):
        return False, "mc_unknown"

    if mc >= NO_CHASE_MC_USD and not already_bought:
        return False, "no_chase_above_4m"

    if already_bought:
        return False, "already_bought"

    # Primary dip
    first: Optional[float] = None
    if first_sight is not None:
        try:
            first = float(first_sight)
        except (TypeError, ValueError):
            first = None

    dip_ok = mc <= DIP_MC_USD_MAX and (
        first is None or mc <= DIP_VS_FIRST_SIGHT_MAX * first
    )
    if dip_ok:
        return True, "watch_dip"

    # Reclaim after sub-2M print (one shot if never bought)
    min_seen: Optional[float] = None
    if min_mc_seen is not None:
        try:
            min_seen = float(min_mc_seen)
        except (TypeError, ValueError):
            min_seen = None
    if min_seen is not None and min_seen < DIP_MC_USD_MAX:
        if first is not None:
            reclaim_cap = min(first * RECLAIM_VS_FIRST_SIGHT_MAX, RECLAIM_MC_USD_CAP)
        else:
            reclaim_cap = RECLAIM_MC_USD_CAP
        if mc < reclaim_cap:
            return True, "watch_reclaim"
        return False, "reclaim_above_cap"

    return False, "no_dip_no_reclaim"


# ---------------------------------------------------------------------------
# MC fetch (pump.fun primary; Dex optional)
# ---------------------------------------------------------------------------


def fetch_mc_quote(
    ca: str,
    *,
    chain: str = "solana",
    timeout_s: float = 8.0,
    allow_dex: Optional[bool] = None,
    jup_asset: Optional[dict[str, Any]] = None,
    try_jup: bool = True,
) -> dict[str, Any]:
    """Fetch USD market cap with provenance.

    Returns {mc_usd, source, price_updated_at, price_live}. mc_usd None when
    unknown. Solana: Jupiter (prefetched ``jup_asset`` or live) → pump.fun
    coin detail → Dex (emergency even when SKIP_DEX). EVM: Dex only.
    price_live is True when the price printed within PRICE_LIVE_MAX_AGE_SEC
    (Jupiter updatedAt) or came from pump/Dex live endpoints.
    """
    empty: dict[str, Any] = {"mc_usd": None, "source": None, "price_updated_at": None, "price_live": False}
    ca = (ca or "").strip()
    jup_meta: dict[str, Any] = {}
    if not ca:
        return empty
    chain_n = (chain or "").strip().lower()
    is_evm = chain_n in {"base", "ethereum", "bsc", "arbitrum", "polygon", "avalanche", "optimism"} or ca.lower().startswith("0x")

    if allow_dex is None:
        use_dex = True if is_evm else (not skip_dex())
    else:
        use_dex = allow_dex

    if not is_evm:
        if jup_asset is None and try_jup and not skip_jup():
            assets, _errs = fetch_jup_assets([ca], timeout=timeout_s)
            jup_asset = assets.get(ca)
        if jup_asset is not None:
            q = quote_from_jup_asset(ca, jup_asset)
            jup_meta = {
                "holder_count": q.get("holder_count"),
                "liq_usd": q.get("liq_usd"),
                "jup_seen": True,
            }
            if q.get("mc_usd") is not None:
                return {
                    "mc_usd": float(q["mc_usd"]),
                    "source": "jupiter",
                    "price_updated_at": q.get("price_updated_at"),
                    "price_live": bool(q.get("price_live")),
                    **jup_meta,
                }
        mc = _fetch_pumpfun_mc(ca, timeout_s=timeout_s)
        if mc is not None:
            return {"mc_usd": mc, "source": "pump.fun", "price_updated_at": None, "price_live": True}
        # Emergency: pump coin detail 404s — try Dex even when SKIP_DEX.
        if not use_dex:
            log.warning(
                "fetch_mc_usd: pump miss; emergency Dex fallback ca=%s…", ca[:12]
            )
            use_dex = True
    dex_status: dict[str, Any] = {}
    if use_dex:
        mc = _fetch_dex_mc(
            ca, chain=chain_n if is_evm else None, timeout_s=timeout_s, status=dex_status
        )
        if mc is not None:
            return {"mc_usd": mc, "source": "dexscreener", "price_updated_at": None, "price_live": True}
    # dex_answered: True = Dex replied (no usable pair), False = Dex errored
    # (429/network — an outage, not evidence of a dead coin), None = unknown.
    return {**empty, **jup_meta, "dex_answered": dex_status.get("answered")}


def fetch_mc_usd(
    ca: str,
    *,
    chain: str = "solana",
    timeout_s: float = 8.0,
    allow_dex: Optional[bool] = None,
) -> Optional[float]:
    """Fetch USD market cap (see fetch_mc_quote). Never invents MC."""
    return fetch_mc_quote(ca, chain=chain, timeout_s=timeout_s, allow_dex=allow_dex).get("mc_usd")


def _fetch_pumpfun_mc(ca: str, *, timeout_s: float) -> Optional[float]:
    url = PUMPFUN_COIN_URL.format(mint=ca)
    try:
        req = Request(url, headers={"User-Agent": "x-intel-discovery/0.1"})
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        log.debug("pump.fun mc fetch failed ca=%s…: %s", ca[:12], e)
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("usd_market_cap", "market_cap_usd"):
        v = payload.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _fetch_dex_mc(
    ca: str,
    *,
    chain: Optional[str] = None,
    timeout_s: float,
    status: Optional[dict[str, Any]] = None,
) -> Optional[float]:
    url = DEX_TOKEN_URL.format(ca=ca)
    try:
        req = Request(url, headers={"User-Agent": "x-intel-discovery/0.1"})
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        log.debug("dex mc fetch failed ca=%s…: %s", ca[:12], e)
        if status is not None:
            status["answered"] = False
            status["error"] = str(e)[:120]
        return None
    if status is not None:
        status["answered"] = True
    if not isinstance(payload, dict):
        return None
    pairs = payload.get("pairs") or []
    want = (chain or "").lower() if chain and chain not in {"", "solana"} else None
    best: Optional[float] = None
    best_liq = -1.0
    for p in pairs:
        if not isinstance(p, dict):
            continue
        if want and str(p.get("chainId") or "").lower() != want:
            continue
        liq = (p.get("liquidity") or {}).get("usd")
        try:
            liq_f = float(liq) if liq is not None else 0.0
        except (TypeError, ValueError):
            liq_f = 0.0
        mc = p.get("marketCap") or p.get("fdv")
        if mc is None:
            continue
        try:
            mc_f = float(mc)
        except (TypeError, ValueError):
            continue
        if liq_f >= best_liq:
            best_liq = liq_f
            best = mc_f
    # If chain filter emptied results, do not invent — return None
    return best


# ---------------------------------------------------------------------------
# Persist helpers
# ---------------------------------------------------------------------------


def _persist_candidate_meta(
    ledger: CandidateLedger,
    cand: CandidateV1,
    *,
    mc_usd_now: Optional[float] = None,
    min_mc_usd_seen: Optional[float] = None,
    refreshed_at: Optional[datetime] = None,
    extra_updates: Optional[dict[str, Any]] = None,
) -> CandidateV1:
    """Write candidate JSON preserving outcomes extras (mc_usd_now etc.)."""
    path = ledger.paths.candidates / f"{cand.candidate_id}.json"
    raw = atomic_read_json(path) or cand.model_dump(mode="json")
    now = refreshed_at or _now()
    if mc_usd_now is not None:
        raw["mc_usd_now"] = mc_usd_now
    if min_mc_usd_seen is not None:
        raw["min_mc_usd_seen"] = min_mc_usd_seen
    if refreshed_at is not None or mc_usd_now is not None:
        raw["refreshed_at"] = now.isoformat().replace("+00:00", "Z")
    if extra_updates:
        raw.update(extra_updates)
    outcomes = raw.get("outcomes")
    if not isinstance(outcomes, dict):
        outcomes = {}
    if mc_usd_now is not None:
        outcomes["mc_usd_now"] = mc_usd_now
    if min_mc_usd_seen is not None:
        outcomes["min_mc_usd_seen"] = min_mc_usd_seen
    if refreshed_at is not None or mc_usd_now is not None:
        outcomes["refreshed_at"] = raw["refreshed_at"]
    raw["outcomes"] = outcomes
    raw["updated_at"] = now.isoformat().replace("+00:00", "Z")
    atomic_write_json(path, raw)
    return CandidateV1.model_validate(raw)


# ---------------------------------------------------------------------------
# Refresh all open WATCH
# ---------------------------------------------------------------------------


def _normalize_fetch_result(res: Any) -> dict[str, Any]:
    """mc_fetcher may return a float (legacy/tests) or a fetch_mc_quote dict."""
    if isinstance(res, dict):
        mc = res.get("mc_usd")
        return {
            "mc_usd": float(mc) if mc is not None else None,
            "source": res.get("source"),
            "price_updated_at": res.get("price_updated_at"),
            "price_live": bool(res.get("price_live", mc is not None)),
            "holder_count": res.get("holder_count"),
            "liq_usd": res.get("liq_usd"),
            "dex_answered": res.get("dex_answered"),
        }
    if res is None:
        return {"mc_usd": None, "source": None, "price_updated_at": None, "price_live": False}
    return {"mc_usd": float(res), "source": "fetcher", "price_updated_at": None, "price_live": True}


def _fnum(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_last_print(q: dict[str, Any], *, now: datetime) -> bool:
    """MC known but the print is >24h old (no trades) — live_quote LAST_PRINT."""
    if q.get("mc_usd") is None or q.get("price_live"):
        return False
    upd = _parse_dt(q.get("price_updated_at"))
    if upd is None:
        return False
    if upd.tzinfo is None:
        upd = upd.replace(tzinfo=timezone.utc)
    return (now - upd).total_seconds() > PRICE_LIVE_MAX_AGE_SEC


def dead_coin_corroboration(
    *,
    holder_count: Optional[float],
    liq_usd: Optional[float],
    mc_usd: Optional[float],
    floor: Optional[float] = None,
) -> Optional[str]:
    """Return which dead-coin signal holds (holders<=1 / liq~0 / MC<floor), else None."""
    floor = watch_buy_min_mc_usd() if floor is None else floor
    h, liq, mc = _fnum(holder_count), _fnum(liq_usd), _fnum(mc_usd)
    if h is not None and h <= WATCH_DEAD_MAX_HOLDERS:
        return f"holders={int(h)}"
    if liq is not None and liq < WATCH_DEAD_MAX_LIQ_USD:
        return f"liq_usd={liq:.0f}"
    if mc is not None and mc < floor:
        return f"mc_below_floor mc={mc:.0f} floor={floor:.0f}"
    return None


def evaluate_dead_watch(
    *,
    kind: Optional[str],
    streak: int,
    holder_count: Optional[float],
    liq_usd: Optional[float],
    mc_usd: Optional[float],
    floor: Optional[float] = None,
) -> Optional[str]:
    """Pure: close reason when a WATCH is a confirmed dead coin, else None.

    kind is "last_print" or "no_mc" (this refresh's dead signal) and streak the
    number of consecutive refreshes (incl. this one) with a dead signal.
    """
    if kind == "last_print":
        need = WATCH_DEAD_LAST_PRINT_STREAK
    elif kind in ("no_mc", "no_mc_all_sources"):
        need = WATCH_DEAD_NULL_STREAK
    else:
        return None
    if streak < need:
        return None
    why = dead_coin_corroboration(
        holder_count=holder_count, liq_usd=liq_usd, mc_usd=mc_usd, floor=floor
    )
    if why is None:
        return None
    return f"dead_coin_{kind} streak={streak} {why}"


def _expire_watch(
    ledger: CandidateLedger,
    cand: CandidateV1,
    *,
    now: datetime,
    reason: str,
    last_refresh: Optional[datetime],
    last_print: Optional[dict[str, Any]] = None,
) -> CandidateV1:
    """Close a WATCH as expired_stale. Keeps the file + all prior fields (history)."""
    path = ledger.paths.candidates / f"{cand.candidate_id}.json"
    raw = atomic_read_json(path) or cand.model_dump(mode="json")
    now_z = now.isoformat().replace("+00:00", "Z")
    raw["decision"] = CandidateDecision.reject.value
    raw["reject_reason"] = WATCH_EXPIRED_STATUS
    raw["watch_status"] = WATCH_EXPIRED_STATUS
    raw["prior_decision"] = CandidateDecision.watch.value
    raw["watch_closed_at"] = now_z
    raw["watch_close_reason"] = reason
    raw["watch_last_refresh_at"] = (
        last_refresh.isoformat().replace("+00:00", "Z") if last_refresh else None
    )
    if last_print and last_print.get("mc_usd") is not None:
        raw["watch_last_print"] = {
            "mc_usd": last_print.get("mc_usd"),
            "source": last_print.get("source"),
            "price_updated_at": last_print.get("price_updated_at"),
            "checked_at": now_z,
        }
    hist = raw.get("watch_status_history")
    if not isinstance(hist, list):
        hist = []
    hist.append({"at": now_z, "from": "watch", "to": WATCH_EXPIRED_STATUS, "reason": reason})
    raw["watch_status_history"] = hist
    raw["updated_at"] = now_z
    closed = CandidateV1.model_validate(raw)
    # save_candidate appends the transition to _index.jsonl (audit trail)
    ledger.save_candidate(closed, append_index=True)
    return closed


def is_buyable_mc(mc: Optional[float], floor: Optional[float] = None) -> bool:
    """Last known MC >= XINTEL_WATCH_BUY_MIN_MC. Unknown MC counts as buyable
    (conservative: we cannot prove it is a dead sub-floor coin)."""
    floor = watch_buy_min_mc_usd() if floor is None else floor
    m = _fnum(mc)
    return m is None or m >= floor


def order_watches_for_refresh(
    watches: list[CandidateV1], floor: Optional[float] = None
) -> list[CandidateV1]:
    """Buyable WATCHes (MC >= floor) first, highest MC first; then sub-floor.

    Jupiter batches (and 429-retry halves) follow this order, so STAMP/OURA/
    IOF/USOS-class coins get quoted before $3k dead coins."""
    floor = watch_buy_min_mc_usd() if floor is None else floor

    def key(c: CandidateV1) -> tuple[int, float]:
        m = _fnum(get_mc_now(c))
        if m is None:
            return (1, 0.0)  # unknown: after known-buyable, before sub-floor
        return (0, -m) if m >= floor else (2, -m)

    return sorted(watches, key=key)


def refresh_open_watches(
    *,
    ledger: Optional[CandidateLedger] = None,
    data_root: Optional[Path] = None,
    live: bool = True,
    mc_fetcher: Optional[Any] = None,
    expire_stale: Optional[bool] = None,
) -> dict[str, Any]:
    """Refresh MC for all open WATCH candidates; write freshness + heartbeat fields.

    expire_stale (default: on when live) closes WATCHes with no live price whose
    last successful refresh is >WATCH_EXPIRE_SEC old, so the watchdog only sees
    the open set.
    """
    root = data_root or data_dir()
    led = ledger or CandidateLedger(RepoPaths(data=root))
    fetcher = mc_fetcher or (fetch_mc_quote if live else (lambda *a, **k: None))
    do_expire = live if expire_stale is None else bool(expire_stale)
    now = _now()

    floor = watch_buy_min_mc_usd()
    watches = order_watches_for_refresh(
        led.list_candidates(decision=CandidateDecision.watch.value), floor
    )

    # One batched Jupiter call for every Solana WATCH (primary MC source);
    # buyable first so a 429 retry with a smaller batch covers them first.
    jup_map: dict[str, dict[str, Any]] = {}
    jup_errors: list[str] = []
    jup_stats: dict[str, Any] = {}
    if live and mc_fetcher is None and not skip_jup():
        sol = [
            (c.contract_address or "").strip()
            for c in watches
            if (c.chain or "solana").lower() == "solana"
            and not (c.contract_address or "").lower().startswith("0x")
        ]
        if sol:
            _enrich.JUP_LAST_STATS.clear()
            jup_map, jup_errors = fetch_jup_assets(sol, timeout=10.0)
            jup_stats = dict(_enrich.JUP_LAST_STATS)
            if jup_errors:
                log.warning(
                    "watch refresh: jupiter batch errors=%s stats=%s", jup_errors, jup_stats
                )
            elif jup_stats.get("http429"):
                log.info("watch refresh: jupiter 429 recovered stats=%s", jup_stats)

    rows: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    stale_count = 0  # buyable-only (alerting)
    stale_subfloor = 0
    stale_total = 0
    buyable_n = 0
    oldest_age: Optional[float] = None  # buyable-only
    oldest_age_all: Optional[float] = None
    source_counts: dict[str, int] = {}
    jup_outage = bool(mc_fetcher is None and live and jup_errors)

    for cand in watches:
        err = None
        prev_refreshed = get_refreshed_at(cand)
        ca = (cand.contract_address or "").strip()
        try:
            if mc_fetcher is None and live:
                res = fetch_mc_quote(
                    ca,
                    chain=cand.chain,
                    jup_asset=jup_map.get(ca),
                    try_jup=False,  # batch already covered Jupiter
                )
            else:
                try:
                    res = fetcher(cand.contract_address, chain=cand.chain)
                except TypeError:
                    res = fetcher(cand.contract_address)
        except Exception as e:  # noqa: BLE001
            err = str(e)
            res = None
        q = _normalize_fetch_result(res)
        mc = q["mc_usd"]
        price_live = bool(q["price_live"]) and mc is not None

        # Dead-coin streak: LAST_PRINT or no MC from a source that did answer.
        # Fetch exceptions / Jupiter batch outages never count (one failed
        # fetch must not close a real coin).
        extra0 = _cand_extra(cand)
        dead_kind: Optional[str] = None
        if not price_live and err is None:
            if _is_last_print(q, now=now):
                dead_kind = "last_print"
            elif mc is None and (not jup_outage or ca in jup_map):
                # every source answered (Jupiter row had no MC / not indexed)
                dead_kind = "no_mc"
            elif (
                mc is None
                and q.get("dex_answered") is not False
                and not is_buyable_mc(get_mc_now(cand), floor)
            ):
                # Jupiter unanswered (429/outage) AND pump.fun + Dex fallback
                # empty for a sub-floor coin: counts (never for buyable ones).
                # A Dex error (429/network) is an outage too → does not count.
                dead_kind = "no_mc_all_sources"
        prev_streak = int(_fnum(extra0.get("watch_dead_streak")) or 0)
        streak = prev_streak + 1 if dead_kind else 0
        holders = q.get("holder_count")
        if holders is None:
            holders = extra0.get("holder_count")
        liq_now = q.get("liq_usd")
        if liq_now is None:
            liq_now = extra0.get("liq_usd_now")
        dead_reason = None
        if do_expire and dead_kind:
            dead_reason = evaluate_dead_watch(
                kind=dead_kind,
                streak=streak,
                holder_count=holders,
                liq_usd=liq_now,
                mc_usd=mc if mc is not None else get_refreshed_mc_now(cand),
            )
        if dead_reason is not None:
            _expire_watch(
                led,
                cand,
                now=now,
                reason=dead_reason,
                last_refresh=prev_refreshed,
                last_print=q if mc is not None else None,
            )
            expired.append(
                {
                    "candidate_id": str(cand.candidate_id),
                    "ticker": cand.ticker,
                    "ca": ca,
                    "reason": dead_reason,
                    "last_refresh_at": prev_refreshed.isoformat() if prev_refreshed else None,
                    "last_print_mc_usd": mc,
                    "last_print_at": q.get("price_updated_at"),
                    "holder_count": holders,
                    "liq_usd": liq_now,
                }
            )
            continue
        dead_updates: dict[str, Any] = {}
        if q.get("holder_count") is not None:
            dead_updates["holder_count"] = q.get("holder_count")
        if q.get("liq_usd") is not None:
            dead_updates["liq_usd_now"] = q.get("liq_usd")
        if streak != prev_streak:
            dead_updates["watch_dead_streak"] = streak
            dead_updates["watch_dead_signal"] = dead_kind

        if do_expire and not price_live:
            baseline = prev_refreshed or _parse_dt(cand.first_seen_at)
            if baseline is not None and baseline.tzinfo is None:
                baseline = baseline.replace(tzinfo=timezone.utc)
            since = (now - baseline).total_seconds() if baseline else None
            if since is None or since > WATCH_EXPIRE_SEC:
                reason = "no_trades_24h" if mc is not None else "no_live_price_24h"
                _expire_watch(
                    led,
                    cand,
                    now=now,
                    reason=reason,
                    last_refresh=prev_refreshed,
                    last_print=q if mc is not None else None,
                )
                expired.append(
                    {
                        "candidate_id": str(cand.candidate_id),
                        "ticker": cand.ticker,
                        "ca": ca,
                        "reason": reason,
                        "last_refresh_at": prev_refreshed.isoformat() if prev_refreshed else None,
                        "last_print_mc_usd": mc,
                        "last_print_at": q.get("price_updated_at"),
                    }
                )
                continue

        prev_min = get_min_mc_seen(cand)
        if mc is not None and price_live:
            new_min = mc if prev_min is None else min(prev_min, mc)
            cand = _persist_candidate_meta(
                led,
                cand,
                mc_usd_now=mc,
                min_mc_usd_seen=new_min,
                refreshed_at=now,
                extra_updates={
                    "mc_source": q.get("source"),
                    "price_updated_at": q.get("price_updated_at"),
                    **dead_updates,
                },
            )
            refreshed_at = now
            src = str(q.get("source") or "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1
        else:
            refreshed_at = prev_refreshed
            # still track min from whatever we know
            known = get_mc_now(cand)
            if known is not None and (prev_min is None or known < prev_min):
                cand = _persist_candidate_meta(
                    led,
                    cand,
                    min_mc_usd_seen=known,
                    extra_updates=dead_updates or None,
                )
            elif dead_updates:
                cand = _persist_candidate_meta(led, cand, extra_updates=dead_updates)

        last_mc = get_mc_now(cand)
        buyable = is_buyable_mc(last_mc, floor)
        if buyable:
            buyable_n += 1
        age_sec: Optional[float] = None
        if refreshed_at is not None:
            rt = refreshed_at if refreshed_at.tzinfo else refreshed_at.replace(tzinfo=timezone.utc)
            age_sec = max(0.0, (now - rt).total_seconds())
            eff_age = age_sec
        else:
            # treat never-refreshed as very old for oldest metric
            eff_age = float(WATCH_STALE_SEC * 10)
        refresh_stale = age_sec is None or age_sec > WATCH_STALE_SEC
        if oldest_age_all is None or eff_age > oldest_age_all:
            oldest_age_all = eff_age
        if buyable and (oldest_age is None or eff_age > oldest_age):
            oldest_age = eff_age
        if refresh_stale:
            stale_total += 1
            if buyable:
                stale_count += 1
            else:
                stale_subfloor += 1

        rows.append(
            {
                "candidate_id": str(cand.candidate_id),
                "ticker": cand.ticker,
                "ca": cand.contract_address,
                "chain": cand.chain,
                "mc_usd_now": last_mc,
                "min_mc_usd_seen": get_min_mc_seen(cand),
                "mc_usd_at_first_sight": cand.mc_usd_at_first_sight,
                "mc_source": q.get("source") if price_live else None,
                "price_updated_at": q.get("price_updated_at"),
                "refreshed_at": (refreshed_at.isoformat() if refreshed_at else None),
                "refresh_age_sec": age_sec,
                # ``stale`` is buyable-only (what the watchdog alerts on);
                # ``refresh_stale`` is the raw age check for every WATCH.
                "stale": refresh_stale and buyable,
                "refresh_stale": refresh_stale,
                "buyable": buyable,
                "subfloor": not buyable,
                "fetch_error": err,
                "dead_signal": dead_kind,
                "dead_streak": streak,
            }
        )

    open_n = len(rows)
    freshness = {
        "checked_at": now.isoformat(),
        "watch_count": open_n,
        # Buyable-only staleness (alerting). stale == stale_buyable == watch_stale_count.
        "watch_stale_count": stale_count,
        "stale": stale_count,
        "stale_buyable": stale_count,
        "stale_subfloor": stale_subfloor,
        "stale_total": stale_total,
        "stale_watch_alert": stale_count > 0,
        "watch_buyable_count": buyable_n,
        "watch_subfloor_count": open_n - buyable_n,
        "watch_buy_min_mc_usd": floor,
        "oldest_watch_refresh_age_sec": oldest_age,
        "oldest_watch_refresh_age_sec_all": oldest_age_all,
        "stale_threshold_sec": WATCH_STALE_SEC,
        "expire_threshold_sec": WATCH_EXPIRE_SEC,
        "watch_expired_n": len(expired),
        "mc_source_counts": source_counts,
        "jup_errors": jup_errors,
        "jup_stats": jup_stats,
        "skip_dex": skip_dex(),
        "armed": is_armed(),
        "watches": rows,
        "expired_this_cycle": expired,
    }
    health_dir = root / "health"
    health_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(health_dir / "watch_freshness.json", freshness)
    _patch_heartbeat(
        health_dir / "heartbeat.json",
        watch_count=open_n,
        watch_stale_count=stale_count,
        oldest_watch_refresh_age_sec=oldest_age,
        watch_expired_n=len(expired),
        watch_stale_subfloor=stale_subfloor,
        oldest_watch_refresh_age_sec_all=oldest_age_all,
    )
    log.info(
        "watch refresh n=%d stale_buyable=%d stale_subfloor=%d expired=%d oldest_age=%s sources=%s",
        open_n,
        stale_count,
        stale_subfloor,
        len(expired),
        oldest_age,
        source_counts,
    )
    return freshness


def _patch_heartbeat(
    path: Path,
    *,
    watch_count: int,
    watch_stale_count: int,
    oldest_watch_refresh_age_sec: Optional[float],
    watch_expired_n: int = 0,
    watch_stale_subfloor: int = 0,
    oldest_watch_refresh_age_sec_all: Optional[float] = None,
) -> None:
    raw = atomic_read_json(path) or {}
    raw["watch_count"] = watch_count
    raw["watch_expired_n"] = watch_expired_n
    # Buyable-only (MC >= XINTEL_WATCH_BUY_MIN_MC); sub-floor never alerts.
    raw["watch_stale_count"] = watch_stale_count
    raw["watch_stale_buyable"] = watch_stale_count
    raw["watch_stale_subfloor"] = watch_stale_subfloor
    raw["stale_watch_alert"] = watch_stale_count > 0
    raw["oldest_watch_refresh_age_sec"] = oldest_watch_refresh_age_sec
    raw["oldest_watch_refresh_age_sec_all"] = oldest_watch_refresh_age_sec_all
    raw["watch_freshness_checked_at"] = _now().isoformat()
    atomic_write_json(path, raw)


# ---------------------------------------------------------------------------
# Dip-buy dedup + escalate
# ---------------------------------------------------------------------------


def _prior_dip_buy(
    ledger: CandidateLedger,
    ca: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[DecisionV1]:
    """Return unexpired watch_dip_buy BUY for this CA, if any.

    Reads decision JSON defensively (skip corrupt/fixture rows) so escalate
    never crashes the runner.
    """
    now = now or _now()
    ca_n = ca.strip()
    ddir = ledger.paths.decisions
    if not ddir.is_dir():
        return None
    for path in sorted(ddir.glob("*.json")):
        if path.name.startswith("_") or path.name.endswith(".tmp"):
            continue
        raw = atomic_read_json(path)
        if not isinstance(raw, dict):
            continue
        if str(raw.get("action") or "").upper() != "BUY":
            continue
        if (raw.get("contract_address") or "").strip() != ca_n:
            continue
        flags = set(raw.get("risk_flags") or [])
        if "watch_dip_buy" not in flags:
            continue
        # Normalize null refs so DecisionV1 validates
        evs = raw.get("evidence") or []
        if isinstance(evs, list):
            for e in evs:
                if isinstance(e, dict) and e.get("refs") is None:
                    e["refs"] = []
        try:
            dec = DecisionV1.model_validate(raw)
        except Exception:  # noqa: BLE001
            # Fallback: treat unexpired by expires_at string alone
            exp = _parse_dt(raw.get("expires_at"))
            if exp is None:
                continue
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now <= exp:
                # synthesize minimal marker via re-read after refs fix failed
                return DecisionV1.model_construct(  # type: ignore[call-arg]
                    decision_id=raw.get("decision_id"),
                    action=DecisionAction.BUY,
                    issued_at=_parse_dt(raw.get("issued_at")) or now,
                    expires_at=exp,
                    contract_address=ca_n,
                    chain="solana",
                    confidence=float(raw.get("confidence") or 0.5),
                    evidence=[],
                    risk_flags=list(flags),
                    do_not_execute_until_armed=bool(raw.get("do_not_execute_until_armed", True)),
                )
            continue
        if is_decision_stale(dec, now=now):
            continue
        return dec
    return None


def _watch_buy_history(ledger: CandidateLedger) -> dict[str, datetime]:
    """Map CA -> latest issued_at of ANY prior watch_dip_buy BUY decision.

    Includes expired / cancelled decisions on purpose: the 2h dedupe must hold
    even after a prior BUY was TTL-expired or cancelled (STAMP re-fire).
    """
    out: dict[str, datetime] = {}
    ddir = ledger.paths.decisions
    if not ddir.is_dir():
        return out
    for path in ddir.glob("*.json"):
        if path.name.startswith("_") or path.name.endswith(".tmp"):
            continue
        raw = atomic_read_json(path)
        if not isinstance(raw, dict):
            continue
        if str(raw.get("action") or "").upper() != "BUY":
            continue
        if "watch_dip_buy" not in set(raw.get("risk_flags") or []):
            continue
        ca = (raw.get("contract_address") or "").strip()
        issued = _parse_dt(raw.get("issued_at"))
        if not ca or issued is None:
            continue
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=timezone.utc)
        if ca not in out or issued > out[ca]:
            out[ca] = issued
    return out


def _last_watch_buy_at(
    cand: CandidateV1, history: dict[str, datetime]
) -> Optional[datetime]:
    ca = (cand.contract_address or "").strip()
    best = history.get(ca)
    meta = _parse_dt(_cand_extra(cand).get("watch_dip_buy_emitted_at"))
    if meta is not None:
        if meta.tzinfo is None:
            meta = meta.replace(tzinfo=timezone.utc)
        if best is None or meta > best:
            best = meta
    return best


def _watch_hints(cand: CandidateV1, *, data_root: Optional[Path] = None) -> tuple[Any, dict[str, Any]]:
    """Discovery hints for a WATCH with fresh ``data/x_organic/<mint>.json`` merged."""
    from types import SimpleNamespace

    extra = _cand_extra(cand)
    disc = extra.get("discovery") if isinstance(extra.get("discovery"), dict) else {}
    hints = dict((disc or {}).get("confidence_hints") or {})
    sources = [str(s) for s in (cand.source_accounts or []) if s]
    for s_ in (disc or {}).get("sources") or []:
        if s_ and str(s_) not in sources:
            sources.append(str(s_))
    rec = SimpleNamespace(ca=(cand.contract_address or "").strip(), confidence_hints=hints, sources=sources)
    try:
        annotate_organic_x_hints(rec, data_root=data_root)
    except Exception as e:  # noqa: BLE001
        log.debug("organic_x annotate failed ca=%s…: %s", rec.ca[:12], e)
    return rec, dict(rec.confidence_hints or {})


def watch_buy_hard_gate(
    cand: CandidateV1,
    *,
    mc_now: Optional[float],
    now: datetime,
    history: dict[str, datetime],
    data_root: Optional[Path] = None,
) -> Optional[str]:
    """Return a reject reason when a WATCH dip/reclaim BUY must not emit, else None."""
    floor = watch_buy_min_mc_usd()
    try:
        mc = float(mc_now) if mc_now is not None else None
    except (TypeError, ValueError):
        mc = None
    if mc is None or mc < floor:
        return f"mc_below_watch_buy_floor mc={mc} floor={floor:.0f}"
    last = _last_watch_buy_at(cand, history)
    window = watch_buy_dedupe_sec()
    if last is not None and (now - last).total_seconds() < window:
        return (
            f"watch_buy_dedupe_{window // 3600}h last_watch_buy_at="
            f"{last.isoformat().replace('+00:00', 'Z')}"
        )
    if watch_buy_requires_organic_x():
        rec, hints = _watch_hints(cand, data_root=data_root)
        if not has_real_organic_x(rec, hints):  # type: ignore[arg-type]
            return "watch_buy_requires_organic_x — no real organic X (soft paths downgraded)"
    return None


def _mark_dip_emitted(
    ledger: CandidateLedger,
    cand: CandidateV1,
    decision: DecisionV1,
) -> None:
    _persist_candidate_meta(
        ledger,
        cand,
        extra_updates={
            "watch_dip_buy_decision_id": str(decision.decision_id),
            "watch_dip_buy_emitted_at": decision.issued_at.isoformat().replace("+00:00", "Z"),
            "watch_dip_buy_expires_at": decision.expires_at.isoformat().replace("+00:00", "Z"),
        },
    )


def escalate_watch_dips(
    *,
    ledger: Optional[CandidateLedger] = None,
    data_root: Optional[Path] = None,
    emit: bool = True,
) -> list[dict[str, Any]]:
    """For each WATCH after MC refresh, maybe emit one dip-BUY."""
    root = data_root or data_dir()
    led = ledger or CandidateLedger(RepoPaths(data=root))
    now = _now()
    results: list[dict[str, Any]] = []
    history = _watch_buy_history(led)

    for cand in led.list_candidates(decision=CandidateDecision.watch.value):
        # reload to pick up refresh meta
        fresh = led.get_candidate(cand.candidate_id) or cand
        ca = (fresh.contract_address or "").strip()
        # Skip obvious fixture / template CAs
        if not ca or ca.upper().startswith("TEMPLATE") or "111111" in ca:
            results.append(
                {
                    "candidate_id": str(fresh.candidate_id),
                    "ticker": fresh.ticker,
                    "ca": ca,
                    "mc_usd_now": None,
                    "min_mc_usd_seen": get_min_mc_seen(fresh),
                    "should_buy": False,
                    "reason": "fixture_or_template_ca",
                    "buy_decision_id": None,
                    "do_not_execute_until_armed": do_not_execute_until_armed(),
                }
            )
            continue
        mc_now = get_refreshed_mc_now(fresh)
        if mc_now is None:
            results.append(
                {
                    "candidate_id": str(fresh.candidate_id),
                    "ticker": fresh.ticker,
                    "ca": ca,
                    "mc_usd_now": None,
                    "min_mc_usd_seen": get_min_mc_seen(fresh),
                    "should_buy": False,
                    "reason": "mc_not_refreshed",
                    "buy_decision_id": None,
                    "do_not_execute_until_armed": do_not_execute_until_armed(),
                }
            )
            continue
        min_seen = get_min_mc_seen(fresh)
        first = fresh.mc_usd_at_first_sight
        prior = _prior_dip_buy(led, fresh.contract_address, now=now)
        if prior is not None:
            already = True
        elif _cand_extra(fresh).get("watch_dip_buy_decision_id"):
            # Meta emit only blocks while prior TTL still open
            already = not _prior_expired_meta(fresh, now=now)
        else:
            already = False

        should, reason = evaluate_watch_dip_buy(
            mc_now=mc_now,
            first_sight=first,
            min_mc_seen=min_seen,
            already_bought=already,
            hard_rugged=_is_hard_rugged(fresh),
            name_parasite=_is_name_parasite(fresh),
            solana=_solana_preferred(fresh),
        )
        row: dict[str, Any] = {
            "candidate_id": str(fresh.candidate_id),
            "ticker": fresh.ticker,
            "ca": fresh.contract_address,
            "mc_usd_now": mc_now,
            "min_mc_usd_seen": min_seen,
            "should_buy": should,
            "reason": reason,
            "buy_decision_id": None,
            "do_not_execute_until_armed": do_not_execute_until_armed(),
        }
        if should:
            hard = watch_buy_hard_gate(
                fresh, mc_now=mc_now, now=now, history=history, data_root=led.paths.data
            )
            if hard is not None:
                row["reason"] = f"gate_reject:{hard}"
                row["should_buy"] = False
                row["watch_signal"] = reason
                results.append(row)
                log.info(
                    "watch dip-buy gate reject ca=%s… ticker=%s signal=%s mc=%s: %s",
                    ca[:12],
                    fresh.ticker,
                    reason,
                    mc_now,
                    hard,
                )
                continue

        if not should or not emit:
            results.append(row)
            continue

        try:
            decision = _emit_watch_dip_buy(fresh, ledger=led, mc_now=mc_now, reason=reason)
        except GateReject as e:
            row["reason"] = f"gate_reject:{e.reason}"
            row["should_buy"] = False
            results.append(row)
            log.info(
                "watch dip-buy gate reject ca=%s… ticker=%s: %s",
                fresh.contract_address[:12],
                fresh.ticker,
                e.reason,
            )
            continue
        except Exception as e:  # noqa: BLE001
            row["reason"] = f"emit_error:{e}"
            row["should_buy"] = False
            results.append(row)
            log.warning("watch dip-buy emit failed: %s", e)
            continue

        _mark_dip_emitted(led, fresh, decision)
        history[ca] = decision.issued_at if decision.issued_at.tzinfo else decision.issued_at.replace(tzinfo=timezone.utc)
        row["buy_decision_id"] = str(decision.decision_id)
        row["do_not_execute_until_armed"] = decision.do_not_execute_until_armed
        results.append(row)
        log.info(
            "watch dip-BUY emitted decision_id=%s ca=%s… ticker=%s mc=%s reason=%s armed=%s",
            decision.decision_id,
            fresh.contract_address[:12],
            fresh.ticker,
            mc_now,
            reason,
            is_armed(),
        )
    return results


def _prior_expired_meta(cand: CandidateV1, *, now: datetime) -> bool:
    exp = _parse_dt(_cand_extra(cand).get("watch_dip_buy_expires_at"))
    if exp is None:
        # unknown expiry — treat as expired so we don't block forever without a live decision
        return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return now > exp


def _emit_watch_dip_buy(
    cand: CandidateV1,
    *,
    ledger: CandidateLedger,
    mc_now: Optional[float],
    reason: str,
) -> DecisionV1:
    """Emit via emit_pursue_buy with watch_dip_buy flag; never calibration_shadow."""
    floor = watch_buy_min_mc_usd()
    if mc_now is None or float(mc_now) < floor:
        raise GateReject(f"mc_below_watch_buy_floor mc={mc_now} floor={floor:.0f}")
    # Temporarily treat as pursue for gate path
    pursue_cand = cand.model_copy(update={"decision": CandidateDecision.pursue})
    # Ensure evidence has refs=[] compatible channels; inject launch_metrics if empty
    if not pursue_cand.evidence:
        from x_intel.schemas.models import EvidenceItem

        pursue_cand = pursue_cand.model_copy(
            update={
                "evidence": [
                    EvidenceItem(
                        channel="launch_metrics",
                        summary=f"watch_dip_buy {reason} mc_now={mc_now}",
                        observed_at=_now(),
                        refs=[],
                    )
                ]
            }
        )

    # Stamp live MC into discovery hints so watch-dip quality can see it
    extra = dict(getattr(pursue_cand, "__pydantic_extra__", None) or {})
    disc = dict(extra.get("discovery") or {})
    ch = dict(disc.get("confidence_hints") or {})
    # Fresh CA-scoped organic X (data/x_organic/<mint>.json) for the organic gate
    _rec, fresh_hints = _watch_hints(cand, data_root=ledger.paths.data)
    for k in ("organic_x", "x_social", "organic_x_eval", "organic_x_refs"):
        if k in fresh_hints:
            ch[k] = fresh_hints[k]
    if mc_now is not None:
        ch["mc_usd_now"] = mc_now
    disc["confidence_hints"] = ch
    if "sources" not in disc:
        disc["sources"] = list(pursue_cand.source_accounts or [])
    extra["discovery"] = disc
    data = pursue_cand.model_dump(mode="json")
    data.update(extra)
    pursue_cand = CandidateV1.model_validate(data)

    # Real dip vs first_sight may use looser non-clone quality (no organic X).
    # Reclaim keeps full publish-quality bar to avoid same-cycle farm reclaim BUYs.
    relax = reason == "watch_dip"

    thesis = (
        f"watch_dip_buy/{reason} {cand.ticker or cand.contract_address[:8]} "
        f"mc_now={mc_now} first_sight={cand.mc_usd_at_first_sight} "
        f"min_seen={get_min_mc_seen(cand)}"
    )
    decision = emit_pursue_buy(
        pursue_cand,
        ledger=ledger,
        persist=True,
        confidence=DIP_BUY_CONFIDENCE,
        percent_equity=DIP_BUY_PERCENT_EQUITY,
        early_mc_usd_max=None,  # dip/reclaim bands are the MC gate
        thesis=thesis,
        extra_risk_flags=["watch_dip_buy", reason],
        market_mc_usd=mc_now,
        ttl_seconds=DIP_BUY_TTL_SECONDS,
        allow_watch_dip_quality=relax,
        require_organic_x=watch_buy_requires_organic_x(),
    )
    # Final assert: no shadow flags, refs empty, armed policy
    banned = {"calibration_shadow", "shadow_only", "pipe_check", "PIPECHECK"}
    if banned.intersection(decision.risk_flags or []):
        raise GateReject("shadow flags leaked into watch_dip_buy")
    if "watch_dip_buy" not in (decision.risk_flags or []):
        raise GateReject("watch_dip_buy flag missing after emit")
    for ev in decision.evidence:
        if ev.refs:
            raise GateReject("evidence refs must be []")
    assert decision.do_not_execute_until_armed == do_not_execute_until_armed()
    return decision


def run_watch_escalate_cycle(
    *,
    ledger: Optional[CandidateLedger] = None,
    data_root: Optional[Path] = None,
    live: bool = True,
    emit_buy: bool = True,
    mc_fetcher: Optional[Any] = None,
) -> dict[str, Any]:
    """refresh watches → escalate dip-buys. Called from discovery runner."""
    freshness = refresh_open_watches(
        ledger=ledger,
        data_root=data_root,
        live=live,
        mc_fetcher=mc_fetcher,
    )
    emits = escalate_watch_dips(
        ledger=ledger,
        data_root=data_root,
        emit=emit_buy,
    )
    bought = [e for e in emits if e.get("buy_decision_id")]
    return {
        "watch_count": freshness.get("watch_count"),
        "watch_stale_count": freshness.get("watch_stale_count"),
        "watch_stale_buyable": freshness.get("stale_buyable"),
        "watch_stale_subfloor": freshness.get("stale_subfloor"),
        "stale_watch_alert": freshness.get("stale_watch_alert"),
        "oldest_watch_refresh_age_sec_all": freshness.get("oldest_watch_refresh_age_sec_all"),
        "jup_errors": freshness.get("jup_errors"),
        "jup_stats": freshness.get("jup_stats"),
        "watch_expired_n": freshness.get("watch_expired_n"),
        "expired": freshness.get("expired_this_cycle"),
        "oldest_watch_refresh_age_sec": freshness.get("oldest_watch_refresh_age_sec"),
        "escalate_n": len(emits),
        "buy_emitted_n": len(bought),
        "buys": bought,
        "escalate": emits,
    }
