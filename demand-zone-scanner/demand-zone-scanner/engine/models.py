"""
Core data models for the Demand Zone Scanner.

These are deliberately plain dataclasses (no ORM / pydantic) so the
detection engine can be unit-tested in complete isolation from the
database, API, and Telegram layers that will be built on top of it later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Timeframe(str, Enum):
    D3 = "3D"
    D1 = "1D"
    H4 = "4H"


class Freshness(str, Enum):
    FRESH = "fresh"              # never revisited
    TESTED_ONCE = "tested_once"  # revisited once and held
    MULTIPLE_TESTS = "multiple_tests"  # revisited 2+ times
    INVALIDATED = "invalidated"  # price closed clean through the zone low


class ZoneStatus(str, Enum):
    WAITING = "WAITING"          # price above zone, not yet reached
    ENTERED = "ENTERED"          # price currently inside the zone
    INVALIDATED = "INVALIDATED"  # zone broken, no longer valid
    STALE = "STALE"              # too old / too many tests, excluded


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def body_high(self) -> float:
        return max(self.open, self.close)

    @property
    def body_low(self) -> float:
        return min(self.open, self.close)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)


@dataclass
class ZoneEvidence:
    """Raw evidence collected about a candidate zone before scoring."""
    fresh: Freshness = Freshness.FRESH
    test_count: int = 0
    departure_pct: float = 0.0          # % move away from zone after formation
    departure_atr_multiple: float = 0.0  # departure normalized by ATR
    volume_expansion_ratio: float = 1.0  # displacement volume / baseline avg volume
    break_of_structure: bool = False
    liquidity_sweep: bool = False
    equal_lows_nearby: bool = False
    fib_confluence: Optional[float] = None  # e.g. 0.5 or 0.66 if aligned, else None


@dataclass
class DemandZone:
    symbol: str
    timeframe: Timeframe
    zone_low: float
    zone_high: float
    formed_at: datetime
    base_candle_indices: list = field(default_factory=list)
    displacement_candle_indices: list = field(default_factory=list)
    evidence: ZoneEvidence = field(default_factory=ZoneEvidence)

    # populated by the scoring engine
    score: Optional[float] = None
    score_breakdown: dict = field(default_factory=dict)
    grade: Optional[str] = None

    # populated by multi-timeframe confluence pass
    confluent_timeframes: list = field(default_factory=list)

    # populated by the monitoring layer (not this module) but modeled
    # here so downstream code has a stable shape to persist
    status: ZoneStatus = ZoneStatus.WAITING
    current_price: Optional[float] = None

    @property
    def height(self) -> float:
        return self.zone_high - self.zone_low

    @property
    def midpoint(self) -> float:
        return (self.zone_high + self.zone_low) / 2

    def distance_pct(self, price: float) -> float:
        """% distance of price above the top of the zone. Negative if inside/below."""
        if price <= self.zone_high:
            return 0.0
        return ((price - self.zone_high) / self.zone_high) * 100

    def contains(self, price: float) -> bool:
        return self.zone_low <= price <= self.zone_high
