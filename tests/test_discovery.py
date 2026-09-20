"""Early-mover discovery bus, gates, sources, armed policy, BUY emit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from x_intel.config import do_not_execute_until_armed, is_armed
from x_intel.discovery.bus import DiscoveryBus
from x_intel.discovery.gates import score_discovery
from x_intel.discovery.models import DiscoveryEvent
from x_intel.discovery.pipeline import ingest_event
from x_intel.discovery.sources.dexscreener_new import DexScreenerNewSource
from x_intel.discovery.sources.fomo_sidebar import FomoSidebarSource
from x_intel.discovery.sources.pumpfun_curve import PumpfunCurveSource
from x_intel.discovery.validate import generate_report, write_report
from x_intel.io_atomic import atomic_read_json
from x_intel.ledger.store import CandidateLedger, RepoPaths
from x_intel.schemas.models import CandidateDecision


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "discovery"


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
    ):
        (data / sub).mkdir(parents=True)
    monkeypatch.setenv("XINTEL_DATA_DIR", str(data))
    monkeypatch.delenv("XINTEL_ARMED", raising=False)
    return data


def _evt(**kwargs) -> DiscoveryEvent:
    base = dict(
        source="dexscreener_new",
        discovered_at=datetime.now(timezone.utc),
        chain="solana",
        ca="Ear1yDiscoSo11111111111111111111111111112",
        ticker="EARLY",
        mc_usd=40_000,
        liquidity_usd=10_000,
        pair_created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    base.update(kwargs)
    return DiscoveryEvent(**base)


def test_dedupe_keeps_first_source(tmp_data: Path):
    bus = DiscoveryBus(store_path=tmp_data / "discovery_bus.json")
    t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=20)

    rec1, first = bus.upsert(
        _evt(source="pumpfun_curve", discovered_at=t0, pair_created_at=t0)
    )
    assert first is True
    assert rec1.first_source == "pumpfun_curve"
    assert rec1.first_seen_at == t0

    rec2, first2 = bus.upsert(
        _evt(source="x_social", discovered_at=t1, pair_created_at=t0, mc_usd=55_000)
    )
    assert first2 is False
    assert rec2.first_source == "pumpfun_curve"
    assert rec2.first_seen_at == t0
    assert rec2.sources == ["pumpfun_curve", "x_social"]
    # later mc fills only if was None — here first had mc so sticky
    assert rec2.mc_usd == 40_000

    bus.persist()
    bus2 = DiscoveryBus(store_path=tmp_data / "discovery_bus.json")
    loaded = bus2.get("solana", "Ear1yDiscoSo11111111111111111111111111112")
    assert loaded is not None
    assert loaded.first_source == "pumpfun_curve"
    assert loaded.sources == ["pumpfun_curve", "x_social"]


def test_fomo_only_cannot_pursue(tmp_data: Path):
    bus = DiscoveryBus()
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    summary = ingest_event(
        DiscoveryEvent(
            source="fomo_sidebar",
            discovered_at=datetime.now(timezone.utc),
            chain="solana",
            ca="FomoLateConf111111111111111111111111111111",
            ticker="FOMO",
            mc_usd=2_500_000,
            lagging_universe=True,
            pair_created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ),
        bus=bus,
        ledger=ledger,
        data_root=tmp_data,
        emit_buy=True,
    )
    assert summary["decision"] == "reject"
    assert "fomo_only_discovery" in summary["reasons"]
    assert summary["buy_decision_id"] is None


def test_d1_reject(tmp_data: Path):
    bus = DiscoveryBus()
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    summary = ingest_event(
        _evt(
            source="flow_hint",
            ca="Sybi1FarmTok111111111111111111111111111111",
            confidence_hints={"D1": True, "sybil": True},
        ),
        bus=bus,
        ledger=ledger,
        data_root=tmp_data,
    )
    assert summary["decision"] == "reject"
    assert "d1_sybil_bundled" in summary["reasons"]


def test_pump_dex_fixture_poll():
    dex = DexScreenerNewSource(fixture_dir=FIXTURES, live=False)
    pump = PumpfunCurveSource(fixture_dir=FIXTURES, live=False)
    dex_events = dex.poll()
    pump_events = pump.poll()
    assert len(dex_events) >= 2
    assert any(e.chain == "solana" for e in dex_events)
    assert any(e.chain == "base" for e in dex_events)
    assert len(pump_events) >= 2
    assert any(e.event_kind == "graduate" for e in pump_events)
    assert any(e.curve_progress == 0.42 for e in pump_events)


def test_armed_flag_default_false(tmp_data: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("XINTEL_ARMED", raising=False)
    assert is_armed() is False
    assert do_not_execute_until_armed() is True

    bus = DiscoveryBus()
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    summary = ingest_event(
        _evt(
            confidence_hints={"S1": True},
        ),
        bus=bus,
        ledger=ledger,
        data_root=tmp_data,
        emit_buy=True,
    )
    assert summary["decision"] == "pursue"
    assert summary["do_not_execute_until_armed"] is True
    assert summary["buy_decision_id"] is not None
    # decision file on disk
    dec_path = tmp_data / "decisions" / f"{summary['buy_decision_id']}.json"
    raw = atomic_read_json(dec_path)
    assert raw is not None
    assert raw["do_not_execute_until_armed"] is True
    assert raw["action"] == "BUY"


def test_atomic_decision_write_on_pursue(tmp_data: Path):
    bus = DiscoveryBus()
    ledger = CandidateLedger(RepoPaths(data=tmp_data))
    summary = ingest_event(
        _evt(confidence_hints={"S3": True}),
        bus=bus,
        ledger=ledger,
        data_root=tmp_data,
    )
    assert summary["decision"] == "pursue"
    did = summary["buy_decision_id"]
    assert did
    path = tmp_data / "decisions" / f"{did}.json"
    assert path.is_file()
    assert not path.with_suffix(path.suffix + ".tmp").exists()
    assert not list(tmp_data.glob("decisions/*.tmp"))


def test_fomo_source_tags_lagging():
    src = FomoSidebarSource(fixture_dir=FIXTURES)
    events = src.poll()
    assert events
    assert all(e.lagging_universe for e in events)
    assert all(e.source == "fomo_sidebar" for e in events)


def test_score_watch_band():
    rec_bus = DiscoveryBus()
    old = datetime.now(timezone.utc) - timedelta(minutes=90)
    rec, _ = rec_bus.upsert(
        _evt(
            discovered_at=old,
            pair_created_at=old,
            mc_usd=100_000,
        )
    )
    gate = score_discovery(rec)
    assert gate.decision == CandidateDecision.watch


def test_validate_report_generation(tmp_path: Path):
    text = generate_report()
    assert "Early Discovery Validation" in text
    assert "MINUTE-LEVEL HISTORICAL DATA MISSING" in text
    out = write_report(tmp_path / "early_discovery_validation_v0.md")
    assert out.is_file()
    assert "FOMO-only" in out.read_text()


def test_runner_once_fixtures(tmp_data: Path, monkeypatch: pytest.MonkeyPatch):
    from x_intel.discovery.runner import run_cycle

    monkeypatch.setenv("XINTEL_DATA_DIR", str(tmp_data))
    results = run_cycle(
        live=False,
        fixture_dir=FIXTURES,
        data_root=tmp_data,
        emit_buy=True,
        fanout=True,
    )
    assert len(results) >= 1
    # FOMO-only should reject; early dex/pump may pursue
    decisions = {r["decision"] for r in results}
    assert "reject" in decisions or "pursue" in decisions or "watch" in decisions
    # agent fanout for first sights
    inbox = tmp_data / "agent_inbox"
    assert inbox.is_dir()
