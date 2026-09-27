"""Clone-farm / ticker-storm detection for early discovery BUY gates.

Stops curve+twitter spam floods (HELLO/NIMA/Rupert/DUMBCOIN/SMARTCOIN at ~$3k)
where the same ticker launches many mints in a short window.

SCAT / Shielded Cat is NOT a STAMP parasite — handled in parasite.py.
Clone-storm here is ticker×many-mints, independent of STAMP.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from x_intel.discovery.bus import normalize_ca
from x_intel.discovery.enrich import is_real_social_url

# Seed set from observed pump.fun clone farms that flooded BUY emits.
# Dynamic detection (bus + recent emit history) also flags storms; this catches
# the first mint of a known farm ticker that only has a bare twitter link.
KNOWN_CLONE_FARM_TICKERS = frozenset(
    {
        "HELLO",
        "NIMA",
        "RUPERT",
        "DUMBCOIN",
        "SMARTCOIN",
        "NIMO",
        "JEANPHIL",
    }
)

# Same ticker, this many distinct mints in the window → clone storm.
CLONE_STORM_MIN_DISTINCT_CAS = 3
CLONE_STORM_WINDOW_MINUTES = 60.0

_STATUS_URL_RE = re.compile(
    r"https?://(www\.)?(x\.com|twitter\.com)/[^/]+/status/\d+",
    re.I,
)
_PROFILE_URL_RE = re.compile(
    r"https?://(www\.)?(x\.com|twitter\.com)/[A-Za-z0-9_]+/?(\?.*)?$",
    re.I,
)


def _norm_ticker(ticker: Optional[str]) -> str:
    return (ticker or "").strip().lstrip("$").upper()


def _rec_time(rec: Any) -> Optional[datetime]:
    for attr in ("updated_at", "first_seen_at", "pair_created_at", "enriched_at"):
        t = getattr(rec, attr, None)
        if t is None and isinstance(rec, dict):
            t = rec.get(attr)
        if t is None:
            continue
        if isinstance(t, str):
            try:
                t = datetime.fromisoformat(t.replace("Z", "+00:00"))
            except ValueError:
                continue
        if isinstance(t, datetime):
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t
    return None


def is_status_only_twitter(url: Optional[str]) -> bool:
    """True when the only social is a status/status dump link (common farm paste)."""
    if not url or not is_real_social_url(url):
        return False
    return bool(_STATUS_URL_RE.search(url.strip()))


def is_profile_twitter(url: Optional[str]) -> bool:
    """Profile URL (not a /status/ dump) — stronger than bare status paste."""
    if not url or not is_real_social_url(url):
        return False
    u = url.strip().split("?")[0].rstrip("/")
    if "/status/" in u.lower():
        return False
    return bool(_PROFILE_URL_RE.match(u) or _PROFILE_URL_RE.match(url.strip()))


def non_status_spam_twitter(hints: dict[str, Any]) -> bool:
    """Organic-enough twitter: profile link, or twitter+telegram both real."""
    tw = hints.get("pump_twitter") or hints.get("dex_twitter")
    tg = hints.get("pump_telegram") or hints.get("dex_telegram")
    if is_profile_twitter(tw if isinstance(tw, str) else None):
        return True
    if (
        is_real_social_url(tw if isinstance(tw, str) else None)
        and is_real_social_url(tg if isinstance(tg, str) else None)
    ):
        return True
    return False


def distinct_cas_for_ticker(
    ticker: Optional[str],
    peers: Iterable[Any],
    *,
    now: Optional[datetime] = None,
    window_minutes: float = CLONE_STORM_WINDOW_MINUTES,
    self_ca: Optional[str] = None,
) -> set[str]:
    """Distinct mints sharing ticker within the recent window (includes self_ca)."""
    nt = _norm_ticker(ticker)
    if not nt:
        return set()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(minutes=window_minutes)
    cas: set[str] = set()
    self_n = normalize_ca(self_ca or "")
    if self_n:
        cas.add(self_n)
    for peer in peers:
        pt = _norm_ticker(getattr(peer, "ticker", None) or (peer.get("ticker") if isinstance(peer, dict) else None))
        if pt != nt:
            # also check symbol
            ps = _norm_ticker(
                getattr(peer, "symbol", None)
                or (peer.get("symbol") if isinstance(peer, dict) else None)
            )
            if ps != nt:
                continue
        pca = normalize_ca(
            getattr(peer, "ca", None) or (peer.get("ca") if isinstance(peer, dict) else "") or ""
        )
        if not pca:
            continue
        t = _rec_time(peer)
        if t is not None and t < cutoff:
            continue
        cas.add(pca)
    return cas


def is_clone_storm(
    ticker: Optional[str],
    peers: Iterable[Any],
    *,
    self_ca: Optional[str] = None,
    now: Optional[datetime] = None,
    min_distinct: int = CLONE_STORM_MIN_DISTINCT_CAS,
    window_minutes: float = CLONE_STORM_WINDOW_MINUTES,
) -> bool:
    return (
        len(
            distinct_cas_for_ticker(
                ticker,
                peers,
                now=now,
                window_minutes=window_minutes,
                self_ca=self_ca,
            )
        )
        >= min_distinct
    )


def spam_tickers_from_emit_history(
    decisions: Iterable[Any],
    *,
    now: Optional[datetime] = None,
    window_minutes: float = CLONE_STORM_WINDOW_MINUTES,
    min_distinct: int = CLONE_STORM_MIN_DISTINCT_CAS,
) -> set[str]:
    """Tickers that already emitted BUY for many distinct mints recently."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(minutes=window_minutes)
    by_ticker: dict[str, set[str]] = {}
    for d in decisions:
        if isinstance(d, dict):
            action = d.get("action")
            ticker = d.get("ticker")
            ca = d.get("contract_address") or d.get("ca")
            issued = d.get("issued_at")
        else:
            action = getattr(d, "action", None)
            ticker = getattr(d, "ticker", None)
            ca = getattr(d, "contract_address", None) or getattr(d, "ca", None)
            issued = getattr(d, "issued_at", None)
        act = action.value if hasattr(action, "value") else action
        if str(act).upper() not in {"BUY", "DecisionAction.BUY"}:
            if str(act) != "BUY":
                continue
        nt = _norm_ticker(ticker)
        ca_n = normalize_ca(ca or "")
        if not nt or not ca_n:
            continue
        if isinstance(issued, str):
            try:
                issued = datetime.fromisoformat(issued.replace("Z", "+00:00"))
            except ValueError:
                issued = None
        if isinstance(issued, datetime):
            if issued.tzinfo is None:
                issued = issued.replace(tzinfo=timezone.utc)
            if issued < cutoff:
                continue
        by_ticker.setdefault(nt, set()).add(ca_n)
    return {t for t, cas in by_ticker.items() if len(cas) >= min_distinct}


