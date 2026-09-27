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
            if q.get("mc_usd") is not None:
                return {
                    "mc_usd": float(q["mc_usd"]),
                    "source": "jupiter",
                    "price_updated_at": q.get("price_updated_at"),
                    "price_live": bool(q.get("price_live")),
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
    if use_dex:
        mc = _fetch_dex_mc(ca, chain=chain_n if is_evm else None, timeout_s=timeout_s)
        if mc is not None:
            return {"mc_usd": mc, "source": "dexscreener", "price_updated_at": None, "price_live": True}
    return empty


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
) -> Optional[float]:
    url = DEX_TOKEN_URL.format(ca=ca)
    try:
        req = Request(url, headers={"User-Agent": "x-intel-discovery/0.1"})
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        log.debug("dex mc fetch failed ca=%s…: %s", ca[:12], e)
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
        }
    if res is None:
        return {"mc_usd": None, "source": None, "price_updated_at": None, "price_live": False}
    return {"mc_usd": float(res), "source": "fetcher", "price_updated_at": None, "price_live": True}


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

    watches = led.list_candidates(decision=CandidateDecision.watch.value)

    # One batched Jupiter call for every Solana WATCH (primary MC source).
    jup_map: dict[str, dict[str, Any]] = {}
    jup_errors: list[str] = []
    if live and mc_fetcher is None and not skip_jup():
        sol = [
            (c.contract_address or "").strip()
            for c in watches
            if (c.chain or "solana").lower() == "solana"
            and not (c.contract_address or "").lower().startswith("0x")
        ]
        if sol:
            jup_map, jup_errors = fetch_jup_assets(sol, timeout=10.0)
            if jup_errors:
                log.warning("watch refresh: jupiter batch errors=%s", jup_errors)

    rows: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    stale_count = 0
    oldest_age: Optional[float] = None
    source_counts: dict[str, int] = {}

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
                )

        age_sec: Optional[float] = None
        if refreshed_at is not None:
            rt = refreshed_at if refreshed_at.tzinfo else refreshed_at.replace(tzinfo=timezone.utc)
            age_sec = max(0.0, (now - rt).total_seconds())
            if oldest_age is None or age_sec > oldest_age:
                oldest_age = age_sec
            if age_sec > WATCH_STALE_SEC:
                stale_count += 1
        else:
            stale_count += 1
            # treat never-refreshed as infinitely old for oldest metric
            if oldest_age is None or oldest_age < WATCH_STALE_SEC * 10:
                oldest_age = max(oldest_age or 0.0, float(WATCH_STALE_SEC * 10))

        rows.append(
            {
                "candidate_id": str(cand.candidate_id),
                "ticker": cand.ticker,
                "ca": cand.contract_address,
                "chain": cand.chain,
                "mc_usd_now": get_mc_now(cand),
                "min_mc_usd_seen": get_min_mc_seen(cand),
                "mc_usd_at_first_sight": cand.mc_usd_at_first_sight,
                "mc_source": q.get("source") if price_live else None,
                "price_updated_at": q.get("price_updated_at"),
                "refreshed_at": (refreshed_at.isoformat() if refreshed_at else None),
                "refresh_age_sec": age_sec,
                "stale": age_sec is None or age_sec > WATCH_STALE_SEC,
                "fetch_error": err,
            }
        )

    open_n = len(rows)
    freshness = {
        "checked_at": now.isoformat(),
        "watch_count": open_n,
        "watch_stale_count": stale_count,
        "oldest_watch_refresh_age_sec": oldest_age,
        "stale_threshold_sec": WATCH_STALE_SEC,
        "expire_threshold_sec": WATCH_EXPIRE_SEC,
        "watch_expired_n": len(expired),
        "mc_source_counts": source_counts,
        "jup_errors": jup_errors,
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
    )
    log.info(
        "watch refresh n=%d stale=%d expired=%d oldest_age=%s sources=%s",
        open_n,
        stale_count,
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
) -> None:
    raw = atomic_read_json(path) or {}
    raw["watch_count"] = watch_count
    raw["watch_expired_n"] = watch_expired_n
    raw["watch_stale_count"] = watch_stale_count
    raw["oldest_watch_refresh_age_sec"] = oldest_watch_refresh_age_sec
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
        "watch_expired_n": freshness.get("watch_expired_n"),
        "expired": freshness.get("expired_this_cycle"),
        "oldest_watch_refresh_age_sec": freshness.get("oldest_watch_refresh_age_sec"),
        "escalate_n": len(emits),
        "buy_emitted_n": len(bought),
        "buys": bought,
        "escalate": emits,
    }
