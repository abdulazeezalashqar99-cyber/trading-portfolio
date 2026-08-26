"""
CoinMarketCap client.

Responsible only for the coin *universe* (Top 100 by market cap, spot-
relevant metadata, stablecoin tagging) - not price candles. CMC's candle
data is restricted on the free/Basic tier, so OHLCV comes from
binance_client.py instead. Keeping these as separate clients means either
one can be swapped for an alternate provider later without touching the
other (per the spec's requirement to allow changing the market-data
provider without rebuilding the system).
"""

from __future__ import annotations

import logging
from typing import List

from .exceptions import DataProviderError
from .http_client import get_with_retry
from .models import UniverseCoin

logger = logging.getLogger(__name__)

BASE_URL = "https://pro-api.coinmarketcap.com/v1"
LISTINGS_ENDPOINT = f"{BASE_URL}/cryptocurrency/listings/latest"

# CMC tags that mark a coin as a stablecoin in the listings/latest response
# when requested with aux=tags.
STABLECOIN_TAGS = {"stablecoin", "asset-backed-stablecoin", "fiat-stablecoin"}


class CoinMarketCapClient:
    def __init__(self, api_key: str, timeout: float = 10.0):
        if not api_key:
            raise DataProviderError("CoinMarketCap API key is required but was empty")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "X-CMC_PRO_API_KEY": self.api_key,
            "Accept": "application/json",
        }

    def get_top_n(
        self,
        n: int = 100,
        stablecoin_volatility_threshold_pct: float = 1.0,
    ) -> List[UniverseCoin]:
        """
        Fetches the Top N cryptocurrencies by market cap.

        Stablecoins are kept in the universe (per spec) unless their 24h
        price change is smaller than stablecoin_volatility_threshold_pct
        - i.e. they're trading essentially exactly at their peg and offer
        no real opportunity, in which case they're dropped.
        """
        params = {
            "start": 1,
            "limit": n,
            "convert": "USD",
            "aux": "tags",
            "sort": "market_cap",
        }
        response = get_with_retry(LISTINGS_ENDPOINT, params=params, headers=self._headers(), timeout=self.timeout)
        payload = response.json()

        if "data" not in payload:
            raise DataProviderError(f"Unexpected CoinMarketCap response shape: missing 'data' key")

        coins: List[UniverseCoin] = []
        for entry in payload["data"]:
            try:
                coin = self._parse_entry(entry)
            except (KeyError, TypeError) as e:
                logger.warning("Skipping malformed CMC entry (%s): %s", e, entry.get("symbol", "?"))
                continue

            if coin.is_stablecoin and abs(coin.percent_change_24h) < stablecoin_volatility_threshold_pct:
                logger.info(
                    "Excluding %s: stablecoin with negligible 24h move (%.3f%%)",
                    coin.symbol, coin.percent_change_24h,
                )
                continue

            coins.append(coin)

        return coins

    @staticmethod
    def _parse_entry(entry: dict) -> UniverseCoin:
        quote = entry["quote"]["USD"]
        tags = set(entry.get("tags") or [])
        return UniverseCoin(
            symbol=entry["symbol"],
            name=entry["name"],
            provider_id=str(entry["id"]),
            market_cap_rank=entry["cmc_rank"],
            market_cap_usd=float(quote["market_cap"] or 0.0),
            volume_24h_usd=float(quote["volume_24h"] or 0.0),
            percent_change_24h=float(quote["percent_change_24h"] or 0.0),
            is_stablecoin=bool(tags & STABLECOIN_TAGS),
        )
