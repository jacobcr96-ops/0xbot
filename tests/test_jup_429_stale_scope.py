"""Jupiter 429 retry/backoff + buyable-only stale-watch alert scope."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

import pytest

from x_intel.discovery import enrich
from x_intel.discovery.watch_escalate import order_watches_for_refresh, refresh_open_watches
from x_intel.io_atomic import atomic_read_json
from x_intel.ledger.store import CandidateLedger, RepoPaths
from x_intel.schemas.models import CandidateDecision, CandidateV1


def _mint(tag: str) -> str:
    return (tag * 44)[:40] + "pump"


def _http429(url: str, retry_after: Optional[str] = None) -> HTTPError:
    hdrs = Message()
    if retry_after is not None:
        hdrs["Retry-After"] = retry_after
    return HTTPError(url, 429, "Too Many Requests", hdrs, None)  # type: ignore[arg-type]


def _asset(mint: str, mcap: float, *, updated: Optional[datetime] = None) -> dict[str, Any]:
    updated = updated or datetime.now(timezone.utc)
    return {
        "id": mint,
        "symbol": "T",
        "mcap": mcap,
        "liquidity": 50_000.0,
        "holderCount": 500,
        "updatedAt": updated.strftime("%Y-%m-%dT%H:%M:%S.123456Z"),
    }


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def mono(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(enrich, "_monotonic", c.mono)
    monkeypatch.setattr(enrich, "_sleep", c.sleep)
    return c


def _query_mints(url: str) -> list[str]:
    return url.split("query=", 1)[1].split(",")


# --------------------------------------------------------------------------
# fetch_jup_assets 429 handling
# --------------------------------------------------------------------------


def test_429_respects_retry_after_and_halves_batch(clock: _Clock):
    mints = [_mint(chr(65 + i)) for i in range(20)]
    calls: list[list[str]] = []

    def fake_get(url: str, *, timeout: float = 10.0) -> Any:
        q = _query_mints(url)
        calls.append(q)
        if len(calls) == 1:
            raise _http429(url, retry_after="2")
        return [_asset(m, 50_000.0) for m in q]

    with patch.object(enrich, "_http_get_json", side_effect=fake_get):
        out, errs = enrich.fetch_jup_assets(mints)
    assert errs == [] and set(out) == set(mints)
    assert clock.sleeps == [2.0]  # Retry-After honoured exactly
    assert [len(c) for c in calls] == [20, 10, 10]  # smaller batch on retry
    assert calls[1] == mints[:10]  # priority order kept (first half first)
    st = enrich.JUP_LAST_STATS
    assert st["http429"] == 1 and st["retries"] == 1 and st["recovered"] and not st["gave_up"]


def test_429_exponential_backoff_with_jitter_bounded_tries(clock: _Clock):
    mints = [_mint("Q")]
    n = {"calls": 0}

    def always_429(url: str, *, timeout: float = 10.0) -> Any:
        n["calls"] += 1
        raise _http429(url)

    with patch.object(enrich, "_http_get_json", side_effect=always_429):
        out, errs = enrich.fetch_jup_assets(mints)
    assert out == {} and errs == ["jup:HTTP429"]
    assert n["calls"] == enrich.JUP_MAX_TRIES == 3
    assert len(clock.sleeps) == 2
    b = enrich.JUP_BACKOFF_BASE_SEC
    j = enrich.JUP_BACKOFF_JITTER_SEC
    assert b <= clock.sleeps[0] <= b + j
    assert 2 * b <= clock.sleeps[1] <= 2 * b + j
    assert sum(clock.sleeps) <= enrich.JUP_RETRY_BUDGET_SEC
    assert enrich.JUP_LAST_STATS["gave_up"] and enrich.JUP_LAST_STATS["unanswered"] == 1


def test_429_long_retry_after_gives_up_without_sleeping(clock: _Clock):
    def fake_get(url: str, *, timeout: float = 10.0) -> Any:
        raise _http429(url, retry_after="60")

    with patch.object(enrich, "_http_get_json", side_effect=fake_get) as m:
        out, errs = enrich.fetch_jup_assets([_mint("L")])
    assert errs == ["jup:HTTP429"] and m.call_count == 1 and clock.sleeps == []
    # cooldown: subsequent per-mint quote skips Jupiter entirely (no hammering)
    with patch.object(enrich, "_http_get_json", side_effect=AssertionError("no call")):
        out2, errs2 = enrich.fetch_jup_assets([_mint("M")], max_tries=1)
    assert out2 == {} and errs2 == ["jup:HTTP429_cooldown"]
    clock.t += 61
    with patch.object(enrich, "_http_get_json", return_value=[_asset(_mint("M"), 1.0)]):
        out3, errs3 = enrich.fetch_jup_assets([_mint("M")], max_tries=1)
    assert errs3 == [] and _mint("M") in out3


def test_per_mint_quote_path_never_retries(clock: _Clock, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XINTEL_SKIP_DEX", "true")
    calls: list[str] = []

    def fake_get(url: str, *, timeout: float = 10.0) -> Any:
        calls.append(url)
        if "datapi.jup.ag" in url:
            raise _http429(url)
        return {"usd_market_cap": 12_345.0, "symbol": "P", "name": "P"}

    with patch.object(enrich, "_http_get_json", side_effect=fake_get):
        q = enrich.quote_mint(_mint("P"))
    assert q["source"] == "pump.fun" and clock.sleeps == []
    assert sum("datapi.jup.ag" in u for u in calls) == 1


def test_retry_after_http_date_parsed():
    hdrs = Message()
    when = datetime.now(timezone.utc) + timedelta(seconds=3)
    hdrs["Retry-After"] = when.strftime("%a, %d %b %Y %H:%M:%S GMT")
    v = enrich._parse_retry_after(hdrs)
    assert v is not None and 0.0 <= v <= 4.0


# --------------------------------------------------------------------------
# Watch refresh ordering + stale scope
# --------------------------------------------------------------------------


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


def _watch(ca: str, ticker: str, mc: float, *, refreshed_ago: timedelta) -> CandidateV1:
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


DEAD = {"ZMERTCOINS": 3_407.0, "autism": 3_141.0, "NUT": 3_798.0, "NGGR": 3_356.0, "PumpFi": 4_468.0}
BUYABLE = {"STAMP": 293_000.0, "OURA": 3_508_000.0, "IOF": 3_585_000.0, "USOS": 920_000.0}


def _seed(led: CandidateLedger) -> dict[str, str]:
    cas: dict[str, str] = {}
    for i, (t, mc) in enumerate({**DEAD, **BUYABLE}.items()):
        ca = _mint(chr(66 + i))
        cas[t] = ca
        led.save_candidate(_watch(ca, t, mc, refreshed_ago=timedelta(minutes=14)))
    return cas


def test_order_buyable_first(tmp_data: Path):
    led = CandidateLedger(RepoPaths(data=tmp_data))
    _seed(led)
    order = [c.ticker for c in order_watches_for_refresh(led.list_candidates(decision="watch"))]
    assert order[:4] == ["IOF", "OURA", "USOS", "STAMP"]
    assert set(order[4:]) == set(DEAD)


def test_subfloor_stale_never_alerts_buyable_stale_does(tmp_data: Path):
    led = CandidateLedger(RepoPaths(data=tmp_data))
    cas = _seed(led)
    jup_calls: list[list[str]] = []

    # Jupiter 429 (unrecovered); Dex fallback quotes the buyables only
    def fake_jup(mints: list[str], *, timeout: float = 10.0):
        jup_calls.append(list(mints))
        return {}, ["jup:HTTP429"]

    by_ca = {cas[t]: mc for t, mc in BUYABLE.items()}
    with patch("x_intel.discovery.watch_escalate.fetch_jup_assets", side_effect=fake_jup), patch(
        "x_intel.discovery.watch_escalate._fetch_pumpfun_mc", return_value=None
    ), patch(
        "x_intel.discovery.watch_escalate._fetch_dex_mc",
        side_effect=lambda ca, **k: by_ca.get(ca),
    ):
        fr = refresh_open_watches(ledger=led, data_root=tmp_data, live=True)

    # buyable mints first in the Jupiter batch
    assert set(jup_calls[0][:4]) == set(by_ca)
    assert fr["watch_count"] == 9
    assert fr["stale_buyable"] == fr["stale"] == fr["watch_stale_count"] == 0
    assert fr["stale_subfloor"] == 5 and fr["stale_total"] == 5
    assert fr["stale_watch_alert"] is False
    assert fr["oldest_watch_refresh_age_sec"] == 0  # buyable-only
    assert fr["oldest_watch_refresh_age_sec_all"] > 600
    rows = {r["ticker"]: r for r in fr["watches"]}
    for t in DEAD:
        assert rows[t]["stale"] is False and rows[t]["refresh_stale"] is True
        assert rows[t]["subfloor"] is True and rows[t]["dead_signal"] == "no_mc_all_sources"
    hb = atomic_read_json(tmp_data / "health" / "heartbeat.json")
    assert hb["watch_stale_count"] == 0 and hb["watch_stale_buyable"] == 0
    assert hb["watch_stale_subfloor"] == 5 and hb["stale_watch_alert"] is False

    # Now a buyable one goes unquoted too → alert (and only it counts)
    by_ca.pop(cas["STAMP"])
    with patch("x_intel.discovery.watch_escalate.fetch_jup_assets", side_effect=fake_jup), patch(
        "x_intel.discovery.watch_escalate._fetch_pumpfun_mc", return_value=None
    ), patch(
        "x_intel.discovery.watch_escalate._fetch_dex_mc",
        side_effect=lambda ca, **k: by_ca.get(ca),
    ), patch(
        "x_intel.discovery.watch_escalate._now",
        return_value=datetime.now(timezone.utc) + timedelta(minutes=11),
    ):
        fr2 = refresh_open_watches(ledger=led, data_root=tmp_data, live=True, expire_stale=False)
    assert fr2["stale_buyable"] == 1 and fr2["stale_watch_alert"] is True
    stale_rows = [r["ticker"] for r in fr2["watches"] if r["stale"]]
    assert stale_rows == ["STAMP"]
    hb2 = atomic_read_json(tmp_data / "health" / "heartbeat.json")
    assert hb2["stale_watch_alert"] is True and hb2["watch_stale_buyable"] == 1


def test_five_dead_coins_close_after_three_outage_refreshes(tmp_data: Path):
    led = CandidateLedger(RepoPaths(data=tmp_data))
    cas = _seed(led)
    by_ca = {cas[t]: mc for t, mc in BUYABLE.items()}

    def fake_jup(mints: list[str], *, timeout: float = 10.0):
        return {}, ["jup:HTTP429"]

    for i in range(3):
        with patch("x_intel.discovery.watch_escalate.fetch_jup_assets", side_effect=fake_jup), patch(
            "x_intel.discovery.watch_escalate._fetch_pumpfun_mc", return_value=None
        ), patch(
            "x_intel.discovery.watch_escalate._fetch_dex_mc",
            side_effect=lambda ca, **k: by_ca.get(ca),
        ):
            fr = refresh_open_watches(ledger=led, data_root=tmp_data, live=True)
    assert fr["watch_expired_n"] == 5
    assert {e["ticker"] for e in fr["expired_this_cycle"]} == set(DEAD)
    assert {r["ticker"] for r in fr["watches"]} == set(BUYABLE)
    assert fr["stale_subfloor"] == 0 and fr["stale_buyable"] == 0


def test_runner_heartbeat_carries_buyable_only_fields(tmp_path: Path):
    from x_intel.discovery.runner import _write_cycle_heartbeat

    _write_cycle_heartbeat(
        tmp_path,
        enrich_ok_n=1,
        enrich_n=1,
        enrich_ok_rate=1.0,
        pursue_quality_n=0,
        n_results=1,
        watch_summary={
            "watch_count": 34,
            "watch_stale_count": 0,
            "watch_stale_buyable": 0,
            "watch_stale_subfloor": 5,
            "stale_watch_alert": False,
            "oldest_watch_refresh_age_sec": 0.0,
            "oldest_watch_refresh_age_sec_all": 874.0,
            "jup_errors": ["jup:HTTP429"],
            "jup_stats": {"http429": 3, "gave_up": True},
        },
        live=True,
    )
    hb = atomic_read_json(tmp_path / "health" / "heartbeat.json")
    assert hb["watch_stale_count"] == 0 and hb["stale_watch_alert"] is False
    assert hb["watch_stale_subfloor"] == 5 and hb["oldest_watch_refresh_age_sec_all"] == 874.0
    assert hb["watch_jup_stats"]["http429"] == 3


def test_retry_after_zero_still_backs_off(clock: _Clock):
    """datapi sends Retry-After: 0 — never hammer: backoff floor applies."""
    calls = {"n": 0}

    def fake_get(url: str, *, timeout: float = 10.0) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http429(url, retry_after="0")
        return [_asset(m, 1.0) for m in _query_mints(url)]

    with patch.object(enrich, "_http_get_json", side_effect=fake_get):
        out, errs = enrich.fetch_jup_assets([_mint("Z")])
    assert errs == [] and len(out) == 1
    assert enrich.JUP_BACKOFF_BASE_SEC <= clock.sleeps[0] <= (
        enrich.JUP_BACKOFF_BASE_SEC + enrich.JUP_BACKOFF_JITTER_SEC
    )


def test_dex_error_during_jup_outage_is_not_dead_evidence(tmp_data: Path):
    led = CandidateLedger(RepoPaths(data=tmp_data))
    cas = _seed(led)

    def fake_jup(mints: list[str], *, timeout: float = 10.0):
        return {}, ["jup:HTTP429"]

    def dex_429(ca: str, *, status: Optional[dict] = None, **k: Any) -> None:
        if status is not None:
            status["answered"] = False  # Dex rate-limited too
        return None

    for _ in range(4):
        with patch("x_intel.discovery.watch_escalate.fetch_jup_assets", side_effect=fake_jup), patch(
            "x_intel.discovery.watch_escalate._fetch_pumpfun_mc", return_value=None
        ), patch("x_intel.discovery.watch_escalate._fetch_dex_mc", side_effect=dex_429):
            fr = refresh_open_watches(ledger=led, data_root=tmp_data, live=True)
    assert fr["watch_expired_n"] == 0 and fr["watch_count"] == len(cas)
    assert all(r["dead_streak"] == 0 for r in fr["watches"])


def test_per_mint_cooldown_does_not_block_watch_batch(clock: _Clock):
    with patch.object(enrich, "_http_get_json", side_effect=lambda url, **k: (_ for _ in ()).throw(_http429(url, "0"))):
        enrich.fetch_jup_assets([_mint("C")], max_tries=1)
    assert enrich.jup_cooldown_left() > 0
    with patch.object(enrich, "_http_get_json", return_value=[_asset(_mint("W"), 300_000.0)]):
        out, errs = enrich.fetch_jup_assets([_mint("W")])  # batch path still tries
    assert errs == [] and _mint("W") in out