def annotate_clone_farm_hints(
    rec: Any,
    *,
    peers: Optional[Iterable[Any]] = None,
    recent_decisions: Optional[Iterable[Any]] = None,
    now: Optional[datetime] = None,
) -> Any:
    """Stamp confidence_hints used by publish-quality / hard reject gates."""
    hints = dict(getattr(rec, "confidence_hints", None) or {})
    ticker = getattr(rec, "ticker", None) or hints.get("ticker") or hints.get("symbol")
    ca = getattr(rec, "ca", None) or ""
    peer_list = list(peers or [])
    cas = distinct_cas_for_ticker(ticker, peer_list, now=now, self_ca=ca)
    n = len(cas)
    hints["ticker_distinct_cas_recent"] = n
    hints["ticker_unique_recent"] = n <= 1
    storm = n >= CLONE_STORM_MIN_DISTINCT_CAS
    hints["clone_storm"] = storm

    nt = _norm_ticker(ticker)
    emit_spam = set()
    if recent_decisions is not None:
        emit_spam = spam_tickers_from_emit_history(recent_decisions, now=now)
    farm = nt in KNOWN_CLONE_FARM_TICKERS or nt in emit_spam or storm
    hints["spam_farm_ticker"] = farm
    if farm:
        hints["clone_farm_ticker"] = nt

    tw = hints.get("pump_twitter") or hints.get("dex_twitter")
    if isinstance(tw, str):
        hints["twitter_status_only"] = is_status_only_twitter(tw)
        hints["twitter_profile"] = is_profile_twitter(tw)

    rec.confidence_hints = hints
    return rec


__all__ = [
    "KNOWN_CLONE_FARM_TICKERS",
    "CLONE_STORM_MIN_DISTINCT_CAS",
    "CLONE_STORM_WINDOW_MINUTES",
    "is_status_only_twitter",
    "is_profile_twitter",
    "non_status_spam_twitter",
    "distinct_cas_for_ticker",
    "is_clone_storm",
    "spam_tickers_from_emit_history",
    "annotate_clone_farm_hints",
]
