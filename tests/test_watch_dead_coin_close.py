"""Dead-coin early close of WATCHes (LAST_PRINT / null MC + holders/liq/floor)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch
from uuid import uuid4

import pytest

from x_intel.discovery.watch_escalate import (
    WATCH_EXPIRED_STATUS,
    evaluate_dead_watch,
    refresh_open_watches,
)
from x_intel.io_atomic import atomic_read_json
from x_intel.ledger.store import CandidateLedger, RepoPaths
from x_intel.schemas.models import CandidateDecision, CandidateV1

PUMPAY = "GfBVvMiPDeBMYaqx7pAJuPgmbDV3u1BXNf6JN2pRpump"
STAMP = "EKtmPPLaCbEEKiwoHHtV7TsRsmPXs5CMGtQtZFSiinsc"
OURA = "OuraOuraOuraOuraOuraOuraOuraOuraOuraOpump"
SUBFLOOR_LIVE = "SubFloorLiveSubFloorLiveSubFloorLivepump"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.123456Z")


def _asset(mint: str, mcap: Optional[float], updated: datetime, *, holders: int, liq: float) -> dict[str, Any]:
    return {
        "id": mint,
        "symbol": "T",
        "name": "T",
        "mcap": mcap,
        "fdv": mcap,
        "usdPrice": (mcap or 0) / 1e9,
        "liquidity": liq,
        "holderCount": holders,
        "bondingCurve": 50,
        "updatedAt": _iso(updated),
    }


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    for sub in ("candidates", "decisions", "outcomes", "execution_reports", "health"):
        (data / sub).mkdir(parents=True)
    monkeypatch.setenv("XINTEL_DATA_DIR", str(data))
    monkeypatch.setenv("XINTEL_SKIP_DEX", "true")
    monkeypatch.delenv("XINTEL_SKIP_JUP", raising=False)
    monkeypatch.delenv("XINTEL_WATCH_BUY_MIN_MC", raising=False)
    return data


def _watch(ca: str, ticker: str, mc: float, *, refreshed_ago: timedelta = timedelta(minutes=20)) -> CandidateV1:
    now = datetime.now(timezone.utc)
    return CandidateV1(
        candidate_id=uuid4(),
        first_seen_at=now - timedelta(days=7),
        contract_address=ca,
        chain="solana",
        ticker=ticker,
        mc_usd_at_first_sight=mc,
        decision=CandidateDecision.watch,
        evidence=[],
        source_accounts=["pumpfun_curve"],
        mc_usd_now=mc,
        min_mc_usd_seen=mc,
        refreshed_at=(now - refreshed_ago).isoformat().replace("+00:00", "Z"),
    )


def _run(led: CandidateLedger, root: Path, assets: dict[str, dict[str, Any]], errs: list[str] | None = None):
    def fake_jup(mints: list[str], *, timeout: float = 10.0):
        return {m: a for m, a in assets.items() if m in mints}, list(errs or [])

    with patch("x_intel.discovery.watch_escalate.fetch_jup_assets", side_effect=fake_jup), patch(
        "x_intel.discovery.watch_escalate._fetch_pumpfun_mc", return_value=None
    ), patch("x_intel.discovery.watch_escalate._fetch_dex_mc", return_value=None):
        return refresh_open_watches(ledger=led, data_root=root, live=True)


def test_evaluate_dead_watch_pure():
    # LAST_PRINT needs 2 consecutive + corroboration
    assert evaluate_dead_watch(kind="last_print", streak=1, holder_count=1, liq_usd=0, mc_usd=4000) is None
    assert evaluate_dead_watch(kind="last_print", streak=2, holder_count=1, liq_usd=0, mc_usd=4000)
    # null MC needs 3
    assert evaluate_dead_watch(kind="no_mc", streak=2, holder_count=None, liq_usd=None, mc_usd=4000) is None
    assert evaluate_dead_watch(kind="no_mc", streak=3, holder_count=None, liq_usd=None, mc_usd=4000)
    # real coin: no corroborating dead signal → never closed
    for mc in (300_000.0, 900_000.0, 3_600_000.0):
        assert evaluate_dead_watch(kind="no_mc", streak=10, holder_count=500, liq_usd=50_000, mc_usd=mc) is None
        assert evaluate_dead_watch(kind="last_print", streak=10, holder_count=500, liq_usd=50_000, mc_usd=mc) is None
    assert evaluate_dead_watch(kind=None, streak=99, holder_count=1, liq_usd=0, mc_usd=1) is None


def test_pumpay_last_print_closes_real_coins_stay(tmp_data: Path):
    led = CandidateLedger(RepoPaths(data=tmp_data))
    now = datetime.now(timezone.utc)
    pumpay = _watch(PUMPAY, "PUMPAY", 4_182.0)
    stamp = _watch(STAMP, "STAMP", 307_000.0)
    oura = _watch(OURA, "OURA", 3_560_000.0)
    sub = _watch(SUBFLOOR_LIVE, "SUB", 3_400.0)
    for c in (pumpay, stamp, oura, sub):
        led.save_candidate(c)
    assets = {
        PUMPAY: _asset(PUMPAY, 4_182.0, now - timedelta(hours=24, minutes=12), holders=1, liq=0.0),
        STAMP: _asset(STAMP, 307_000.0, now - timedelta(seconds=10), holders=900, liq=60_000.0),
        # STAMP-like real coin whose print is momentarily old: must not close
        OURA: _asset(OURA, 3_560_000.0, now - timedelta(hours=30), holders=5000, liq=400_000.0),
        SUBFLOOR_LIVE: _asset(SUBFLOOR_LIVE, 3_400.0, now - timedelta(seconds=30), holders=40, liq=5_000.0),
    }
    r1 = _run(led, tmp_data, assets)
    assert r1["watch_expired_n"] == 0  # one LAST_PRINT refresh is not enough
    assert {w["ca"]: w["dead_streak"] for w in r1["watches"]}[PUMPAY] == 1
    r2 = _run(led, tmp_data, assets)
    assert [e["ca"] for e in r2["expired_this_cycle"]] == [PUMPAY]
    assert r2["expired_this_cycle"][0]["reason"].startswith("dead_coin_last_print")
    assert r2["watch_count"] == 3
    open_cas = {w["ca"] for w in r2["watches"]}
    assert open_cas == {STAMP, OURA, SUBFLOOR_LIVE}

    closed = atomic_read_json(tmp_data / "candidates" / f"{pumpay.candidate_id}.json")
    assert closed["decision"] == "reject"
    assert closed["reject_reason"] == WATCH_EXPIRED_STATUS == closed["watch_status"]
    assert closed["prior_decision"] == "watch"
    assert closed["watch_last_print"]["mc_usd"] == 4_182.0

    # Closed watch never counts toward freshness stale counts
    fr = atomic_read_json(tmp_data / "health" / "watch_freshness.json")
    assert all(w["ca"] != PUMPAY for w in fr["watches"])
    # OURA's print is old (not live) so it ages, but it is still open
    assert fr["watch_count"] == 3


def test_null_mc_streak_and_outage_guard(tmp_data: Path):
    led = CandidateLedger(RepoPaths(data=tmp_data))
    now = datetime.now(timezone.utc)
    dead = _watch(PUMPAY, "PUMPAY", 4_000.0)
    stamp = _watch(STAMP, "STAMP", 307_000.0)
    for c in (dead, stamp):
        led.save_candidate(c)
    # Jupiter batch outage: nothing counts toward a buyable WATCH's streak
    for _ in range(4):
        r = _run(led, tmp_data, {}, errs=["jup:http_503"])
        assert {w["ca"]: w["dead_streak"] for w in r["watches"]}[STAMP] == 0
    # Source answers with no MC for STAMP several refreshes in a row
    for _ in range(2):
        r = _run(led, tmp_data, {})
        assert r["watch_expired_n"] == 0
    # A live print resets the streak for STAMP
    r = _run(led, tmp_data, {STAMP: _asset(STAMP, 305_000.0, now, holders=900, liq=60_000.0)})
    assert r["watches"][0]["dead_streak"] == 0
    # STAMP: many consecutive null MC refreshes never close it (real MC ≥ floor)
    for _ in range(6):
        r = _run(led, tmp_data, {})
    assert r["watch_expired_n"] == 0 and [w["ca"] for w in r["watches"]] == [STAMP]
    assert r["watches"][0]["dead_streak"] == 6
    # PUMPAY (sub-floor) was closed during the outage: all sources failed 3x
    closed = atomic_read_json(tmp_data / "candidates" / f"{dead.candidate_id}.json")
    assert closed["decision"] == "reject" and closed["watch_status"] == WATCH_EXPIRED_STATUS
    assert "dead_coin_no_mc_all_sources streak=3" in closed["watch_close_reason"]


def test_subfloor_outage_closes_after_three_all_source_failures(tmp_data: Path):
    """Jupiter 429 + pump/Dex empty: sub-floor dead coin closes on refresh 3; buyable never."""
    led = CandidateLedger(RepoPaths(data=tmp_data))
    dead = _watch(PUMPAY, "NGGR", 3_356.0)
    stamp = _watch(STAMP, "STAMP", 293_000.0)
    for c in (dead, stamp):
        led.save_candidate(c)
    for i in (1, 2):
        r = _run(led, tmp_data, {}, errs=["jup:HTTP429"])
        assert r["watch_expired_n"] == 0
        streaks = {w["ca"]: (w["dead_signal"], w["dead_streak"]) for w in r["watches"]}
        assert streaks[PUMPAY] == ("no_mc_all_sources", i)
        assert streaks[STAMP] == (None, 0)
    r = _run(led, tmp_data, {}, errs=["jup:HTTP429"])
    assert [e["ca"] for e in r["expired_this_cycle"]] == [PUMPAY]
    assert "mc_below_floor" in r["expired_this_cycle"][0]["reason"]
    assert [w["ca"] for w in r["watches"]] == [STAMP]


def test_subfloor_streak_resets_when_a_source_quotes_it(tmp_data: Path):
    led = CandidateLedger(RepoPaths(data=tmp_data))
    now = datetime.now(timezone.utc)
    sub = _watch(SUBFLOOR_LIVE, "SUB", 3_400.0)
    led.save_candidate(sub)
    for _ in range(2):
        _run(led, tmp_data, {}, errs=["jup:HTTP429"])
    r = _run(led, tmp_data, {SUBFLOOR_LIVE: _asset(SUBFLOOR_LIVE, 3_300.0, now, holders=40, liq=5_000.0)})
    assert r["watch_expired_n"] == 0 and r["watches"][0]["dead_streak"] == 0
    r = _run(led, tmp_data, {}, errs=["jup:HTTP429"])
    assert r["watch_expired_n"] == 0 and r["watches"][0]["dead_streak"] == 1
