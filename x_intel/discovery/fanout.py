"""Agent fan-out — write work orders for specialist desks.

Schema (per file ``{XINTEL_DATA_DIR}/agent_inbox/{agent}/{event_id}.json``):

```json
{
  "schema_version": "xintel.agent_work_order.v0",
  "event_id": "<uuid>",
  "agent": "narrative|market|flow|outcome",
  "issued_at": "<iso8601>",
  "chain": "solana",
  "ca": "<contract>",
  "ticker": null,
  "candidate_id": null,
  "discovery": {
    "first_source": "dexscreener_new",
    "first_seen_at": "<iso>",
    "sources": ["dexscreener_new"],
    "mc_usd": 12000,
    "lagging_universe": false
  },
  "task": "score_at_t",
  "priority": "normal",
  "notes": optional string
}
```
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from uuid import uuid4

from x_intel.config import data_dir
from x_intel.discovery.models import DiscoveryRecord
from x_intel.io_atomic import atomic_write_json

DEFAULT_AGENTS = ("narrative", "market", "flow", "outcome")


def agent_inbox_root(root: Optional[Path] = None) -> Path:
    return (root or data_dir()) / "agent_inbox"


def write_work_orders(
    rec: DiscoveryRecord,
    *,
    agents: Iterable[str] = DEFAULT_AGENTS,
    data_root: Optional[Path] = None,
    event_id: Optional[str] = None,
    priority: str = "normal",
    notes: Optional[str] = None,
) -> list[Path]:
    """Write one work-order JSON per agent. Returns written paths."""
    eid = event_id or str(uuid4())
    now = datetime.now(timezone.utc)
    root = agent_inbox_root(data_root)
    written: list[Path] = []
    for agent in agents:
        payload = {
            "schema_version": "xintel.agent_work_order.v0",
            "event_id": eid,
            "agent": agent,
            "issued_at": now.isoformat(),
            "chain": rec.chain,
            "ca": rec.ca,
            "ticker": rec.ticker,
            "candidate_id": rec.candidate_id,
            "discovery": {
                "first_source": rec.first_source,
                "first_seen_at": rec.first_seen_at.isoformat(),
                "sources": list(rec.sources),
                "mc_usd": rec.mc_usd,
                "curve_progress": rec.curve_progress,
                "lagging_universe": rec.lagging_universe,
                "discovery_latency_features": rec.discovery_latency_features,
            },
            "task": "score_at_t",
            "priority": priority,
            "notes": notes,
        }
        path = root / agent / f"{eid}.json"
        atomic_write_json(path, payload)
        written.append(path)
    return written
