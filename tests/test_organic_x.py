"""Organic X disk-cache hints + ping preference."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from x_intel.discovery.gates import (
    has_ping_quality_evidence,
    has_publish_quality_evidence,
    score_discovery,
)
from x_intel.discovery.models import DiscoveryRecord
from x_intel.discovery.organic_x import (
    annotate_organic_x_hints,
    evaluate_organic_payload,
    write_organic_payload,
)
from x_intel.schemas.models import CandidateDecision

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "organic_x"
SCAT_CA = "He5XcCaLrygisurxfDYH7vumKW5yopVZcA9DKPvpH4Lz"
NOW = datetime(2026, 9, 20, 23, 55, tzinfo=timezone.utc)


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    for sub in ("candidates", "decisions", "outcomes", "execution_reports", "x_organic"):
        (data / sub).mkdir(parents=True)
    monkeypatch.setenv("XINTEL_DATA_DIR", str(data))
    monkeypatch.delenv("XINTEL_ARMED", raising=False)
    monkeypatch.setenv("XINTEL_SKIP_ENRICH", "true")
    return data


def _rec(**kwargs) -> DiscoveryRecord:
    base = dict(
        chain="solana",
        ca=SCAT_CA,
        first_source="pumpfun_curve",
        first_seen_at=NOW - timedelta(minutes=5),
        sources=["pumpfun_curve"],
        ticker="SCAT",
        name="Shielded Cat",
        mc_usd=276_000,
        pair_created_at=NOW - timedelta(minutes=8),
        confidence_hints={},
    )
    base.update(kwargs)
    return DiscoveryRecord(**base)


def test_evaluate_fixture_sets_organic_x():
    payload = json.loads((FIXTURES / "sample_mint.json").read_text())
    result = evaluate_organic_payload(SCAT_CA, payload, now=NOW)
    assert result["organic_x"] is True
    assert result["x_social"] is True
    assert result["matching_n"] >= 1
    assert "recent_ca_mention" in result["reasons"]


def test_pump_twitter_reuse_rejected():
    payload = json.loads((FIXTURES / "pump_reuse.json").read_text())
    # Post text is only the pump status URL and does NOT mention CA → no match
    result = evaluate_organic_payload(
        SCAT_CA,
        payload,
        now=NOW,
        pump_twitter="https://x.com/Noorii__5/status/2101759322233417764",
    )
    assert result["organic_x"] is False


def test_ticker_only_not_organic():
    payload = {
        "query": SCAT_CA,
        "queried_at": NOW.isoformat(),
        "posts": [
            {
                "id": "1",
                "text": "$SCAT to the moon boys",
                "author": "spam",
                "created_at": (NOW - timedelta(minutes=10)).isoformat(),
                "url": "https://x.com/spam/status/1",
                "metrics": {},
            }
        ],
    }
    result = evaluate_organic_payload(SCAT_CA, payload, now=NOW)
    assert result["organic_x"] is False
    assert result["matching_n"] == 0


def test_annotate_from_disk_fixture(tmp_data: Path):
    payload = json.loads((FIXTURES / "sample_mint.json").read_text())
    write_organic_payload(SCAT_CA, payload, data_root=tmp_data)
    rec = _rec()
    annotate_organic_x_hints(rec, data_root=tmp_data, now=NOW)
    assert rec.confidence_hints.get("organic_x") is True
    assert rec.confidence_hints.get("x_social") is True
    assert "x_social" in rec.sources
    assert has_publish_quality_evidence(rec) is True


def test_organic_x_alone_pursue_eligible(tmp_data: Path):
    payload = json.loads((FIXTURES / "sample_mint.json").read_text())
    write_organic_payload(SCAT_CA, payload, data_root=tmp_data)
    rec = _rec(
        confidence_hints={},  # no curve social reinforcement
    )
    annotate_organic_x_hints(rec, data_root=tmp_data, now=NOW)
    gate = score_discovery(rec, now=NOW)
    assert gate.decision == CandidateDecision.pursue
    assert gate.pursue_eligible is True
    assert "publish_quality_evidence" in gate.reasons


def test_ping_requires_organic_or_strong_unique(tmp_data: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XINTEL_ORGANIC_X_REQUIRED_FOR_PING", "1")
    # Curve + status twitter only (HELLO-like) — publish may fail reinforcement,
    # ping must fail.
    hello = _rec(
        ticker="HELLO",
        ca="HelloFarm1111111111111111111111111111111pump",
        mc_usd=3_000,
        confidence_hints={
            "pump_curve": True,
            "verified_social": True,
            "pump_twitter": "https://x.com/foo/status/1234567890123456789",
            "mc_source": "pump.fun",
            "mc_live_early": True,
            "spam_farm_ticker": True,
        },
    )
    assert has_ping_quality_evidence(hello) is False

    # Real organic_x → ping ok
    payload = json.loads((FIXTURES / "sample_mint.json").read_text())
    write_organic_payload(SCAT_CA, payload, data_root=tmp_data)
    scat = _rec()
    annotate_organic_x_hints(scat, data_root=tmp_data, now=NOW)
    assert has_ping_quality_evidence(scat) is True

    # Strong unique curve+profile without organic_x → ping ok
    unique = _rec(
        confidence_hints={
            "pump_curve": True,
            "verified_social": True,
            "pump_twitter": "https://x.com/ShieldedCat",
            "ticker_unique_recent": True,
            "mc_source": "pump.fun",
            "mc_live_early": True,
        },
    )
    assert has_ping_quality_evidence(unique) is True


def test_ingest_cli_from_json(tmp_data: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XINTEL_DATA_DIR", str(tmp_data))
    from x_intel.tools.organic_x_ingest import main

    src = FIXTURES / "sample_mint.json"
    rc = main(["--mint", SCAT_CA, "--from-json", str(src), "--data-dir", str(tmp_data)])
    assert rc == 0
    out = tmp_data / "x_organic" / f"{SCAT_CA}.json"
    assert out.is_file()
    raw = json.loads(out.read_text())
    assert raw["query"] == SCAT_CA
    assert len(raw["posts"]) >= 1
