"""DexScreener newest profiles/pairs — Solana+Base+ETH+BSC minimum."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen

from x_intel.discovery.models import DiscoveryEvent

log = logging.getLogger(__name__)

# Public DexScreener endpoints (no API key). Live mode optional.
TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKEN_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"

CHAIN_MAP = {
    "solana": "solana",
    "base": "base",
    "ethereum": "ethereum",
    "bsc": "bsc",
    "eth": "ethereum",
}

DEFAULT_CHAINS = frozenset({"solana", "base", "ethereum", "bsc"})


class DexScreenerNewSource:
    source_id = "dexscreener_new"

    def __init__(
        self,
        *,
        fixture_dir: Optional[Path] = None,
        live: bool = False,
        chains: Optional[set[str]] = None,
        timeout_s: float = 8.0,
    ) -> None:
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.live = live
        self.chains = chains or set(DEFAULT_CHAINS)
        self.timeout_s = timeout_s

    def poll(self) -> list[DiscoveryEvent]:
        if not self.live:
            return self._poll_fixture()
        try:
            return self._poll_live()
        except Exception as e:  # noqa: BLE001 — discovery must not crash runner
            log.warning("dexscreener_new live poll failed: %s — no fixture fallback in live mode", e)
            return []

    def _poll_fixture(self) -> list[DiscoveryEvent]:
        path = self._fixture_path()
        if path is None or not path.is_file():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("pairs") or raw.get("events") or []
        return [self._row_to_event(r) for r in rows if self._row_to_event(r)]

    def _fixture_path(self) -> Optional[Path]:
        if self.fixture_dir:
            p = self.fixture_dir / "dexscreener_new.json"
            if p.is_file():
                return p
        # bundled fixtures next to tests
        here = Path(__file__).resolve()
        for parent in here.parents:
            cand = parent / "tests" / "fixtures" / "discovery" / "dexscreener_new.json"
            if cand.is_file():
                return cand
        return None

    def _poll_live(self) -> list[DiscoveryEvent]:
        events: list[DiscoveryEvent] = []
        for url in (TOKEN_PROFILES_URL, TOKEN_BOOSTS_URL):
            is_boost = url == TOKEN_BOOSTS_URL
            req = Request(url, headers={"User-Agent": "x-intel-discovery/0.1"})
            with urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310 — public API
                payload = json.loads(resp.read().decode("utf-8"))
            rows = payload if isinstance(payload, list) else []
            for r in rows:
                ev = self._profile_to_event(r, from_boost=is_boost)
                if ev:
                    events.append(ev)
        return events

    def _profile_to_event(
        self, r: dict[str, Any], *, from_boost: bool = False
    ) -> Optional[DiscoveryEvent]:
        chain_id = str(r.get("chainId") or r.get("chain") or "").lower()
        chain = CHAIN_MAP.get(chain_id, chain_id)
        if chain not in self.chains:
            return None
        ca = (r.get("tokenAddress") or r.get("ca") or "").strip()
        if not ca:
            return None
        now = datetime.now(timezone.utc)
        hints: dict[str, Any] = {"dexscreener": True, "thin_dex_new": True}
        if from_boost:
            hints["boost_only"] = True
            hints["paid_boost"] = True
        return DiscoveryEvent(
            source=self.source_id,
            discovered_at=now,
            chain=chain,
            ca=ca,
            ticker=r.get("symbol") or r.get("ticker"),
            raw_ref=r.get("url") or r.get("description") or url_safe(r),
            mc_usd=_f(r.get("mc_usd") or r.get("marketCap")),
            liquidity_usd=_f(r.get("liquidity_usd")),
            event_kind="boost" if from_boost else "new_profile",
            confidence_hints=hints,
        )

    def _row_to_event(self, r: dict[str, Any]) -> Optional[DiscoveryEvent]:
        if not isinstance(r, dict):
            return None
        chain = str(r.get("chain") or "solana").lower()
        ca = (r.get("ca") or r.get("tokenAddress") or r.get("contract_address") or "").strip()
        if not ca:
            return None
        discovered = _parse_dt(r.get("discovered_at") or r.get("pairCreatedAt")) or datetime.now(
            timezone.utc
        )
        pair_created = _parse_dt(r.get("pair_created_at") or r.get("pairCreatedAt"))
        return DiscoveryEvent(
            source=self.source_id,
            discovered_at=discovered,
            chain=chain,
            ca=ca,
            ticker=r.get("ticker") or r.get("symbol"),
            raw_ref=r.get("raw_ref") or r.get("url") or r.get("pairAddress"),
            mc_usd=_f(r.get("mc_usd") or r.get("marketCap")),
            liquidity_usd=_f(r.get("liquidity_usd") or _nested(r, "liquidity", "usd")),
            pair_created_at=pair_created,
            event_kind=r.get("event_kind") or "new_pair",
            confidence_hints=dict(r.get("confidence_hints") or {"dexscreener": True, "thin_dex_new": True}),
        )


def url_safe(r: dict[str, Any]) -> str:
    return str(r.get("url") or "")


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _nested(d: dict, *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        # ms vs s
        ts = float(v)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
