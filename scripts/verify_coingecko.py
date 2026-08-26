#!/usr/bin/env python3
"""
Live smoke test for CoinGecko's public market data (no API key needed).
Run this yourself (this sandbox has no network access, so it can't be
run here):

    python scripts/verify_coingecko.py

Confirms: the markets endpoint returns data, stablecoin tagging works,
and prints a short sample so you can eyeball that the shape matches what
coingecko.py expects.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.coingecko import CoinGeckoClient
from data.exceptions import DataProviderError, RateLimitError


def main():
    client = CoinGeckoClient()

    print("Calling CoinGecko /coins/markets (Top 10, for a quick check)...")
    try:
        coins = client.get_top_n(n=10)
    except RateLimitError:
        print("FAILED: rate limited. CoinGecko's free tier is ~10-30 calls/min - "
              "wait a minute and try again.")
        sys.exit(1)
    except DataProviderError as e:
        print(f"FAILED: {e}")
        sys.exit(1)

    if not coins:
        print("Connected, but got zero coins back - unexpected. Inspect the raw response.")
        sys.exit(1)

    print(f"\nSUCCESS - CoinGecko is reachable, no key needed. Top {len(coins)} by market cap:\n")
    for c in coins:
        stable_tag = " [stablecoin]" if c.is_stablecoin else ""
        print(f"  #{c.market_cap_rank:>3}  {c.symbol:<8} "
              f"mcap=${c.market_cap_usd:,.0f}  24h={c.percent_change_24h:+.2f}%{stable_tag}")

    print("\nNext: run scripts/verify_binance.py to confirm candle data works too.")


if __name__ == "__main__":
    main()
