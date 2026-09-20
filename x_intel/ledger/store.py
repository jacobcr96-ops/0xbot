"""Filesystem candidate ledger + decision store."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import UUID

from x_intel.schemas.models import CandidateV1, DecisionV1, OutcomeFillV1

SKIP_NAMES = {"_index.jsonl", "_TEMPLATE.json"}


@dataclass(frozen=True)
class RepoPaths:
    root: Path

    @property
    def candidates(self) -> Path:
        return self.root / "data" / "candidates"

    @property
    def outcomes(self) -> Path:
        return self.root / "data" / "outcomes"

    @property
    def decisions(self) -> Path:
        return self.root / "data" / "fixtures" / "decisions"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def index(self) -> Path:
        return self.candidates / "_index.jsonl"


def default_paths(start: Optional[Path] = None) -> RepoPaths:
    """Walk up from start (or this file) to find repo root with data/candidates."""
    here = (start or Path(__file__)).resolve()
    for p in [here, *here.parents]:
        if (p / "data" / "candidates").is_dir():
            return RepoPaths(root=p)
    # fallback: package parent parents[2] = repo if x_intel/ledger/store.py
    return RepoPaths(root=Path(__file__).resolve().parents[2])


class CandidateLedger:
    """pursue/watch/reject logging + outcome overlays. Measurement only."""

    def __init__(self, paths: Optional[RepoPaths] = None) -> None:
        self.paths = paths or default_paths()

    def _iter_candidate_files(self) -> Iterator[Path]:
        d = self.paths.candidates
        if not d.is_dir():
            return
        for p in sorted(d.glob("*.json")):
            if p.name in SKIP_NAMES or p.name.startswith("_"):
                continue
            yield p

    def list_candidates(
        self,
        decision: Optional[str] = None,
        experiment_id: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> list[CandidateV1]:
        out: list[CandidateV1] = []
        for path in self._iter_candidate_files():
            raw = json.loads(path.read_text())
            cand = CandidateV1.model_validate(raw)
            if decision and cand.decision.value != decision:
                continue
            if experiment_id and cand.experiment_id != experiment_id:
                continue
            if since is not None:
                fs = cand.first_seen_at
                if fs.tzinfo is None:
                    fs = fs.replace(tzinfo=timezone.utc)
                s = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
                if fs < s:
                    continue
            out.append(cand)
        return out

    def get_candidate(self, candidate_id: str | UUID) -> Optional[CandidateV1]:
        path = self.paths.candidates / f"{candidate_id}.json"
        if not path.exists():
            return None
        return CandidateV1.model_validate(json.loads(path.read_text()))

    def save_candidate(self, candidate: CandidateV1, append_index: bool = True) -> Path:
        self.paths.candidates.mkdir(parents=True, exist_ok=True)
        path = self.paths.candidates / f"{candidate.candidate_id}.json"
        path.write_text(candidate.model_dump_json(indent=2) + "\n")
        if append_index:
            line = {
                "candidate_id": str(candidate.candidate_id),
                "first_seen_at": candidate.first_seen_at.isoformat(),
                "contract_address": candidate.contract_address,
                "chain": candidate.chain,
                "decision": candidate.decision.value,
                "experiment_id": candidate.experiment_id,
                "logged_at": datetime.now(timezone.utc).isoformat(),
            }
            with self.paths.index.open("a") as f:
                f.write(json.dumps(line) + "\n")
        return path

    def get_outcome(self, candidate_id: str | UUID) -> Optional[OutcomeFillV1]:
        path = self.paths.outcomes / f"{candidate_id}.json"
        if not path.exists():
            return None
        return OutcomeFillV1.model_validate(json.loads(path.read_text()))

    def save_outcome(self, fill: OutcomeFillV1) -> Path:
        self.paths.outcomes.mkdir(parents=True, exist_ok=True)
        path = self.paths.outcomes / f"{fill.candidate_id}.json"
        path.write_text(fill.model_dump_json(indent=2) + "\n")
        return path

    def list_decisions(
        self,
        unexpired_only: bool = False,
        min_confidence: float = 0.0,
        now: Optional[datetime] = None,
    ) -> list[DecisionV1]:
        from x_intel.schemas.models import is_decision_stale

        ddir = self.paths.decisions
        if not ddir.is_dir():
            return []
        now = now or datetime.now(timezone.utc)
        out: list[DecisionV1] = []
        for path in sorted(ddir.glob("*.json")):
            if path.name.startswith("_"):
                continue
            dec = DecisionV1.model_validate(json.loads(path.read_text()))
            if dec.confidence < min_confidence:
                continue
            if unexpired_only and is_decision_stale(dec, now=now):
                continue
            out.append(dec)
        return out

    def get_decision(self, decision_id: str | UUID) -> Optional[DecisionV1]:
        path = self.paths.decisions / f"{decision_id}.json"
        if not path.exists():
            return None
        return DecisionV1.model_validate(json.loads(path.read_text()))

    def save_decision(self, decision: DecisionV1) -> Path:
        self.paths.decisions.mkdir(parents=True, exist_ok=True)
        path = self.paths.decisions / f"{decision.decision_id}.json"
        path.write_text(decision.model_dump_json(indent=2) + "\n")
        return path

    def ack_decision(
        self,
        decision_id: str | UUID,
        status: str,
        note: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record ack sidecar. status: consumed | rejected_by_execution | expired."""
        allowed = {"consumed", "rejected_by_execution", "expired"}
        if status not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        dec = self.get_decision(decision_id)
        if dec is None:
            raise KeyError(f"decision not found: {decision_id}")
        ack_dir = self.paths.decisions / "acks"
        ack_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "decision_id": str(decision_id),
            "status": status,
            "acked_at": datetime.now(timezone.utc).isoformat(),
            "note": note,
            "do_not_execute_until_armed": True,
        }
        (ack_dir / f"{decision_id}.json").write_text(json.dumps(record, indent=2) + "\n")
        return record

    def account_leaderboard_stub(self) -> dict[str, Any]:
        """Stub leaderboard from candidates' source_accounts (n only until outcomes fill)."""
        counts: dict[str, int] = {}
        by_decision: dict[str, int] = {}
        for cand in self.list_candidates(experiment_id="xintel_v0"):
            by_decision[cand.decision.value] = by_decision.get(cand.decision.value, 0) + 1
            for acct in cand.source_accounts or []:
                counts[acct] = counts.get(acct, 0) + 1
        return {
            "experiment_id": "xintel_v0",
            "n_candidates": sum(by_decision.values()),
            "by_decision": by_decision,
            "by_source_account": counts,
            "note": "Prospective stub — promote ranks only with filled OOS outcomes. See reports/signal_leaderboard_v0.md",
        }
