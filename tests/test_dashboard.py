"""
Tests for dashboard/data_builder.py. Uses InMemoryZoneStore so this
exercises the actual ZoneStore interface, not a hand-rolled fake shaped
to fit the test.

Run with: pytest tests/test_dashboard.py -v
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.data_builder import ScanStats, build_dashboard_data
from db.memory_store import InMemoryZoneStore
from db.models import TradedStatus, ZoneRecord


def _record(**overrides) -> ZoneRecord:
    defaults = dict(
        id=None, symbol="BTC/USDT", name=None, timeframe="1D",
        zone_low=100.0, zone_high=105.0, creation_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        score=90.0, grade="A", freshness="fresh", test_count=0,
        departure_pct=20.0, departure_atr_multiple=4.0, volume_expansion_ratio=2.0,
        break_of_structure=True, liquidity_sweep=False, fibonacci_confluence=None,
        current_price=110.0, zone_entered=False, status="WAITING", btc_condition="Neutral",
        confluent_timeframes=[], alert_sent=False, alert_time=None, result=None,
        traded_skipped=TradedStatus.UNDECIDED, updated_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ZoneRecord(**defaults)


class TestOpportunityGrouping:
    def test_a_plus_and_a_grades_separated_into_own_sections(self):
        store = InMemoryZoneStore()
        store.insert_zone(_record(symbol="X/USDT", zone_low=1, zone_high=2, grade="A+", score=95))
        store.insert_zone(_record(symbol="Y/USDT", zone_low=3, zone_high=4, grade="A", score=85))
        store.insert_zone(_record(symbol="Z/USDT", zone_low=5, zone_high=6, grade="B", score=75))

        data = build_dashboard_data(store)
        assert len(data["a_plus_opportunities"]) == 1
        assert data["a_plus_opportunities"][0]["symbol"] == "X/USDT"
        assert len(data["a_opportunities"]) == 1
        assert data["a_opportunities"][0]["symbol"] == "Y/USDT"
        # B grade shouldn't appear in either A-grade section
        all_a_symbols = {r["symbol"] for r in data["a_plus_opportunities"] + data["a_opportunities"]}
        assert "Z/USDT" not in all_a_symbols

    def test_opportunities_sorted_by_score_descending(self):
        store = InMemoryZoneStore()
        store.insert_zone(_record(symbol="LOW/USDT", zone_low=1, zone_high=2, grade="A+", score=91))
        store.insert_zone(_record(symbol="HIGH/USDT", zone_low=3, zone_high=4, grade="A+", score=99))

        data = build_dashboard_data(store)
        scores = [r["score"] for r in data["a_plus_opportunities"]]
        assert scores == sorted(scores, reverse=True)

    def test_invalidated_zones_excluded_from_opportunities(self):
        store = InMemoryZoneStore()
        store.insert_zone(_record(grade="A+", status="INVALIDATED"))
        data = build_dashboard_data(store)
        assert data["a_plus_opportunities"] == []


class TestWatchlist:
    def test_watchlist_includes_waiting_and_entered(self):
        store = InMemoryZoneStore()
        store.insert_zone(_record(symbol="A/USDT", zone_low=1, zone_high=2, status="WAITING"))
        store.insert_zone(_record(symbol="B/USDT", zone_low=3, zone_high=4, status="ENTERED"))
        store.insert_zone(_record(symbol="C/USDT", zone_low=5, zone_high=6, status="INVALIDATED"))

        data = build_dashboard_data(store)
        symbols = {r["symbol"] for r in data["watchlist"]}
        assert symbols == {"A/USDT", "B/USDT"}


class TestTriggeredAlerts:
    def test_only_alerted_zones_appear(self):
        store = InMemoryZoneStore()
        alerted = store.insert_zone(_record(symbol="ALERTED/USDT", zone_low=1, zone_high=2, alert_sent=True,
                                             alert_time=datetime.now(timezone.utc)))
        store.insert_zone(_record(symbol="QUIET/USDT", zone_low=3, zone_high=4, alert_sent=False))

        data = build_dashboard_data(store)
        symbols = {r["symbol"] for r in data["triggered_alerts"]}
        assert symbols == {"ALERTED/USDT"}

    def test_most_recent_alert_first(self):
        store = InMemoryZoneStore()
        now = datetime.now(timezone.utc)
        store.insert_zone(_record(symbol="OLD/USDT", zone_low=1, zone_high=2, alert_sent=True, alert_time=now - timedelta(hours=2)))
        store.insert_zone(_record(symbol="NEW/USDT", zone_low=3, zone_high=4, alert_sent=True, alert_time=now))

        data = build_dashboard_data(store)
        assert data["triggered_alerts"][0]["symbol"] == "NEW/USDT"


class TestDistanceCalculation:
    def test_distance_positive_when_price_above_zone(self):
        store = InMemoryZoneStore()
        store.insert_zone(_record(zone_low=100, zone_high=105, current_price=115.5))
        data = build_dashboard_data(store)
        # (115.5 - 105) / 105 * 100 = 10.0
        assert data["watchlist"][0]["distance_pct"] == 10.0

    def test_distance_zero_when_price_inside_or_below_zone(self):
        store = InMemoryZoneStore()
        store.insert_zone(_record(symbol="A/USDT", zone_low=100, zone_high=105, current_price=102, status="ENTERED"))
        store.insert_zone(_record(symbol="B/USDT", zone_low=200, zone_high=205, current_price=190))

        data = build_dashboard_data(store)
        for row in data["watchlist"]:
            assert row["distance_pct"] == 0.0


class TestSystemStatus:
    def test_reflects_scan_stats_when_provided(self):
        store = InMemoryZoneStore()
        store.insert_zone(_record(status="WAITING"))
        stats = ScanStats(
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
            universe_size=100, coins_scanned=97, alerts_sent_this_run=2,
        )
        data = build_dashboard_data(store, stats)

        assert data["system_status"]["coins_scanned"] == 97
        assert data["system_status"]["universe_size"] == 100
        assert data["system_status"]["active_zones"] == 1
        assert data["system_status"]["alerts_sent_last_run"] == 2
        assert data["system_status"]["health"] == "OK"

    def test_api_errors_mark_system_degraded(self):
        store = InMemoryZoneStore()
        stats = ScanStats(
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
            universe_size=100, coins_scanned=80,
            api_errors={"binance": "timeout on 20 symbols"},
        )
        data = build_dashboard_data(store, stats)
        assert data["system_status"]["health"] == "DEGRADED"
        assert data["system_status"]["api_status"]["binance"] == "error"
        assert data["system_status"]["api_status"]["coingecko"] == "ok"

    def test_works_without_scan_stats(self):
        # generate_dashboard could theoretically be called standalone
        # (e.g. manually inspecting current state) without a fresh scan
        store = InMemoryZoneStore()
        data = build_dashboard_data(store, scan_stats=None)
        assert data["system_status"]["coins_scanned"] is None
        assert data["system_status"]["health"] == "OK"


if __name__ == "__main__":
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ModuleNotFoundError:
        print("pytest not installed; run via a plain test runner instead.")
