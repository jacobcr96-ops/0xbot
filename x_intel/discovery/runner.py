"""CLI runner: poll all sources each cycle → ingest pipeline.

Usage:
  python -m x_intel.discovery.runner once
  python -m x_intel.discovery.runner loop --interval 30
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Optional

from x_intel.config import data_dir, is_armed
from x_intel.discovery.bus import DiscoveryBus
from x_intel.discovery.pipeline import ingest_event
from x_intel.discovery.sources import default_sources
from x_intel.ledger.store import CandidateLedger, RepoPaths

log = logging.getLogger(__name__)


def run_cycle(
    *,
    live: bool = False,
    fixture_dir: Optional[Path] = None,
    data_root: Optional[Path] = None,
    emit_buy: bool = True,
    fanout: bool = True,
) -> list[dict]:
    root = data_root or data_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "fomo_inbox").mkdir(parents=True, exist_ok=True)
    (root / "flow_inbox").mkdir(parents=True, exist_ok=True)
    (root / "agent_inbox").mkdir(parents=True, exist_ok=True)

    bus = DiscoveryBus(store_path=root / "discovery_bus.json")
    ledger = CandidateLedger(RepoPaths(data=root))
    sources = default_sources(fixture_dir=fixture_dir, live=live, data_dir=root)

    results: list[dict] = []
    for src in sources:
        try:
            events = src.poll()
        except Exception as e:  # noqa: BLE001
            log.warning("source %s poll failed: %s", getattr(src, "source_id", src), e)
            continue
        log.info("source %s → %d events", getattr(src, "source_id", type(src).__name__), len(events))
        for ev in events:
            summary = ingest_event(
                ev,
                bus=bus,
                ledger=ledger,
                fanout=fanout,
                emit_buy=emit_buy,
                data_root=root,
            )
            results.append(summary)
            log.info(
                "ingest ca=%s… decision=%s first=%s sources=%s",
                summary["ca"][:12],
                summary["decision"],
                summary["is_first"],
                summary["sources"],
            )
    bus.persist()
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Early-mover discovery runner (disarmed)")
    parser.add_argument("mode", choices=["once", "loop"], help="once=single cycle; loop=poll forever")
    parser.add_argument("--interval", type=float, default=30.0, help="loop interval seconds (default 30)")
    parser.add_argument("--live", action="store_true", help="hit public HTTP endpoints (still disarmed)")
    parser.add_argument("--fixture-dir", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None, help="override XINTEL_DATA_DIR")
    parser.add_argument("--no-emit", action="store_true", help="skip pursue→BUY disk emit")
    parser.add_argument("--no-fanout", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("discovery runner mode=%s armed=%s live=%s", args.mode, is_armed(), args.live)

    def _cycle() -> list[dict]:
        return run_cycle(
            live=args.live,
            fixture_dir=args.fixture_dir,
            data_root=args.data_dir,
            emit_buy=not args.no_emit,
            fanout=not args.no_fanout,
        )

    if args.mode == "once":
        results = _cycle()
        print(f"cycle_complete n_results={len(results)} armed={is_armed()}")
        return 0

    while True:
        _cycle()
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
