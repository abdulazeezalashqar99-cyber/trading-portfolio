#!/usr/bin/env python3
"""
Main entrypoint for the GitHub-Actions-scheduled scan (spec section 12,
adapted to a no-VPS deployment per your choice: scheduled runs instead
of a continuously running process).

Each run:
  1. Loads persistent state from db/scanner_state.db (SQLite, committed
     back into the repo by the workflow after this script finishes)
  2. Builds the Top N universe (CoinGecko + Binance)
  3. Classifies the current BTC market condition
  4. Scans every symbol across 3D/1D/4H, detects + scores demand zones
  5. Reconciles results into the store (updates existing zones, inserts
     new ones, never touches alert state on a match)
  6. Sends a Telegram alert for every zone that just became eligible
     (ENTERED, A/A+ grade, not yet alerted) - and only marks it alerted
     after a confirmed successful send
  7. Regenerates the static dashboard (docs/) from current store state

Required environment variables:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Exits non-zero on a hard failure (e.g. can't build the universe at all)
so the GitHub Actions run shows as failed and you'll notice; a single
symbol failing mid-scan is logged and skipped, not fatal.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.binance import BinanceClient
from data.coingecko import CoinGeckoClient
from data.exceptions import DataProviderError
from data.universe_service import UniverseService
from dashboard import ScanStats, generate_dashboard
from db.service import reconcile_scan_results, update_universe, zones_needing_alert, confirm_alert_sent
from db.sqlite_store import SQLiteZoneStore
from engine import DEFAULT_CONFIG, Timeframe, classify_btc_condition, detect_demand_zones, score_all_opportunities
from notifications.notifier import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_scheduled_scan")

DB_PATH = os.environ.get("SCANNER_DB_PATH", str(Path(__file__).resolve().parents[1] / "db" / "scanner_state.db"))
DASHBOARD_DIR = os.environ.get("SCANNER_DASHBOARD_DIR", str(Path(__file__).resolve().parents[1] / "docs"))
TOP_N = int(os.environ.get("SCANNER_TOP_N", "100"))
CANDLE_LIMIT = int(os.environ.get("SCANNER_CANDLE_LIMIT", "300"))


def main() -> int:
    run_started_at = datetime.now(timezone.utc)
    api_errors: dict[str, str] = {}

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set")
        return 1

    notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)
    store = SQLiteZoneStore(DB_PATH)
    binance = BinanceClient()

    try:
        logger.info("Building universe (Top %d)...", TOP_N)
        universe_service = UniverseService(CoinGeckoClient(), binance)
        universe = universe_service.build_universe(top_n=TOP_N)
        logger.info("%d coins have valid Binance spot pairs", len(universe))
    except DataProviderError as e:
        logger.error("Failed to build universe, aborting run: %s", e)
        api_errors["coingecko"] = str(e)
        # still publish a dashboard reflecting the failure, using whatever
        # is already in the store, so a broken run is visible on the site
        # rather than just silently not updating
        generate_dashboard(store, DASHBOARD_DIR, ScanStats(
            started_at=run_started_at, finished_at=datetime.now(timezone.utc),
            universe_size=0, coins_scanned=0, api_errors=api_errors,
        ))
        store.close()
        return 1

    events = update_universe(store, [c.symbol for c in universe])
    for e in events:
        logger.info("Universe change: %s %s", e.symbol, e.event_type)

    logger.info("Checking BTC market condition...")
    try:
        btc_df = binance.get_klines("BTC", "1D", limit=CANDLE_LIMIT)
        btc_condition = classify_btc_condition(btc_df)
    except DataProviderError as e:
        logger.warning("Could not fetch BTC condition, defaulting to Neutral: %s", e)
        api_errors["binance_btc"] = str(e)
        btc_condition = "Neutral"
    logger.info("BTC condition: %s", btc_condition)

    all_scored = []
    coins_scanned = 0
    for coin in universe:
        zones_by_tf = {}
        symbol_had_error = False
        for tf in (Timeframe.D3, Timeframe.D1, Timeframe.H4):
            try:
                df = binance.get_klines(coin.symbol, tf.value, limit=CANDLE_LIMIT)
            except DataProviderError as e:
                logger.warning("Skipping %s %s: %s", coin.symbol, tf.value, e)
                symbol_had_error = True
                continue
            zones_by_tf[tf] = detect_demand_zones(df, f"{coin.symbol}/USDT", tf, DEFAULT_CONFIG)

        if not symbol_had_error:
            coins_scanned += 1
        all_scored.extend(score_all_opportunities(zones_by_tf, DEFAULT_CONFIG))

    logger.info("%d scored opportunities found this scan", len(all_scored))
    records = reconcile_scan_results(store, all_scored, btc_condition=btc_condition)
    logger.info("%d zone records reconciled", len(records))

    pending = zones_needing_alert(store)
    logger.info("%d zone(s) pending alert", len(pending))

    sent, failed = 0, 0
    for zone in pending:
        if notifier.send_alert(zone):
            confirm_alert_sent(store, zone.id)
            sent += 1
            logger.info("Alert sent: %s [%s]", zone.symbol, zone.timeframe)
        else:
            failed += 1
            api_errors["telegram"] = "one or more alerts failed to send"
            logger.error("Alert FAILED to send (will retry next run): %s [%s]", zone.symbol, zone.timeframe)

    logger.info("Run complete: %d alerts sent, %d failed", sent, failed)

    generate_dashboard(store, DASHBOARD_DIR, ScanStats(
        started_at=run_started_at, finished_at=datetime.now(timezone.utc),
        universe_size=len(universe), coins_scanned=coins_scanned,
        api_errors=api_errors, alerts_sent_this_run=sent,
    ))
    logger.info("Dashboard regenerated at %s", DASHBOARD_DIR)

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
