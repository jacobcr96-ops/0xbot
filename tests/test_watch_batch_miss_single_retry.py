"""WATCH refresh: Jupiter batch miss → single-mint retry; offline refresh never
scores dead signals; a batch miss never auto-closes a live buyable WATCH."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import unquote
from uuid import uuid4

import pytest

from x_intel.discovery import enrich
from x_intel.discovery import watch_escalate as we
from x_intel.discovery.watch_escalate import refresh_open_watches, retry_single_mint_jup
from x_intel.io_atomic import atomic_read_json
from x_intel.ledger.store import CandidateLedger, RepoPaths
from x_intel.schemas.models import CandidateDecision, CandidateV1

STAMP = "EKtmPPLaCbEEKiwoHHtV7TsRsmPXs5CMGtQtZFSiinsc"
OURA = "yftbjdPbF1bdmeQTve97rFnYznyZQPSC6iphW34pump"
IOF = "2sY7rkMCQyFNcSpHm3fciJf2ptYRg3cYpn4srN6Bpump"
SUB = "SubFloorSubFloorSubFloorSubFloorSubFlpump"
BUYABLE = {STAMP: 290_000.0, OURA: 3_700_000.0, IOF: 3_400_000.0}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def _asset(mint: str, mcap: Optional[float], *, holders: int = 900, liq: float = 60_000.0) -> dict[str, Any]:
    return {
        "id": mint,
        "symbol": "T",
        "name": "T",
        "mcap": mcap,
        "fdv": mcap,
        "usdPrice": (mcap or 0) / 1e9,
        "liquidity": liq,
        "holderCount": holders,
        "bondingCurve": 100,
        "updatedAt": _iso(datetime.now(timezone.utc) - timedelta(seconds=5)),
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
    monkeypatch.setattr(enrich, "_sleep", lambda s: None)
    enrich.reset_jup_cooldown()
    return data


def _watch(ca: str, mc: float, *, streak: int = 0, holders: Optional[int] = None) -> CandidateV1:
    now = datetime.now(timezone.utc)
    extra: dict[str, Any] = {
        "mc_usd_now": mc,
        "min_mc_usd_seen": mc,
        "refreshed_at": (now - timedelta(minutes=12)).isoformat().replace("+00:00", "Z"),
    }
    if streak:
        extra["watch_dead_streak"] = streak
        extra["watch_dead_signal"] = "no_mc"
    if holders is not None:
        extra["holder_count"] = holders
        extra["liq_usd_now"] = 0.0
    return CandidateV1(
        candidate_id=uuid4(),
        first_seen_at=now - timedelta(days=1),
        contract_address=ca,
        chain="solana",
        ticker=ca[:4],
        mc_usd_at_first_sight=mc,
        decision=CandidateDecision.watch,
        evidence=[],
        source_accounts=["pumpfun_curve"],
        **extra,
    )


def _seed(led: CandidateLedger, *, streak: int = 0, holders: Optional[int] = None) -> None:
    for ca, mc in BUYABLE.items():
        led.save_candidate(_watch(ca, mc, streak=streak, holders=holders))
    led.save_candidate(_watch(SUB, 3_300.0))


def _http429(url: str, retry_after: str) -> HTTPError:
    h = Message()
    h["Retry-After"] = retry_after
    return HTTPError(url, 429, "Too Many Requests", h, None)  # type: ignore[arg-type]


def _query_mints(url: str) -> list[str]:
    return unquote(url.split("query=", 1)[1]).split(",")


def _no_fallbacks():
    return (
        patch("x_intel.discovery.watch_escalate._fetch_pumpfun_mc", return_value=None),
        patch("x_intel.discovery.watch_escalate._fetch_dex_mc", return_value=None),
    )


def _refresh(led, root, fake_get, **kw):
    p1, p2 = _no_fallbacks()
    with patch.object(enrich, "_http_get_json", side_effect=fake_get), p1, p2:
        return refresh_open_watches(ledger=led, data_root=root, live=True, **kw)


def test_batch_empty_single_mint_recovers_all_buyables(tmp_data: Path):
    """Joined query returns [] (the batch failure mode); single-mint works."""
    led = CandidateLedger(RepoPaths(data=tmp_data))
    _seed(led, streak=2)
    urls: list[str] = []

    def fake_get(url: str, *, timeout: float = 10.0) -> Any:
        urls.append(url)
        mints = _query_mints(url)
        if len(mints) > 1:
            return []  # batch: nothing usable
        m = mints[0]
        return [_asset(m, BUYABLE[m] * 1.01)] if m in BUYABLE else []

    fr = _refresh(led, tmp_data, fake_get)
    rows = {r["ca"]: r for r in fr["watches"]}
    for ca in BUYABLE:
        r = rows[ca]
        assert r["mc_source"] == "jupiter" and r["mc_fetch_path"] == "jup_single"
        assert r["refresh_age_sec"] == 0 and r["stale"] is False
        assert r["dead_signal"] is None and r["dead_streak"] == 0  # live print resets
    assert fr["stale_buyable"] == 0 and fr["stale_watch_alert"] is False
    assert fr["mc_source_counts"] == {"jupiter": 3}
    assert fr["mc_fetch_path_counts"] == {"jup_single": 3}
    assert fr["jup_single_stats"]["attempted"] == 3 and fr["jup_single_stats"]["recovered"] == 3
    # single-mint retries only for buyable ones, highest MC first
    singles = [_query_mints(u)[0] for u in urls if len(_query_mints(u)) == 1]
    assert singles == [OURA, IOF, STAMP]
    assert SUB not in singles
    on_disk = atomic_read_json(tmp_data / "health" / "watch_freshness.json")
    assert on_disk["refresh_mode"] == "live" and on_disk["jup_single_stats"]["recovered"] == 3


def test_batch_miss_and_single_429_never_counts_or_closes(tmp_data: Path):
    """Batch misses + single-mint 429s: buyable streak frozen, never auto-closed,
    even with corroborating dead-looking holder/liq data on file."""
    led = CandidateLedger(RepoPaths(data=tmp_data))
    _seed(led, streak=2, holders=1)

    def fake_get(url: str, *, timeout: float = 10.0) -> Any:
        if len(_query_mints(url)) > 1:
            return []
        raise _http429(url, "1")

    for _ in range(5):
        enrich.reset_jup_cooldown()
        fr = _refresh(led, tmp_data, fake_get)
    assert fr["watch_expired_n"] == 0
    rows = {r["ca"]: r for r in fr["watches"]}
    for ca in BUYABLE:
        assert rows[ca]["dead_signal"] is None
        assert rows[ca]["dead_streak"] == 2  # unchanged: no evidence
        assert rows[ca]["dead_signal_suppressed"] in {"single_mint_error", "single_mint_not_tried"}
    assert {c.contract_address for c in led.list_candidates(decision="watch")} >= set(BUYABLE)
    assert fr["jup_single_errors"] and all("HTTP429" in e for e in fr["jup_single_errors"])
    # two consecutive single-mint 429s stop the retry loop (no hammering)
    assert fr["jup_single_stats"]["attempted"] == 2 and fr["jup_single_stats"]["skipped"] == 1
    assert {d["ca"] for d in fr["dead_signal_suppressed"]} == set(BUYABLE)


def test_single_mint_answered_without_mc_counts(tmp_data: Path):
    led = CandidateLedger(RepoPaths(data=tmp_data))
    led.save_candidate(_watch(STAMP, 290_000.0))

    def fake_get(url: str, *, timeout: float = 10.0) -> Any:
        return []  # Jupiter answers: not indexed

    fr = _refresh(led, tmp_data, fake_get)
    r = fr["watches"][0]
    assert r["dead_signal"] == "no_mc" and r["dead_streak"] == 1
    assert fr["jup_single_stats"]["answered_no_mc"] == 1


def test_offline_refresh_never_scores_no_mc(tmp_data: Path):
    """runner once without --live: no fetch, no dead signal, streak untouched."""
    led = CandidateLedger(RepoPaths(data=tmp_data))
    _seed(led, streak=2)
    with patch.object(enrich, "_http_get_json", side_effect=AssertionError("no network offline")):
        fr = refresh_open_watches(ledger=led, data_root=tmp_data, live=False)
    assert fr["refresh_mode"] == "offline" and fr["live"] is False
    assert fr["watch_expired_n"] == 0
    for r in fr["watches"]:
        assert r["dead_signal"] is None
    rows = {r["ca"]: r for r in fr["watches"]}
    assert all(rows[ca]["dead_streak"] == 2 for ca in BUYABLE)
    assert rows[SUB]["dead_streak"] == 0


def test_retry_single_respects_retry_after_and_order(monkeypatch: pytest.MonkeyPatch):
    enrich.reset_jup_cooldown()
    sleeps: list[float] = []
    monkeypatch.setattr(enrich, "_sleep", lambda s: sleeps.append(s))
    calls: list[str] = []
    hit = {"n": 0}

    def fake_get(url: str, *, timeout: float = 10.0) -> Any:
        m = _query_mints(url)[0]
        calls.append(m)
        if m == OURA and hit["n"] == 0:
            hit["n"] += 1
            raise _http429(url, "2")
        return [_asset(m, 1e6)]

    with patch.object(enrich, "_http_get_json", side_effect=fake_get):
        res, stats, errs = retry_single_mint_jup([OURA, IOF, STAMP], gap_sec=0.1)
    assert calls == [OURA, OURA, IOF, STAMP]
    assert all(res[m]["status"] == "ok" for m in (OURA, IOF, STAMP))
    assert stats["http429"] == 1 and stats["recovered"] == 3 and errs == []
    assert any(s >= 2.0 for s in sleeps)  # Retry-After honoured
    assert sleeps.count(0.1) == 2  # small gap between single-mint calls


def test_single_retry_budget(monkeypatch: pytest.MonkeyPatch):
    t = {"now": 0.0}
    monkeypatch.setattr(enrich, "_monotonic", lambda: t["now"])
    monkeypatch.setattr(enrich, "_sleep", lambda s: None)

    def slow_fetch(mints, *, timeout: float = 10.0):
        t["now"] += 10.0
        return {}, ["jup:TimeoutError"]

    with patch.object(we, "fetch_jup_assets", side_effect=slow_fetch):
        res, stats, errs = retry_single_mint_jup([OURA, IOF, STAMP, SUB], budget_sec=15.0)
    assert stats["attempted"] == 2 and stats["skipped"] == 2 and stats["stopped"] == "budget"
    assert all(v["status"] == "error" for v in res.values())
