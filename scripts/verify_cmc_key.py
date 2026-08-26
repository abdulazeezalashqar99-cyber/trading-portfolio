#!/usr/bin/env python3
"""
Live smoke test for a CoinMarketCap API key. Run this yourself once you
have a key (this sandbox has no network access, so it can't be run here):

    export CMC_API_KEY=your-key-here
    python scripts/verify_cmc_key.py

Confirms: the key authenticates, the listings endpoint returns data, tags
are present (needed for stablecoin filtering), and prints a short sample
so you can eyeball that the shape matches what coinmarketcap.py expects.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.coinmarketcap import CoinMarketCapClient
from data.exceptions import AuthenticationError, DataProviderError


def main():
    api_key = os.environ.get("CMC_API_KEY")
    if not api_key:
        print("ERROR: set the CMC_API_KEY environment variable first.")
        sys.exit(1)

    client = CoinMarketCapClient(api_key=api_key)

    print("Calling CoinMarketCap listings/latest (Top 10, for a quick check)...")
    try:
        coins = client.get_top_n(n=10)
    except AuthenticationError:
        print("FAILED: key was rejected (401/403). Double check it was copied correctly "
              "and that you're using the API key from pro.coinmarketcap.com, not a CMC.com login.")
        sys.exit(1)
    except DataProviderError as e:
        print(f"FAILED: {e}")
        sys.exit(1)

    if not coins:
        print("Connected, but got zero coins back - unexpected. Inspect the raw response.")
        sys.exit(1)

    print(f"\nSUCCESS - key is valid. Top {len(coins)} by market cap:\n")
    for c in coins:
        stable_tag = " [stablecoin]" if c.is_stablecoin else ""
        print(f"  #{c.market_cap_rank:>3}  {c.symbol:<8} "
              f"mcap=${c.market_cap_usd:,.0f}  24h={c.percent_change_24h:+.2f}%{stable_tag}")

    print("\nNext: run scripts/verify_binance.py to confirm candle data works too "
          "(no key needed for that one).")


if __name__ == "__main__":
    main()
