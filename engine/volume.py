"""Volume expansion analysis."""

from __future__ import annotations

import pandas as pd


def volume_expansion_ratio(
    df: pd.DataFrame,
    displacement_indices: list[int],
    baseline_window: int = 20,
) -> float:
    """
    Ratio of average volume during the displacement move vs the average
    volume of the `baseline_window` candles preceding the zone. A ratio
    > 1.5 is generally considered meaningful expansion.
    """
    if not displacement_indices:
        return 1.0

    start = displacement_indices[0]
    baseline_start = max(0, start - baseline_window)
    baseline = df["volume"].iloc[baseline_start:start]
    if baseline.empty or baseline.mean() == 0:
        return 1.0

    displacement_avg = df["volume"].iloc[displacement_indices].mean()
    return float(displacement_avg / baseline.mean())


def is_volume_expansion(ratio: float, threshold: float = 1.5) -> bool:
    return ratio >= threshold
