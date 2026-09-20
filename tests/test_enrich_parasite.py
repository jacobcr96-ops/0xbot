"""Tests for live enrich + parasite-by-CA (not ticker-alone)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from x_intel.discovery.bus import DiscoveryBus
from x_intel.discovery.enrich import (
    apply_quote_to_record,
    enrich_record,
    is_real_social_url,
    quote_mint,
)
from x_intel.discovery.gates import has_publish_quality_evidence, score_discovery
from x_intel.discovery.models import DiscoveryEvent, DiscoveryRecord
from x_intel.discovery.parasite import (
    KNOWN_RUNNER_CAS,
    detect_parasite_by_ca,
    mimics_known_runner,
)
from x_intel.discovery.pipeline import enrich_market, ingest_event
from x_intel.ledger.store import CandidateLedger, RepoPaths
from x_intel.schemas.models import CandidateDecision

STAMP_CA = KNOWN_RUNNER_CAS["STAMP"]
SCAT_CA = "He5XcCaLrygisurxfDYH7vumKW5yopVZcA9DKPvpH4Lz"
STAMP_CLONE_CA = "Mkxb4wvrkjRMPumpC1one1111111111111111111"


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    for sub in (
        "candidates",
        "decisions",
        "outcomes",
        "execution_reports",
        "fomo_inbox",
        "flow_inbox",
        "agent_inbox",
        "health",
    ):
        (data / sub).mkdir(parents=True)
    monkeypatch.setenv("XINTEL_DATA_DIR", str(data))
    monkeypatch.delenv("XINTEL_ARMED", raising=False)
    monkeypatch.setenv("XINTEL_SKIP_ENRICH", "true")
    monkeypatch.setenv("XINTEL_SKIP_DEX", "true")
    return data


def _rec(**kwargs: Any) -> DiscoveryRecord:
    base = dict(
        chain="solana",
        ca="Ear1yDiscoSo11111111111111111111111111112",
        first_source="pumpfun_curve",
        first_seen_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        sources=["pumpfun_curve"],
        ticker="EARLY",
        mc_usd=40_000,
        pair_created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        confidence_hints={"pump_curve": True},
    )
    base.update(kwargs)
    return DiscoveryRecord(**base)


def test_is_real_social_url():
    assert is_real_social_url("https://x.com/foo/status/1")
    assert is_real_social_url("https://twitter.com/bar")
    assert is_real_social_url("https://t.me/baz")
    assert not is_real_social_url("")
    assert not is_real_social_url("n/a")
    assert not is_real_social_url("discord.gg/xyz")


def test_mimics_stamp_not_scat():
    assert mimics_known_runner("STAMPDOG", "STAMP")
    assert mimics_known_runner("Stamp Pepe", "STAMP")
    assert mimics_known_runner("STAMPXMR", "STAMP")
    assert mimics_known_runner("STAMP", "STAMP")
    assert not mimics_known_runner("SCAT", "STAMP")
    assert not mimics_known_runner("Shielded Cat", "STAMP")
    assert not mimics_known_runner("SCAT Shielded Cat", "STAMP")


def test_parasite_by_ca_rejects_stamp_clone_keeps_scat():
    clone = _rec(ca=STAMP_CLONE_CA, ticker="STAMP", name="STAMP")
    assert detect_parasite_by_ca(clone) == "name_parasite_heuristic"

    authentic = _rec(ca=STAMP_CA, ticker="STAMP", name="STAMP")
    assert detect_parasite_by_ca(authentic) is None

    scat = _rec(ca=SCAT_CA, ticker="SCAT", name="Shielded Cat")
    assert detect_parasite_by_ca(scat) is None


def test_score_rejects_stamp_clone_not_scat():
    clone = _rec(ca=STAMP_CLONE_CA, ticker="STAMPDOG", name="STAMPDOG")
    gate = score_discovery(clone)
    assert gate.decision == CandidateDecision.reject
    assert "name_parasite_heuristic" in gate.reasons

    scat = _rec(
        ca=SCAT_CA,
        ticker="SCAT",
        name="Shielded Cat",
        mc_usd=250_000,
        confidence_hints={
            "pump_curve": True,
            "verified_social": True,
            "pump_twitter": "https://x.com/Noorii__5/status/1",
            "mc_source": "pump.fun",
            "mc_live_early": True,
        },
    )
    gate = score_discovery(scat)
    assert gate.decision == CandidateDecision.pursue
    assert gate.pursue_eligible is True
    assert "publish_quality_evidence" in gate.reasons


def test_curve_plus_social_quality_not_weak_alone():
    # Weak social alone (no curve context beyond missing) on dex-only → no quality
    thin = _rec(
        ca=SCAT_CA,
        first_source="dexscreener_new",
        sources=["dexscreener_new"],
        confidence_hints={
            "verified_social": True,
            "pump_twitter": "https://x.com/foo",
            "mc_source": "pump.fun",
            "mc_live_early": True,
        },
    )
    # dexscreener alone without pump_curve → thin reject before quality
    gate = score_discovery(thin)
    assert gate.decision == CandidateDecision.reject

    # Curve + social + early MC → publish quality
    good = _rec(
        ca=SCAT_CA,
        ticker="SCAT",
        name="Shielded Cat",
        mc_usd=200_000,
        confidence_hints={
            "pump_curve": True,
            "verified_social": True,
            "pump_twitter": "https://x.com/foo/status/1",
            "mc_source": "pump.fun",
            "mc_live_early": True,
        },
    )
    assert has_publish_quality_evidence(good) is True


def test_apply_quote_sets_identity_and_social():
    rec = _rec(ca=SCAT_CA, ticker=None, mc_usd=None)
    quote = {
        "ok": True,
        "mint": SCAT_CA,
        "ticker": "SCAT",
        "name": "Shielded Cat",
        "mc_usd": 243_165.5,
        "price_usd": None,
        "liq_usd": 48_000.0,
        "twitter": "https://x.com/Noorii__5/status/2101759322233417764",
        "telegram": "",
        "source": "pump.fun",
        "virtual_sol_reserves": 30.0,
    }
    apply_quote_to_record(rec, quote)
    assert rec.mc_usd == pytest.approx(243_165.5)
    assert rec.ticker == "SCAT"
    assert rec.name == "Shielded Cat"
    assert rec.mc_source == "pump.fun"
    assert rec.liquidity_usd == pytest.approx(48_000.0)
    assert rec.confidence_hints.get("verified_social") is True
    assert rec.confidence_hints.get("volume_flow_hint") is True
    assert rec.discovery_latency_features.get("enrich_ok") is True


def test_enrich_market_not_noop_stub(monkeypatch: pytest.MonkeyPatch):
    """Even with SKIP_ENRICH, enrich_market stamps enriched_at (never silent stub)."""
    monkeypatch.setenv("XINTEL_SKIP_ENRICH", "true")
    rec = _rec()
    out = enrich_market(rec)
    assert out.discovery_latency_features.get("enriched_at")
    assert "enrich_ok" in out.discovery_latency_features


def test_enrich_record_uses_quote(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("XINTEL_SKIP_ENRICH", raising=False)

    fake = {
        "ok": True,
        "mint": SCAT_CA,
        "ticker": "SCAT",
        "name": "Shielded Cat",
        "mc_usd": 100_000.0,
        "liq_usd": 10_000.0,
        "twitter": "https://x.com/a/status/1",
        "telegram": "",
        "source": "pump.fun",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "age_sec": 0,
    }
    with patch("x_intel.discovery.enrich.quote_mint", return_value=fake):
        rec = _rec(ca=SCAT_CA, mc_usd=None, ticker="SCAT")
        out = enrich_record(rec, force=True)
    assert out.mc_usd == 100_000.0
    assert out.name == "Shielded Cat"
    assert out.confidence_hints.get("pump_twitter")


def test_ingest_keeps_name_identity(tmp_data: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XINTEL_SKIP_ENRICH", "true")
    bus = DiscoveryBus(store_path=tmp_data / "discovery_bus.json")
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    ev = DiscoveryEvent(
        source="pumpfun_curve",
        discovered_at=datetime.now(timezone.utc),
        chain="solana",
        ca=SCAT_CA,
        ticker="SCAT",
        name="Shielded Cat",
        symbol="SCAT",
        mc_usd=200_000,
        pair_created_at=datetime.now(timezone.utc) - timedelta(minutes=3),
        confidence_hints={
            "pump_curve": True,
            "verified_social": True,
            "pump_twitter": "https://x.com/foo/status/1",
            "mc_source": "pump.fun",
            "mc_live_early": True,
            "name": "Shielded Cat",
        },
    )
    # Manually enrich-like hints already on event; skip live HTTP
    summary = ingest_event(ev, bus=bus, ledger=ledger, fanout=True, emit_buy=False, data_root=tmp_data)
    assert summary["decision"] == "pursue"
    assert summary["pursue_eligible"] is True
    rec = bus.get("solana", SCAT_CA)
    assert rec is not None
    assert rec.name == "Shielded Cat"
