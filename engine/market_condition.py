"""
BTC market condition monitoring (spec section 8).

Deliberately minimal for this version, per the spec: "monitor BTC trend,
BTC market structure" at minimum, with BTC/USDT Dominance flagged as a
later addition. This gives every alert a market-context label
(Strong/Neutral/Weak) without ever converting that context into a signal
- the spec is explicit that a weak BTC condition must only produce a
warning alongside a zone alert, never suppress or auto-convert it.

Classification is intentionally simple and auditable: recent swing
structure (higher highs/higher lows vs. lower highs/lower lows) plus
price position relative to a short moving average. Nothing here is a
trading signal in itself.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from .swing_points import find_swing_points

STRONG = "Strong"
NEUTRAL = "Neutral"
WEAK = "Weak"


def classify_btc_condition(df: pd.DataFrame, ma_period: int = 20, swing_lookback: int = 3) -> str:
    """
    df: BTC/USDT daily OHLCV, chronologically ordered.

    Strong: price above its short MA AND recent structure is making
            higher highs and higher lows.
    Weak:   price below its short MA AND recent structure is making
            lower highs and lower lows.
    Neutral: anything mixed (e.g. above MA but structure rolling over,
             or vice versa) - the honest "no clean read" case.
    """
    if len(df) < ma_period + swing_lookback * 2 + 5:
        return NEUTRAL  # not enough history for a confident read

    close = df["close"]
    ma = close.rolling(ma_period).mean()
    price_above_ma = bool(close.iloc[-1] > ma.iloc[-1])

    swings = find_swing_points(df, lookback=swing_lookback)
    structure = _recent_structure_direction(swings)

    if price_above_ma and structure == "up":
        return STRONG
    if not price_above_ma and structure == "down":
        return WEAK
    return NEUTRAL


def _recent_structure_direction(swings: List, lookback_points: int = 4) -> str:
    """
    Looks at the last few swing highs and the last few swing lows
    separately: the window nets higher (last > first) on both highs and
    lows -> "up"; nets lower on both -> "down"; anything mixed -> "mixed".

    Uses net movement across the window rather than requiring every
    consecutive swing to move the same direction - real structure isn't
    perfectly monotonic even in a clear trend, and requiring strict
    step-by-step monotonicity made this classifier too fragile against
    ordinary noise.
    """
    highs = [s.price for s in swings if s.kind == "high"][-lookback_points:]
    lows = [s.price for s in swings if s.kind == "low"][-lookback_points:]

    if len(highs) < 2 or len(lows) < 2:
        return "mixed"

    highs_up, highs_down = highs[-1] > highs[0], highs[-1] < highs[0]
    lows_up, lows_down = lows[-1] > lows[0], lows[-1] < lows[0]

    if highs_up and lows_up:
        return "up"
    if highs_down and lows_down:
        return "down"
    return "mixed"
