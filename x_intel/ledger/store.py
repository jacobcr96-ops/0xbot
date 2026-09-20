"""Filesystem candidate ledger + decision disk queue + execution reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import UUID

from x_intel.config import data_dir, do_not_execute_until_armed
from x_intel.io_atomic import append_jsonl, atomic_read_json, atomic_write_json
from x_intel.schemas.models import (
    CandidateV1,
    DecisionV1,
    ExecutionReportV1,
    OutcomeFillV1,
)

SKIP_NAMES = {"_index.jsonl", "_TEMPLATE.json", "inbox.jsonl"}


@dataclass(frozen=True)
class RepoPaths:
    """Paths under the configurable data root (XINTEL_DATA_DIR, default data/)."""

    data: Path

    @property
    def root(self) -> Path:
        """Repo root (parent of data/) when using default layout."""
        return self.data.parent

    @property
    def candidates(self) -> Path:
        return self.data / "candidates"

    @property
    def outcomes(self) -> Path:
        return self.data / "outcomes"

    @property
    def decisions(self) -> Path:
        """Production disk decision queue (Tampermonkey / bridge reads here)."""
        return self.data / "decisions"

    @property
    def decisions_inbox(self) -> Path:
        return self.decisions / "inbox.jsonl"

    @property
    def fixture_decisions(self) -> Path:
        """Legacy demo fixtures (still readable by list_decisions)."""
        return self.data / "fixtures" / "decisions"

    @property
    def execution_reports(self) -> Path:
        return self.data / "execution_reports"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def index(self) -> Path:
        return self.candidates / "_index.jsonl"


def default_paths(start: Optional[Path] = None) -> RepoPaths:
    """Resolve data root via XINTEL_DATA_DIR or repo walk."""
    return RepoPaths(data=data_dir(start))


class CandidateLedger:
    """pursue/watch/reject logging + decision disk queue. Measurement / intents only."""

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
            raw = atomic_read_json(path)
            if raw is None:
                continue
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
        raw = atomic_read_json(path)
        if raw is None:
            return None
        return CandidateV1.model_validate(raw)

    def save_candidate(self, candidate: CandidateV1, append_index: bool = True) -> Path:
        self.paths.candidates.mkdir(parents=True, exist_ok=True)
        path = self.paths.candidates / f"{candidate.candidate_id}.json"
        atomic_write_json(path, candidate)
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
            append_jsonl(self.paths.index, line)
        return path

    def get_outcome(self, candidate_id: str | UUID) -> Optional[OutcomeFillV1]:
        path = self.paths.outcomes / f"{candidate_id}.json"
        raw = atomic_read_json(path)
        if raw is None:
            return None
        return OutcomeFillV1.model_validate(raw)

    def save_outcome(self, fill: OutcomeFillV1) -> Path:
        self.paths.outcomes.mkdir(parents=True, exist_ok=True)
        path = self.paths.outcomes / f"{fill.candidate_id}.json"
        atomic_write_json(path, fill)
        return path

    def _decision_dirs(self) -> list[Path]:
        """Production queue first, then legacy fixtures."""
        dirs = [self.paths.decisions, self.paths.fixture_decisions]
        seen: set[Path] = set()
        out: list[Path] = []
        for d in dirs:
            rp = d.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            out.append(d)
        return out

    def list_decisions(
        self,
        unexpired_only: bool = False,
        min_confidence: float = 0.0,
        now: Optional[datetime] = None,
    ) -> list[DecisionV1]:
        from x_intel.schemas.models import is_decision_stale

        now = now or datetime.now(timezone.utc)
        out: list[DecisionV1] = []
        seen_ids: set[str] = set()
        for ddir in self._decision_dirs():
            if not ddir.is_dir():
                continue
            for path in sorted(ddir.glob("*.json")):
                if path.name.startswith("_") or path.name.endswith(".tmp"):
                    continue
                raw = atomic_read_json(path)
                if raw is None:
                    continue
                dec = DecisionV1.model_validate(raw)
                did = str(dec.decision_id)
                if did in seen_ids:
                    continue
                seen_ids.add(did)
                if dec.confidence < min_confidence:
                    continue
                if unexpired_only and is_decision_stale(dec, now=now):
                    continue
                out.append(dec)
        return out

    def get_decision(self, decision_id: str | UUID) -> Optional[DecisionV1]:
        for ddir in self._decision_dirs():
            path = ddir / f"{decision_id}.json"
            raw = atomic_read_json(path)
            if raw is None:
                continue
            return DecisionV1.model_validate(raw)
        return None

    def save_decision(
        self,
        decision: DecisionV1,
        *,
        append_inbox: bool = True,
    ) -> Path:
        """Atomically write decision to production disk queue ``data/decisions/``.

        Never leaves partial JSON under the final name. Optionally appends
        ``data/decisions/inbox.jsonl`` index line (flushed).
        """
        self.paths.decisions.mkdir(parents=True, exist_ok=True)
        path = self.paths.decisions / f"{decision.decision_id}.json"
        atomic_write_json(path, decision)
        if append_inbox:
            append_jsonl(
                self.paths.decisions_inbox,
                {
                    "decision_id": str(decision.decision_id),
                    "action": decision.action.value,
                    "issued_at": decision.issued_at.isoformat(),
                    "expires_at": decision.expires_at.isoformat(),
                    "contract_address": decision.contract_address,
                    "chain": decision.chain,
                    "confidence": decision.confidence,
                    "candidate_id": decision.candidate_id,
                    "do_not_execute_until_armed": decision.do_not_execute_until_armed,
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                },
            )
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
            "do_not_execute_until_armed": do_not_execute_until_armed(),
        }
        atomic_write_json(ack_dir / f"{decision_id}.json", record)
        return record

    def save_execution_report(self, report: ExecutionReportV1) -> Path:
        self.paths.execution_reports.mkdir(parents=True, exist_ok=True)
        path = self.paths.execution_reports / f"{report.report_id}.json"
        atomic_write_json(path, report)
        return path

    def get_execution_report(self, report_id: str | UUID) -> Optional[ExecutionReportV1]:
        path = self.paths.execution_reports / f"{report_id}.json"
        raw = atomic_read_json(path)
        if raw is None:
            return None
        return ExecutionReportV1.model_validate(raw)

    def list_execution_reports(self) -> list[ExecutionReportV1]:
        ddir = self.paths.execution_reports
        if not ddir.is_dir():
            return []
        out: list[ExecutionReportV1] = []
        for path in sorted(ddir.glob("*.json")):
            if path.name.startswith("_") or path.name.endswith(".tmp"):
                continue
            raw = atomic_read_json(path)
            if raw is None:
                continue
            out.append(ExecutionReportV1.model_validate(raw))
        return out

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
