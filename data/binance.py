"""
Binance public client for OHLCV (candle) data.

Public market-data endpoints require no API key. Used for candles rather
than CoinMarketCap because CMC's historical OHLCV is restricted on the
free/Basic tier - this keeps the system fully functional without a paid
CMC plan. Swappable later per the spec's "change provider without
rebuilding" requirement: any replacement just needs to implement
get_klines() returning the same DataFrame shape the engine expects.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from .exceptions import DataProviderError, SymbolNotFoundError
from .http_client import get_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://api.binance.com/api/v3"
KLINES_ENDPOINT = f"{BASE_URL}/klines"
EXCHANGE_INFO_ENDPOINT = f"{BASE_URL}/exchangeInfo"

# Maps the engine's Timeframe values to Binance's kline interval strings.
INTERVAL_MAP = {
    "3D": "3d",
    "1D": "1d",
    "4H": "4h",
}

REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class BinanceClient:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._tradable_symbols_cache: Optional[set] = None

    def get_tradable_spot_symbols(self, quote_asset: str = "USDT", force_refresh: bool = False) -> set:
        """
        Returns the set of base symbols (e.g. {"BTC", "ETH", ...}) with an
        actively trading SPOT pair against quote_asset. Cached in-process
        since exchangeInfo is a large, slow-changing payload - callers
        doing a full universe pass shouldn't refetch it per-symbol.
        """
        if self._tradable_symbols_cache is not None and not force_refresh:
            return self._tradable_symbols_cache

        response = get_with_retry(EXCHANGE_INFO_ENDPOINT, timeout=self.timeout)
        payload = response.json()

        symbols = set()
        for s in payload.get("symbols", []):
            if (
                s.get("quoteAsset") == quote_asset
                and s.get("status") == "TRADING"
                and s.get("isSpotTradingAllowed", True)
            ):
                symbols.add(s["baseAsset"])

        self._tradable_symbols_cache = symbols
        return symbols

    def get_klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        quote_asset: str = "USDT",
    ) -> pd.DataFrame:
        """
        symbol: base asset, e.g. "BTC" (quote_asset is appended automatically)
        timeframe: one of "3D", "1D", "4H" (engine Timeframe values)
        limit: number of candles (Binance max per call is 1000)

        Returns a DataFrame with exactly the columns the engine expects:
        timestamp, open, high, low, close, volume - chronologically sorted.
        """
        if timeframe not in INTERVAL_MAP:
            raise DataProviderError(f"Unsupported timeframe '{timeframe}', expected one of {list(INTERVAL_MAP)}")

        binance_symbol = f"{symbol}{quote_asset}"
        params = {
            "symbol": binance_symbol,
            "interval": INTERVAL_MAP[timeframe],
            "limit": min(limit, 1000),
        }

        try:
            response = get_with_retry(KLINES_ENDPOINT, params=params, timeout=self.timeout)
        except DataProviderError:
            raise

        payload = response.json()

        if isinstance(payload, dict) and payload.get("code"):
            # Binance error responses are a dict with "code"/"msg" instead of a list
            if payload["code"] == -1121:
                raise SymbolNotFoundError(f"{binance_symbol} is not a valid Binance spot symbol")
            raise DataProviderError(f"Binance error for {binance_symbol}: {payload.get('msg')}")

        if not payload:
            raise DataProviderError(f"Binance returned no candle data for {binance_symbol} {timeframe}")

        rows = []
        for k in payload:
            rows.append({
                "timestamp": pd.to_datetime(k[0], unit="ms", utc=True),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })

        df = pd.DataFrame(rows, columns=REQUIRED_COLUMNS)
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def get_klines_for_universe(
        self,
        symbols: List[str],
        timeframe: str,
        limit: int = 500,
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetches candles for many symbols, skipping (and logging) any that
        fail rather than aborting the whole batch - one bad/delisted
        symbol shouldn't take down the entire scan.
        """
        results: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            try:
                results[symbol] = self.get_klines(symbol, timeframe, limit)
            except SymbolNotFoundError:
                logger.warning("Skipping %s: no Binance spot pair", symbol)
            except DataProviderError as e:
                logger.warning("Skipping %s (%s): %s", symbol, timeframe, e)
        return results
