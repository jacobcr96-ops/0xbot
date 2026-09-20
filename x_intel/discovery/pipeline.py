"""Discovery pipeline: ingest → ledger upsert → score → optional BUY emit.

Fan-out order: ledger write → market enrich → score → decision.
Live execution stays DISARMED unless XINTEL_ARMED.

Real BUY emit only via emit_pursue_buy when gate.decision==pursue and
pursue_eligible. Never invent calibration_shadow / shadow_only stubs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from x_intel.config import data_dir, do_not_execute_until_armed, is_armed
from x_intel.discovery.bus import DiscoveryBus
from x_intel.discovery.fanout import write_work_orders
from x_intel.discovery.gates import BUY_TTL_SECONDS, GateResult, score_discovery
from x_intel.discovery.models import DiscoveryEvent, DiscoveryRecord
from x_intel.emit.pursue_buy import GateReject, emit_pursue_buy
from x_intel.ledger.store import CandidateLedger, RepoPaths
from x_intel.schemas.models import (
    CandidateDecision,
    CandidateV1,
    EvidenceItem,
    FeatureScoreEntry,
    FeatureScoresV1,
)

log = logging.getLogger(__name__)


def _evidence_from_record(rec: DiscoveryRecord) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for src in rec.sources:
        channel = {
            "x_social": "x_social",
            "flow_hint": "onchain_flow",
            "dexscreener_new": "launch_metrics",
            "pumpfun_curve": "launch_metrics",
            "fomo_sidebar": "cross_source",
        }.get(src, "cross_source")
        items.append(
            {
                "channel": channel,
                "summary": f"discovery source={src} first={rec.first_source}",
                "observed_at": rec.first_seen_at.isoformat(),
                "refs": [],
                "weight": 0.3 if src == "fomo_sidebar" else 0.6,
            }
        )
    if not items:
        items.append(
            {
                "channel": "launch_metrics",
                "summary": "discovery bus empty sources — stub",
                "observed_at": rec.first_seen_at.isoformat(),
            }
        )
    return items


def _feature_scores_from_hints(rec: DiscoveryRecord) -> Optional[FeatureScoresV1]:
    hints = rec.confidence_hints or {}
    ids = ("D1", "S1", "S2", "S3", "S4", "S5", "D2", "D4")
    scores: dict[str, FeatureScoreEntry] = {}
    any_set = False
    meta_dir = {
        "D1": "bearish",
        "D2": "bearish",
        "D4": "bearish",
        "S1": "context",
        "S2": "bullish",
        "S3": "bullish",
        "S4": "context",
        "S5": "bullish",
    }
    for fid in ids:
        if fid not in hints:
            continue
        any_set = True
        scores[fid] = FeatureScoreEntry(
            feature_id=fid,
            value=hints[fid],
            direction=meta_dir.get(fid, "context"),  # type: ignore[arg-type]
            notes="from discovery confidence_hints",
        )
    if not any_set:
        return None
    return FeatureScoresV1(
        scored_at=datetime.now(timezone.utc),
        scored_by="discovery_pipeline",
        scores=scores,
        window_type=hints.get("window_type"),
    )


def enrich_market(rec: DiscoveryRecord) -> DiscoveryRecord:
    """Lightweight enrich stub — uses already-known fields; live fetch optional later."""
    # Prospectively log latency features
    rec.discovery_latency_features = {
        **(rec.discovery_latency_features or {}),
        "source": rec.first_source,
        "first_seen_at": rec.first_seen_at.isoformat(),
        "mc_at_first_seen": rec.mc_usd,
        "liquidity_usd": rec.liquidity_usd,
        "enriched_at": datetime.now(timezone.utc).isoformat(),
    }
    return rec


def _refresh_candidate_from_record(
    cand: CandidateV1, rec: DiscoveryRecord, gate: GateResult
) -> CandidateV1:
    """Merge latest discovery sources/evidence onto an existing candidate."""
    cand.decision = gate.decision
    cand.reject_reason = gate.reject_reason
    srcs = [rec.first_source, *[s for s in rec.sources if s != rec.first_source]]
    # Keep prior accounts, append new
    merged_accounts = list(dict.fromkeys([*(cand.source_accounts or []), *srcs]))
    cand.source_accounts = merged_accounts
    # Prefer freshest evidence from full record (refs always [])
    cand.evidence = _evidence_from_record(rec)
    fs = _feature_scores_from_hints(rec)
    if fs is not None:
        cand.feature_scores = fs
    if (rec.confidence_hints or {}).get("window_type"):
        cand.window_type = rec.confidence_hints.get("window_type")
    data = cand.model_dump(mode="json")
    data["discovery"] = {
        "first_source": rec.first_source,
        "sources": list(rec.sources),
        "lagging_universe": rec.lagging_universe,
        "discovery_latency_features": rec.discovery_latency_features,
        "gate_reasons": gate.reasons,
        "gate_warnings": gate.warnings,
    }
    return CandidateV1.model_validate(data)


def upsert_candidate(
    rec: DiscoveryRecord,
    gate: GateResult,
    *,
    ledger: CandidateLedger,
) -> CandidateV1:
    """Write / update candidate ledger row from discovery record + gate."""
    # Reuse existing candidate if bus already linked
    if rec.candidate_id:
        existing = ledger.get_candidate(rec.candidate_id)
        if existing is not None:
            existing = _refresh_candidate_from_record(existing, rec, gate)
            ledger.save_candidate(existing, append_index=False)
            return existing

    # Search by ca+chain among recent candidates
    for c in ledger.list_candidates():
        if c.contract_address == rec.ca and c.chain == rec.chain:
            c = _refresh_candidate_from_record(c, rec, gate)
            rec.candidate_id = str(c.candidate_id)
            ledger.save_candidate(c, append_index=False)
            return c

    fs = _feature_scores_from_hints(rec)
    cand = CandidateV1(
        candidate_id=uuid4(),
        first_seen_at=rec.first_seen_at,
        contract_address=rec.ca,
        chain=rec.chain,
        ticker=rec.ticker,
        mc_usd_at_first_sight=rec.mc_usd,
        decision=gate.decision,
        reject_reason=gate.reject_reason,
        evidence=_evidence_from_record(rec),
        source_accounts=[rec.first_source, *[s for s in rec.sources if s != rec.first_source]],
        feature_scores=fs,
        window_type=(rec.confidence_hints or {}).get("window_type"),
    )
    # stash discovery meta as extra
    extra = {
        "discovery": {
            "first_source": rec.first_source,
            "sources": list(rec.sources),
            "lagging_universe": rec.lagging_universe,
            "discovery_latency_features": rec.discovery_latency_features,
            "gate_reasons": gate.reasons,
            "gate_warnings": gate.warnings,
        }
    }
    # Pydantic extra=allow — revalidate with extras
    data = cand.model_dump(mode="json")
    data.update(extra)
    cand = CandidateV1.model_validate(data)
    ledger.save_candidate(cand)
    rec.candidate_id = str(cand.candidate_id)
    rec.last_decision = gate.decision.value
    return cand


def maybe_emit_buy(
    cand: CandidateV1,
    gate: GateResult,
    *,
    ledger: CandidateLedger,
    emit: bool = True,
) -> Optional[Any]:
    if not emit or gate.decision != CandidateDecision.pursue or not gate.pursue_eligible:
        return None
    try:
        decision = emit_pursue_buy(cand, ledger=ledger, persist=True)
        # TTL already 300s via default_expires_at; assert armed policy
        assert decision.do_not_execute_until_armed == do_not_execute_until_armed()
        log.info(
            "discovery pursue→BUY decision_id=%s armed=%s do_not_execute=%s ttl=%ss",
            decision.decision_id,
            is_armed(),
            decision.do_not_execute_until_armed,
            BUY_TTL_SECONDS,
        )
        return decision
    except GateReject as e:
        log.info("pursue candidate failed BUY gates: %s", e.reason)
        return None


def ingest_event(
    event: DiscoveryEvent,
    *,
    bus: Optional[DiscoveryBus] = None,
    ledger: Optional[CandidateLedger] = None,
    fanout: bool = True,
    emit_buy: bool = True,
    data_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Full path for one discovery event.

    Returns summary dict for runner/logging.
    """
    root = data_root or data_dir()
    led = ledger or CandidateLedger(RepoPaths(data=root))
    discovery_bus = bus or DiscoveryBus(store_path=root / "discovery_bus.json")

    rec, is_first = discovery_bus.upsert(event)
    rec = enrich_market(rec)
    gate = score_discovery(rec, enriched_liq_usd=rec.liquidity_usd)
    cand = upsert_candidate(rec, gate, ledger=led)
    discovery_bus.persist()

    work_paths: list[Path] = []
    if fanout and is_first:
        work_paths = write_work_orders(rec, data_root=root)

    decision = None
    if is_first or gate.decision == CandidateDecision.pursue:
        # Only emit BUY once when newly pursuing
        if gate.decision == CandidateDecision.pursue:
            decision = maybe_emit_buy(cand, gate, ledger=led, emit=emit_buy)

    return {
        "is_first": is_first,
        "chain": rec.chain,
        "ca": rec.ca,
        "first_source": rec.first_source,
        "sources": list(rec.sources),
        "decision": gate.decision.value,
        "reasons": gate.reasons,
        "warnings": gate.warnings,
        "candidate_id": str(cand.candidate_id),
        "buy_decision_id": str(decision.decision_id) if decision else None,
        "do_not_execute_until_armed": (
            decision.do_not_execute_until_armed if decision else do_not_execute_until_armed()
        ),
        "fanout_paths": [str(p) for p in work_paths],
        "discovery_latency_features": rec.discovery_latency_features,
    }
