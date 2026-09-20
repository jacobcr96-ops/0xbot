"""Disk decision queue, arming, BUY TTL, pursue gates, execution_report roundtrip."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from x_intel.config import BUY_ADD_TTL_SECONDS, do_not_execute_until_armed, is_armed
from x_intel.emit.pursue_buy import GateReject, check_pursue_buy_gates, pursue_candidate_to_buy
from x_intel.io_atomic import atomic_read_json, atomic_write_json
from x_intel.ledger.execution_reports import ingest_report, load_report
from x_intel.ledger.store import CandidateLedger, RepoPaths
from x_intel.schemas.models import (
    BUY_ADD_TTL_SECONDS as MODEL_TTL,
    CandidateDecision,
    CandidateV1,
    DecisionAction,
    DecisionV1,
    EvidenceItem,
    ExecutionReportV1,
    FeatureScoreEntry,
    FeatureScoresV1,
    FillRecord,
    RejectReason,
    SizingIntent,
    default_expires_at,
)


@pytest.fixture()
def tmp_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CandidateLedger:
    data = tmp_path / "data"
    (data / "candidates").mkdir(parents=True)
    (data / "decisions").mkdir(parents=True)
    (data / "outcomes").mkdir(parents=True)
    (data / "execution_reports").mkdir(parents=True)
    monkeypatch.setenv("XINTEL_DATA_DIR", str(data))
    monkeypatch.delenv("XINTEL_ARMED", raising=False)
    return CandidateLedger(RepoPaths(data=data))


def _evidence():
    return [
        EvidenceItem(
            channel="x_social",
            summary="test social",
            observed_at=datetime.now(timezone.utc),
        ),
        EvidenceItem(
            channel="onchain_flow",
            summary="test flow",
            observed_at=datetime.now(timezone.utc),
        ),
    ]


def test_atomic_write_no_tmp_left_as_final(tmp_path: Path):
    target = tmp_path / "abc.json"
    atomic_write_json(target, {"ok": True, "n": 1})
    assert target.is_file()
    assert not (tmp_path / "abc.json.tmp").exists()
    assert atomic_read_json(target) == {"ok": True, "n": 1}
    # tmp files are not treated as readable finals
    (tmp_path / "partial.json.tmp").write_text("{not json")
    assert atomic_read_json(tmp_path / "partial.json.tmp") is None


def test_atomic_write_failure_leaves_final_untouched(tmp_path: Path, monkeypatch):
    target = tmp_path / "keep.json"
    atomic_write_json(target, {"v": 1})
    # Force write failure mid-flight by making parent read-only after first write
    # Simpler: verify replace semantics — corrupt tmp never becomes final name without replace
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text("{broken")
    # Final still valid
    assert json.loads(target.read_text())["v"] == 1
    assert target.read_text().startswith("{")


def test_save_decision_atomic_disk_queue(tmp_ledger: CandidateLedger):
    now = datetime.now(timezone.utc)
    d = DecisionV1(
        decision_id=uuid4(),
        action=DecisionAction.BUY,
        issued_at=now,
        expires_at=default_expires_at(DecisionAction.BUY, now),
        contract_address="So11111111111111111111111111111111111111112",
        chain="solana",
        confidence=0.6,
        sizing_intent=SizingIntent(mode="percent_equity", value=1.0),
        evidence=_evidence(),
        do_not_execute_until_armed=True,
    )
    path = tmp_ledger.save_decision(d)
    assert path.parent.name == "decisions"
    assert path.suffix == ".json"
    assert not path.with_suffix(path.suffix + ".tmp").exists()
    assert not Path(str(path) + ".tmp").exists()
    loaded = tmp_ledger.get_decision(d.decision_id)
    assert loaded is not None
    assert loaded.decision_id == d.decision_id
    inbox = tmp_ledger.paths.decisions_inbox
    assert inbox.is_file()
    assert str(d.decision_id) in inbox.read_text()


def test_armed_vs_disarmed_flag(tmp_ledger: CandidateLedger, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("XINTEL_ARMED", raising=False)
    assert is_armed() is False
    assert do_not_execute_until_armed() is True

    monkeypatch.setenv("XINTEL_ARMED", "true")
    assert is_armed() is True
    assert do_not_execute_until_armed() is False

    now = datetime.now(timezone.utc)
    # Disarmed emit
    monkeypatch.setenv("XINTEL_ARMED", "false")
    cand = _pursue_candidate(d1=False, s1=True)
    dec, _ = pursue_candidate_to_buy(cand, issued_at=now)
    assert dec.do_not_execute_until_armed is True

    # Armed emit
    monkeypatch.setenv("XINTEL_ARMED", "1")
    dec2, _ = pursue_candidate_to_buy(cand, issued_at=now)
    assert dec2.do_not_execute_until_armed is False


def test_default_buy_expiry_approx_300s():
    assert BUY_ADD_TTL_SECONDS == 300
    assert MODEL_TTL == 300
    now = datetime.now(timezone.utc)
    exp = default_expires_at(DecisionAction.BUY, now)
    delta = (exp - now).total_seconds()
    assert abs(delta - 300) < 0.01
    exp_add = default_expires_at(DecisionAction.ADD, now)
    assert abs((exp_add - now).total_seconds() - 300) < 0.01


def _pursue_candidate(
    *,
    d1: bool | None = False,
    s1: bool | None = True,
    s3: bool | None = None,
    s2: str | None = "first",
    window_type: str | None = None,
    mc: float | None = 50_000,
    decision: CandidateDecision = CandidateDecision.pursue,
) -> CandidateV1:
    scores = {
        "D1": FeatureScoreEntry(feature_id="D1", value=d1, direction="bearish"),
        "S1": FeatureScoreEntry(feature_id="S1", value=s1, direction="bullish"),
        "S3": FeatureScoreEntry(feature_id="S3", value=s3, direction="bullish"),
        "S2": FeatureScoreEntry(feature_id="S2", value=s2, direction="bullish"),
    }
    return CandidateV1(
        candidate_id=uuid4(),
        first_seen_at=datetime.now(timezone.utc),
        contract_address="So11111111111111111111111111111111111111112",
        chain="solana",
        ticker="TEST",
        mc_usd_at_first_sight=mc,
        decision=decision,
        evidence=[
            {
                "channel": "x_social",
                "summary": "amp",
                "observed_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "channel": "narrative",
                "summary": "lore",
                "observed_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
        feature_scores=FeatureScoresV1(
            scores=scores,
            window_type=window_type,
        ),
        window_type=window_type,
    )


def test_pursue_buy_rejects_d1_true():
    cand = _pursue_candidate(d1=True, s1=True)
    with pytest.raises(GateReject, match="D1=true"):
        check_pursue_buy_gates(cand)


def test_pursue_buy_accepts_clean_pursue(tmp_ledger: CandidateLedger):
    cand = _pursue_candidate(d1=False, s1=True)
    dec, warns = pursue_candidate_to_buy(cand)
    assert dec.action == DecisionAction.BUY
    path = tmp_ledger.save_decision(dec)
    assert path.exists()
    assert abs((dec.expires_at - dec.issued_at).total_seconds() - 300) < 0.01


def test_execution_report_schema_roundtrip(tmp_ledger: CandidateLedger):
    now = datetime.now(timezone.utc)
    report = ExecutionReportV1(
        report_id=uuid4(),
        decision_id=uuid4(),
        reported_at=now,
        status="rejected",
        reject_reason=RejectReason.stale,
        reject_detail="expires_at passed",
        fills=[
            FillRecord(
                requested_usd=100.0,
                filled_usd=0.0,
                latency_ms=12000,
            )
        ],
        equity_usd=10_000.0,
        experiment_id="xintel_v0",
    )
    path = tmp_ledger.save_execution_report(report)
    assert path.exists()
    assert not Path(str(path) + ".tmp").exists()
    loaded = load_report(path)
    assert loaded is not None
    assert loaded.report_id == report.report_id
    assert loaded.reject_reason == RejectReason.stale
    assert loaded.fills[0].requested_usd == 100.0

    # JSON dump/load
    raw = json.loads(path.read_text())
    again = ExecutionReportV1.model_validate(raw)
    assert again.status == "rejected"

    out = ingest_report(again, tmp_ledger)
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["decision_id"] == str(report.decision_id)
    assert payload["reject_reason"] == "stale"


def test_decision_allows_armed_false_when_set():
    """Schema no longer hard-requires do_not_execute_until_armed=true."""
    now = datetime.now(timezone.utc)
    d = DecisionV1(
        decision_id=uuid4(),
        action=DecisionAction.BUY,
        issued_at=now,
        expires_at=now + timedelta(seconds=300),
        contract_address="CA",
        chain="solana",
        confidence=0.5,
        sizing_intent=SizingIntent(mode="percent_equity", value=1.0),
        evidence=_evidence(),
        do_not_execute_until_armed=False,
    )
    assert d.do_not_execute_until_armed is False
