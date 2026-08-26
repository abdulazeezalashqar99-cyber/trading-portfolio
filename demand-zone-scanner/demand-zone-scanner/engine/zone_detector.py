"""
Demand Zone detection engine.

Detects zones matching the pattern described in the spec:
  base / consolidation -> liquidity sweep or clear low -> strong bullish
  displacement -> break of structure -> departure from the zone, with
  volume expansion, that price has not yet returned to (fresh).

This module only *detects and characterizes* zones. Scoring happens in
scoring.py, freshness classification in freshness.py — kept separate so
each piece is independently testable.
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from .config import DetectionConfig, DEFAULT_CONFIG
from .fibonacci import fibonacci_confluence
from .freshness import classify_freshness
from .models import Candle, DemandZone, Timeframe, ZoneEvidence, ZoneStatus
from .structure import detect_break_of_structure, detect_equal_lows, detect_liquidity_sweep
from .swing_points import find_swing_points
from .volume import volume_expansion_ratio


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _find_base_candidates(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, open_: np.ndarray,
    atr: np.ndarray, swings: List, config: DetectionConfig,
) -> List[List[int]]:
    """
    A base/consolidation candidate must sit at an actual swing low — not
    just any low-range candle, which would false-positive on every quiet
    candle inside an ongoing trend. For each swing low we grow a small
    window of adjacent low-range, overlapping candles around it (the
    "base"), then require a bullish candle immediately after.

    Operates on raw numpy arrays rather than DataFrame .iloc access:
    repeated scalar .iloc/.loc lookups carry real per-call pandas
    overhead, which dominates runtime on multi-year OHLCV history when
    this is called across a large coin universe. See PERFORMANCE.md.
    """
    candidates: List[List[int]] = []
    n = len(high)
    swing_low_indices = sorted(s.index for s in swings if s.kind == "low")

    for anchor in swing_low_indices:
        if anchor >= n - 1:
            continue
        if high[anchor] - low[anchor] > atr[anchor] * config.base_range_atr_multiple:
            continue

        group = [anchor]
        j = anchor + 1
        while (
            j < n
            and len(group) < config.max_base_candles
            and high[j] - low[j] <= atr[j] * config.base_range_atr_multiple
            and close[j] <= high[group[-1]]  # still overlapping/consolidating
            and low[j] >= low[anchor] * (1 - 0.5 / 100)  # doesn't make a new lower low
        ):
            group.append(j)
            j += 1

        # must be followed by a genuinely bullish displacement candle
        if j < n and close[j] > open_[j]:
            candidates.append(group)

    return candidates


def _find_displacement(
    open_: np.ndarray, close: np.ndarray, base_end_index: int, config: DetectionConfig,
) -> List[int]:
    """Consecutive bullish candles immediately after the base, up to the lookahead window."""
    n = len(close)
    end = min(n, base_end_index + 1 + config.displacement_lookahead)
    indices = []
    for i in range(base_end_index + 1, end):
        if close[i] > open_[i]:
            indices.append(i)
        else:
            break
    return indices


def _departure_metrics(
    high: np.ndarray, zone_high: float, displacement_indices: List[int], atr: np.ndarray,
) -> tuple[float, float]:
    if not displacement_indices:
        return 0.0, 0.0
    peak = high[displacement_indices].max()
    departure_pct = ((peak - zone_high) / zone_high) * 100
    ref_atr = atr[displacement_indices[0]] or 1e-9
    departure_atr_multiple = (peak - zone_high) / ref_atr
    return float(departure_pct), float(departure_atr_multiple)


def detect_demand_zones(
    df: pd.DataFrame,
    symbol: str,
    timeframe: Timeframe,
    config: DetectionConfig = DEFAULT_CONFIG,
) -> List[DemandZone]:
    """
    df must be chronologically ordered with columns:
      timestamp, open, high, low, close, volume
    (reset_index so integer position == row order — the engine indexes by
    position throughout).

    Raises ValueError if required columns are missing or if the data is
    not chronologically sorted. Fails loudly on purpose: a malformed
    OHLCV frame (e.g. a bad API response) should never be allowed to
    silently produce an empty or garbage zone list — with 100 symbols
    running unattended, a silent failure here just looks like "no
    opportunities found today" and nobody would ever notice the API
    problem underneath it.
    """
    required_cols = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"OHLCV dataframe for {symbol} {timeframe.value} is missing columns: {sorted(missing)}")

    df = df.reset_index(drop=True)
    if len(df) < 30:
        return []

    if not df["timestamp"].is_monotonic_increasing:
        raise ValueError(
            f"OHLCV dataframe for {symbol} {timeframe.value} is not chronologically sorted"
        )

    if df[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError(f"OHLCV dataframe for {symbol} {timeframe.value} contains NaN price values")

    atr_series = _atr(df)
    atr = atr_series.to_numpy()
    open_arr = df["open"].to_numpy()
    high_arr = df["high"].to_numpy()
    low_arr = df["low"].to_numpy()
    close_arr = df["close"].to_numpy()

    swings = find_swing_points(df, lookback=config.swing_lookback)
    bases = _find_base_candidates(high_arr, low_arr, close_arr, open_arr, atr, swings, config)

    zones: List[DemandZone] = []
    current_price = float(close_arr[-1])

    for base in bases:
        base_end = base[-1]
        displacement = _find_displacement(open_arr, close_arr, base_end, config)
        if not displacement:
            continue

        zone_low = float(low_arr[base].min())
        zone_high = float(high_arr[base].max())

        if config.require_price_above_zone and current_price <= zone_high:
            continue

        departure_pct, departure_atr_multiple = _departure_metrics(
            high_arr, zone_high, displacement, atr
        )

        # freshness is evaluated from the point price has actually left the
        # zone (end of the displacement move) - not from the base candle
        # itself, since the first displacement candle's low commonly still
        # wicks inside the zone it's departing from.
        freshness, test_count = classify_freshness(
            low_arr, high_arr, close_arr, zone_low, zone_high,
            displacement[-1], config.invalidation_buffer_pct,
        )

        vol_ratio = volume_expansion_ratio(df, displacement, config.volume_baseline_window)

        evidence = ZoneEvidence(
            fresh=freshness,
            test_count=test_count,
            departure_pct=departure_pct,
            departure_atr_multiple=departure_atr_multiple,
            volume_expansion_ratio=vol_ratio,
            break_of_structure=detect_break_of_structure(
                df, swings, displacement[-1], config.bos_lookahead
            ),
            liquidity_sweep=detect_liquidity_sweep(
                df, swings, base[0], config.liquidity_sweep_tolerance_pct
            ),
            equal_lows_nearby=detect_equal_lows(
                swings, base[0], config.equal_lows_tolerance_pct, config.equal_lows_window
            ),
            fib_confluence=fibonacci_confluence(
                swings, zone_low, zone_high, base[0],
                config.fib_tolerance_pct, config.fib_levels,
            ),
        )

        zone = DemandZone(
            symbol=symbol,
            timeframe=timeframe,
            zone_low=zone_low,
            zone_high=zone_high,
            formed_at=df["timestamp"].iloc[base_end],
            base_candle_indices=base,
            displacement_candle_indices=displacement,
            evidence=evidence,
            current_price=current_price,
            status=ZoneStatus.ENTERED if zone_low <= current_price <= zone_high else ZoneStatus.WAITING,
        )
        zones.append(zone)

    return zones
