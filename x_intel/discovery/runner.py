"""CLI runner: watch refresh/escalate → poll sources → capped ingest → heartbeat.

Usage:
  python -m x_intel.discovery.runner once
  python -m x_intel.discovery.runner loop --interval 30
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from datetime import datetime, timezone
from x_intel.config import (
    data_dir,
    ingest_budget_sec,
    ingest_stale_skip_sec,
    is_armed,
    max_ingest_per_cycle,
)
from x_intel.discovery.bus import DiscoveryBus
from x_intel.discovery.pipeline import ingest_event
from x_intel.discovery.sources import default_sources
from x_intel.discovery.watch_escalate import refresh_open_watches, run_watch_escalate_cycle
from x_intel.io_atomic import atomic_read_json, atomic_write_json
from x_intel.ledger.store import CandidateLedger, RepoPaths

log = logging.getLogger(__name__)


def _event_created_age_sec(ev: Any, *, now: datetime) -> Optional[float]:
    """Seconds since mint/pair creation; None when unknown (treated as fresh)."""
    ref = getattr(ev, "pair_created_at", None)
    if ref is None:
        return None
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ref).total_seconds())


def plan_ingest(
    polled: list[tuple[Any, list[Any]]],
    *,
    cap: int,
    stale_skip_sec: float,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Pick which events to ingest this cycle.

    - Events from sources without carry-forward (dex/x/fomo/flow — small,
      re-polled) go first in poll order.
    - Backlog-capable sources (pump curve) fill the rest, **newest first**.
    - Only when the total exceeds ``cap``: backlog-capable events older than
      ``stale_skip_sec`` are skipped (logged) so fresh mints aren't delayed.
    - Whatever still doesn't fit is deferred to the source (carried forward).

    Returns dict(queue=[(src, ev)], deferred={id(src): (src, [ev])},
    skipped_stale={id(src): (src, [ev])}, dropped_n, counts...).
    """
    now = now or datetime.now(timezone.utc)
    cap = max(1, int(cap))
    plain: list[tuple[Any, Any]] = []
    backlog_capable: list[tuple[Any, Any]] = []
    for src, events in polled:
        if getattr(src, "supports_backlog", False) and hasattr(src, "defer_events"):
            backlog_capable.extend((src, ev) for ev in events)
        else:
            plain.extend((src, ev) for ev in events)

    total = len(plain) + len(backlog_capable)
    skipped: list[tuple[Any, Any]] = []
    if total > cap:
        fresh: list[tuple[Any, Any]] = []
        for pair in backlog_capable:
            age = _event_created_age_sec(pair[1], now=now)
            if age is not None and age > stale_skip_sec:
                skipped.append(pair)
            else:
                fresh.append(pair)
        backlog_capable = fresh

    def _sort_key(pair: tuple[Any, Any]) -> float:
        age = _event_created_age_sec(pair[1], now=now)
        return -1.0 if age is None else age

    backlog_capable.sort(key=_sort_key)  # newest (smallest age) first

    queue: list[tuple[Any, Any]] = plain[:cap]
    dropped_plain = plain[cap:]
    room = max(0, cap - len(queue))
    queue.extend(backlog_capable[:room])
    deferred_pairs = backlog_capable[room:]

    def _group(pairs: list[tuple[Any, Any]]) -> dict[int, tuple[Any, list[Any]]]:
        out: dict[int, tuple[Any, list[Any]]] = {}
        for src, ev in pairs:
            out.setdefault(id(src), (src, []))[1].append(ev)
        return out

    return {
        "queue": queue,
        "deferred": _group(deferred_pairs),
        "skipped_stale": _group(skipped),
        "total_polled": total,
        "deferred_n": len(deferred_pairs),
        "skipped_stale_n": len(skipped),
        "dropped_n": len(dropped_plain),
    }


def _refresh_watches_only(*, ledger: CandidateLedger, root: Path, live: bool) -> dict:
    """Cheap post-ingest MC refresh (one batched Jupiter call); no escalate."""
    fresh = refresh_open_watches(ledger=ledger, data_root=root, live=live)
    return {
        "watch_count": fresh.get("watch_count"),
        "watch_stale_count": fresh.get("watch_stale_count"),
        "watch_stale_buyable": fresh.get("stale_buyable"),
        "watch_stale_subfloor": fresh.get("stale_subfloor"),
        "stale_watch_alert": fresh.get("stale_watch_alert"),
        "watch_expired_n": fresh.get("watch_expired_n"),
        "oldest_watch_refresh_age_sec": fresh.get("oldest_watch_refresh_age_sec"),
        "oldest_watch_refresh_age_sec_all": fresh.get("oldest_watch_refresh_age_sec_all"),
        "jup_errors": fresh.get("jup_errors"),
        "jup_stats": fresh.get("jup_stats"),
        "jup_single_stats": fresh.get("jup_single_stats"),
        "refresh_mode": fresh.get("refresh_mode"),
        "mc_source_counts": fresh.get("mc_source_counts"),
    }


