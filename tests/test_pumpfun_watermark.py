"""Unit tests: pump.fun live poll watermark pagination + cursor advance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from x_intel.discovery.sources.pumpfun_curve import (
    ASSUMED_SCAN_INTERVAL_MS,
    PumpfunCurveSource,
)
from x_intel.io_atomic import atomic_read_json


def _row(mint: str, created_ms: int, *, symbol: str = "T") -> dict[str, Any]:
    return {
        "mint": mint,
        "created_timestamp": created_ms,
        "symbol": symbol,
        "name": symbol,
        "usd_market_cap": 12_000,
        "complete": False,
        "bonding_curve_progress": 0.1,
    }


class _FakePump(PumpfunCurveSource):
    """Inject page responses without HTTP."""

    def __init__(self, pages: list[list[dict[str, Any]]], **kwargs: Any) -> None:
        super().__init__(live=True, **kwargs)
        self._pages = pages
        self.fetch_offsets: list[int] = []

    def _fetch_coins_page(self, offset: int, limit: int) -> list[dict[str, Any]]:
        self.fetch_offsets.append(offset)
        idx = offset // limit if limit else 0
        if idx < 0 or idx >= len(self._pages):
            return []
        return list(self._pages[idx])


@pytest.fixture()
def cursor_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    (d / "health").mkdir(parents=True)
    return d


def test_pagination_stops_at_watermark(cursor_dir: Path):
    """Page until oldest created_timestamp <= watermark; do not fetch further."""
    # Page0: 300,290,280  Page1: 270,260,250  Page2: 240,230,220 (should not fetch if wm=255)
    pages = [
        [_row(f"m0{i}", ts) for i, ts in enumerate([300, 290, 280])],
        [_row(f"m1{i}", ts) for i, ts in enumerate([270, 260, 250])],
        [_row(f"m2{i}", ts) for i, ts in enumerate([240, 230, 220])],
    ]
    src = _FakePump(
        pages,
        data_dir=cursor_dir,
        page_limit=3,
        max_pages=10,
        cursor_path=cursor_dir / "health" / "pump_cursor.json",
    )
    # Seed watermark so page1's oldest (250) <= 255 stops after page1.
    src._save_watermark_ms(255)

    events = src._poll_live_pumpfun_api()
    assert src.fetch_offsets == [0, 3]
    # Coins with created > 255: 300,290,280,270,260 (250 skipped)
    mints = {e.ca for e in events}
    assert mints == {"m00", "m01", "m02", "m10", "m11"}
    assert "m12" not in mints  # 250 <= watermark
    # Watermark advanced to max observed (300)
    cur = atomic_read_json(cursor_dir / "health" / "pump_cursor.json")
    assert cur is not None
    assert cur["created_timestamp"] == 300
    assert cur["pages_fetched"] == 2
    assert cur["miss_risk"] is False


def test_cold_start_lookback_paginates(cursor_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """No cursor → paginate until age coverage (cold lookback), then persist watermark."""
    now_ms = 1_000_000_000_000
    monkeypatch.setattr(
        "x_intel.discovery.sources.pumpfun_curve.time.time",
        lambda: now_ms / 1000.0,
    )
    lookback = 45 * 60 * 1000
    stop = now_ms - lookback

    # Newest page well inside lookback; second page crosses stop.
    pages = [
        [_row(f"n{i}", now_ms - i * 60_000) for i in range(3)],  # 0,1,2 min old
        [
            _row("old_a", stop + 30_000),  # still newer than stop
            _row("old_b", stop - 1_000),  # crosses stop
            _row("old_c", stop - 60_000),
        ],
        [_row("should_not_fetch", stop - 120_000)],
    ]
    src = _FakePump(
        pages,
        data_dir=cursor_dir,
        page_limit=3,
        max_pages=10,
        cold_start_lookback_ms=lookback,
        cursor_path=cursor_dir / "health" / "pump_cursor.json",
    )
    assert src._load_watermark_ms() is None

    events = src._poll_live_pumpfun_api()
    assert src.fetch_offsets == [0, 3]
    mints = {e.ca for e in events}
    assert "n0" in mints and "old_a" in mints
    assert "old_b" not in mints and "old_c" not in mints
    assert "should_not_fetch" not in mints

    cur = atomic_read_json(cursor_dir / "health" / "pump_cursor.json")
    assert cur is not None
    assert cur["created_timestamp"] == now_ms  # max observed
    assert cur["cold_start"] is True


def test_empty_page_stops_and_advances(cursor_dir: Path):
    pages = [
        [_row("a", 500), _row("b", 490)],
        [],  # empty → stop
    ]
    src = _FakePump(
        pages,
        data_dir=cursor_dir,
        page_limit=2,
        max_pages=5,
        cursor_path=cursor_dir / "health" / "pump_cursor.json",
    )
    src._save_watermark_ms(100)
    events = src._poll_live_pumpfun_api()
    assert {e.ca for e in events} == {"a", "b"}
    assert src.fetch_offsets == [0, 2]
    cur = atomic_read_json(cursor_dir / "health" / "pump_cursor.json")
    assert cur["created_timestamp"] == 500


def test_max_pages_safety_and_miss_risk(cursor_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Safety max pages stops pagination; miss_risk when watermark not reached."""
    now_ms = 2_000_000_000_000
    monkeypatch.setattr(
        "x_intel.discovery.sources.pumpfun_curve.time.time",
        lambda: now_ms / 1000.0,
    )
    # All pages newer than watermark=1; each page oldest still within scan interval.
    pages = []
    for p in range(3):
        base = now_ms - p * 10_000  # 10s apart — all within 5m
        pages.append([_row(f"p{p}_{i}", base - i) for i in range(2)])

    src = _FakePump(
        pages,
        data_dir=cursor_dir,
        page_limit=2,
        max_pages=3,
        cursor_path=cursor_dir / "health" / "pump_cursor.json",
    )
    src._save_watermark_ms(1)  # ancient watermark, never reached

    events = src._poll_live_pumpfun_api()
    assert len(src.fetch_offsets) == 3
    assert len(events) == 6
    cur = atomic_read_json(cursor_dir / "health" / "pump_cursor.json")
    assert cur["miss_risk"] is True
    assert cur["created_timestamp"] == now_ms  # still advances to max observed
    assert cur["pages_fetched"] == 3
    # Sanity: ASSUMED_SCAN_INTERVAL used for miss_risk freshness check
    assert ASSUMED_SCAN_INTERVAL_MS == 5 * 60 * 1000


