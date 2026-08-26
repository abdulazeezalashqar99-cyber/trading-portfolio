"""
Persistence-layer data models.

ZoneRecord mirrors the `demand_zones` table (spec section 14's required
field list) and section 9's watchlist fields - the two overlap almost
entirely, so one table serves both the live watchlist and the historical
record used for backtesting later.

Deliberately plain dataclasses, not ORM models - the storage interface
(store.py) is what matters for testability; whatever persistence tech
implements it (Postgres via psycopg2, or an in-memory dict for tests)
just needs to move these in and out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TradedStatus(str, Enum):
    UNDECIDED = "UNDECIDED"
    TRADED = "TRADED"
    SKIPPED = "SKIPPED"


@dataclass
class ZoneRecord:
    # identity (id is None until the store assigns one on first insert)
    id: Optional[int]
    symbol: str
    name: Optional[str]
    timeframe: str  # "3D" / "1D" / "4H"

    # zone geometry + when it formed
    zone_low: float
    zone_high: float
    creation_date: datetime

    # scoring snapshot (updated on every rescan)
    score: float
    grade: str
    freshness: str
    test_count: int
    departure_pct: float
    departure_atr_multiple: float
    volume_expansion_ratio: float
    break_of_structure: bool
    liquidity_sweep: bool
    fibonacci_confluence: Optional[float]

    # live monitoring state (updated on every rescan)
    current_price: float
    zone_entered: bool
    status: str  # ZoneStatus value: WAITING / ENTERED / INVALIDATED / STALE
    btc_condition: Optional[str] = None
    confluent_timeframes: list = field(default_factory=list)  # e.g. ["3D", "1D"]

    # alert state (spec section 11: no duplicate alerts per zone)
    alert_sent: bool = False
    alert_time: Optional[datetime] = None

    # backtesting annotations (spec section 21) - not set automatically;
    # populated later, manually or by a future outcome-tracking job
    result: Optional[str] = None
    traded_skipped: TradedStatus = TradedStatus.UNDECIDED

    updated_at: datetime = field(default_factory=datetime.utcnow)
    last_seen_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class UniverseSnapshot:
    """One point-in-time record of the Top N universe, for auditing and
    for diffing against the next scan (spec section 1)."""
    id: Optional[int]
    taken_at: datetime
    symbols: list[str]


@dataclass
class UniverseEvent:
    """One entry/exit event, logged when the universe changes between scans."""
    id: Optional[int]
    occurred_at: datetime
    symbol: str
    event_type: str  # "ENTERED" or "EXITED"
