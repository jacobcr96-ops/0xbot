"""Ingest execution_report.v1 files dropped by the 0xbot / Tampermonkey gateway.

Reject stream is the primary feedback for confidence weights.
Attaches outcomes under data/outcomes/ keyed by decision_id / report_id.
Never places trades.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from x_intel.io_atomic import append_jsonl, atomic_read_json, atomic_write_json
from x_intel.ledger.store import CandidateLedger, default_paths
from x_intel.schemas.models import ExecutionReportV1

log = logging.getLogger(__name__)


def load_report(path: Path) -> Optional[ExecutionReportV1]:
    """Atomic-read tolerant load; skips *.tmp and corrupt partials."""
    raw = atomic_read_json(path)
    if raw is None:
        return None
    return ExecutionReportV1.model_validate(raw)


def outcome_payload_from_report(report: ExecutionReportV1) -> dict[str, Any]:
    """Measurement overlay attached to data/outcomes/ for a report."""
    return {
        "schema_version": "xintel.execution_outcome.v1",
        "report_id": str(report.report_id),
        "decision_id": str(report.decision_id),
        "candidate_id": report.candidate_id,
        "reported_at": report.reported_at.isoformat(),
        "status": report.status,
        "reject_reason": report.reject_reason.value if report.reject_reason else None,
        "reject_detail": report.reject_detail,
        "fills": [f.model_dump(mode="json") for f in report.fills],
        "positions": [p.model_dump(mode="json") for p in report.positions],
        "equity_usd": report.equity_usd,
        "closed_round": report.closed_round.model_dump(mode="json")
        if report.closed_round
        else None,
        "experiment_id": report.experiment_id,
        "notes": report.notes,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "feedback_note": (
            "Reject taxonomy is primary feedback for confidence weights "
            "(stale|no_cash|chain_unsupported|ca_unresolved|disable_buying|"
            "duplicate|below_min_size|other)."
        ),
    }


def ingest_report(
    report: ExecutionReportV1,
    ledger: Optional[CandidateLedger] = None,
    *,
    copy_to_queue: bool = False,
) -> Path:
    """Persist outcome overlay for a report; optionally ensure report is on disk."""
    led = ledger or CandidateLedger(default_paths())
    if copy_to_queue:
        led.save_execution_report(report)

    outcomes_dir = led.paths.outcomes
    outcomes_dir.mkdir(parents=True, exist_ok=True)
    # Prefer decision_id linkage; fall back to report_id
    out_name = f"exec_{report.decision_id}_{report.report_id}.json"
    out_path = outcomes_dir / out_name
    payload = outcome_payload_from_report(report)
    atomic_write_json(out_path, payload)

    # Append ingest index
    append_jsonl(
        outcomes_dir / "_execution_ingest.jsonl",
        {
            "report_id": str(report.report_id),
            "decision_id": str(report.decision_id),
            "status": report.status,
            "reject_reason": report.reject_reason.value if report.reject_reason else None,
            "outcome_path": str(out_path.name),
            "ingested_at": payload["ingested_at"],
        },
    )
    return out_path


def ingest_new_reports(
    ledger: Optional[CandidateLedger] = None,
    *,
    reports_dir: Optional[Path] = None,
) -> list[Path]:
    """Load all new execution_reports and attach outcomes. Idempotent by report_id."""
    led = ledger or CandidateLedger(default_paths())
    ddir = reports_dir or led.paths.execution_reports
    if not ddir.is_dir():
        return []

    outcomes_dir = led.paths.outcomes
    ingested: set[str] = set()
    index_path = outcomes_dir / "_execution_ingest.jsonl"
    if index_path.is_file():
        for line in index_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ingested.add(json.loads(line)["report_id"])
            except (json.JSONDecodeError, KeyError):
                continue

    written: list[Path] = []
    for path in sorted(ddir.glob("*.json")):
        if path.name.startswith("_") or path.name.endswith(".tmp"):
            continue
        report = load_report(path)
        if report is None:
            log.warning("skip unreadable report %s", path)
            continue
        rid = str(report.report_id)
        if rid in ingested:
            continue
        out = ingest_report(report, led, copy_to_queue=False)
        written.append(out)
        log.info(
            "ingested report_id=%s decision_id=%s status=%s → %s",
            report.report_id,
            report.decision_id,
            report.status,
            out,
        )
    return written


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest execution_report.v1 files into data/outcomes/ (measurement only)."
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Override execution_reports directory",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Ingest a single report file",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    led = CandidateLedger(default_paths())
    if args.file:
        report = load_report(args.file)
        if report is None:
            print(f"ERROR: could not load {args.file}")
            return 1
        # Ensure stored in queue if coming from elsewhere
        led.save_execution_report(report)
        path = ingest_report(report, led)
        print(path)
        return 0

    paths = ingest_new_reports(led, reports_dir=args.dir)
    print(f"ingested {len(paths)} report(s)")
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
