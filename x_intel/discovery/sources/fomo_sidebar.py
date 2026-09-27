"""FOMO / 0xbot sidebar dump reader.

Reads ``{XINTEL_DATA_DIR}/fomo_inbox/*.json``.

Schema (documented):
```json
{
  "schema_version": "xintel.fomo_inbox.v0",
  "observed_at": "2026-09-20T12:00:00Z",
  "chain": "solana",
  "ca": "<contract>",
  "ticker": "EXAMPLE",
  "mc_usd": 1500000,
  "source_ui": "fomo_sidebar",
  "notes": "optional"
}
```

ALWAYS tagged ``lagging_universe=true`` — late confirmation only.
NEVER sole pursue trigger (enforced in gates.py).
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


class FomoSidebarSource:
    source_id = "fomo_sidebar"

    def __init__(
        self,
        *,
        data_dir: Optional[Path] = None,  # noqa: A002 — mirrors env name
        fixture_dir: Optional[Path] = None,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir else None
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None

    def _inbox_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        root = self._data_dir or data_dir()
        dirs.append(root / "fomo_inbox")
        if self.fixture_dir:
            dirs.append(self.fixture_dir / "fomo_inbox")
            # also allow flat fixture file
        return dirs

    def poll(self) -> list[DiscoveryEvent]:
        events: list[DiscoveryEvent] = []
        # Flat fixture file support
        if self.fixture_dir:
            flat = self.fixture_dir / "fomo_sidebar.json"
            if flat.is_file():
                raw = json.loads(flat.read_text(encoding="utf-8"))
                rows = raw if isinstance(raw, list) else raw.get("events") or [raw]
                for r in rows:
                    ev = self._row_to_event(r)
                    if ev:
                        events.append(ev)
        for d in self._inbox_dirs():
            if not d.is_dir():
                continue
            for path in sorted(d.glob("*.json")):
                if path.name.endswith(".tmp") or path.name.startswith("_"):
                    continue
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as e:
                    log.warning("skip bad fomo inbox %s: %s", path, e)
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
        return DiscoveryEvent(
            source=self.source_id,
            discovered_at=discovered,
            chain=str(r.get("chain") or "solana"),
            ca=ca,
            ticker=r.get("ticker"),
            raw_ref=raw_ref or r.get("raw_ref") or r.get("source_ui") or "fomo_inbox",
            mc_usd=_f(r.get("mc_usd")),
            liquidity_usd=_f(r.get("liquidity_usd")),
            lagging_universe=True,  # ALWAYS
            event_kind="fomo_sidebar",
            confidence_hints={
                "lagging_universe": True,
                "fomo_sidebar": True,
                **(r.get("confidence_hints") or {}),
            },
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