def test_dedupe_by_mint_within_poll(cursor_dir: Path):
    pages = [
        [_row("same", 900), _row("other", 880)],
        [_row("same", 900), _row("third", 700)],  # duplicate mint across pages
    ]
    src = _FakePump(
        pages,
        data_dir=cursor_dir,
        page_limit=2,
        max_pages=5,
        cursor_path=cursor_dir / "health" / "pump_cursor.json",
    )
    src._save_watermark_ms(500)
    events = src._poll_live_pumpfun_api()
    mints = [e.ca for e in events]
    assert mints.count("same") == 1
    assert set(mints) == {"same", "other", "third"}


def test_watermark_not_advanced_on_fetch_failure(cursor_dir: Path):
    """If pagination raises mid-poll, prior watermark stays put."""

    class Boom(_FakePump):
        def _fetch_coins_page(self, offset: int, limit: int) -> list[dict[str, Any]]:
            if offset > 0:
                raise RuntimeError("boom")
            return super()._fetch_coins_page(offset, limit)

    pages = [
        [_row("a", 800)],
        [_row("b", 700)],
    ]
    src = Boom(
        pages,
        data_dir=cursor_dir,
        page_limit=1,
        max_pages=5,
        cursor_path=cursor_dir / "health" / "pump_cursor.json",
    )
    src._save_watermark_ms(100)
    with pytest.raises(RuntimeError, match="boom"):
        src._poll_live_pumpfun_api()
    cur = atomic_read_json(cursor_dir / "health" / "pump_cursor.json")
    assert cur["created_timestamp"] == 100


def test_dex_fallback_when_pump_api_fails(cursor_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep Dex fallback path if pump API fails entirely."""

    class FailPump(PumpfunCurveSource):
        def _poll_live_pumpfun_api(self) -> list:
            raise RuntimeError("pump down")

        def _poll_live_dex(self) -> list:
            from datetime import datetime, timezone
            from x_intel.discovery.models import DiscoveryEvent

            return [
                DiscoveryEvent(
                    source=self.source_id,
                    discovered_at=datetime.now(timezone.utc),
                    chain="solana",
                    ca="DexFallback11111111111111111111111111111",
                    ticker="DEX",
                    event_kind="curve_new",
                    confidence_hints={"pump_curve": True, "dexId": "pumpswap"},
                )
            ]

    src = FailPump(live=True, data_dir=cursor_dir)
    events = src._poll_live()
    assert len(events) == 1
    assert events[0].ca.startswith("DexFallback")


def test_fixture_poll_unchanged():
    """Non-live fixture path still works (regression)."""
    fixtures = Path(__file__).resolve().parent / "fixtures" / "discovery"
    src = PumpfunCurveSource(fixture_dir=fixtures, live=False)
    events = src.poll()
    assert len(events) >= 1
    assert all(e.source == "pumpfun_curve" for e in events)
