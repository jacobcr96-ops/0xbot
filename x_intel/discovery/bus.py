"""Discovery bus — normalize + dedupe by (chain, ca)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from x_intel.discovery.models import DiscoveryEvent, DiscoveryRecord
from x_intel.io_atomic import atomic_read_json, atomic_write_json

# Solana base58-ish / EVM 0x
_CA_RE_EVM = re.compile(r"^0x[a-fA-F0-9]{40}$")
_CA_RE_SOL = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def normalize_ca(ca: str) -> str:
    ca = (ca or "").strip()
    if ca.startswith("0x"):
        return ca.lower()
    return ca


def normalize_chain(chain: str) -> str:
    c = (chain or "").strip().lower()
    aliases = {
        "sol": "solana",
        "eth": "ethereum",
        "ether": "ethereum",
        "bnb": "bsc",
        "binance": "bsc",
        "binance-smart-chain": "bsc",
    }
    return aliases.get(c, c)


def is_resolvable_ca(ca: str, chain: str) -> bool:
    ca = normalize_ca(ca)
    if not ca or ca.upper().startswith("TEMPLATE") or ca in {"unknown", "n/a", "-"}:
        return False
    chain = normalize_chain(chain)
    if chain in {"ethereum", "base", "bsc", "arbitrum", "polygon", "avalanche"}:
        return bool(_CA_RE_EVM.match(ca))
    if chain == "solana":
        return bool(_CA_RE_SOL.match(ca))
    return len(ca) >= 20


def record_key(chain: str, ca: str) -> str:
    return f"{normalize_chain(chain)}::{normalize_ca(ca)}"


class DiscoveryBus:
    """In-memory + optional on-disk dedupe store.

    first_source + first_seen_at are sticky; later sources append to sources[].
    """

    def __init__(self, store_path: Optional[Path] = None) -> None:
        self.store_path = store_path
        self._records: dict[str, DiscoveryRecord] = {}
        if store_path and store_path.is_file():
            self._load()

    def _load(self) -> None:
        raw = atomic_read_json(self.store_path) if self.store_path else None
        if not raw or not isinstance(raw, dict):
            return
        for key, val in raw.get("records", {}).items():
            self._records[key] = DiscoveryRecord.model_validate(val)

    def persist(self) -> None:
        if not self.store_path:
            return
        payload = {
            "schema": "xintel.discovery_bus.v0",
            "records": {k: v.model_dump(mode="json") for k, v in self._records.items()},
        }
        atomic_write_json(self.store_path, payload)

    def get(self, chain: str, ca: str) -> Optional[DiscoveryRecord]:
        return self._records.get(record_key(chain, ca))

    def all_records(self) -> list[DiscoveryRecord]:
        return list(self._records.values())

    def upsert(self, event: DiscoveryEvent) -> tuple[DiscoveryRecord, bool]:
        """Normalize event into the bus.

        Returns (record, is_first_sighting).
        Subsequent sources append without changing first_seen_at / first_source.
        """
        chain = normalize_chain(event.chain)
        ca = normalize_ca(event.ca)
        key = record_key(chain, ca)
        now = datetime.now(timezone.utc)
        discovered = event.discovered_at
        if discovered.tzinfo is None:
            discovered = discovered.replace(tzinfo=timezone.utc)

        existing = self._records.get(key)
        if existing is None:
            rec = DiscoveryRecord(
                chain=chain,
                ca=ca,
                first_source=str(event.source),
                first_seen_at=discovered,
                sources=[str(event.source)],
                ticker=event.ticker,
                mc_usd=event.mc_usd,
                curve_progress=event.curve_progress,
                liquidity_usd=event.liquidity_usd,
                pair_created_at=event.pair_created_at,
                lagging_universe=bool(event.lagging_universe),
                raw_refs=[event.raw_ref] if event.raw_ref else [],
                confidence_hints=dict(event.confidence_hints or {}),
                discovery_latency_features={
                    "source": str(event.source),
                    "first_seen_at": discovered.isoformat(),
                    "mc_at_first_seen": event.mc_usd,
                    "lagging_universe": bool(event.lagging_universe),
                },
                updated_at=now,
            )
            self._records[key] = rec
            return rec, True

        # Subsequent sighting — sticky first_*, append sources
        src = str(event.source)
        if src not in existing.sources:
            existing.sources.append(src)
        if event.ticker and not existing.ticker:
            existing.ticker = event.ticker
        if event.mc_usd is not None and existing.mc_usd is None:
            existing.mc_usd = event.mc_usd
        if event.curve_progress is not None:
            existing.curve_progress = event.curve_progress
        if event.liquidity_usd is not None and (
            existing.liquidity_usd is None or event.liquidity_usd > existing.liquidity_usd
        ):
            existing.liquidity_usd = event.liquidity_usd
        if event.pair_created_at and not existing.pair_created_at:
            existing.pair_created_at = event.pair_created_at
        if event.raw_ref and event.raw_ref not in existing.raw_refs:
            existing.raw_refs.append(event.raw_ref)
        # lagging flag stays True if ANY source was lagging-only and first was lagging;
        # clear lagging_universe if a non-lagging source arrives (upgrade)
        if not event.lagging_universe:
            existing.lagging_universe = False
        # merge hints
        for k, v in (event.confidence_hints or {}).items():
            existing.confidence_hints.setdefault(k, v)
        existing.updated_at = now
        self._records[key] = existing
        return existing, False
