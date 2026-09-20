"""X social discovery adapter — wraps existing ingest interface.

Secondary channel: emit when CA / ticker+launch cues appear. On-chain sources
remain primary for earliest practical discovery.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from x_intel.discovery.models import DiscoveryEvent
from x_intel.ingest.interfaces import FixtureIngest

# Solana CA-ish + EVM
_CA_PAT = re.compile(
    r"(?:^|[\s\"'(])((?:0x[a-fA-F0-9]{40})|(?:[1-9A-HJ-NP-Za-km-z]{32,44}))(?:$|[\s\"',).])"
)
_LAUNCH_CUES = re.compile(
    r"\b(launch(?:ing|ed)?|just\s+launched|ca[:\s]|contract[:\s]|pump\.fun|new\s+pair)\b",
    re.I,
)


class XSocialSource:
    source_id = "x_social"

    def __init__(
        self,
        *,
        fixture_dir: Optional[Path] = None,
        live: bool = False,
        ingest: Optional[FixtureIngest] = None,
    ) -> None:
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.live = live  # live X requires MCP/keys — not used in unit tests
        self.ingest = ingest

    def poll(self) -> list[DiscoveryEvent]:
        # Live mode: only real X ingest (MCP/keys) — never bleed fixtures into live cycles
        if self.live and self.ingest is None:
            return []
        # Prefer dedicated discovery fixture when present (replay/tests)
        path = self._fixture_path()
        if path and path.is_file() and not self.live:
            return self._from_fixture_file(path)
        # Fall back to wrapping FixtureIngest candidate replay
        try:
            src = self.ingest or FixtureIngest()
            rows = src.fetch_recent(limit=30)
        except Exception:  # noqa: BLE001
            return []
        events: list[DiscoveryEvent] = []
        for r in rows:
            ev = self._from_ingest_row(r)
            if ev:
                events.append(ev)
        return events

    def _fixture_path(self) -> Optional[Path]:
        if self.fixture_dir:
            p = self.fixture_dir / "x_social.json"
            if p.is_file():
                return p
        here = Path(__file__).resolve()
        for parent in here.parents:
            cand = parent / "tests" / "fixtures" / "discovery" / "x_social.json"
            if cand.is_file():
                return cand
        return None

    def _from_fixture_file(self, path: Path) -> list[DiscoveryEvent]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else raw.get("events") or raw.get("posts") or []
        out: list[DiscoveryEvent] = []
        for r in rows:
            ev = self._row_to_event(r)
            if ev:
                out.append(ev)
        return out

    def _row_to_event(self, r: dict[str, Any]) -> Optional[DiscoveryEvent]:
        text = str(r.get("text") or r.get("summary") or "")
        ca = (r.get("ca") or r.get("contract_address") or "").strip()
        if not ca:
            m = _CA_PAT.search(text)
            if m:
                ca = m.group(1)
        if not ca:
            return None
        # Require launch cue OR explicit ca field in fixture
        if not r.get("ca") and not r.get("contract_address") and not _LAUNCH_CUES.search(text):
            return None
        discovered = _parse_dt(r.get("discovered_at") or r.get("created_at")) or datetime.now(
            timezone.utc
        )
        hints = dict(r.get("confidence_hints") or {"x_social": True})
        # Paid airdrop-shill templates are not organic X evidence
        if re.search(r"\bcrypto airdrop\b|\bairdrop\b.*\b(memecoin|token)\b", text, re.I):
            hints["airdrop_farm"] = True
            hints["organic_x"] = False
        return DiscoveryEvent(
            source=self.source_id,
            discovered_at=discovered,
            chain=str(r.get("chain") or "solana"),
            ca=ca,
            ticker=r.get("ticker"),
            raw_ref=r.get("raw_ref") or r.get("post_id") or r.get("url"),
            mc_usd=_f(r.get("mc_usd")),
            event_kind="x_ca_mention",
            confidence_hints=hints,
        )

    def _from_ingest_row(self, r: dict[str, Any]) -> Optional[DiscoveryEvent]:
        ca = (r.get("contract_address") or "").strip()
        if not ca:
            return None
        discovered = _parse_dt(r.get("first_seen_at")) or datetime.now(timezone.utc)
        return DiscoveryEvent(
            source=self.source_id,
            discovered_at=discovered,
            chain=str(r.get("chain") or "solana"),
            ca=ca,
            ticker=r.get("ticker"),
            raw_ref=str(r.get("candidate_id") or "fixture_ingest"),
            event_kind="x_fixture_replay",
            confidence_hints={"x_social": True, "secondary": True},
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
