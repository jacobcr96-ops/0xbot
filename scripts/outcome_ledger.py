#!/usr/bin/env python3
"""Outcome Desk filesystem ledger helper.

Measurement only — never trades.
Commands:
  list-pending  — candidates on disk with no outcome file, or fill_status pending/partial
  status        — counts of candidates, outcomes, fill statuses, feature_scores coverage

DexScreener price pull is stubbed (TODO); list/status work against the local FS.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator, Optional

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_DIR = ROOT / "data" / "candidates"
OUTCOMES_DIR = ROOT / "data" / "outcomes"
INDEX_PATH = CANDIDATES_DIR / "_index.jsonl"
PENDING_PATH = OUTCOMES_DIR / "_pending.jsonl"

SKIP_CANDIDATE_NAMES = {"_index.jsonl", "_TEMPLATE.json"}


def _iter_candidate_files() -> Iterator[Path]:
    if not CANDIDATES_DIR.is_dir():
        return
    for p in sorted(CANDIDATES_DIR.glob("*.json")):
        if p.name in SKIP_CANDIDATE_NAMES or p.name.startswith("_"):
            continue
        yield p


def _load_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        with path.open() as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _outcome_path(candidate_id: str) -> Path:
    return OUTCOMES_DIR / f"{candidate_id}.json"


def _fill_status_for(candidate_id: str) -> str:
    op = _outcome_path(candidate_id)
    if not op.exists():
        return "missing"
    data = _load_json(op)
    if not data:
        return "invalid"
    return str(data.get("fill_status", "unknown"))


def cmd_list_pending(_: argparse.Namespace) -> int:
    """List candidates that still need Outcome Desk fills."""
    rows: list[tuple[str, str, str]] = []
    for path in _iter_candidate_files():
        data = _load_json(path)
        if not data:
            rows.append((path.stem, "invalid-candidate", path.name))
            continue
        cid = str(data.get("candidate_id") or path.stem)
        status = _fill_status_for(cid)
        if status in ("missing", "pending", "partial", "invalid"):
            decision = str(data.get("decision", "?"))
            rows.append((cid, status, decision))

    if not rows:
        print("No pending fills. (n_candidates with open work = 0)")
        return 0

    print(f"{'candidate_id':<40} {'fill_status':<14} decision")
    print("-" * 70)
    for cid, status, decision in rows:
        print(f"{cid:<40} {status:<14} {decision}")
    print(f"\npending_count={len(rows)}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    """Summarize ledger coverage."""
    candidates = list(_iter_candidate_files())
    n_cand = len(candidates)
    status_counts: dict[str, int] = {
        "missing": 0,
        "pending": 0,
        "partial": 0,
        "complete": 0,
        "unavailable": 0,
        "invalid": 0,
        "unknown": 0,
    }
    by_decision: dict[str, int] = {}
    experiment_ids: dict[str, int] = {}
    feature_scores_present = 0
    feature_scores_missing = 0
    feature_nonnull_values = 0

    for path in candidates:
        data = _load_json(path) or {}
        cid = str(data.get("candidate_id") or path.stem)
        status = _fill_status_for(cid)
        status_counts[status] = status_counts.get(status, 0) + 1
        d = str(data.get("decision", "unknown"))
        by_decision[d] = by_decision.get(d, 0) + 1
        eid = str(data.get("experiment_id", "unknown"))
        experiment_ids[eid] = experiment_ids.get(eid, 0) + 1
        fs = data.get("feature_scores")
        if isinstance(fs, dict) and fs.get("scores"):
            feature_scores_present += 1
            for entry in fs["scores"].values():
                if isinstance(entry, dict) and entry.get("value") is not None:
                    feature_nonnull_values += 1
        else:
            feature_scores_missing += 1

    n_outcome_files = len(
        [p for p in OUTCOMES_DIR.glob("*.json") if not p.name.startswith("_")]
    ) if OUTCOMES_DIR.is_dir() else 0

    index_lines = 0
    if INDEX_PATH.exists():
        with INDEX_PATH.open() as f:
            index_lines = sum(1 for line in f if line.strip())

    print("Outcome ledger status")
    print(f"  root:              {ROOT}")
    print(f"  candidates:        {n_cand}")
    print(f"  index_lines:       {index_lines}")
    print(f"  outcome_files:     {n_outcome_files}")
    print("  fill_status:")
    for k in ("missing", "pending", "partial", "complete", "unavailable", "invalid", "unknown"):
        print(f"    {k:<14} {status_counts.get(k, 0)}")
    print("  by_decision:")
    if by_decision:
        for k, v in sorted(by_decision.items()):
            print(f"    {k:<14} {v}")
    else:
        print("    (none)")
    print("  by_experiment_id:")
    if experiment_ids:
        for k, v in sorted(experiment_ids.items()):
            print(f"    {k:<14} {v}")
    else:
        print("    (none)")
    open_work = (
        status_counts.get("missing", 0)
        + status_counts.get("pending", 0)
        + status_counts.get("partial", 0)
        + status_counts.get("invalid", 0)
    )
    print(f"  open_work:         {open_work}")
    print("  feature_scores:")
    print(f"    present        {feature_scores_present}")
    print(f"    missing        {feature_scores_missing}")
    print(f"    nonnull_values {feature_nonnull_values}")
    return 0


def fetch_price_dexscreener(chain: str, contract_address: str) -> Optional[dict[str, Any]]:
    """TODO: DexScreener public API pull.

    Intended endpoint (no API key for basic pair lookup):
      GET https://api.dexscreener.com/latest/dex/tokens/{contract_address}

    Not implemented yet — returns None so callers can stub fills without
    network dependency. When implemented: pick best liquidity pair for
    `chain`, return {price_usd, liquidity_usd, pair_address, queried_at}.
    """
    _ = (chain, contract_address)
    # TODO: implement HTTP GET + pair selection; handle rate limits / empty pairs.
    return None


def cmd_stub_fetch(args: argparse.Namespace) -> int:
    """Demonstrate the price-fetch stub (always None until TODO is done)."""
    result = fetch_price_dexscreener(args.chain, args.contract)
    print(json.dumps({"chain": args.chain, "contract": args.contract, "result": result, "todo": True}))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Outcome Desk ledger helper (measurement only; never trades)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-pending", help="List candidates missing or incomplete fills")
    p_list.set_defaults(func=cmd_list_pending)

    p_status = sub.add_parser("status", help="Summarize candidate/outcome coverage")
    p_status.set_defaults(func=cmd_status)

    p_fetch = sub.add_parser(
        "stub-fetch",
        help="Stub DexScreener price fetch (always returns null until TODO)",
    )
    p_fetch.add_argument("--chain", default="solana")
    p_fetch.add_argument("--contract", required=True)
    p_fetch.set_defaults(func=cmd_stub_fetch)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
