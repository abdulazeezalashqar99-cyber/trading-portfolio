"""
CoinGecko client.

Drop-in alternative to coinmarketcap.py for the coin *universe* (Top N by
market cap + stablecoin tagging) - implements the same get_top_n()
signature and returns the same UniverseCoin model, so universe_service.py
doesn't need to know or care which provider is behind it. Chosen over CMC
per user preference: CoinGecko's market data endpoints need no API key
and no account at all on the free/public tier.

Public API docs: https://docs.coingecko.com/reference/coins-markets
"""

from __future__ import annotations

import logging
from typing import List

from .exceptions import DataProviderError
from .http_client import get_with_retry
from .models import UniverseCoin

logger = logging.getLogger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"
MARKETS_ENDPOINT = f"{BASE_URL}/coins/markets"


class CoinGeckoClient:
    def __init__(self, timeout: float = 10.0):
        # no API key on the free/public tier - nothing to store here
        self.timeout = timeout

    def get_top_n(
        self,
        n: int = 100,
        stablecoin_volatility_threshold_pct: float = 1.0,
    ) -> List[UniverseCoin]:
        """
        Fetches the Top N cryptocurrencies by market cap.

        Two calls: the main market-cap-ranked page, plus a second query
        scoped to CoinGecko's "stablecoins" category to know which of
        those N coins are stablecoins (the main /coins/markets response
        has no per-coin category field). Stablecoins are kept unless
        their 24h move is negligible, per spec - same rule as the CMC
        client, just sourced from a different field name.
        """
        coins_payload = self._fetch_markets_page(n)
        stablecoin_ids = self._fetch_stablecoin_ids()

        universe: List[UniverseCoin] = []
        for entry in coins_payload:
            try:
                coin = self._parse_entry(entry, stablecoin_ids)
            except (KeyError, TypeError) as e:
                logger.warning("Skipping malformed CoinGecko entry (%s): %s", e, entry.get("symbol", "?"))
                continue

            if coin.is_stablecoin and abs(coin.percent_change_24h) < stablecoin_volatility_threshold_pct:
                logger.info(
                    "Excluding %s: stablecoin with negligible 24h move (%.3f%%)",
                    coin.symbol, coin.percent_change_24h,
                )
                continue

            universe.append(coin)

        return universe

    def _fetch_markets_page(self, n: int) -> list:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": min(n, 250),  # CoinGecko's max page size
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
        response = get_with_retry(MARKETS_ENDPOINT, params=params, timeout=self.timeout)
        payload = response.json()
        if not isinstance(payload, list):
            raise DataProviderError(f"Unexpected CoinGecko response shape: expected a list, got {type(payload)}")
        return payload

    def _fetch_stablecoin_ids(self) -> set:
        params = {
            "vs_currency": "usd",
            "category": "stablecoins",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": 1,
            "sparkline": "false",
        }
        response = get_with_retry(MARKETS_ENDPOINT, params=params, timeout=self.timeout)
        payload = response.json()
        if not isinstance(payload, list):
            raise DataProviderError(f"Unexpected CoinGecko stablecoins response shape: expected a list, got {type(payload)}")
        return {entry["id"] for entry in payload if "id" in entry}

    @staticmethod
    def _parse_entry(entry: dict, stablecoin_ids: set) -> UniverseCoin:
        return UniverseCoin(
            symbol=entry["symbol"].upper(),
            name=entry["name"],
            provider_id=entry["id"],
            market_cap_rank=entry["market_cap_rank"] or 0,
            market_cap_usd=float(entry["market_cap"] or 0.0),
            volume_24h_usd=float(entry["total_volume"] or 0.0),
            percent_change_24h=float(entry.get("price_change_percentage_24h") or 0.0),
            is_stablecoin=entry["id"] in stablecoin_ids,
        )
