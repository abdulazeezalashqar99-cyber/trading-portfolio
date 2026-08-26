"""
Market structure analysis: Break of Structure, liquidity sweeps, equal lows.

These functions all operate on a df of OHLCV candles plus a pre-computed
list of swing points (see swing_points.py) so they can be reused by both
the zone detector and, later, any live monitoring code without recomputing
swings repeatedly.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from .swing_points import SwingPoint, most_recent_swing_high, most_recent_swing_low


def detect_break_of_structure(
    df: pd.DataFrame,
    swings: List[SwingPoint],
    displacement_end_index: int,
    lookahead: int = 10,
) -> bool:
    """
    Bullish BOS: after the displacement move out of the zone, does price
    close above the most recent swing high that preceded the zone?
    """
    prior_high = most_recent_swing_high(swings, displacement_end_index)
    if prior_high is None:
        return False

    end = min(len(df), displacement_end_index + lookahead)
    window = df.iloc[displacement_end_index:end]
    return bool((window["close"] > prior_high.price).any())


def detect_liquidity_sweep(
    df: pd.DataFrame,
    swings: List[SwingPoint],
    zone_low_index: int,
    tolerance_pct: float = 0.15,
) -> bool:
    """
    A liquidity sweep is when the candle(s) forming the zone low wick below
    a prior swing low (grabbing resting stop-loss liquidity) before closing
    back above it — i.e. a false breakdown.
    """
    prior_low = most_recent_swing_low(swings, zone_low_index)
    if prior_low is None:
        return False

    candle = df.iloc[zone_low_index]
    swept_below = candle["low"] < prior_low.price * (1 - tolerance_pct / 100)
    closed_back_above = candle["close"] > prior_low.price
    return bool(swept_below and closed_back_above)


def detect_equal_lows(
    swings: List[SwingPoint],
    around_index: int,
    tolerance_pct: float = 0.25,
    window: int = 20,
) -> bool:
    """
    Equal lows (a common liquidity pool) near the zone: two or more swing
    lows within `window` candles of each other whose prices are within
    tolerance_pct of one another.
    """
    nearby_lows = [
        s for s in swings
        if s.kind == "low" and abs(s.index - around_index) <= window
    ]
    for i in range(len(nearby_lows)):
        for j in range(i + 1, len(nearby_lows)):
            a, b = nearby_lows[i].price, nearby_lows[j].price
            if abs(a - b) / min(a, b) * 100 <= tolerance_pct:
                return True
    return False
