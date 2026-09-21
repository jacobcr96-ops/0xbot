"""Parallel discovery source adapters (fixture/replay by default)."""

from __future__ import annotations

from typing import Optional

from x_intel.config import configured_chains
from x_intel.discovery.sources.dexscreener_new import DexScreenerNewSource
from x_intel.discovery.sources.pumpfun_curve import PumpfunCurveSource
from x_intel.discovery.sources.x_social import XSocialSource
from x_intel.discovery.sources.fomo_sidebar import FomoSidebarSource
from x_intel.discovery.sources.flow_hint import FlowHintSource


def default_sources(
    *,
    fixture_dir: Optional[object] = None,
    live: bool = False,
    data_dir: Optional[object] = None,
) -> list:
    """Build the standard parallel adapter set.

    live=False (default): fixture/replay only — no network required for tests.
    Dex chains come from ``XINTEL_CHAINS`` (default solana/base/ethereum/bsc).
    """
    from pathlib import Path

    fd = Path(fixture_dir) if fixture_dir else None
    dd = Path(data_dir) if data_dir else None
    chains = set(configured_chains())
    return [
        DexScreenerNewSource(fixture_dir=fd, live=live, chains=chains, data_dir_path=dd),
        PumpfunCurveSource(fixture_dir=fd, live=live, data_dir=dd),
        XSocialSource(fixture_dir=fd, live=live),
        FomoSidebarSource(data_dir=dd, fixture_dir=fd),
        FlowHintSource(data_dir=dd, fixture_dir=fd),
    ]


__all__ = [
    "DexScreenerNewSource",
    "PumpfunCurveSource",
    "XSocialSource",
    "FomoSidebarSource",
    "FlowHintSource",
    "default_sources",
]
