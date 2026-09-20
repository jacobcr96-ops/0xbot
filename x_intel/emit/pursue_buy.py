"""Turn a pursue candidate into a BUY decision when soft gates pass.

Emits to the disk decision queue via CandidateLedger.save_decision.
Never places trades.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from x_intel.config import (
    DEFAULT_BUY_PERCENT_EQUITY,
    DEFAULT_EARLY_MC_USD_MAX,
    do_not_execute_until_armed,
    is_armed,
)
from x_intel.ledger.store import CandidateLedger, default_paths
from x_intel.schemas.models import (
    CandidateDecision,
    CandidateV1,
    DecisionAction,
    DecisionV1,
    EvidenceItem,
    MarketSnapshot,
    SizingIntent,
    default_expires_at,
)

log = logging.getLogger(__name__)

KNOWN_CHAINS = {
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
}


class GateReject(Exception):
    """Soft/hard gate failed — do not emit BUY."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _feature_value(candidate: CandidateV1, feature_id: str) -> Any:
    fs = candidate.feature_scores
    if fs is None or not fs.scores:
        return None
    entry = fs.scores.get(feature_id)
    if entry is None:
        return None
    return entry.value


def _window_type(candidate: CandidateV1) -> Optional[str]:
    if candidate.window_type:
        return candidate.window_type
    fs = candidate.feature_scores
    if fs is None:
        return None
    if fs.window_type:
        return fs.window_type
    # S2 entry may carry window_type as extra
    s2 = fs.scores.get("S2") if fs.scores else None
    if s2 is not None:
        extra = getattr(s2, "__pydantic_extra__", None) or {}
        wt = extra.get("window_type")
        if wt:
            return str(wt)
        if hasattr(s2, "window_type"):
            return getattr(s2, "window_type")
    return None


def _normalize_evidence(candidate: CandidateV1) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    now = datetime.now(timezone.utc)
    for raw in candidate.evidence or []:
        if isinstance(raw, EvidenceItem):
            items.append(raw)
            continue
        if not isinstance(raw, dict):
            continue
        channel = raw.get("channel")
        # Map non-schema channels (e.g. outcome_desk) into cross_source for decision handoff
        if channel not in {
            "x_social",
            "onchain_flow",
            "narrative",
            "launch_metrics",
            "wallet_behavior",
            "cross_source",
            "historical_analog",
        }:
            channel = "cross_source"
        obs = raw.get("observed_at") or candidate.first_seen_at or now
        if isinstance(obs, str):
            obs = datetime.fromisoformat(obs.replace("Z", "+00:00"))
        items.append(
            EvidenceItem(
                channel=channel,  # type: ignore[arg-type]
                summary=str(raw.get("summary") or ""),
                observed_at=obs,
                refs=raw.get("refs"),
                weight=raw.get("weight"),
            )
        )
    return items


def check_pursue_buy_gates(
    candidate: CandidateV1,
    *,
    early_mc_usd_max: Optional[float] = DEFAULT_EARLY_MC_USD_MAX,
) -> list[str]:
    """Return warning strings; raise GateReject on hard failures."""
    warnings_out: list[str] = []

    if candidate.decision != CandidateDecision.pursue:
        raise GateReject(f"candidate decision is {candidate.decision.value!r}, need pursue")

    ca = (candidate.contract_address or "").strip()
    if not ca or ca.upper().startswith("TEMPLATE"):
        raise GateReject("missing or template contract_address")

    chain = (candidate.chain or "").strip().lower()
    if not chain:
        raise GateReject("missing chain")
    if chain not in KNOWN_CHAINS:
        warnings_out.append(f"chain {chain!r} not in known set; mapping to other")

    evidence = _normalize_evidence(candidate)
    if not evidence:
        raise GateReject("multi-channel evidence empty")

    mc = candidate.mc_usd_at_first_sight
    # Prefer detection MC if present as extra
    extra = getattr(candidate, "__pydantic_extra__", None) or {}
    if extra.get("mc_usd_at_detection") is not None:
        mc = extra["mc_usd_at_detection"]
    if mc is None:
        warnings_out.append("mc_usd_at_first_sight is null — early-MC gate skipped with warning")
    elif early_mc_usd_max is not None and mc > early_mc_usd_max:
        raise GateReject(
            f"mc_usd {mc} exceeds early MC threshold {early_mc_usd_max}"
        )

    # Feature gates (soft for v0) — only when feature_scores present
    fs = candidate.feature_scores
    if fs is not None and fs.scores:
        d1 = _feature_value(candidate, "D1")
        if d1 is True:
            raise GateReject("feature gate: D1=true (bundled concentration)")

        s1 = _feature_value(candidate, "S1")
        s3 = _feature_value(candidate, "S3")
        # Require S1 true OR S3 true when scores exist and either is scored boolean
        scored_bools = [v for v in (s1, s3) if isinstance(v, bool)]
        if scored_bools:
            if not (s1 is True or s3 is True):
                raise GateReject("feature gate: require S1=true OR S3=true when scores exist")
        else:
            # scores object present but S1/S3 null — soft warn
            warnings_out.append("S1/S3 unset — soft pass (v0)")

        window = _window_type(candidate)
        if window != "frenzy-lane":
            s2 = _feature_value(candidate, "S2")
            if s2 is not None and s2 not in ("first", "early", True):
                # Soft for v0: warn rather than hard-reject late/unknown unless clearly late
                if s2 == "late_challenger":
                    raise GateReject("feature gate: S2=late_challenger (non-frenzy)")
                warnings_out.append(f"S2={s2!r} — soft warn (non-frenzy)")
        else:
            warnings_out.append("window_type=frenzy-lane — S2 not required")

    return warnings_out


