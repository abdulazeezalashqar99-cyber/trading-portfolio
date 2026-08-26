"""
Swing high / swing low detection.

Everything else in the engine (demand zones, BOS, liquidity sweeps,
Fibonacci confluence) is built on top of swing points, so this module
has zero dependencies on the rest of the package.
"""

from __future__ import annotations

from typing import List, NamedTuple

import pandas as pd


class SwingPoint(NamedTuple):
    index: int
    price: float
    kind: str  # "high" or "low"


def find_swing_points(df: pd.DataFrame, lookback: int = 3) -> List[SwingPoint]:
    """
    A candle at index i is a swing low if its low is the lowest low among
    the `lookback` candles on either side of it (and symmetrically for highs).

    df must have columns: high, low (indexed 0..n-1, chronological order).
    """
    n = len(df)
    swings: List[SwingPoint] = []
    highs = df["high"].values
    lows = df["low"].values

    for i in range(lookback, n - lookback):
        window_low = lows[i - lookback : i + lookback + 1]
        window_high = highs[i - lookback : i + lookback + 1]

        if lows[i] == window_low.min() and (window_low == lows[i]).sum() == 1:
            swings.append(SwingPoint(i, lows[i], "low"))

        if highs[i] == window_high.max() and (window_high == highs[i]).sum() == 1:
            swings.append(SwingPoint(i, highs[i], "high"))

    swings.sort(key=lambda s: s.index)
    return swings


def most_recent_swing_high(swings: List[SwingPoint], before_index: int) -> SwingPoint | None:
    candidates = [s for s in swings if s.kind == "high" and s.index < before_index]
    return candidates[-1] if candidates else None


def most_recent_swing_low(swings: List[SwingPoint], before_index: int) -> SwingPoint | None:
    candidates = [s for s in swings if s.kind == "low" and s.index < before_index]
    return candidates[-1] if candidates else None


def prior_swing_lows(swings: List[SwingPoint], before_index: int, count: int = 5) -> List[SwingPoint]:
    candidates = [s for s in swings if s.kind == "low" and s.index < before_index]
    return candidates[-count:]
