"""
Tests for the watchlist reconciliation service (db/service.py), run
against InMemoryZoneStore. This is where the actual business logic
lives and where bugs would hide - alert deduplication, zone matching
across rescans, and universe diffing - so it's tested thoroughly here
even though the Postgres adapter itself isn't (see postgres_store.py's
docstring for why).

Run with: pytest tests/test_db.py -v
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.memory_store import InMemoryZoneStore
from db.models import TradedStatus
from db.service import (
    confirm_alert_sent,
    reconcile_scan_results,
    reconcile_zone,
    update_universe,
    zones_needing_alert,
)
from engine.models import DemandZone, Freshness, Timeframe, ZoneEvidence, ZoneStatus


def _make_engine_zone(
    symbol="BTC/USDT", timeframe=Timeframe.D1, zone_low=100.0, zone_high=105.0,
    score=92.0, grade="A+", status=ZoneStatus.WAITING, current_price=110.0,
    fresh=Freshness.FRESH, formed_at=None,
) -> DemandZone:
    zone = DemandZone(
        symbol=symbol, timeframe=timeframe, zone_low=zone_low, zone_high=zone_high,
        formed_at=formed_at or datetime(2024, 1, 1, tzinfo=timezone.utc),
        evidence=ZoneEvidence(
            fresh=fresh, test_count=0, departure_pct=25.0, departure_atr_multiple=5.0,
            volume_expansion_ratio=3.0, break_of_structure=True, liquidity_sweep=True,
            fib_confluence=0.5,
        ),
        current_price=current_price, status=status,
    )
    zone.score = score
    zone.grade = grade
    return zone


class TestReconcileZone:
    def test_new_zone_creates_a_record(self):
        store = InMemoryZoneStore()
        zone = _make_engine_zone()
        record = reconcile_zone(store, zone)

        assert record.id is not None
        assert record.symbol == "BTC/USDT"
        assert record.zone_low == 100.0
        assert record.alert_sent is False
        assert record.traded_skipped == TradedStatus.UNDECIDED

    def test_rescanning_the_same_zone_updates_not_duplicates(self):
        store = InMemoryZoneStore()
        zone1 = _make_engine_zone(score=85.0, current_price=110.0)
        record1 = reconcile_zone(store, zone1)

        # rescan: same price area, price has moved, score changed slightly
        zone2 = _make_engine_zone(score=88.0, current_price=112.0)
        record2 = reconcile_zone(store, zone2)

        assert record1.id == record2.id, "should update the same row, not create a second one"
        assert len(store.list_watchlist(statuses=("WAITING", "ENTERED", "INVALIDATED", "STALE"))) == 1
        assert record2.score == 88.0
        assert record2.current_price == 112.0

    def test_non_overlapping_zone_creates_a_separate_record(self):
        store = InMemoryZoneStore()
        reconcile_zone(store, _make_engine_zone(zone_low=100.0, zone_high=105.0))
        reconcile_zone(store, _make_engine_zone(zone_low=200.0, zone_high=205.0))  # different price area

        watchlist = store.list_watchlist(statuses=("WAITING", "ENTERED"))
        assert len(watchlist) == 2

    def test_different_timeframe_creates_a_separate_record(self):
        store = InMemoryZoneStore()
        reconcile_zone(store, _make_engine_zone(timeframe=Timeframe.D1))
        reconcile_zone(store, _make_engine_zone(timeframe=Timeframe.H4))  # same price, different tf

        watchlist = store.list_watchlist(statuses=("WAITING", "ENTERED"))
        assert len(watchlist) == 2

    def test_update_preserves_creation_date_and_id(self):
        store = InMemoryZoneStore()
        formed = datetime(2024, 1, 1, tzinfo=timezone.utc)
        record1 = reconcile_zone(store, _make_engine_zone(formed_at=formed))

        later_formed = datetime(2024, 1, 5, tzinfo=timezone.utc)  # shouldn't matter on update
        record2 = reconcile_zone(store, _make_engine_zone(formed_at=later_formed, score=70.0))

        assert record2.id == record1.id
        assert record2.creation_date == formed, "creation_date must not change on update"

    def test_update_preserves_alert_state_and_backtest_annotations(self):
        store = InMemoryZoneStore()
        record = reconcile_zone(store, _make_engine_zone(status=ZoneStatus.ENTERED, grade="A+"))
        confirm_alert_sent(store, record.id)
        store.get_zone(record.id).result = "big winner"
        store.get_zone(record.id).traded_skipped = TradedStatus.TRADED

        # rescan the same zone again - alert state and annotations must survive
        updated = reconcile_zone(store, _make_engine_zone(status=ZoneStatus.ENTERED, grade="A+", score=91))
        assert updated.alert_sent is True
        assert updated.result == "big winner"
        assert updated.traded_skipped == TradedStatus.TRADED

    def test_invalidated_zone_is_excluded_from_matching(self):
        store = InMemoryZoneStore()
        record1 = reconcile_zone(store, _make_engine_zone(fresh=Freshness.FRESH))
        record1.status = "INVALIDATED"  # simulate the zone breaking down
        store.update_zone(record1)

        # a new zone re-forms in the same price area later - should be a
        # new record, not a resurrection of the invalidated one
        record2 = reconcile_zone(store, _make_engine_zone(fresh=Freshness.FRESH))
        assert record2.id != record1.id


class TestReconcileScanResults:
    def test_reconciles_a_batch_of_zones_in_one_call(self):
        store = InMemoryZoneStore()
        zones = [
            _make_engine_zone(symbol="BTC/USDT", zone_low=100, zone_high=105),
            _make_engine_zone(symbol="ETH/USDT", zone_low=50, zone_high=55),
        ]
        records = reconcile_scan_results(store, zones)
        assert len(records) == 2
        assert {r.symbol for r in records} == {"BTC/USDT", "ETH/USDT"}


class TestAlertDeduplication:
    def test_entered_ab_grade_zone_is_pending_alert(self):
        store = InMemoryZoneStore()
        reconcile_zone(store, _make_engine_zone(status=ZoneStatus.ENTERED, grade="A+"))
        pending = zones_needing_alert(store)
        assert len(pending) == 1

    def test_waiting_zone_is_not_pending_alert(self):
        store = InMemoryZoneStore()
        reconcile_zone(store, _make_engine_zone(status=ZoneStatus.WAITING, grade="A+"))
        assert zones_needing_alert(store) == []

    def test_b_grade_entered_zone_is_not_pending_alert(self):
        # spec: only A+ and A should normally alert
        store = InMemoryZoneStore()
        reconcile_zone(store, _make_engine_zone(status=ZoneStatus.ENTERED, grade="B"))
        assert zones_needing_alert(store) == []

    def test_alert_does_not_refire_after_being_sent(self):
        store = InMemoryZoneStore()
        record = reconcile_zone(store, _make_engine_zone(status=ZoneStatus.ENTERED, grade="A+"))
        assert len(zones_needing_alert(store)) == 1

        confirm_alert_sent(store, record.id)
        assert zones_needing_alert(store) == [], "must not appear again after being marked sent"

        # rescanning the same still-entered zone repeatedly must not
        # resurrect it into the pending-alert list
        reconcile_zone(store, _make_engine_zone(status=ZoneStatus.ENTERED, grade="A+", score=93))
        reconcile_zone(store, _make_engine_zone(status=ZoneStatus.ENTERED, grade="A+", score=94))
        assert zones_needing_alert(store) == []

    def test_alert_records_sent_time(self):
        store = InMemoryZoneStore()
        record = reconcile_zone(store, _make_engine_zone(status=ZoneStatus.ENTERED, grade="A+"))
        before = datetime.now(timezone.utc)
        confirm_alert_sent(store, record.id)
        after = datetime.now(timezone.utc)

        updated = store.get_zone(record.id)
        assert updated.alert_time is not None
        assert before <= updated.alert_time <= after

    def test_multiple_zones_only_unsent_ones_are_pending(self):
        store = InMemoryZoneStore()
        r1 = reconcile_zone(store, _make_engine_zone(symbol="BTC/USDT", zone_low=100, zone_high=105,
                                                        status=ZoneStatus.ENTERED, grade="A+"))
        reconcile_zone(store, _make_engine_zone(symbol="ETH/USDT", zone_low=50, zone_high=55,
                                                  status=ZoneStatus.ENTERED, grade="A"))
        confirm_alert_sent(store, r1.id)

        pending = zones_needing_alert(store)
        assert len(pending) == 1
        assert pending[0].symbol == "ETH/USDT"


class TestUniverseTracking:
    def test_first_snapshot_has_no_diff_events(self):
        store = InMemoryZoneStore()
        events = update_universe(store, ["BTC", "ETH", "SOL"])
        assert events == []  # nothing to diff against yet
        assert store.get_latest_universe_symbols() == ["BTC", "ETH", "SOL"]

    def test_detects_entries_and_exits_between_scans(self):
        store = InMemoryZoneStore()
        update_universe(store, ["BTC", "ETH", "SOL"])
        events = update_universe(store, ["BTC", "ETH", "AVAX"])  # SOL out, AVAX in

        entered = {e.symbol for e in events if e.event_type == "ENTERED"}
        exited = {e.symbol for e in events if e.event_type == "EXITED"}
        assert entered == {"AVAX"}
        assert exited == {"SOL"}

    def test_no_change_produces_no_events(self):
        store = InMemoryZoneStore()
        update_universe(store, ["BTC", "ETH"])
        events = update_universe(store, ["BTC", "ETH"])
        assert events == []

    def test_latest_snapshot_always_reflects_most_recent_call(self):
        store = InMemoryZoneStore()
        update_universe(store, ["BTC", "ETH"])
        update_universe(store, ["BTC", "ETH", "SOL"])
        assert store.get_latest_universe_symbols() == ["BTC", "ETH", "SOL"]


if __name__ == "__main__":
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ModuleNotFoundError:
        print("pytest not installed; run via a plain test runner instead.")
