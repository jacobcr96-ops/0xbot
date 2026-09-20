"""Ingest interfaces — fixture/replay by default (no live trading, no keys)."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from x_intel.ledger.store import default_paths
from x_intel.schemas.models import CandidateV1, MarketSnapshot


class XPostSource(ABC):
    @abstractmethod
    def fetch_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        ...


class MarketSnapshotSource(ABC):
    @abstractmethod
    def snapshot(self, chain: str, contract_address: str) -> Optional[MarketSnapshot]:
        ...


class FixtureIngest(XPostSource, MarketSnapshotSource):
    """Replay from on-disk candidates / history — default safe mode."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.paths = default_paths(root) if root else default_paths()

    def fetch_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cand_dir = self.paths.candidates
        for path in sorted(cand_dir.glob("*.json")):
            if path.name.startswith("_"):
                continue
            raw = json.loads(path.read_text())
            rows.append(
                {
                    "source": "fixture",
                    "candidate_id": raw.get("candidate_id"),
                    "ticker": raw.get("ticker"),
                    "contract_address": raw.get("contract_address"),
                    "first_seen_at": raw.get("first_seen_at"),
                    "evidence": raw.get("evidence", [])[:2],
                }
            )
            if len(rows) >= limit:
                break
        return rows

    def snapshot(self, chain: str, contract_address: str) -> Optional[MarketSnapshot]:
        for path in self.paths.candidates.glob("*.json"):
            if path.name.startswith("_"):
                continue
            raw = json.loads(path.read_text())
            if raw.get("contract_address") == contract_address and raw.get("chain") == chain:
                return MarketSnapshot(
                    mc_usd=raw.get("mc_usd_at_first_sight"),
                    price_usd=raw.get("price_usd_at_first_sight"),
                    as_of=datetime.now(timezone.utc),
                )
        return None

    def load_candidates(self) -> list[CandidateV1]:
        out: list[CandidateV1] = []
        for path in sorted(self.paths.candidates.glob("*.json")):
            if path.name.startswith("_"):
                continue
            out.append(CandidateV1.model_validate(json.loads(path.read_text())))
        return out
