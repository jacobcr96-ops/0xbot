"""Pydantic models matching candidate_v1 / decision_v1 / outcome_fill_v1 /
feature_scores_v1 / execution_report_v1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_DIR = Path(__file__).resolve().parent

Chain = Literal[
    "solana",
    "base",
    "bsc",
    "ethereum",
    "arbitrum",
    "avalanche",
    "polygon",
    "ton",
    "tron",
    "sui",
    "other",
]

EvidenceChannel = Literal[
    "x_social",
    "onchain_flow",
    "narrative",
    "launch_metrics",
    "wallet_behavior",
    "cross_source",
    "historical_analog",
]

# BUY/ADD default TTL (seconds). Bridge RTT is 5–15s; 5 minutes is the handoff contract.
BUY_ADD_TTL_SECONDS = 300


class DecisionAction(str, Enum):
    BUY = "BUY"
    ADD = "ADD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    HOLD = "HOLD"


class CandidateDecision(str, Enum):
    pursue = "pursue"
    watch = "watch"
    reject = "reject"


class RejectReason(str, Enum):
    """Gateway reject taxonomy for execution_report.v1."""

    stale = "stale"
    no_cash = "no_cash"
    chain_unsupported = "chain_unsupported"
    ca_unresolved = "ca_unresolved"
    disable_buying = "disable_buying"
    duplicate = "duplicate"
    below_min_size = "below_min_size"
    other = "other"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    channel: EvidenceChannel
    summary: str
    observed_at: datetime
    refs: Optional[list[str]] = None
    weight: Optional[float] = None


class SizingIntent(BaseModel):
    mode: Literal["percent_equity", "notional_usd", "units", "flat_exit", "hold"]
    value: Optional[float] = None
    max_slippage_bps: Optional[int] = None
    urgency: Literal["low", "normal", "high"] = "normal"


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    mc_usd: Optional[float] = None
    price_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    volume_1h_usd: Optional[float] = None
    pair_age_minutes: Optional[float] = None
    as_of: Optional[datetime] = None


class Outcomes(BaseModel):
    """Fractional returns from first_seen (0.25 = +25%)."""

    ret_5m: Optional[float] = None
    ret_15m: Optional[float] = None
    ret_1h: Optional[float] = None
    ret_6h: Optional[float] = None
    ret_24h: Optional[float] = None
    max_runup: Optional[float] = None
    max_drawdown: Optional[float] = None


class FeatureScoreEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    feature_id: str
    value: Optional[Union[bool, str, float, int]] = None
    direction: Literal["bullish", "bearish", "context"]
    provisional: Literal[True] = True
    batch: Literal["BATCH_01"] = "BATCH_01"
    desk: Optional[str] = None
    notes: Optional[str] = None
    observed_at: Optional[datetime] = None


class FeatureScoresV1(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal["xintel.feature_scores.v1"] = "xintel.feature_scores.v1"
    experiment_id: str = "xintel_v0"
    batch: Literal["BATCH_01"] = "BATCH_01"
    provisional: Literal[True] = True
    scored_at: Optional[datetime] = None
    scored_by: Optional[str] = None
    scores: dict[str, FeatureScoreEntry] = Field(default_factory=dict)
    window_type: Optional[str] = None


class CandidateV1(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal["xintel.candidate.v1"] = "xintel.candidate.v1"
    candidate_id: UUID
    first_seen_at: datetime
    contract_address: str
    chain: str
    ticker: Optional[str] = None
    mc_usd_at_first_sight: Optional[float] = None
    price_usd_at_first_sight: Optional[float] = None
    decision: CandidateDecision
    reject_reason: Optional[str] = None
    evidence: list[Any] = Field(default_factory=list)
    source_accounts: list[str] = Field(default_factory=list)
    experiment_id: str = "xintel_v0"
    outcomes: Optional[Outcomes] = None
    feature_scores: Optional[FeatureScoresV1] = None
    window_type: Optional[str] = None


class DecisionV1(BaseModel):
    """Handoff intent from intelligence to 0xbot. Never executes here."""

    schema_version: Literal["xintel.decision.v1"] = "xintel.decision.v1"
    decision_id: UUID
    action: DecisionAction
    issued_at: datetime
    expires_at: datetime
    contract_address: str
    chain: Chain
    ticker: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: Optional[str] = None
    sizing_intent: SizingIntent
    market_snapshot: Optional[MarketSnapshot] = None
    evidence: list[EvidenceItem] = Field(min_length=1)
    risk_flags: list[str] = Field(default_factory=list)
    experiment_id: str = "xintel_v0"
    candidate_id: Optional[str] = None
    # When XINTEL_ARMED=false (default), MUST be true. When armed, false on emit.
    do_not_execute_until_armed: bool = True

    @model_validator(mode="after")
    def _expires_after_issued(self) -> "DecisionV1":
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self




class OutcomeFillV1(BaseModel):
    schema_version: Literal["xintel.outcome_fill.v1"] = "xintel.outcome_fill.v1"
    candidate_id: UUID
    filled_at: datetime
    fill_status: Literal["pending", "partial", "complete", "unavailable"]
    experiment_id: str = "xintel_v0"
    price_sources: list[dict[str, Any]] = Field(default_factory=list)
    returns: Optional[Outcomes] = None
    max_runup: Optional[float] = None
    max_drawdown: Optional[float] = None
    horizon_filled_at: Optional[dict[str, Optional[datetime]]] = None
    lp_rug_detected_at: Optional[datetime] = None
    notes: Optional[str] = None


class FillRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    requested_usd: Optional[float] = None
    filled_usd: Optional[float] = None
    price: Optional[float] = None
    mc_at_fill: Optional[float] = None
    slippage_bps: Optional[float] = None
    latency_ms: Optional[float] = None
    filled_at: Optional[datetime] = None
    tx_ref: Optional[str] = None


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    contract_address: Optional[str] = None
    chain: Optional[str] = None
    units: Optional[float] = None
    notional_usd: Optional[float] = None
    avg_entry_price: Optional[float] = None
    unrealized_pnl_usd: Optional[float] = None
    as_of: Optional[datetime] = None


class ClosedRound(BaseModel):
    model_config = ConfigDict(extra="allow")

    multiple: Optional[float] = None
    hold_time_s: Optional[float] = None
    mfe: Optional[float] = None
    mae: Optional[float] = None
    realized_pnl_usd: Optional[float] = None
    closed_at: Optional[datetime] = None


class ExecutionReportV1(BaseModel):
    """Return path from 0xbot / Tampermonkey gateway → x_intel."""

    schema_version: Literal["xintel.execution_report.v1"] = "xintel.execution_report.v1"
    report_id: UUID
    decision_id: UUID
    reported_at: datetime
    status: Literal["filled", "partial", "rejected", "skipped", "error"]
    reject_reason: Optional[RejectReason] = None
    reject_detail: Optional[str] = None
    fills: list[FillRecord] = Field(default_factory=list)
    positions: list[PositionSnapshot] = Field(default_factory=list)
    equity_usd: Optional[float] = None
    closed_round: Optional[ClosedRound] = None
    experiment_id: str = "xintel_v0"
    candidate_id: Optional[str] = None
    notes: Optional[str] = None


def default_expires_at(
    action: DecisionAction | str,
    issued_at: datetime,
    *,
    ttl_seconds: Optional[int] = None,
) -> datetime:
    """Default expiry: BUY/ADD → issued_at + 5 minutes; others → +15 minutes."""
    act = action.value if isinstance(action, DecisionAction) else str(action)
    if ttl_seconds is not None:
        delta = timedelta(seconds=ttl_seconds)
    elif act in ("BUY", "ADD"):
        delta = timedelta(seconds=BUY_ADD_TTL_SECONDS)
    else:
        delta = timedelta(minutes=15)
    return issued_at + delta


def is_decision_stale(decision: DecisionV1, now: Optional[datetime] = None) -> bool:
    """Hard staleness: True if now > expires_at. 0xbot MUST ignore stale intents."""
    now = now or datetime.now(timezone.utc)
    exp = decision.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now > exp


def json_schema_path(name: str) -> Path:
    return SCHEMA_DIR / name
