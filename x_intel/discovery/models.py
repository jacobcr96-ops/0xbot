"""Discovery event + ledger record models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

DiscoverySource = Literal[
    "dexscreener_new",
    "pumpfun_curve",
    "x_social",
    "fomo_sidebar",
    "flow_hint",
    "fixture",
    "other",
]

KNOWN_FARM_DOMAINS = frozenset(
    {
        "airdrop-claim.io",
        "claim-airdrop.net",
        "free-mint.xyz",
        "presale-farm.com",
    }
)


class DiscoveryEvent(BaseModel):
    """Normalized discovery ping from any parallel source adapter."""

    model_config = ConfigDict(extra="allow")

    source: DiscoverySource | str
    discovered_at: datetime
    chain: str
    ca: str
    ticker: Optional[str] = None
    raw_ref: Optional[str] = None
    mc_usd: Optional[float] = None
    curve_progress: Optional[float] = None  # 0..1 for bonding curves
    liquidity_usd: Optional[float] = None
    pair_created_at: Optional[datetime] = None
    confidence_hints: dict[str, Any] = Field(default_factory=dict)
    # FOMO/0xbot sidebar dumps are lagging confirmation only
    lagging_universe: bool = False
    event_kind: Optional[str] = None  # e.g. new_pair, curve_new, graduate, migrate


class DiscoveryRecord(BaseModel):
    """Dedupe ledger entry keyed by (chain, ca). first_seen never moves."""

    model_config = ConfigDict(extra="allow")

    chain: str
    ca: str
    first_source: str
    first_seen_at: datetime
    sources: list[str] = Field(default_factory=list)
    ticker: Optional[str] = None
    mc_usd: Optional[float] = None
    curve_progress: Optional[float] = None
    liquidity_usd: Optional[float] = None
    pair_created_at: Optional[datetime] = None
    lagging_universe: bool = False
    raw_refs: list[str] = Field(default_factory=list)
    confidence_hints: dict[str, Any] = Field(default_factory=dict)
    discovery_latency_features: dict[str, Any] = Field(default_factory=dict)
    candidate_id: Optional[str] = None
    last_decision: Optional[str] = None  # pursue|watch|reject
    updated_at: Optional[datetime] = None