def run_cycle(
    *,
    live: bool = False,
    fixture_dir: Optional[Path] = None,
    data_root: Optional[Path] = None,
    emit_buy: bool = True,
    fanout: bool = True,
    max_ingest: Optional[int] = None,
    budget_sec: Optional[float] = None,
    stale_skip_sec: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
) -> list[dict]:
    """One discovery cycle.

    Order (watch freshness must not depend on ingest size):
      1. watch refresh + dip-escalate (single batched Jupiter call)
      2. poll sources → capped, newest-first ingest within wall-clock budget;
         remainder carried forward on the pump cursor backlog
      3. cheap second watch refresh (picks up WATCHes created by ingest)
      4. heartbeat (always written, even if ingest raised / hit budget)
    """
    t0 = clock()
    root = data_root or data_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "fomo_inbox").mkdir(parents=True, exist_ok=True)
    (root / "flow_inbox").mkdir(parents=True, exist_ok=True)
    (root / "agent_inbox").mkdir(parents=True, exist_ok=True)

    cap = int(max_ingest) if max_ingest is not None else max_ingest_per_cycle()
    budget = float(budget_sec) if budget_sec is not None else ingest_budget_sec()
    stale_sec = float(stale_skip_sec) if stale_skip_sec is not None else ingest_stale_skip_sec()

    bus = DiscoveryBus(store_path=root / "discovery_bus.json")
    ledger = CandidateLedger(RepoPaths(data=root))

    # 1) Anti-stale WATCH MC refresh + dip-buy escalate FIRST (no X API spend).
    watch_summary: dict = {}
    try:
        watch_summary = run_watch_escalate_cycle(
            ledger=ledger,
            data_root=root,
            live=live,
            emit_buy=emit_buy,
        )
        log.info(
            "watch_escalate (pre-ingest) watches=%s stale=%s buys=%s",
            watch_summary.get("watch_count"),
            watch_summary.get("watch_stale_count"),
            watch_summary.get("buy_emitted_n"),
        )
    except Exception as e:  # noqa: BLE001 — never crash ingest on escalate
        log.warning("watch_escalate failed: %s", e)
    t_watch = clock() - t0

    results: list[dict] = []
    ingest_stats: dict[str, Any] = {
        "cap": cap,
        "budget_sec": budget,
        "stale_skip_sec": stale_sec,
        "polled_n": 0,
        "ingested_n": 0,
        "deferred_n": 0,
        "skipped_stale_n": 0,
        "dropped_n": 0,
        "budget_hit": False,
        "backlog_n": None,
    }
    try:
        # 2) Poll + capped ingest.
        sources = default_sources(fixture_dir=fixture_dir, live=live, data_dir=root)
        polled: list[tuple[Any, list[Any]]] = []
        for src in sources:
            try:
                events = src.poll()
            except Exception as e:  # noqa: BLE001
                log.warning("source %s poll failed: %s", getattr(src, "source_id", src), e)
                continue
            log.info(
                "source %s → %d events", getattr(src, "source_id", type(src).__name__), len(events)
            )
            polled.append((src, list(events)))

        plan = plan_ingest(polled, cap=cap, stale_skip_sec=stale_sec)
        ingest_stats["polled_n"] = plan["total_polled"]
        ingest_stats["skipped_stale_n"] = plan["skipped_stale_n"]
        ingest_stats["dropped_n"] = plan["dropped_n"]
        if plan["skipped_stale_n"]:
            log.warning(
                "ingest backlog over cap: skipped %d stale mints older than %.0fs",
                plan["skipped_stale_n"],
                stale_sec,
            )
        if plan["dropped_n"]:
            log.warning(
                "ingest cap: dropped %d non-carry-forward events (re-polled next cycle)",
                plan["dropped_n"],
            )

        queue = plan["queue"]
        deferred = plan["deferred"]  # id(src) -> (src, [ev])
        consumed: dict[int, tuple[Any, list[str]]] = {}
        for sid, (src, evs) in plan["skipped_stale"].items():
            consumed.setdefault(sid, (src, []))[1].extend(e.ca for e in evs)

        next_i = 0
        try:
            for i, (src, ev) in enumerate(queue):
                if clock() - t0 >= budget:
                    ingest_stats["budget_hit"] = True
                    log.warning(
                        "ingest wall-clock budget %.0fs hit after %d events; %d left "
                        "(carried forward)",
                        budget,
                        i,
                        len(queue) - i,
                    )
                    break
                try:
                    summary = ingest_event(
                        ev,
                        bus=bus,
                        ledger=ledger,
                        fanout=fanout,
                        emit_buy=emit_buy,
                        data_root=root,
                    )
                except Exception as e:  # noqa: BLE001 — one bad mint must not stall the rest
                    log.warning("ingest failed ca=%s…: %s", str(ev.ca)[:12], e)
                    summary = None
                next_i = i + 1
                if getattr(src, "supports_backlog", False):
                    # consumed even on failure: no poison-pill retry loop
                    consumed.setdefault(id(src), (src, []))[1].append(ev.ca)
                if summary is None:
                    continue
                results.append(summary)
                log.info(
                    "ingest ca=%s… decision=%s first=%s sources=%s",
                    summary["ca"][:12],
                    summary["decision"],
                    summary["is_first"],
                    summary["sources"],
                )
        finally:
            bus.persist()
            # Anything not reached (budget hit / unexpected error) is carried
            # forward for backlog-capable sources, else counted as dropped.
            for rsrc, rev in queue[next_i:]:
                if getattr(rsrc, "supports_backlog", False) and hasattr(rsrc, "defer_events"):
                    deferred.setdefault(id(rsrc), (rsrc, []))[1].append(rev)
                else:
                    ingest_stats["dropped_n"] += 1
            # Carry remainder forward (also clears consumed backlog rows).
            backlog_total = 0
            deferred_total = 0
            for sid in set(deferred) | set(consumed):
                src = (deferred.get(sid) or consumed.get(sid))[0]
                rem = deferred.get(sid, (src, []))[1]
                done = consumed.get(sid, (src, []))[1]
                deferred_total += len(rem)
                try:
                    backlog_total += int(src.defer_events(rem, consumed=done) or 0)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "defer_events failed for %s (%d events): %s",
                        getattr(src, "source_id", src),
                        len(rem),
                        e,
                    )
            ingest_stats["deferred_n"] = deferred_total
            ingest_stats["backlog_n"] = backlog_total
    except Exception as e:  # noqa: BLE001 — heartbeat must still be written
        log.warning("ingest phase failed: %s", e)

    ingest_stats["ingested_n"] = len(results)
    t_ingest = clock() - t0 - t_watch
    log.info(
        "ingest polled=%d ingested=%d deferred=%d skipped_stale=%d dropped=%d "
        "backlog=%s budget_hit=%s cap=%d took=%.1fs",
        ingest_stats["polled_n"],
        ingest_stats["ingested_n"],
        ingest_stats["deferred_n"],
        ingest_stats["skipped_stale_n"],
        ingest_stats["dropped_n"],
        ingest_stats["backlog_n"],
        ingest_stats["budget_hit"],
        cap,
        t_ingest,
    )

    # Enrich success rate for heartbeat
    enrich_n = sum(1 for r in results if isinstance(r, dict))
    enrich_ok_n = sum(1 for r in results if isinstance(r, dict) and r.get("enrich_ok"))
    enrich_ok_rate = (enrich_ok_n / enrich_n) if enrich_n else None
    pursue_quality_n = sum(
        1
        for r in results
        if isinstance(r, dict) and r.get("decision") == "pursue" and r.get("pursue_eligible")
    )
    log.info(
        "cycle enrich_ok=%s/%s rate=%s pursue_quality=%s",
        enrich_ok_n,
        enrich_n,
        f"{enrich_ok_rate:.2f}" if enrich_ok_rate is not None else "n/a",
        pursue_quality_n,
    )

    # 3) Cheap second refresh so freshness reflects end-of-cycle state.
    try:
        post = _refresh_watches_only(ledger=ledger, root=root, live=live)
        watch_summary = {**watch_summary, **post}
        log.info(
            "watch refresh (post-ingest) watches=%s stale=%s oldest_age=%s",
            post.get("watch_count"),
            post.get("watch_stale_count"),
            post.get("oldest_watch_refresh_age_sec"),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("post-ingest watch refresh failed: %s", e)

    # Keep ingest summaries homogeneous; stash escalate on the side.
    if watch_summary:
        if results and isinstance(results[-1], dict):
            results[-1]["_watch_escalate"] = watch_summary
        else:
            log.info("watch_escalate summary=%s", watch_summary)

    duration = clock() - t0
    ingest_stats["cycle_duration_sec"] = round(duration, 2)
    ingest_stats["watch_phase_sec"] = round(t_watch, 2)
    ingest_stats["ingest_phase_sec"] = round(t_ingest, 2)

    # 4) Heartbeat — always.
    _write_cycle_heartbeat(
        root,
        enrich_ok_n=enrich_ok_n,
        enrich_n=enrich_n,
        enrich_ok_rate=enrich_ok_rate,
        pursue_quality_n=pursue_quality_n,
        n_results=len(results),
        watch_summary=watch_summary,
        live=live,
        ingest_stats=ingest_stats,
    )
    log.info("cycle done in %.1fs (watch=%.1fs ingest=%.1fs)", duration, t_watch, t_ingest)

    return results


def _write_cycle_heartbeat(
    root: Path,
    *,
    enrich_ok_n: int,
    enrich_n: int,
    enrich_ok_rate: float | None,
    pursue_quality_n: int,
    n_results: int,
    watch_summary: dict,
    live: bool,
    ingest_stats: Optional[dict] = None,
) -> None:
    health = root / "health"
    health.mkdir(parents=True, exist_ok=True)
    path = health / "heartbeat.json"
    raw = atomic_read_json(path) or {}
    if not isinstance(raw, dict):
        raw = {}
    now = datetime.now(timezone.utc).isoformat()
    raw.update(
        {
            "checked_at": now,
            "discovery_cycle_at": now,
            "n_discovered": n_results,
            "enrich_ok_n": enrich_ok_n,
            "enrich_n": enrich_n,
            "enrich_ok_rate": enrich_ok_rate,
            "pursue_quality_n": pursue_quality_n,
            "live": live,
            "armed": is_armed(),
        }
    )
    if watch_summary:
        raw["watch_count"] = watch_summary.get("watch_count", raw.get("watch_count"))
        raw["watch_stale_count"] = watch_summary.get(
            "watch_stale_count", raw.get("watch_stale_count")
        )
        raw["buy_emitted"] = watch_summary.get("buy_emitted_n", raw.get("buy_emitted"))
        raw["watch_expired_n"] = watch_summary.get("watch_expired_n", 0)
        raw["oldest_watch_refresh_age_sec"] = watch_summary.get(
            "oldest_watch_refresh_age_sec", raw.get("oldest_watch_refresh_age_sec")
        )
        # Buyable-only staleness (see watch_escalate); sub-floor reported apart.
        for k in (
            "watch_stale_buyable",
            "watch_stale_subfloor",
            "stale_watch_alert",
            "oldest_watch_refresh_age_sec_all",
        ):
            if k in watch_summary:
                raw[k] = watch_summary.get(k)
        if "watch_stale_buyable" not in watch_summary and "watch_stale_count" in watch_summary:
            raw["watch_stale_buyable"] = watch_summary.get("watch_stale_count")
            raw["stale_watch_alert"] = bool(watch_summary.get("watch_stale_count"))
        if "jup_stats" in watch_summary:
            raw["watch_jup_stats"] = watch_summary.get("jup_stats")
            raw["watch_jup_errors"] = watch_summary.get("jup_errors")
            raw["watch_jup_single_stats"] = watch_summary.get("jup_single_stats")
            raw["watch_refresh_mode"] = watch_summary.get("refresh_mode")
            raw["watch_mc_source_counts"] = watch_summary.get("mc_source_counts")
    if ingest_stats:
        raw["ingest"] = dict(ingest_stats)
        raw["cycle_duration_sec"] = ingest_stats.get("cycle_duration_sec")
    atomic_write_json(path, raw)


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
    if not args.live and args.fixture_dir is None:
        log.warning(
            "discovery runner WITHOUT --live: WATCH MC refresh is offline (no Jupiter/pump/Dex "
            "fetch, refresh ages grow, stale_watch_alert may trip). Use --live for the routine scan."
        )

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
        ok_n = sum(1 for r in results if isinstance(r, dict) and r.get("enrich_ok"))
        pq = sum(
            1
            for r in results
            if isinstance(r, dict) and r.get("decision") == "pursue" and r.get("pursue_eligible")
        )
        print(
            f"cycle_complete n_results={len(results)} enrich_ok={ok_n} "
            f"pursue_quality={pq} armed={is_armed()}"
        )
        return 0

    while True:
        _cycle()
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
