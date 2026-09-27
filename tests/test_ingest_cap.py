"""Runner cycle order, per-cycle ingest cap, stale skip, carry-forward, budget."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from x_intel.discovery import runner as runner_mod
from x_intel.discovery.models import DiscoveryEvent
from x_intel.discovery.runner import plan_ingest, run_cycle
from x_intel.discovery.sources.pumpfun_curve import BACKLOG_KEY, PumpfunCurveSource
from x_intel.io_atomic import atomic_read_json

NOW = datetime.now(timezone.utc)


def _ev(ca: str, age_min: float | None, source: str = "pumpfun_curve") -> DiscoveryEvent:
    return DiscoveryEvent(
        source=source,
        discovered_at=NOW,
        chain="solana",
        ca=ca,
        ticker=ca.upper(),
        pair_created_at=(NOW - timedelta(minutes=age_min)) if age_min is not None else None,
        event_kind="curve_new",
        confidence_hints={"pump_curve": True},
    )


class _Plain:
    """Non-backlog source (dex/x/fomo-like)."""

    source_id = "dexscreener_new"

    def __init__(self, events: list[DiscoveryEvent]) -> None:
        self._events = events

    def poll(self) -> list[DiscoveryEvent]:
        return list(self._events)


class _Pump(PumpfunCurveSource):
    """Pump source with injected poll batches; backlog on (explicit data_dir)."""

    def __init__(self, batches: list[list[DiscoveryEvent]], data_dir: Path) -> None:
        super().__init__(live=False, data_dir=data_dir)
        self._batches = batches

    def _poll_fixture(self) -> list[DiscoveryEvent]:
        return list(self._batches.pop(0)) if self._batches else []


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    (d / "health").mkdir(parents=True)
    return d


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub watch refresh/escalate + ingest; record call order."""
    log: list[str] = []

    def fake_escalate(**kw: Any) -> dict:
        log.append("watch_escalate")
        return {"watch_count": 13, "watch_stale_count": 0, "buy_emitted_n": 0,
                "oldest_watch_refresh_age_sec": 1.0}

    def fake_refresh(**kw: Any) -> dict:
        log.append("watch_refresh")
        return {"watch_count": 13, "watch_stale_count": 0, "watch_expired_n": 0,
                "oldest_watch_refresh_age_sec": 0.5}

    def fake_ingest(ev: DiscoveryEvent, **kw: Any) -> dict:
        log.append(f"ingest:{ev.ca}")
        return {"ca": ev.ca, "decision": "watch", "is_first": True,
                "sources": [ev.source], "enrich_ok": True}

    monkeypatch.setattr(runner_mod, "run_watch_escalate_cycle", fake_escalate)
    monkeypatch.setattr(runner_mod, "refresh_open_watches", fake_refresh)
    monkeypatch.setattr(runner_mod, "ingest_event", fake_ingest)
    return log


def _use_sources(monkeypatch: pytest.MonkeyPatch, sources: list, log: list[str] | None = None):
    def fake_default_sources(**kw: Any) -> list:
        if log is not None:
            log.append("poll")
        return sources

    monkeypatch.setattr(runner_mod, "default_sources", fake_default_sources)


# ── plan_ingest (pure) ────────────────────────────────────────────────────


def test_plan_under_cap_keeps_everything_including_stale():
    pump = _Pump([], Path("/nonexistent"))
    polled = [(pump, [_ev("old", 90), _ev("new", 1)])]
    plan = plan_ingest(polled, cap=10, stale_skip_sec=1800, now=NOW)
    assert [e.ca for _, e in plan["queue"]] == ["new", "old"]  # newest first
    assert plan["skipped_stale_n"] == 0 and plan["deferred_n"] == 0


