"""Schema validation tests for decision_v1 / candidate_v1."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from x_intel.schemas.models import (
    CandidateDecision,
    CandidateV1,
    DecisionAction,
    DecisionV1,
    EvidenceItem,
    SizingIntent,
    default_expires_at,
    is_decision_stale,
)


def _evidence():
    return [
        EvidenceItem(
            channel="x_social",
            summary="test",
            observed_at=datetime.now(timezone.utc),
        )
    ]


def test_decision_default_armed_true():
    now = datetime.now(timezone.utc)
    d = DecisionV1(
        decision_id=uuid4(),
        action=DecisionAction.HOLD,
        issued_at=now,
        expires_at=now + timedelta(minutes=3),
        contract_address="So11111111111111111111111111111111111111112",
        chain="solana",
        confidence=0.5,
        sizing_intent=SizingIntent(mode="hold"),
        evidence=_evidence(),
        do_not_execute_until_armed=True,
    )
    assert d.do_not_execute_until_armed is True


def test_decision_allows_do_not_execute_false_when_armed():
    now = datetime.now(timezone.utc)
    d = DecisionV1(
        decision_id=uuid4(),
        action=DecisionAction.BUY,
        issued_at=now,
        expires_at=default_expires_at(DecisionAction.BUY, now),
        contract_address="So11111111111111111111111111111111111111112",
        chain="solana",
        confidence=0.9,
        sizing_intent=SizingIntent(mode="percent_equity", value=1.0),
        evidence=_evidence(),
        do_not_execute_until_armed=False,
    )
    assert d.do_not_execute_until_armed is False


def test_decision_actions_enum():
    now = datetime.now(timezone.utc)
    for action in DecisionAction:
        d = DecisionV1(
            decision_id=uuid4(),
            action=action,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
            contract_address="CA",
            chain="solana",
            confidence=0.1,
            sizing_intent=SizingIntent(mode="hold"),
            evidence=_evidence(),
        )
        assert d.action == action


def test_expires_must_be_after_issued():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        DecisionV1(
            decision_id=uuid4(),
            action=DecisionAction.EXIT,
            issued_at=now,
            expires_at=now,
            contract_address="CA",
            chain="base",
            confidence=0.5,
            sizing_intent=SizingIntent(mode="flat_exit"),
            evidence=_evidence(),
        )


def test_staleness_hard_cutoff():
    now = datetime.now(timezone.utc)
    d = DecisionV1(
        decision_id=uuid4(),
        action=DecisionAction.ADD,
        issued_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=5),
        contract_address="CA",
        chain="solana",
        confidence=0.4,
        sizing_intent=SizingIntent(mode="notional_usd", value=100),
        evidence=_evidence(),
    )
    assert is_decision_stale(d, now=now) is True
    fresh = d.model_copy(update={"expires_at": now + timedelta(minutes=5)})
    assert is_decision_stale(fresh, now=now) is False


def test_candidate_pursue_watch_reject():
    for dec in CandidateDecision:
        c = CandidateV1(
            candidate_id=uuid4(),
            first_seen_at=datetime.now(timezone.utc),
            contract_address="CA",
            chain="solana",
            decision=dec,
            evidence=[{"channel": "narrative", "summary": "x"}],
            experiment_id="xintel_v0",
        )
        assert c.decision == dec
