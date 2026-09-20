"""Ledger loads real fixture candidates."""

from x_intel.ledger.store import CandidateLedger, default_paths


def test_list_fixture_candidates():
    ledger = CandidateLedger(default_paths())
    cands = ledger.list_candidates(experiment_id="xintel_v0")
    assert len(cands) >= 1
    assert all(c.schema_version == "xintel.candidate.v1" for c in cands)


def test_leaderboard_stub():
    lb = CandidateLedger(default_paths()).account_leaderboard_stub()
    assert lb["experiment_id"] == "xintel_v0"
    assert "by_decision" in lb
