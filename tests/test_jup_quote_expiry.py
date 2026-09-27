"""Jupiter datapi primary MC + expired_stale WATCH pruning."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

import pytest

from x_intel.discovery import enrich
from x_intel.discovery.enrich import quote_from_jup_asset, quote_mint
from x_intel.discovery.watch_escalate import WATCH_EXPIRED_STATUS, refresh_open_watches
from x_intel.io_atomic import atomic_read_json
from x_intel.ledger.store import CandidateLedger, RepoPaths
from x_intel.schemas.models import CandidateDecision, CandidateV1

LIVE_MINT = "EKtmPPLaCbEEKiwoHHtV7TsRsmPXs5CMGtQtZFSiinsc"
DEAD_MINT = "8piDkR5tpgS21XuEKvgMcEQd1zMRcBmg2moKFNqgpump"
GONE_MINT = "GoneGoneGoneGoneGoneGoneGoneGoneGoneGpump"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def _asset(mint: str, mcap: float, updated: datetime, symbol: str = "TKN") -> dict[str, Any]:
    return {
        "id": mint,
        "symbol": symbol,
        "name": symbol.title(),
        "mcap": mcap,
        "fdv": mcap,
        "usdPrice": mcap / 1e9,
        "liquidity": 40_000.0,
        "bondingCurve": 100,
        "launchpad": "pump.fun",
        "updatedAt": _iso(updated),
        "stats24h": {"buyVolume": 1000.0, "sellVolume": 500.0},
        "stats1h": {"priceChange": -1.5},
    }


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    for sub in ("candidates", "decisions", "outcomes", "execution_reports", "health"):
        (data / sub).mkdir(parents=True)
    monkeypatch.setenv("XINTEL_DATA_DIR", str(data))
    monkeypatch.setenv("XINTEL_SKIP_DEX", "true")
    monkeypatch.delenv("XINTEL_SKIP_JUP", raising=False)
    return data


def test_jup_asset_quote_live_vs_last_print():
    now = datetime.now(timezone.utc)
    q = quote_from_jup_asset(LIVE_MINT, _asset(LIVE_MINT, 315_000.0, now - timedelta(seconds=5)), now=now)
    assert q["ok"] and q["mc_usd"] == 315_000.0 and q["source"] == "jupiter"
    assert q["price_live"] is True and q["stale"] is False
    assert q["volume_h24"] == 1500.0 and q["complete"] is True
    old = quote_from_jup_asset(DEAD_MINT, _asset(DEAD_MINT, 3_100.0, now - timedelta(days=5)), now=now)
    assert old["mc_usd"] == 3_100.0 and old["price_live"] is False and old["stale"] is True


def test_quote_mint_prefers_jupiter_over_404_pump(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XINTEL_SKIP_DEX", "true")
    monkeypatch.delenv("XINTEL_SKIP_JUP", raising=False)
    now = datetime.now(timezone.utc)
    calls: list[str] = []

    def fake_get(url: str, *, timeout: float = 10.0) -> Any:
        calls.append(url)
        if "datapi.jup.ag" in url:
            return [_asset(LIVE_MINT, 315_000.0, now)]
        raise HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]

    with patch.object(enrich, "_http_get_json", side_effect=fake_get):
        q = quote_mint(LIVE_MINT)
    assert q["ok"] is True and q["source"] == "jupiter" and q["mc_usd"] == 315_000.0
    assert len(calls) == 1 and "datapi.jup.ag" in calls[0]


def test_quote_mint_falls_back_when_jupiter_misses(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XINTEL_SKIP_DEX", "true")

    def fake_get(url: str, *, timeout: float = 10.0) -> Any:
        if "datapi.jup.ag" in url:
            return []  # not indexed
        if "pump.fun" in url:
            return {"usd_market_cap": 12_345.0, "symbol": "P", "name": "P"}
        raise AssertionError(url)

    with patch.object(enrich, "_http_get_json", side_effect=fake_get):
        q = quote_mint(LIVE_MINT)
    assert q["source"] == "pump.fun" and q["mc_usd"] == 12_345.0


def _watch(ca: str, ticker: str, *, refreshed_ago: timedelta | None) -> CandidateV1:
    now = datetime.now(timezone.utc)
    extra: dict[str, Any] = {"mc_usd_now": 3_000.0, "min_mc_usd_seen": 3_000.0}
    if refreshed_ago is not None:
        extra["refreshed_at"] = (now - refreshed_ago).isoformat().replace("+00:00", "Z")
    return CandidateV1(
        candidate_id=uuid4(),
        first_seen_at=now - timedelta(days=7),
        contract_address=ca,
        chain="solana",
        ticker=ticker,
        mc_usd_at_first_sight=3_000.0,
        decision=CandidateDecision.watch,
        evidence=[],
        source_accounts=["pumpfun_curve"],
        **extra,
    )


def test_refresh_batches_jupiter_and_expires_stale(tmp_data: Path):
    led = CandidateLedger(RepoPaths(data=tmp_data))
    live = _watch(LIVE_MINT, "STAMP", refreshed_ago=timedelta(days=6))
    dead = _watch(DEAD_MINT, "bond", refreshed_ago=timedelta(days=6))
    gone = _watch(GONE_MINT, "GONE", refreshed_ago=timedelta(days=6))
    recent_miss = _watch("RecentMissRecentMissRecentMissRecenpump", "RM", refreshed_ago=timedelta(hours=2))
    for c in (live, dead, gone, recent_miss):
        led.save_candidate(c)
    now = datetime.now(timezone.utc)
    jup_calls: list[list[str]] = []

    def fake_jup(mints: list[str], *, timeout: float = 10.0):
        jup_calls.append(list(mints))
        return {
            LIVE_MINT: _asset(LIVE_MINT, 315_000.0, now),
            DEAD_MINT: _asset(DEAD_MINT, 3_165.0, now - timedelta(days=5)),
        }, []

    with patch("x_intel.discovery.watch_escalate.fetch_jup_assets", side_effect=fake_jup), patch(
        "x_intel.discovery.watch_escalate._fetch_pumpfun_mc", return_value=None
    ), patch("x_intel.discovery.watch_escalate._fetch_dex_mc", return_value=None):
        report = refresh_open_watches(ledger=led, data_root=tmp_data, live=True)

    assert len(jup_calls) == 1 and len(jup_calls[0]) == 4  # one batch call
    # Kept: live price now + recently refreshed miss (<24h)
    assert report["watch_count"] == 2
    assert report["watch_expired_n"] == 2
    kept = {r["ca"]: r for r in report["watches"]}
    assert kept[LIVE_MINT]["mc_usd_now"] == 315_000.0
    assert kept[LIVE_MINT]["mc_source"] == "jupiter"
    assert kept[LIVE_MINT]["refresh_age_sec"] == 0
    reasons = {e["ca"]: e["reason"] for e in report["expired_this_cycle"]}
    assert reasons == {DEAD_MINT: "no_trades_24h", GONE_MINT: "no_live_price_24h"}

    # Closed, not deleted: history preserved and out of the open set
    closed = atomic_read_json(tmp_data / "candidates" / f"{dead.candidate_id}.json")
    assert closed["decision"] == "reject"
    assert closed["reject_reason"] == WATCH_EXPIRED_STATUS
    assert closed["watch_status"] == WATCH_EXPIRED_STATUS
    assert closed["prior_decision"] == "watch"
    assert closed["mc_usd_at_first_sight"] == 3_000.0
    assert closed["watch_last_print"]["mc_usd"] == 3_165.0
    assert {str(c.candidate_id) for c in led.list_candidates(decision="watch")} == {
        str(live.candidate_id),
        str(recent_miss.candidate_id),
    }

    hb = atomic_read_json(tmp_data / "health" / "heartbeat.json")
    assert hb["watch_count"] == 2 and hb["watch_expired_n"] == 2
    fr = atomic_read_json(tmp_data / "health" / "watch_freshness.json")
    assert all(r["ca"] not in reasons for r in fr["watches"])


def test_no_expiry_when_not_live(tmp_data: Path):
    led = CandidateLedger(RepoPaths(data=tmp_data))
    old = _watch(GONE_MINT, "GONE", refreshed_ago=timedelta(days=6))
    led.save_candidate(old)
    report = refresh_open_watches(ledger=led, data_root=tmp_data, live=False)
    assert report["watch_count"] == 1 and report["watch_expired_n"] == 0
