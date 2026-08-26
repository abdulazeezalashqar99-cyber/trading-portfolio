#!/usr/bin/env python3
"""
Example single scan pass, wiring together everything built so far:
CoinGecko/Binance data -> engine detection+scoring -> db reconciliation.

This is illustrative, not the final scheduled scanner (that's Phase 6,
once Docker/deployment is in place) - but it's a real, runnable pipeline
you can point at a small symbol list right now. Uses InMemoryZoneStore
by default so it runs with zero setup; swap in PostgresZoneStore once
you have a database (see the commented-out line below).

Run:
    python scripts/run_scan_example.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.binance import BinanceClient
from data.coingecko import CoinGeckoClient
from data.universe_service import UniverseService
from db.memory_store import InMemoryZoneStore
from db.service import reconcile_scan_results, update_universe, zones_needing_alert
from engine import DEFAULT_CONFIG, Timeframe, detect_demand_zones, score_all_opportunities

# from db.postgres_store import PostgresZoneStore
# store = PostgresZoneStore(dsn="postgresql://user:pass@localhost:5432/scanner")

TOP_N_FOR_THIS_EXAMPLE = 10  # keep it small for a quick demo run; use 100 in production


def main():
    store = InMemoryZoneStore()

    print(f"Building universe (Top {TOP_N_FOR_THIS_EXAMPLE})...")
    universe_service = UniverseService(CoinGeckoClient(), BinanceClient())
    universe = universe_service.build_universe(top_n=TOP_N_FOR_THIS_EXAMPLE)
    print(f"  {len(universe)} coins have valid Binance spot pairs.\n")

    events = update_universe(store, [c.symbol for c in universe])
    if events:
        print(f"Universe changed: {[e.symbol + ':' + e.event_type for e in events]}\n")

    binance = BinanceClient()
    all_scored = []

    for coin in universe:
        zones_by_tf = {}
        for tf in (Timeframe.D3, Timeframe.D1, Timeframe.H4):
            try:
                df = binance.get_klines(coin.symbol, tf.value, limit=300)
            except Exception as e:
                print(f"  {coin.symbol} {tf.value}: skipped ({e})")
                continue
            zones_by_tf[tf] = detect_demand_zones(df, f"{coin.symbol}/USDT", tf, DEFAULT_CONFIG)

        scored = score_all_opportunities(zones_by_tf, DEFAULT_CONFIG)
        all_scored.extend(scored)

    print(f"\n{len(all_scored)} scored opportunities found (A/B grade or better).")

    records = reconcile_scan_results(store, all_scored)
    print(f"{len(records)} zone records reconciled into the watchlist.\n")

    pending = zones_needing_alert(store)
    if pending:
        print(f"{len(pending)} zone(s) ready for a Telegram alert (Phase 4):")
        for z in pending:
            print(f"  {z.symbol} [{z.timeframe}] {z.zone_low:.4f}-{z.zone_high:.4f} "
                  f"score={z.score} grade={z.grade}")
    else:
        print("No A/A+ zones currently entered - nothing to alert on right now.")


if __name__ == "__main__":
    main()
