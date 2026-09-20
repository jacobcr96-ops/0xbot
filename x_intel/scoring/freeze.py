"""Anti-hindsight feature freeze.

Scores are frozen at detection time T. Later market outcomes must not rewrite
feature values. Weights are inspectable and versioned under experiment_id.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from x_intel.schemas.models import FeatureScoreEntry, FeatureScoresV1

BATCH_01_FEATURE_IDS = ("D1", "S2", "S3", "S4", "D2", "S5", "D4", "S1")

# Inspectable provisional weights for xintel_v0 (not calibrated OOS — placeholders).
# Positive = bullish contribution when feature fires in its natural direction.
DEFAULT_WEIGHTS: dict[str, float] = {
    "D1": -1.0,  # bundled concentration — bearish
    "S2": 0.8,   # first mint — bullish (enum handled separately)
    "S3": 0.6,   # lore-native amplifier
    "S4": 0.0,   # context only
    "D2": -0.7,  # post-leader copycat
    "S5": 0.3,   # LP/mint/CTO custody — weak alone
    "D4": -0.5,  # early CA migration
    "S1": 0.2,   # cultural prior — context/biased
}

FEATURE_META: dict[str, dict[str, str]] = {
    "D1": {"direction": "bearish", "desk": "Flow"},
    "S2": {"direction": "bullish", "desk": "Narrative"},
    "S3": {"direction": "bullish", "desk": "X Scout"},
    "S4": {"direction": "context", "desk": "Market"},
    "D2": {"direction": "bearish", "desk": "Narrative"},
    "S5": {"direction": "bullish", "desk": "Flow"},
    "D4": {"direction": "bearish", "desk": "Market"},
    "S1": {"direction": "context", "desk": "Narrative"},
}


@dataclass(frozen=True)
class FeatureFreeze:
    """Immutable snapshot of feature values + weights at time T."""

    experiment_id: str
    frozen_at: datetime
    scores: Mapping[str, Any]
    weights: Mapping[str, float]
    provisional: bool = True
    batch: str = "BATCH_01"

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "frozen_at": self.frozen_at.isoformat(),
            "scores": dict(self.scores),
            "weights": dict(self.weights),
            "provisional": self.provisional,
            "batch": self.batch,
        }


def freeze_features_at_t(
    feature_scores: FeatureScoresV1 | dict[str, Any] | None,
    *,
    experiment_id: str = "xintel_v0",
    weights: Optional[Mapping[str, float]] = None,
    frozen_at: Optional[datetime] = None,
) -> FeatureFreeze:
    """Freeze observable-at-T feature values. Deep-copy so later mutation cannot rewrite.

    Missing scores become None. Never backfills from post-T outcomes.
    """
    frozen_at = frozen_at or datetime.now(timezone.utc)
    w = dict(weights or DEFAULT_WEIGHTS)

    raw_scores: dict[str, Any] = {}
    if feature_scores is None:
        for fid in BATCH_01_FEATURE_IDS:
            raw_scores[fid] = None
    elif isinstance(feature_scores, FeatureScoresV1):
        for fid in BATCH_01_FEATURE_IDS:
            entry = feature_scores.scores.get(fid)
            raw_scores[fid] = None if entry is None else deepcopy(entry.value)
    else:
        scores_block = feature_scores.get("scores") or feature_scores
        for fid in BATCH_01_FEATURE_IDS:
            entry = scores_block.get(fid) if isinstance(scores_block, dict) else None
            if isinstance(entry, dict):
                raw_scores[fid] = deepcopy(entry.get("value"))
            else:
                raw_scores[fid] = deepcopy(entry)

    return FeatureFreeze(
        experiment_id=experiment_id,
        frozen_at=frozen_at,
        scores=raw_scores,
        weights=w,
        provisional=True,
        batch="BATCH_01",
    )


def _contrib(feature_id: str, value: Any, weight: float) -> float:
    if value is None:
        return 0.0
    direction = FEATURE_META.get(feature_id, {}).get("direction", "context")
    if direction == "context":
        return 0.0
    if isinstance(value, bool):
        fired = value
        # bearish features: True contributes negative weight
        if direction == "bearish":
            return weight if fired else 0.0  # weight already negative for bearish
        return weight if fired else 0.0
    if feature_id == "S2" and isinstance(value, str):
        mapping = {"first": 1.0, "early": 0.5, "late_challenger": -0.8, "unknown": 0.0}
        return weight * mapping.get(value, 0.0)
    if isinstance(value, (int, float)):
        return weight * float(value)
    return 0.0


def score_with_frozen_weights(freeze: FeatureFreeze) -> dict[str, Any]:
    """Inspectable weighted sum over frozen features. Does not mutate freeze."""
    contributions: dict[str, float] = {}
    total = 0.0
    for fid in BATCH_01_FEATURE_IDS:
        c = _contrib(fid, freeze.scores.get(fid), freeze.weights.get(fid, 0.0))
        contributions[fid] = c
        total += c
    return {
        "experiment_id": freeze.experiment_id,
        "frozen_at": freeze.frozen_at.isoformat(),
        "total": total,
        "contributions": contributions,
        "weights": dict(freeze.weights),
        "provisional": freeze.provisional,
        "note": "Weights provisional under xintel_v0 — not calibrated OOS.",
    }


def empty_feature_scores_stub(
    experiment_id: str = "xintel_v0",
    scored_by: str = "stub",
) -> FeatureScoresV1:
    scores = {}
    for fid in BATCH_01_FEATURE_IDS:
        meta = FEATURE_META[fid]
        scores[fid] = FeatureScoreEntry(
            feature_id=fid,
            value=None,
            direction=meta["direction"],  # type: ignore[arg-type]
            desk=meta["desk"],
            notes="null until observed at T",
        )
    return FeatureScoresV1(
        experiment_id=experiment_id,
        scored_at=datetime.now(timezone.utc),
        scored_by=scored_by,
        scores=scores,
    )
