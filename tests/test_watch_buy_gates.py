"""WATCH dip/reclaim BUY hard gates: live-MC floor, organic X, 2h per-mint dedupe."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import pytest

from x_intel.config import watch_buy_dedupe_sec, watch_buy_min_mc_usd
from x_intel.discovery.organic_x import write_organic_payload
from x_intel.discovery.watch_escalate import run_watch_escalate_cycle
from x_intel.emit.pursue_buy import GateReject, check_pursue_buy_gates
from x_intel.ledger.store import CandidateLedger, RepoPaths
from x_intel.schemas.models import (
    CandidateDecision,
    CandidateV1,
    EvidenceItem,
    FeatureScoreEntry,
    FeatureScoresV1,
)

CA = "GateTestMintAbCdEfGhJkLmNoPqRsTuVwXyZ12pump"


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    for sub in ("candidates", "decisions", "outcomes", "execution_reports", "health"):
        (data / sub).mkdir(parents=True)
    monkeypatch.setenv("XINTEL_DATA_DIR", str(data))
    monkeypatch.setenv("XINTEL_ARMED", "true")
    monkeypatch.setenv("XINTEL_SKIP_DEX", "true")
    monkeypatch.delenv("XINTEL_WATCH_BUY_MIN_MC", raising=False)
    monkeypatch.delenv("XINTEL_WATCH_BUY_DEDUPE_SEC", raising=False)
    monkeypatch.delenv("XINTEL_WATCH_BUY_REQUIRE_ORGANIC_X", raising=False)
    return data


def _cand(
    *,
    ca: str = CA,
    ticker: str = "GATE",
    first_mc: float = 60_000,
    mc_now: float = 40_000,
    organic: Optional[bool] = True,
    curve_only: bool = False,
    soft_scores: bool = False,
    extra: Optional[dict[str, Any]] = None,
) -> CandidateV1:
    now = datetime.now(timezone.utc)
    hints: dict[str, Any] = {
        "pump_curve": True,
        "mc_source": "jupiter",
        "ticker_unique_recent": True,
        "clone_storm": False,
        "spam_farm_ticker": False,
    }
    if organic is not None:
        hints["organic_x"] = organic
    fs = None
    if soft_scores:
        fs = FeatureScoresV1(
            scores={
                "S1": FeatureScoreEntry(feature_id="S1", value=None, direction="bullish"),
                "S3": FeatureScoreEntry(feature_id="S3", value=None, direction="bullish"),
            }
        )
    kw: dict[str, Any] = dict(extra or {})
    return CandidateV1(
        candidate_id=uuid4(),
        first_seen_at=now - timedelta(hours=1),
        contract_address=ca,
        chain="solana",
        ticker=ticker,
        mc_usd_at_first_sight=first_mc,
        decision=CandidateDecision.watch,
        evidence=[
            EvidenceItem(channel="launch_metrics", summary="pump curve", observed_at=now, refs=[])
        ],
        source_accounts=["pumpfun_curve"] if curve_only else ["pumpfun_curve", "x_social"],
        feature_scores=fs,
        mc_usd_now=mc_now,
        min_mc_usd_seen=mc_now,
        refreshed_at=now.isoformat().replace("+00:00", "Z"),
        discovery={
            "first_source": "pumpfun_curve",
            "sources": ["pumpfun_curve"],
            "mc_usd": first_mc,
            "mc_source": "jupiter",
            "confidence_hints": hints,
        },
        **kw,
    )


def _run(ledger: CandidateLedger, data: Path, mc: float, *, emit: bool = True) -> dict[str, Any]:
    return run_watch_escalate_cycle(
        ledger=ledger,
        data_root=data,
        live=True,
        emit_buy=emit,
        mc_fetcher=lambda ca, chain="solana": mc,
    )


def _write_prior_watch_buy(data: Path, *, ca: str, issued: datetime, **extra: Any) -> str:
    did = str(uuid4())
    raw = {
        "schema_version": "xintel.decision.v1",
        "decision_id": did,
        "action": "BUY",
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": (issued + timedelta(minutes=20)).isoformat().replace("+00:00", "Z"),
        "contract_address": ca,
        "chain": "solana",
        "confidence": 0.55,
        "sizing_intent": {"mode": "percent_equity", "value": 0.75},
        "evidence": [
            {"channel": "launch_metrics", "summary": "x", "observed_at": issued.isoformat(), "refs": []}
        ],
        "risk_flags": ["pursue_buy_emitter", "watch_dip_buy", "watch_dip"],
        "do_not_execute_until_armed": False,
    }
    raw.update(extra)
    (data / "decisions" / f"{did}.json").write_text(json.dumps(raw))
    return did


# --- config -----------------------------------------------------------------


def test_floor_default_and_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("XINTEL_WATCH_BUY_MIN_MC", raising=False)
    assert watch_buy_min_mc_usd() == 25_000
    monkeypatch.setenv("XINTEL_WATCH_BUY_MIN_MC", "50000")
    assert watch_buy_min_mc_usd() == 50_000
    monkeypatch.setenv("XINTEL_WATCH_BUY_MIN_MC", "garbage")
    assert watch_buy_min_mc_usd() == 25_000


def test_dedupe_window_never_below_2h(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("XINTEL_WATCH_BUY_DEDUPE_SEC", raising=False)
    assert watch_buy_dedupe_sec() == 7200
    monkeypatch.setenv("XINTEL_WATCH_BUY_DEDUPE_SEC", "60")
    assert watch_buy_dedupe_sec() == 7200


# --- MC floor ---------------------------------------------------------------


@pytest.mark.parametrize("mc,first", [(3_141.0, 3_500.0), (3_798.0, 6_000.0), (24_999.0, 40_000.0)])
def test_dead_coin_below_floor_is_gate_reject(tmp_data: Path, mc: float, first: float):
    """autism/NUT/INCOGINU-class ~$3-4k watches never BUY, even with organic X."""
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(first_mc=first, mc_now=mc))
    summary = _run(ledger, tmp_data, mc)
    assert summary["buy_emitted_n"] == 0
    row = summary["escalate"][0]
    assert row["reason"].startswith("gate_reject:mc_below_watch_buy_floor")
    assert row["should_buy"] is False
    assert row["watch_signal"] in {"watch_dip", "watch_reclaim"}
    assert list((tmp_data / "decisions").glob("*.json")) == []


def test_floor_env_override(tmp_data: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XINTEL_WATCH_BUY_MIN_MC", "100000")
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(first_mc=120_000, mc_now=60_000))
    summary = _run(ledger, tmp_data, 60_000)
    assert summary["buy_emitted_n"] == 0
    assert "floor=100000" in summary["escalate"][0]["reason"]


def test_above_floor_with_organic_x_emits(tmp_data: Path):
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(first_mc=60_000, mc_now=40_000))
    summary = _run(ledger, tmp_data, 40_000)
    assert summary["buy_emitted_n"] == 1
    assert summary["buys"][0]["reason"] == "watch_dip"


def test_no_emit_mode_reports_gate_reject_not_should_buy(tmp_data: Path):
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(first_mc=5_000, mc_now=3_400))
    summary = _run(ledger, tmp_data, 3_400, emit=False)
    assert [r for r in summary["escalate"] if r["should_buy"]] == []


# --- organic X --------------------------------------------------------------


def test_watch_dip_quality_path_without_organic_x_rejects(tmp_data: Path):
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(organic=None, curve_only=True, first_mc=500_000, mc_now=300_000))
    summary = _run(ledger, tmp_data, 300_000)
    assert summary["buy_emitted_n"] == 0
    assert summary["escalate"][0]["reason"].startswith("gate_reject:watch_buy_requires_organic_x")


def test_s1_s3_soft_pass_without_organic_x_rejects(tmp_data: Path):
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(
        _cand(organic=False, soft_scores=True, first_mc=500_000, mc_now=300_000)
    )
    summary = _run(ledger, tmp_data, 300_000)
    assert summary["buy_emitted_n"] == 0
    assert "watch_buy_requires_organic_x" in summary["escalate"][0]["reason"]


def test_s1_s3_soft_pass_with_organic_x_still_emits(tmp_data: Path):
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(organic=True, soft_scores=True, first_mc=500_000, mc_now=300_000))
    summary = _run(ledger, tmp_data, 300_000)
    assert summary["buy_emitted_n"] == 1


def test_bare_x_social_source_is_not_organic(tmp_data: Path):
    """Legacy x_social source without organic_x=True does not satisfy the watch gate."""
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(organic=None, first_mc=500_000, mc_now=300_000))
    summary = _run(ledger, tmp_data, 300_000)
    assert summary["buy_emitted_n"] == 0


def test_fresh_organic_x_file_satisfies_gate(tmp_data: Path):
    """CA-scoped data/x_organic/<mint>.json written after first sight is honored."""
    now = datetime.now(timezone.utc)
    write_organic_payload(
        CA,
        {
            "queried_at": now.isoformat(),
            "posts": [
                {
                    "id": "1",
                    "text": f"aping {CA} looks clean",
                    "created_at": (now - timedelta(minutes=10)).isoformat(),
                    "url": "https://x.com/realtrader/status/1",
                    "author": {"username": "realtrader", "followers_count": 5000},
                }
            ],
        },
        data_root=tmp_data,
    )
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(organic=None, curve_only=True, first_mc=500_000, mc_now=300_000))
    summary = _run(ledger, tmp_data, 300_000)
    row = summary["escalate"][0]
    assert "watch_buy_requires_organic_x" not in row["reason"], row
    assert summary["buy_emitted_n"] == 1, row


def test_check_gates_require_organic_x_direct():
    cand = _cand(organic=None, curve_only=True).model_copy(
        update={"decision": CandidateDecision.pursue}
    )
    # Without the requirement the soft watch_dip path passes...
    check_pursue_buy_gates(cand, early_mc_usd_max=None, allow_watch_dip_quality=True)
    # ...with it, it is a hard reject.
    with pytest.raises(GateReject) as ei:
        check_pursue_buy_gates(
            cand, early_mc_usd_max=None, allow_watch_dip_quality=True, require_organic_x=True
        )
    assert "watch_buy_requires_organic_x" in ei.value.reason


# --- 2h dedupe --------------------------------------------------------------


def test_stamp_refire_blocked_within_2h_even_if_prior_expired(tmp_data: Path):
    now = datetime.now(timezone.utc)
    _write_prior_watch_buy(tmp_data, ca=CA, issued=now - timedelta(minutes=47))  # TTL long gone
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(ticker="STAMP", first_mc=2_850_000, mc_now=311_000))
    summary = _run(ledger, tmp_data, 311_000)
    assert summary["buy_emitted_n"] == 0
    assert summary["escalate"][0]["reason"].startswith("gate_reject:watch_buy_dedupe_2h")


def test_dedupe_counts_cancelled_prior(tmp_data: Path):
    now = datetime.now(timezone.utc)
    _write_prior_watch_buy(
        tmp_data, ca=CA, issued=now - timedelta(minutes=90),
        status="cancelled", cancel_reason="dead_coin_floor", do_not_execute_until_armed=True,
    )
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(first_mc=60_000, mc_now=40_000))
    assert _run(ledger, tmp_data, 40_000)["buy_emitted_n"] == 0


def test_dedupe_allows_after_2h(tmp_data: Path):
    now = datetime.now(timezone.utc)
    _write_prior_watch_buy(tmp_data, ca=CA, issued=now - timedelta(hours=2, minutes=5))
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(first_mc=60_000, mc_now=40_000))
    assert _run(ledger, tmp_data, 40_000)["buy_emitted_n"] == 1


def test_dedupe_other_mint_not_blocked(tmp_data: Path):
    now = datetime.now(timezone.utc)
    _write_prior_watch_buy(tmp_data, ca="OtherMintZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZpump", issued=now)
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(first_mc=60_000, mc_now=40_000))
    assert _run(ledger, tmp_data, 40_000)["buy_emitted_n"] == 1


def test_consecutive_cycles_emit_once(tmp_data: Path):
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ledger.save_candidate(_cand(first_mc=60_000, mc_now=40_000))
    assert _run(ledger, tmp_data, 40_000)["buy_emitted_n"] == 1
    s2 = _run(ledger, tmp_data, 40_000)
    assert s2["buy_emitted_n"] == 0
    assert s2["escalate"][0]["reason"] in {"already_bought"} or "dedupe" in s2["escalate"][0]["reason"]
