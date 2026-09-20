"""Parallel discovery source adapters (fixture/replay by default)."""

from __future__ import annotations

from typing import Optional

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
    """
    from pathlib import Path

    fd = Path(fixture_dir) if fixture_dir else None
    dd = Path(data_dir) if data_dir else None
    return [
        DexScreenerNewSource(fixture_dir=fd, live=live),
        PumpfunCurveSource(fixture_dir=fd, live=live),
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
