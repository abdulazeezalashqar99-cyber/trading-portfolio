"""Data models shared across the CoinMarketCap and Binance clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class UniverseCoin:
    """One entry in the scanner's tradeable coin universe."""
    symbol: str                # e.g. "BTC"
    name: str
    provider_id: str           # provider-specific id (CMC's numeric id, or CoinGecko's slug id)
    market_cap_rank: int
    market_cap_usd: float
    volume_24h_usd: float
    percent_change_24h: float
    is_stablecoin: bool
    binance_symbol: Optional[str] = None  # e.g. "BTCUSDT", None if unmapped


@dataclass
class UniverseDiff:
    added: list[str]    # symbols that newly entered the Top 100
    removed: list[str]  # symbols that fell out of the Top 100

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)
