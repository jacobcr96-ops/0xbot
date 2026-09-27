"""Optional first-buyer / cluster hints from Flow Desk drops.

Reads ``{XINTEL_DATA_DIR}/flow_inbox/*.json``.

Schema:
```json
{
  "schema_version": "xintel.flow_inbox.v0",
  "observed_at": "2026-09-20T12:00:00Z",
  "chain": "solana",
  "ca": "<contract>",
  "ticker": "EXAMPLE",
  "cluster_score": 0.2,
  "sybil": false,
  "first_buyer_count": 12,
  "notes": "optional"
}
```
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from x_intel.config import data_dir
from x_intel.discovery.models import DiscoveryEvent

log = logging.getLogger(__name__)


class FlowHintSource:
    source_id = "flow_hint"

    def __init__(
        self,
        *,
        data_dir: Optional[Path] = None,  # noqa: A002
        fixture_dir: Optional[Path] = None,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir else None
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None

    def poll(self) -> list[DiscoveryEvent]:
        events: list[DiscoveryEvent] = []
        if self.fixture_dir:
            flat = self.fixture_dir / "flow_hint.json"
            if flat.is_file():
                raw = json.loads(flat.read_text(encoding="utf-8"))
                rows = raw if isinstance(raw, list) else raw.get("events") or [raw]
                for r in rows:
                    ev = self._row_to_event(r)
                    if ev:
                        events.append(ev)
        root = self._data_dir or data_dir()
        inbox = root / "flow_inbox"
        if inbox.is_dir():
            for path in sorted(inbox.glob("*.json")):
                if path.name.endswith(".tmp") or path.name.startswith("_"):
                    continue
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    log.warning("skip bad flow inbox %s: %s", path, e)
                    continue
                rows = raw if isinstance(raw, list) else [raw]
                for r in rows:
                    ev = self._row_to_event(r, raw_ref=str(path))
                    if ev:
                        events.append(ev)
        return events

    def _row_to_event(
        self, r: dict[str, Any], *, raw_ref: Optional[str] = None
    ) -> Optional[DiscoveryEvent]:
        if not isinstance(r, dict):
            return None
        ca = (r.get("ca") or r.get("contract_address") or "").strip()
        if not ca:
            return None
        discovered = _parse_dt(r.get("observed_at") or r.get("discovered_at")) or datetime.now(
            timezone.utc
        )
        sybil = r.get("sybil")
        hints: dict[str, Any] = {
            "flow_hint": True,
            "cluster_score": r.get("cluster_score"),
            "first_buyer_count": r.get("first_buyer_count"),
        }
        if sybil is True:
            hints["D1"] = True
            hints["sybil"] = True
        elif sybil is False:
            hints["D1"] = False
            hints["sybil"] = False
        if r.get("confidence_hints"):
            hints.update(r["confidence_hints"])
        return DiscoveryEvent(
            source=self.source_id,
            discovered_at=discovered,
            chain=str(r.get("chain") or "solana"),
            ca=ca,
            ticker=r.get("ticker"),
            raw_ref=raw_ref or r.get("raw_ref") or "flow_inbox",
            mc_usd=_f(r.get("mc_usd")),
            event_kind="flow_hint",
            confidence_hints=hints,
        )


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
