"""
Central, overridable configuration for detection thresholds and scoring
weights. Nothing in the engine hard-codes a magic number outside of here —
this is the file you edit to retune the system later without touching
detection logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScoringWeights:
    """Matches the point table in the spec. Must sum to 100 to keep the
    0-100 scale meaningful, but the engine does not enforce that so you
    can experiment with alternate weightings."""
    strong_3d_demand: float = 15
    strong_1d_demand: float = 15
    confirmation_4h: float = 10
    fresh_untested: float = 15
    departure_displacement: float = 15
    volume_expansion: float = 10
    break_of_structure: float = 10
    liquidity_sweep: float = 5
    fibonacci_confluence: float = 5

    def total(self) -> float:
        return sum(vars(self).values())


@dataclass
class DetectionConfig:
    # swing point detection
    swing_lookback: int = 3

    # base/consolidation
    max_base_candles: int = 3
    base_range_atr_multiple: float = 1.2  # base candle range must be <= this * ATR

    # displacement
    displacement_lookahead: int = 6
    min_departure_atr_multiple: float = 2.0  # min move away from zone to count as "strong departure"

    # volume
    volume_baseline_window: int = 20
    volume_expansion_threshold: float = 1.5

    # structure
    bos_lookahead: int = 10
    liquidity_sweep_tolerance_pct: float = 0.15
    equal_lows_tolerance_pct: float = 0.25
    equal_lows_window: int = 20

    # fibonacci
    fib_tolerance_pct: float = 3.0
    fib_levels: tuple = (0.5, 0.66)

    # freshness / invalidation
    invalidation_buffer_pct: float = 1.0

    # zone filtering
    exclude_below_score: float = 70.0
    require_price_above_zone: bool = True

    weights: ScoringWeights = field(default_factory=ScoringWeights)


DEFAULT_CONFIG = DetectionConfig()
