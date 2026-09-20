"""Unit tests for WATCH dip-buy thresholds + refresh/escalate wiring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from x_intel.discovery.watch_escalate import (
    DIP_MC_USD_MAX,
    NO_CHASE_MC_USD,
    escalate_watch_dips,
    evaluate_watch_dip_buy,
    refresh_open_watches,
    run_watch_escalate_cycle,
)
from x_intel.io_atomic import atomic_read_json
from x_intel.ledger.store import CandidateLedger, RepoPaths
from x_intel.schemas.models import CandidateDecision, CandidateV1, EvidenceItem


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    for sub in ("candidates", "decisions", "outcomes", "execution_reports", "health"):
        (data / sub).mkdir(parents=True)
    monkeypatch.setenv("XINTEL_DATA_DIR", str(data))
    monkeypatch.setenv("XINTEL_ARMED", "true")
    monkeypatch.setenv("XINTEL_SKIP_DEX", "true")
    return data


def test_dip_buy_when_sub_2m_and_15pct_below_first():
    ok, reason = evaluate_watch_dip_buy(
        mc_now=1_700_000,
        first_sight=2_850_000,
        min_mc_seen=1_700_000,
        already_bought=False,
    )
    assert ok is True
    assert reason == "watch_dip"
    # 0.85 * 2.85M = 2.4225M — 1.7M qualifies
    assert 1_700_000 <= DIP_MC_USD_MAX
    assert 1_700_000 <= 0.85 * 2_850_000


def test_no_dip_when_above_2m_and_never_sub_2m():
    # 2.2M > 2M (no dip); min never < 2M (no reclaim); still < 4M
    ok, reason = evaluate_watch_dip_buy(
        mc_now=2_200_000,
        first_sight=2_500_000,
        min_mc_seen=2_200_000,
        already_bought=False,
    )
    assert ok is False
    assert reason == "no_dip_no_reclaim"


def test_sub_2m_without_15pct_off_still_reclaims():
    # first 1.5M, now 1.4M: under 2M but > 0.85*first (1.275M) → not dip; reclaim yes
    ok, reason = evaluate_watch_dip_buy(
        mc_now=1_400_000,
        first_sight=1_500_000,
        min_mc_seen=1_400_000,
        already_bought=False,
    )
    assert ok is True
    assert reason == "watch_reclaim"


def test_reclaim_after_sub_2m_print():
    ok, reason = evaluate_watch_dip_buy(
        mc_now=2_400_000,
        first_sight=2_850_000,
        min_mc_seen=1_350_000,
        already_bought=False,
    )
    assert ok is True
    assert reason == "watch_reclaim"
    # reclaim cap = min(2.85M*1.1, 3.5M) = 3.135M


def test_reclaim_blocked_if_already_bought():
    ok, reason = evaluate_watch_dip_buy(
        mc_now=2_400_000,
        first_sight=2_850_000,
        min_mc_seen=1_350_000,
        already_bought=True,
    )
    assert ok is False
    assert reason == "already_bought"


def test_no_chase_at_or_above_4m():
    ok, reason = evaluate_watch_dip_buy(
        mc_now=6_000_000,
        first_sight=2_850_000,
        min_mc_seen=1_350_000,
        already_bought=False,
    )
    assert ok is False
    assert reason == "no_chase_above_4m"
    assert 6_000_000 >= NO_CHASE_MC_USD


def test_blocks_hard_rugged_and_parasite_and_non_solana():
    assert evaluate_watch_dip_buy(
        mc_now=1_000_000, first_sight=2_000_000, min_mc_seen=1_000_000,
        already_bought=False, hard_rugged=True,
    )[0] is False
    assert evaluate_watch_dip_buy(
        mc_now=1_000_000, first_sight=2_000_000, min_mc_seen=1_000_000,
        already_bought=False, name_parasite=True,
    )[0] is False
    assert evaluate_watch_dip_buy(
        mc_now=1_000_000, first_sight=2_000_000, min_mc_seen=1_000_000,
        already_bought=False, solana=False,
    ) == (False, "non_solana")


def test_first_sight_none_allows_sub_2m_dip():
    ok, reason = evaluate_watch_dip_buy(
        mc_now=1_500_000,
        first_sight=None,
        min_mc_seen=1_500_000,
        already_bought=False,
    )
    assert ok is True
    assert reason == "watch_dip"


def _watch_cand(
    *,
    ca: str = "WatchDipSoLanaCaXXXXXXXXXXXXXXXXXXXXXdip",
    ticker: str = "DIP",
    first_mc: float = 2_500_000,
    mc_now: float = 1_600_000,
) -> CandidateV1:
    now = datetime.now(timezone.utc)
    return CandidateV1(
        candidate_id=uuid4(),
        first_seen_at=now - timedelta(hours=1),
        contract_address=ca,
        chain="solana",
        ticker=ticker,
        mc_usd_at_first_sight=first_mc,
        decision=CandidateDecision.watch,
        evidence=[
            EvidenceItem(
                channel="x_social",
                summary="organic watch mention",
                observed_at=now,
                refs=[],
            ),
            EvidenceItem(
                channel="onchain_flow",
                summary="flow positive non-sybil",
                observed_at=now,
                refs=[],
                weight=0.6,
            ),
        ],
        source_accounts=["x_social", "flow_hint"],
        mc_usd_now=mc_now,
        min_mc_usd_seen=mc_now,
        refreshed_at=now.isoformat().replace("+00:00", "Z"),
    )


def test_escalate_emits_watch_dip_buy(tmp_data: Path):
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    cand = _watch_cand()
    ledger.save_candidate(cand)

    def fake_fetch(ca, chain="solana"):
        return 1_600_000.0

    summary = run_watch_escalate_cycle(
        ledger=ledger,
        data_root=tmp_data,
        live=True,
        emit_buy=True,
        mc_fetcher=fake_fetch,
    )
    assert summary["buy_emitted_n"] == 1
    buys = summary["buys"]
    assert buys[0]["reason"] == "watch_dip"
    did = buys[0]["buy_decision_id"]
    assert did
    dec = ledger.get_decision(did)
    assert dec is not None
    assert "watch_dip_buy" in (dec.risk_flags or [])
    assert "calibration_shadow" not in (dec.risk_flags or [])
    assert dec.do_not_execute_until_armed is False  # armed
    assert dec.sizing_intent.value >= 0.75
    assert all(e.refs == [] for e in dec.evidence)
    # Dedup: second cycle must not emit again
    summary2 = run_watch_escalate_cycle(
        ledger=ledger,
        data_root=tmp_data,
        live=True,
        emit_buy=True,
        mc_fetcher=fake_fetch,
    )
    assert summary2["buy_emitted_n"] == 0


def test_no_buy_when_mc_at_6m(tmp_data: Path):
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    cand = _watch_cand(ticker="STAMP", first_mc=2_850_000, mc_now=6_200_000)
    ledger.save_candidate(cand)

    summary = run_watch_escalate_cycle(
        ledger=ledger,
        data_root=tmp_data,
        live=True,
        emit_buy=True,
        mc_fetcher=lambda ca, chain="solana": 6_200_000.0,
    )
    assert summary["buy_emitted_n"] == 0
    assert summary["escalate"][0]["reason"] == "no_chase_above_4m"


def test_freshness_file_written(tmp_data: Path):
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    cand = _watch_cand()
    ledger.save_candidate(cand)
    report = refresh_open_watches(
        ledger=ledger,
        data_root=tmp_data,
        live=True,
        mc_fetcher=lambda ca, chain="solana": 1_550_000.0,
    )
    assert report["watch_count"] == 1
    assert report["watch_stale_count"] == 0
    path = tmp_data / "health" / "watch_freshness.json"
    assert path.is_file()
    raw = atomic_read_json(path)
    assert raw is not None
    assert raw["watch_count"] == 1
    hb = atomic_read_json(tmp_data / "health" / "heartbeat.json")
    assert hb is not None
    assert hb["watch_count"] == 1
    assert "oldest_watch_refresh_age_sec" in hb

    # Persist mc on candidate
    loaded = ledger.get_candidate(cand.candidate_id)
    assert loaded is not None
    extra = getattr(loaded, "__pydantic_extra__", None) or {}
    assert float(extra.get("mc_usd_now") or 0) == 1_550_000.0
    assert float(extra.get("min_mc_usd_seen") or 0) == 1_550_000.0
