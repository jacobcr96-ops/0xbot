#!/usr/bin/env python3
"""Demo: load fixtures, print findings paths, show API curl examples.

Does NOT start live trading. Intelligence only; disarmed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from x_intel.ingest.interfaces import FixtureIngest
from x_intel.ledger.store import CandidateLedger, default_paths
from x_intel.research.cases import findings_summary
from x_intel.scoring.freeze import freeze_features_at_t, score_with_frozen_weights


def main() -> int:
    paths = default_paths(ROOT)
    print("=== x_intel demo (disarmed) ===")
    print(f"repo root: {paths.root}")
    print(f"experiment_id: xintel_v0")
    print(f"do_not_execute_until_armed: true")
    print()

    findings = findings_summary(paths.reports)
    print("--- historical findings paths ---")
    for k, v in findings.items():
        if k != "case_slugs":
            print(f"  {k}: {v}")
    print(f"  case_slugs: {', '.join(findings['case_slugs'][:5])} ... ({findings['n_case_files']} total)")
    print()

    ingest = FixtureIngest(ROOT)
    cands = ingest.load_candidates()
    print(f"--- fixtures: {len(cands)} candidates ---")
    for c in cands[:3]:
        print(f"  {c.candidate_id}  {c.ticker or '?':8}  decision={c.decision.value}  ca={c.contract_address[:12]}...")
    print()

    ledger = CandidateLedger(paths)
    decs = ledger.list_decisions(unexpired_only=False)
    print(f"--- decisions on disk: {len(decs)} ---")
    for d in decs:
        from x_intel.schemas.models import is_decision_stale
        print(f"  {d.decision_id}  action={d.action.value:6}  stale={is_decision_stale(d)}  armed_block={d.do_not_execute_until_armed}")
    print()

    if cands and cands[0].feature_scores:
        fr = freeze_features_at_t(cands[0].feature_scores)
        scored = score_with_frozen_weights(fr)
        print("--- sample frozen feature score (anti-hindsight) ---")
        print(json.dumps({k: scored[k] for k in ("total", "contributions", "provisional")}, indent=2))
        print()

    print("--- run API ---")
    print("  uvicorn x_intel.api.main:app --host 127.0.0.1 --port 8080")
    print("--- curl examples ---")
    print("  curl -s http://127.0.0.1:8080/v1/health | jq")
    print("  curl -s 'http://127.0.0.1:8080/v1/candidates' | jq '.count'")
    print("  curl -s 'http://127.0.0.1:8080/v1/decisions?unexpired=1' | jq")
    print("  curl -s http://127.0.0.1:8080/v1/leaderboard | jq")
    print()
    print("--- MCP tools ---")
    print("  python -m x_intel.mcp_server.server tools")
    print("  python -m x_intel.mcp_server.server call list_candidates '{}'")
    print()
    print("No live trading. Intents stay disarmed until Jacob arms the pipeline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
