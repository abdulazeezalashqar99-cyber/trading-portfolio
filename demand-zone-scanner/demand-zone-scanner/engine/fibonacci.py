"""
Fibonacci confluence.

Checks whether a candidate demand zone lines up with the 50% or 66%
retracement of the most recent significant prior swing (low -> high),
as specified. Returns the matched ratio, or None if there's no confluence.
"""

from __future__ import annotations

from typing import List, Optional

from .swing_points import SwingPoint

FIB_LEVELS = {0.5: "50%", 0.618: "61.8%", 0.66: "66%"}


def fibonacci_confluence(
    swings: List[SwingPoint],
    zone_low: float,
    zone_high: float,
    before_index: int,
    tolerance_pct: float = 3.0,
    levels: tuple[float, ...] = (0.5, 0.66),
) -> Optional[float]:
    """
    Finds the most recent swing_low -> swing_high leg prior to the zone,
    computes the specified retracement levels, and checks whether the
    zone's midpoint falls within tolerance_pct of any of them.
    """
    prior_lows = [s for s in swings if s.kind == "low" and s.index < before_index]
    prior_highs = [s for s in swings if s.kind == "high" and s.index < before_index]
    if not prior_lows or not prior_highs:
        return None

    swing_low = prior_lows[-1]
    highs_after_low = [h for h in prior_highs if h.index > swing_low.index]
    if not highs_after_low:
        return None
    swing_high = highs_after_low[-1]

    leg = swing_high.price - swing_low.price
    if leg <= 0:
        return None

    zone_mid = (zone_low + zone_high) / 2

    for level in levels:
        fib_price = swing_high.price - leg * level
        if abs(zone_mid - fib_price) / fib_price * 100 <= tolerance_pct:
            return level

    return None
