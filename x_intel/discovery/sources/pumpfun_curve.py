"""pump.fun-style bonding-curve activity via public HTTP / fixtures.

Emits on new curve tokens and on graduation/migrate events.
Live path prefers DexScreener search for pump.fun pairs when enabled.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

from x_intel.discovery.models import DiscoveryEvent

log = logging.getLogger(__name__)

DEX_SEARCH = "https://api.dexscreener.com/latest/dex/search?q={q}"


class PumpfunCurveSource:
    source_id = "pumpfun_curve"

    def __init__(
        self,
        *,
        fixture_dir: Optional[Path] = None,
        live: bool = False,
        timeout_s: float = 8.0,
    ) -> None:
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.live = live
        self.timeout_s = timeout_s

    def poll(self) -> list[DiscoveryEvent]:
        if not self.live:
            return self._poll_fixture()
        try:
            return self._poll_live()
        except Exception as e:  # noqa: BLE001
            log.warning("pumpfun_curve live poll failed: %s", e)
            return self._poll_fixture()

    def _fixture_path(self) -> Optional[Path]:
        if self.fixture_dir:
            p = self.fixture_dir / "pumpfun_curve.json"
            if p.is_file():
                return p
        here = Path(__file__).resolve()
        for parent in here.parents:
            cand = parent / "tests" / "fixtures" / "discovery" / "pumpfun_curve.json"
            if cand.is_file():
                return cand
        return None

    def _poll_fixture(self) -> list[DiscoveryEvent]:
        path = self._fixture_path()
        if path is None or not path.is_file():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("events") or []
        out: list[DiscoveryEvent] = []
        for r in rows:
            ev = self._row_to_event(r)
            if ev:
                out.append(ev)
        return out

    def _poll_live(self) -> list[DiscoveryEvent]:
        """Best-effort: DexScreener search for recent pump.fun pairs."""
        url = DEX_SEARCH.format(q=quote("pump.fun"))
        req = Request(url, headers={"User-Agent": "x-intel-discovery/0.1"})
        with urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        pairs = payload.get("pairs") or []
        events: list[DiscoveryEvent] = []
        now = datetime.now(timezone.utc)
        for p in pairs:
            if str(p.get("chainId") or "").lower() != "solana":
                continue
            base = p.get("baseToken") or {}
            ca = (base.get("address") or "").strip()
            if not ca:
                continue
            created_ms = p.get("pairCreatedAt")
            pair_created = None
            if created_ms:
                pair_created = datetime.fromtimestamp(float(created_ms) / 1000.0, tz=timezone.utc)
            liq = (p.get("liquidity") or {}).get("usd")
            mc = p.get("marketCap") or p.get("fdv")
            # Heuristic graduation: raydium dexId after pump
            dex_id = str(p.get("dexId") or "").lower()
            kind = "graduate" if dex_id in {"raydium", "pumpswap"} and ca.endswith("pump") else "curve_new"
            events.append(
                DiscoveryEvent(
                    source=self.source_id,
                    discovered_at=now,
                    chain="solana",
                    ca=ca,
                    ticker=base.get("symbol"),
                    raw_ref=p.get("url") or p.get("pairAddress"),
                    mc_usd=float(mc) if mc is not None else None,
                    liquidity_usd=float(liq) if liq is not None else None,
                    curve_progress=1.0 if kind == "graduate" else None,
                    pair_created_at=pair_created,
                    event_kind=kind,
                    confidence_hints={"pump_curve": True, "dexId": dex_id},
                )
            )
        return events

    def _row_to_event(self, r: dict[str, Any]) -> Optional[DiscoveryEvent]:
        if not isinstance(r, dict):
            return None
        ca = (r.get("ca") or r.get("mint") or "").strip()
        if not ca:
            return None
        discovered = _parse_dt(r.get("discovered_at")) or datetime.now(timezone.utc)
        return DiscoveryEvent(
            source=self.source_id,
            discovered_at=discovered,
            chain=str(r.get("chain") or "solana"),
            ca=ca,
            ticker=r.get("ticker") or r.get("symbol"),
            raw_ref=r.get("raw_ref") or r.get("url"),
            mc_usd=_f(r.get("mc_usd")),
            curve_progress=_f(r.get("curve_progress")),
            liquidity_usd=_f(r.get("liquidity_usd")),
            pair_created_at=_parse_dt(r.get("pair_created_at")),
            event_kind=r.get("event_kind") or "curve_new",
            confidence_hints=dict(r.get("confidence_hints") or {"pump_curve": True}),
        )


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