def test_plan_over_cap_newest_first_skips_stale_defers_rest():
    pump = _Pump([], Path("/nonexistent"))
    plain = _Plain([_ev("dex1", None, "dexscreener_new")])
    pump_evs = [_ev(f"p{i}", i) for i in range(10)]  # ages 0..9 min
    pump_evs += [_ev("stale1", 45), _ev("stale2", 120)]
    plan = plan_ingest([(plain, plain._events), (pump, pump_evs)], cap=4,
                       stale_skip_sec=1800, now=NOW)
    order = [e.ca for _, e in plan["queue"]]
    assert order == ["dex1", "p0", "p1", "p2"]  # plain first, then newest pump
    assert plan["skipped_stale_n"] == 2
    assert plan["deferred_n"] == 7
    (_, deferred), = plan["deferred"].values()
    assert [e.ca for e in deferred] == [f"p{i}" for i in range(3, 10)]


# ── run_cycle order + carry-forward ───────────────────────────────────────


def test_cycle_order_watch_before_ingest(root: Path, calls: list[str],
                                         monkeypatch: pytest.MonkeyPatch):
    pump = _Pump([[_ev("a", 1), _ev("b", 2)]], root)
    _use_sources(monkeypatch, [pump], calls)
    run_cycle(live=False, data_root=root, max_ingest=10, budget_sec=100)
    assert calls == ["watch_escalate", "poll", "ingest:a", "ingest:b", "watch_refresh"]
    hb = atomic_read_json(root / "health" / "heartbeat.json")
    assert hb["watch_count"] == 13
    assert hb["oldest_watch_refresh_age_sec"] == 0.5  # post-ingest refresh wins
    assert hb["ingest"]["ingested_n"] == 2
    assert hb["cycle_duration_sec"] is not None


def test_cap_carries_remainder_forward_nothing_dropped(root: Path, calls: list[str],
                                                        monkeypatch: pytest.MonkeyPatch):
    batch1 = [_ev(f"m{i}", i) for i in range(5)]  # m0 newest
    pump = _Pump([batch1, [_ev("fresh", 0.1)], []], root)
    _use_sources(monkeypatch, [pump])

    r1 = run_cycle(live=False, data_root=root, max_ingest=3, budget_sec=100)
    assert [r["ca"] for r in r1] == ["m0", "m1", "m2"]
    cur = atomic_read_json(root / "health" / "pump_cursor.json")
    assert sorted(e["ca"] for e in cur[BACKLOG_KEY]) == ["m3", "m4"]
    hb = atomic_read_json(root / "health" / "heartbeat.json")
    assert hb["ingest"]["deferred_n"] == 2 and hb["ingest"]["backlog_n"] == 2

    # Cycle 2: one new mint + 2 backlog → all fit, newest first, backlog cleared.
    r2 = run_cycle(live=False, data_root=root, max_ingest=3, budget_sec=100)
    assert [r["ca"] for r in r2] == ["fresh", "m3", "m4"]
    cur = atomic_read_json(root / "health" / "pump_cursor.json")
    assert cur[BACKLOG_KEY] == []

    r3 = run_cycle(live=False, data_root=root, max_ingest=3, budget_sec=100)
    assert r3 == []


def test_large_backlog_skips_stale_and_logs(root: Path, calls: list[str],
                                             monkeypatch: pytest.MonkeyPatch,
                                             caplog: pytest.LogCaptureFixture):
    fresh = [_ev(f"f{i}", i * 0.1) for i in range(5)]
    stale = [_ev(f"s{i}", 31 + i) for i in range(500)]
    pump = _Pump([fresh + stale], root)
    _use_sources(monkeypatch, [pump])
    caplog.set_level("WARNING")
    r = run_cycle(live=False, data_root=root, max_ingest=150, budget_sec=100)
    assert [x["ca"] for x in r] == [f"f{i}" for i in range(5)]
    assert "skipped 500 stale mints" in caplog.text
    hb = atomic_read_json(root / "health" / "heartbeat.json")
    assert hb["ingest"]["skipped_stale_n"] == 500
    cur = atomic_read_json(root / "health" / "pump_cursor.json") or {}
    assert not cur.get(BACKLOG_KEY)  # stale ones are consumed (skipped), not carried


