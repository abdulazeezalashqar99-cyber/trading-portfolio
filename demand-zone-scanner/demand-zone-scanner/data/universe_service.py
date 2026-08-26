"""
Universe service: builds the scanner's tradeable coin universe by
combining a market-cap-ranking provider (CoinGecko by default; CMC also
implemented in coinmarketcap.py) with Binance's set of actively tradable
spot pairs, and detects when the Top 100 changes between scans (spec
section 1: "if a cryptocurrency enters or leaves the Top 100, the
scanner should automatically update its universe").

Takes any client exposing get_top_n(n, stablecoin_volatility_threshold_pct)
-> List[UniverseCoin] - CoinGeckoClient and CoinMarketCapClient both
satisfy this, so the provider can be swapped here without touching
anything downstream.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .binance import BinanceClient
from .models import UniverseCoin, UniverseDiff

logger = logging.getLogger(__name__)


class UniverseService:
    def __init__(self, universe_client, binance_client: BinanceClient):
        self.universe_client = universe_client
        self.binance_client = binance_client
        self._last_universe: Optional[List[UniverseCoin]] = None

    def build_universe(
        self,
        top_n: int = 100,
        stablecoin_volatility_threshold_pct: float = 1.0,
    ) -> List[UniverseCoin]:
        """
        Fetches the Top N ranking, maps each to a Binance USDT spot pair,
        and drops any coin with no valid spot pair (logged, not fatal -
        the spec requires Spot markets only, so an unmapped coin is
        simply outside the tradeable universe, not an error).
        """
        coins = self.universe_client.get_top_n(top_n, stablecoin_volatility_threshold_pct)
        tradable = self.binance_client.get_tradable_spot_symbols()

        universe: List[UniverseCoin] = []
        for coin in coins:
            if coin.symbol in tradable:
                coin.binance_symbol = f"{coin.symbol}USDT"
                universe.append(coin)
            else:
                logger.info("Excluding %s: no Binance USDT spot pair", coin.symbol)

        self._last_universe = universe
        return universe

    def diff_against_previous(self, new_universe: List[UniverseCoin], previous_symbols: List[str]) -> UniverseDiff:
        """
        Compares a newly built universe against a previously stored set of
        symbols (e.g. loaded from the database at scan start) and reports
        what entered/left. Pure function - takes explicit previous state
        rather than only relying on in-memory _last_universe, since in
        production the previous universe should be persisted, not held in
        a single process's memory between scheduled runs.
        """
        new_symbols = {c.symbol for c in new_universe}
        prev_symbols = set(previous_symbols)
        return UniverseDiff(
            added=sorted(new_symbols - prev_symbols),
            removed=sorted(prev_symbols - new_symbols),
        )
