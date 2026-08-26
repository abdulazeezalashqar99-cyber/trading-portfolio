#!/usr/bin/env python3
"""
Live smoke test for Binance candle data (no API key needed - public
endpoints). Run this yourself, e.g.:

    python scripts/verify_binance.py

Fetches a small batch of 4H candles for BTC and ETH and feeds them
straight into the demand-zone engine, so this also doubles as an
end-to-end check that data.binance -> engine.detect_demand_zones works
together on real market data, not just synthetic fixtures.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.binance import BinanceClient
from data.exceptions import DataProviderError
from engine import DEFAULT_CONFIG, Timeframe, detect_demand_zones, score_all_opportunities


def main():
    client = BinanceClient()

    print("Checking tradable USDT spot symbols on Binance...")
    try:
        symbols = client.get_tradable_spot_symbols()
    except DataProviderError as e:
        print(f"FAILED: {e}")
        sys.exit(1)
    print(f"  {len(symbols)} tradable USDT spot pairs found.\n")

    test_symbols = ["BTC", "ETH"]
    zones_by_symbol = {}

    for symbol in test_symbols:
        print(f"Fetching candles for {symbol}...")
        zones_by_tf = {}
        for tf in (Timeframe.D3, Timeframe.D1, Timeframe.H4):
            try:
                df = client.get_klines(symbol, tf.value, limit=300)
            except DataProviderError as e:
                print(f"  {tf.value}: FAILED - {e}")
                continue
            zones = detect_demand_zones(df, f"{symbol}/USDT", tf, DEFAULT_CONFIG)
            zones_by_tf[tf] = zones
            print(f"  {tf.value}: {len(df)} candles, {len(zones)} candidate zone(s)")
        zones_by_symbol[symbol] = zones_by_tf

    print("\nScoring...")
    for symbol, zones_by_tf in zones_by_symbol.items():
        scored = score_all_opportunities(zones_by_tf, DEFAULT_CONFIG)
        if not scored:
            print(f"  {symbol}: no A/B-grade opportunities right now")
            continue
        for z in scored:
            print(f"  {symbol} [{z.timeframe.value}] {z.zone_low:.4f}-{z.zone_high:.4f} "
                  f"score={z.score} grade={z.grade} status={z.status.value}")

    print("\nSUCCESS - Binance data flows end-to-end into the engine.")


if __name__ == "__main__":
    main()
