"""Anti-hindsight feature freeze tests."""

from datetime import datetime, timezone

from x_intel.schemas.models import FeatureScoreEntry, FeatureScoresV1
from x_intel.scoring.freeze import (
    freeze_features_at_t,
    score_with_frozen_weights,
)


def _scores(**values):
    entries = {}
    meta_dir = {
        "D1": "bearish",
        "S2": "bullish",
        "S3": "bullish",
        "S4": "context",
        "D2": "bearish",
        "S5": "bullish",
        "D4": "bearish",
        "S1": "context",
    }
    for fid, direction in meta_dir.items():
        entries[fid] = FeatureScoreEntry(
            feature_id=fid,
            value=values.get(fid),
            direction=direction,  # type: ignore[arg-type]
        )
    return FeatureScoresV1(scores=entries, scored_at=datetime.now(timezone.utc))


def test_freeze_deep_copy_immune_to_mutation():
    fs = _scores(D1=True, S5=True)
    fr = freeze_features_at_t(fs)
    # mutate original after freeze
    fs.scores["D1"].value = False
    assert fr.scores["D1"] is True
    assert fr.scores["S5"] is True


def test_freeze_nulls_for_missing():
    fr = freeze_features_at_t(None)
    assert all(v is None for v in fr.scores.values())


def test_score_inspectable_weights():
    fr = freeze_features_at_t(_scores(D1=True, D2=False, S3=True, S2="first"))
    result = score_with_frozen_weights(fr)
    assert "contributions" in result
    assert "weights" in result
    assert result["provisional"] is True
    # D1 True with negative weight contributes negative
    assert result["contributions"]["D1"] < 0
    assert result["contributions"]["S3"] > 0