def pursue_candidate_to_buy(
    candidate: CandidateV1,
    *,
    confidence: float = 0.55,
    percent_equity: float = DEFAULT_BUY_PERCENT_EQUITY,
    early_mc_usd_max: Optional[float] = DEFAULT_EARLY_MC_USD_MAX,
    thesis: Optional[str] = None,
    issued_at: Optional[datetime] = None,
) -> tuple[DecisionV1, list[str]]:
    """Build a BUY DecisionV1 from a pursue candidate. Raises GateReject on fail."""
    warns = check_pursue_buy_gates(candidate, early_mc_usd_max=early_mc_usd_max)
    for w in warns:
        warnings.warn(w, stacklevel=2)
        log.warning(w)

    now = issued_at or datetime.now(timezone.utc)
    chain = (candidate.chain or "other").strip().lower()
    if chain not in KNOWN_CHAINS:
        chain = "other"

    evidence = _normalize_evidence(candidate)
    armed_flag = do_not_execute_until_armed()

    decision = DecisionV1(
        decision_id=uuid4(),
        action=DecisionAction.BUY,
        issued_at=now,
        expires_at=default_expires_at(DecisionAction.BUY, now),
        contract_address=candidate.contract_address.strip(),
        chain=chain,  # type: ignore[arg-type]
        ticker=candidate.ticker,
        confidence=confidence,
        thesis=thesis
        or f"pursue→BUY stub for {candidate.ticker or candidate.contract_address[:8]}",
        sizing_intent=SizingIntent(
            mode="percent_equity",
            value=percent_equity,
            urgency="normal",
        ),
        market_snapshot=MarketSnapshot(
            mc_usd=candidate.mc_usd_at_first_sight,
            price_usd=candidate.price_usd_at_first_sight,
            as_of=candidate.first_seen_at,
        ),
        evidence=evidence,
        risk_flags=["pursue_buy_emitter", "v0_soft_gates"],
        experiment_id=candidate.experiment_id,
        candidate_id=str(candidate.candidate_id),
        do_not_execute_until_armed=armed_flag,
    )
    return decision, warns


def emit_pursue_buy(
    candidate: CandidateV1 | str | Path,
    *,
    ledger: Optional[CandidateLedger] = None,
    persist: bool = True,
    **kwargs: Any,
) -> DecisionV1:
    """Resolve candidate, gate, build BUY, optionally write disk queue."""
    led = ledger or CandidateLedger(default_paths())
    if isinstance(candidate, CandidateV1):
        cand = candidate
    else:
        ref = str(candidate)
        path = Path(ref)
        if path.is_file():
            cand = CandidateV1.model_validate(json.loads(path.read_text()))
        else:
            # treat as id
            cid = path.stem if ref.endswith(".json") else ref
            loaded = led.get_candidate(cid)
            if loaded is None:
                raise FileNotFoundError(f"candidate not found: {ref}")
            cand = loaded

    decision, _warns = pursue_candidate_to_buy(cand, **kwargs)
    if persist:
        path = led.save_decision(decision)
        log.info(
            "emitted BUY decision_id=%s path=%s armed=%s do_not_execute=%s",
            decision.decision_id,
            path,
            is_armed(),
            decision.do_not_execute_until_armed,
        )
    return decision


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit BUY decision from a pursue candidate (disk queue). Never places trades."
    )
    parser.add_argument(
        "--candidate",
        required=True,
        help="Candidate UUID or path to candidate JSON",
    )
    parser.add_argument("--confidence", type=float, default=0.55)
    parser.add_argument(
        "--percent-equity",
        type=float,
        default=DEFAULT_BUY_PERCENT_EQUITY,
    )
    parser.add_argument(
        "--early-mc-max",
        type=float,
        default=DEFAULT_EARLY_MC_USD_MAX,
        help="Reject if mc_usd_at_first_sight above this (default 500000)",
    )
    parser.add_argument(
        "--allow-any-mc",
        action="store_true",
        help="Disable early MC max gate (null still warns)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write disk queue")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    early_max = None if args.allow_any_mc else args.early_mc_max

    try:
        decision = emit_pursue_buy(
            args.candidate,
            persist=not args.dry_run,
            confidence=args.confidence,
            percent_equity=args.percent_equity,
            early_mc_usd_max=early_max,
        )
    except GateReject as e:
        print(f"REJECT: {e.reason}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(decision.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
