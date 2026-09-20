"""Outcome return math — measurement only, never trades."""

from __future__ import annotations

from typing import Optional, Sequence


def compute_fractional_return(
    price_at_first_sight: float,
    price_at_horizon: float,
) -> Optional[float]:
    """Fractional return: (p_h - p0) / p0. None if baseline invalid."""
    if price_at_first_sight is None or price_at_horizon is None:
        return None
    if price_at_first_sight <= 0:
        return None
    return (price_at_horizon - price_at_first_sight) / price_at_first_sight


def compute_extrema(
    price_at_first_sight: float,
    prices: Sequence[float],
) -> tuple[Optional[float], Optional[float]]:
    """max_runup and max_drawdown (fractional) over a price path from first sight.

    max_runup: peak (p - p0) / p0
    max_drawdown: worst (p - p0) / p0 (most negative)
    """
    if price_at_first_sight is None or price_at_first_sight <= 0:
        return None, None
    if not prices:
        return None, None
    rets = [(p - price_at_first_sight) / price_at_first_sight for p in prices if p is not None]
    if not rets:
        return None, None
    return max(rets), min(rets)


HORIZON_MINUTES = {
    "ret_5m": 5,
    "ret_15m": 15,
    "ret_1h": 60,
    "ret_6h": 360,
    "ret_24h": 1440,
}