def test_wall_clock_budget_stops_ingest_defers_and_writes_heartbeat(
    root: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
):
    t = {"now": 0.0}

    def clock() -> float:
        return t["now"]

    real_ingest = runner_mod.ingest_event

    def slow_ingest(ev: DiscoveryEvent, **kw: Any) -> dict:
        t["now"] += 1.0  # ~1s enrich per mint
        return real_ingest(ev, **kw)

    monkeypatch.setattr(runner_mod, "ingest_event", slow_ingest)
    pump = _Pump([[_ev(f"m{i}", i) for i in range(10)]], root)
    _use_sources(monkeypatch, [pump])

    r = run_cycle(live=False, data_root=root, max_ingest=150, budget_sec=4.0, clock=clock)
    assert [x["ca"] for x in r] == ["m0", "m1", "m2", "m3"]
    assert calls[-1] == "watch_refresh"  # post-ingest refresh still runs
    hb = atomic_read_json(root / "health" / "heartbeat.json")
    assert hb["ingest"]["budget_hit"] is True
    assert hb["ingest"]["deferred_n"] == 6
    cur = atomic_read_json(root / "health" / "pump_cursor.json")
    assert sorted(e["ca"] for e in cur[BACKLOG_KEY]) == [f"m{i}" for i in range(4, 10)]


def test_watch_refresh_runs_even_if_poll_explodes(root: Path, calls: list[str],
                                                  monkeypatch: pytest.MonkeyPatch):
    def boom(**kw: Any) -> list:
        raise RuntimeError("sources down")

    monkeypatch.setattr(runner_mod, "default_sources", boom)
    assert run_cycle(live=False, data_root=root) == []
    assert calls == ["watch_escalate", "watch_refresh"]
    assert (root / "health" / "heartbeat.json").is_file()


# ── pump cursor backlog persistence ───────────────────────────────────────


def test_watermark_save_preserves_backlog_and_defer_merges(root: Path):
    src = PumpfunCurveSource(live=False, data_dir=root)
    assert src.defer_events([_ev("x", 1), _ev("y", 2)]) == 2
    src._save_watermark_ms(12345, meta={"n_events": 0})
    cur = atomic_read_json(root / "health" / "pump_cursor.json")
    assert cur["created_timestamp"] == 12345
    assert {e["ca"] for e in cur[BACKLOG_KEY]} == {"x", "y"}
    # Poll failed this cycle (nothing polled) → prior backlog kept, new merged.
    assert src.defer_events([_ev("z", 0.5)], consumed=["x"]) == 2
    cas = [e.ca for e in src.load_backlog()]
    assert cas == ["z", "y"]  # newest first


def test_backlog_hard_prunes_very_old_rows(root: Path):
    src = PumpfunCurveSource(live=False, data_dir=root)
    src.defer_events([_ev("ancient", 60 * 5), _ev("ok", 5)])
    assert [e.ca for e in src.load_backlog()] == ["ok"]


def test_fixture_mode_without_data_dir_never_touches_backlog():
    src = PumpfunCurveSource(live=False)
    assert src.defer_events([_ev("x", 1)]) == 0
    assert src.load_backlog() == []


def test_env_cap_and_budget(monkeypatch: pytest.MonkeyPatch):
    from x_intel import config

    monkeypatch.delenv("XINTEL_MAX_INGEST_PER_CYCLE", raising=False)
    monkeypatch.delenv("XINTEL_INGEST_BUDGET_SEC", raising=False)
    assert config.max_ingest_per_cycle() == 150
    assert config.ingest_budget_sec() == 200.0
    assert config.ingest_stale_skip_sec() == 1800.0
    monkeypatch.setenv("XINTEL_MAX_INGEST_PER_CYCLE", "40")
    monkeypatch.setenv("XINTEL_INGEST_BUDGET_SEC", "90")
    assert config.max_ingest_per_cycle() == 40
    assert config.ingest_budget_sec() == 90.0
    monkeypatch.setenv("XINTEL_MAX_INGEST_PER_CYCLE", "junk")
    assert config.max_ingest_per_cycle() == 150
