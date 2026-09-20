"""pump.fun-style bonding-curve activity via public HTTP / fixtures.

Emits on new curve tokens and on graduation/migrate events.
Live path prefers pump.fun coins feed (watermark-paginated) with Dex fallback.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

from x_intel.config import data_dir
from x_intel.discovery.models import DiscoveryEvent
from x_intel.io_atomic import atomic_read_json, atomic_write_json

log = logging.getLogger(__name__)

DEX_SEARCH = "https://api.dexscreener.com/latest/dex/search?q={q}"
PUMP_COINS_URL = "https://frontend-api-v3.pump.fun/coins"

# Live poll pagination — newest-50 alone only covers ~2 min at ~25 coins/min.
PAGE_LIMIT = 50
MAX_PAGES = 20  # 1000 coins ≈ ~40 min at current velocity
COLD_START_LOOKBACK_MS = 45 * 60 * 1000  # 45 minutes
# Assumed max scan interval for miss_risk (scanner target ≤5 min).
ASSUMED_SCAN_INTERVAL_MS = 5 * 60 * 1000


class PumpfunCurveSource:
    source_id = "pumpfun_curve"

    def __init__(
        self,
        *,
        fixture_dir: Optional[Path] = None,
        live: bool = False,
        timeout_s: float = 8.0,
        data_dir: Optional[Path] = None,  # noqa: A002 — mirrors env name
        cursor_path: Optional[Path] = None,
        page_limit: int = PAGE_LIMIT,
        max_pages: int = MAX_PAGES,
        cold_start_lookback_ms: int = COLD_START_LOOKBACK_MS,
    ) -> None:
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.live = live
        self.timeout_s = timeout_s
        self._data_dir = Path(data_dir) if data_dir else None
        self._cursor_path_override = Path(cursor_path) if cursor_path else None
        self.page_limit = int(page_limit)
        self.max_pages = int(max_pages)
        self.cold_start_lookback_ms = int(cold_start_lookback_ms)

    def poll(self) -> list[DiscoveryEvent]:
        if not self.live:
            return self._poll_fixture()
        try:
            return self._poll_live()
        except Exception as e:  # noqa: BLE001
            log.warning("pumpfun_curve live poll failed: %s — no fixture fallback in live mode", e)
            return []

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
        """Best-effort live curve poll.

        Prefer public pump.fun coins feed for truly new mints; fall back to
        DexScreener search filtered to early age. Never invent market data.
        """
        try:
            events = self._poll_live_pumpfun_api()
            if events:
                return events
        except Exception as e:  # noqa: BLE001
            log.warning("pumpfun_curve pump.fun API failed: %s", e)
        try:
            return self._poll_live_dex()
        except Exception as e:  # noqa: BLE001
            log.warning("pumpfun_curve dex live failed: %s", e)
            return []

    def _cursor_path(self) -> Path:
        if self._cursor_path_override is not None:
            return self._cursor_path_override
        root = self._data_dir or data_dir()
        return root / "health" / "pump_cursor.json"

    def _load_watermark_ms(self) -> Optional[int]:
        raw = atomic_read_json(self._cursor_path())
        if not raw:
            return None
        ts = raw.get("created_timestamp")
        if ts is None:
            return None
        try:
            return int(ts)
        except (TypeError, ValueError):
            return None

    def _save_watermark_ms(self, ts_ms: int, *, meta: Optional[dict[str, Any]] = None) -> None:
        payload: dict[str, Any] = {
            "created_timestamp": int(ts_ms),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if meta:
            payload.update(meta)
        atomic_write_json(self._cursor_path(), payload)

    def _fetch_coins_page(self, offset: int, limit: int) -> list[dict[str, Any]]:
        """Fetch one page from pump.fun coins API. Overridable in tests."""
        url = (
            f"{PUMP_COINS_URL}"
            f"?offset={offset}&limit={limit}"
            f"&sort=created_timestamp&order=DESC&includeNsfw=false"
        )
        req = Request(url, headers={"User-Agent": "x-intel-discovery/0.1"})
        with urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
            rows = json.loads(resp.read().decode("utf-8"))
        if not isinstance(rows, list):
            return []
        return [r for r in rows if isinstance(r, dict)]

    def _poll_live_pumpfun_api(self) -> list[DiscoveryEvent]:
        """Public pump.fun coins feed — watermark-paginated to cover scan gaps.

        Pages offset=0,50,100,… until page oldest <= watermark (or cold-start
        lookback), empty page, or MAX_PAGES. Persists high-water
        created_timestamp under data/health/pump_cursor.json after a successful
        poll so a 5-minute scan cannot permanently miss mints that age out of
        the newest-50 window (~2 min).
        """
        watermark = self._load_watermark_ms()
        now_ms = int(time.time() * 1000)
        cold_start = watermark is None
        stop_ms = (now_ms - self.cold_start_lookback_ms) if cold_start else int(watermark)

        seen_mints: set[str] = set()
        kept_rows: list[dict[str, Any]] = []
        pages_fetched = 0
        max_created: Optional[int] = None
        min_created: Optional[int] = None
        hit_stop = False
        hit_empty = False

        for page_idx in range(self.max_pages):
            offset = page_idx * self.page_limit
            rows = self._fetch_coins_page(offset, self.page_limit)
            pages_fetched += 1
            if not rows:
                hit_empty = True
                break

            page_ts: list[int] = []
            for r in rows:
                cts = _created_timestamp_ms(r.get("created_timestamp"))
                if cts is not None:
                    page_ts.append(cts)
                    if max_created is None or cts > max_created:
                        max_created = cts
                    if min_created is None or cts < min_created:
                        min_created = cts

                ca = (r.get("mint") or "").strip()
                if not ca or ca in seen_mints:
                    continue
                seen_mints.add(ca)
                # Keep coins strictly newer than stop watermark (already-seen at ==).
                if cts is not None and cts <= stop_ms:
                    continue
                kept_rows.append(r)

            page_oldest = min(page_ts) if page_ts else None
            if page_oldest is not None and page_oldest <= stop_ms:
                hit_stop = True
                break

        # miss_risk: hit safety max pages without reaching watermark/lookback
        # (oldest page may still be newer than the typical scan interval).
        miss_risk = pages_fetched >= self.max_pages and not hit_stop and not hit_empty
        if miss_risk and min_created is not None:
            age_ms = now_ms - min_created
            if age_ms < ASSUMED_SCAN_INTERVAL_MS:
                log.warning(
                    "pumpfun_curve miss_risk: oldest fetched coin only %.1fs old; "
                    "gap may exceed coverage (pages=%d max=%d)",
                    age_ms / 1000.0,
                    pages_fetched,
                    self.max_pages,
                )

        window_minutes: Optional[float] = None
        if max_created is not None and min_created is not None:
            window_minutes = max(0.0, (max_created - min_created) / 60_000.0)

        events: list[DiscoveryEvent] = []
        now = datetime.now(timezone.utc)
        for r in kept_rows:
            ev = self._api_row_to_event(r, now=now)
            if ev:
                events.append(ev)

        log.info(
            "pumpfun_curve pump.fun API → pages_fetched=%d window_minutes=%s "
            "n_events=%d miss_risk=%s cold_start=%s watermark=%s stop_ms=%s",
            pages_fetched,
            f"{window_minutes:.2f}" if window_minutes is not None else "n/a",
            len(events),
            miss_risk,
            cold_start,
            watermark,
            stop_ms,
        )

        # Advance high-water only after a successful complete poll return.
        if max_created is not None:
            self._save_watermark_ms(
                max_created,
                meta={
                    "pages_fetched": pages_fetched,
                    "n_events": len(events),
                    "window_minutes": window_minutes,
                    "miss_risk": miss_risk,
                    "cold_start": cold_start,
                    "min_created_timestamp": min_created,
                },
            )

        return events

    def _api_row_to_event(
        self, r: dict[str, Any], *, now: datetime
    ) -> Optional[DiscoveryEvent]:
        ca = (r.get("mint") or "").strip()
        if not ca:
            return None
        created_ms = r.get("created_timestamp")
        pair_created = None
        cts = _created_timestamp_ms(created_ms)
        if cts is not None:
            try:
                pair_created = datetime.fromtimestamp(cts / 1000.0, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                pair_created = None
        usd_mc = r.get("usd_market_cap") or r.get("market_cap_usd")
        prog = r.get("bonding_curve_progress") or r.get("progress")
        complete = r.get("complete")
        kind = "graduate" if complete is True else "curve_new"
        hints = {
            "pump_curve": True,
            "pumpfun_api": True,
            "complete": complete,
            "name": r.get("name"),
            "symbol": r.get("symbol"),
        }
        tw = (r.get("twitter") or "").strip()
        tg = (r.get("telegram") or "").strip()
        if tw:
            hints["pump_twitter"] = tw
        if tg:
            hints["pump_telegram"] = tg
        return DiscoveryEvent(
            source=self.source_id,
            discovered_at=now,
            chain="solana",
            ca=ca,
            ticker=r.get("symbol") or r.get("name"),
            name=r.get("name"),
            symbol=r.get("symbol"),
            raw_ref=f"https://pump.fun/{ca}",
            mc_usd=float(usd_mc) if usd_mc is not None else None,
            liquidity_usd=None,
            curve_progress=float(prog) if prog is not None else (1.0 if complete else None),
            pair_created_at=pair_created,
            event_kind=kind,
            confidence_hints=hints,
        )

    def _poll_live_dex(self) -> list[DiscoveryEvent]:
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
            # Skip stale dex search hits (not early-mover)
            if pair_created is not None:
                age_m = (now - pair_created).total_seconds() / 60.0
                if age_m > 180:
                    continue
            liq = (p.get("liquidity") or {}).get("usd")
            mc = p.get("marketCap") or p.get("fdv")
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


def _created_timestamp_ms(v: Any) -> Optional[int]:
    """Normalize pump created_timestamp to integer milliseconds.

    Pump.fun API returns ms (~1.7e12). Treat >=1e11 as ms so lookback-window
    timestamps near that boundary are not mis-scaled as seconds.
    """
    if v is None:
        return None
    try:
        ts = float(v)
    except (TypeError, ValueError):
        return None
    if ts >= 1e11:  # milliseconds (post-~1973)
        return int(ts)
    if ts >= 1e9:  # unix seconds
        return int(ts * 1000.0)
    return int(ts)  # small/test values: leave as-is


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
