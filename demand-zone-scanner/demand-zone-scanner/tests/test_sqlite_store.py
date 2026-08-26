"""
Tests for SQLiteZoneStore against a REAL SQLite database file (tempfile,
cleaned up after each test) - not mocked. Since sqlite3 is Python stdlib,
this is the one storage backend in this project that gets genuine
end-to-end database testing rather than testing against an in-memory
fake standing in for it.

Also re-runs a subset of the reconciliation-logic tests from test_db.py
against this real backend, to confirm service.py's behavior is actually
identical across backends, not just identical in theory because they
share an interface.

Run with: pytest tests/test_sqlite_store.py -v
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.models import TradedStatus, UniverseEvent, UniverseSnapshot, ZoneRecord
from db.service import confirm_alert_sent, reconcile_zone, update_universe, zones_needing_alert
from db.sqlite_store import SQLiteZoneStore
from engine.models import DemandZone, Freshness, Timeframe, ZoneEvidence, ZoneStatus


def _temp_store() -> SQLiteZoneStore:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # SQLiteZoneStore creates it fresh with the schema
    return SQLiteZoneStore(path)


def _make_record(**overrides) -> ZoneRecord:
    defaults = dict(
        id=None, symbol="BTC/USDT", name=None, timeframe="1D",
        zone_low=100.0, zone_high=105.0, creation_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        score=88.0, grade="A", freshness="fresh", test_count=0,
        departure_pct=20.0, departure_atr_multiple=4.0, volume_expansion_ratio=2.5,
        break_of_structure=True, liquidity_sweep=False, fibonacci_confluence=0.5,
        current_price=108.0, zone_entered=False, status="WAITING", btc_condition="Strong",
        alert_sent=False, alert_time=None, result=None, traded_skipped=TradedStatus.UNDECIDED,
        updated_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ZoneRecord(**defaults)


def _make_engine_zone(**overrides) -> DemandZone:
    defaults = dict(
        symbol="BTC/USDT", timeframe=Timeframe.D1, zone_low=100.0, zone_high=105.0,
        formed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        evidence=ZoneEvidence(fresh=Freshness.FRESH, test_count=0, departure_pct=25.0,
                               departure_atr_multiple=5.0, volume_expansion_ratio=3.0,
                               break_of_structure=True, liquidity_sweep=True, fib_confluence=0.5),
        current_price=110.0, status=ZoneStatus.WAITING,
    )
    defaults.update(overrides)
    zone = DemandZone(**defaults)
    zone.score = 92.0
    zone.grade = "A+"
    return zone


class TestSQLiteBasicCRUD:
    def test_insert_and_retrieve_zone(self):
        store = _temp_store()
        record = _make_record()
        inserted = store.insert_zone(record)
        assert inserted.id is not None

        fetched = store.get_zone(inserted.id)
        assert fetched.symbol == "BTC/USDT"
        assert fetched.zone_low == 100.0
        assert fetched.break_of_structure is True
        assert fetched.liquidity_sweep is False
        store.close()

    def test_types_round_trip_correctly(self):
        # booleans, None, floats, and enums all need to survive the
        # TEXT/INTEGER-based SQLite storage round trip intact
        store = _temp_store()
        record = _make_record(fibonacci_confluence=None, result=None, alert_time=None)
        inserted = store.insert_zone(record)
        fetched = store.get_zone(inserted.id)

        assert fetched.fibonacci_confluence is None
        assert fetched.result is None
        assert fetched.alert_time is None
        assert fetched.traded_skipped == TradedStatus.UNDECIDED
        assert isinstance(fetched.creation_date, datetime)
        store.close()

    def test_update_zone_persists_changes(self):
        store = _temp_store()
        record = store.insert_zone(_make_record(score=70.0))
        record.score = 95.0
        record.status = "ENTERED"
        store.update_zone(record)

        fetched = store.get_zone(record.id)
        assert fetched.score == 95.0
        assert fetched.status == "ENTERED"
        store.close()

    def test_data_survives_reconnect(self):
        # the whole point of using SQLite here is that state persists
        # across process runs (each GitHub Actions run is a fresh process)
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)

        store1 = SQLiteZoneStore(path)
        inserted = store1.insert_zone(_make_record(symbol="ETH/USDT"))
        store1.close()

        store2 = SQLiteZoneStore(path)  # simulates a new process/run
        fetched = store2.get_zone(inserted.id)
        assert fetched is not None
        assert fetched.symbol == "ETH/USDT"
        store2.close()
        os.unlink(path)


class TestSQLiteQueries:
    def test_find_overlapping_zone(self):
        store = _temp_store()
        store.insert_zone(_make_record(symbol="BTC/USDT", timeframe="1D", zone_low=100, zone_high=105))

        found = store.find_overlapping_zone("BTC/USDT", "1D", 102, 108)
        assert found is not None

        not_found = store.find_overlapping_zone("BTC/USDT", "1D", 200, 210)
        assert not_found is None
        store.close()

    def test_find_overlapping_excludes_invalidated(self):
        store = _temp_store()
        r = store.insert_zone(_make_record(status="INVALIDATED"))
        found = store.find_overlapping_zone("BTC/USDT", "1D", 100, 105)
        assert found is None
        store.close()

    def test_list_watchlist_filters_by_status(self):
        store = _temp_store()
        store.insert_zone(_make_record(symbol="A/USDT", zone_low=1, zone_high=2, status="WAITING"))
        store.insert_zone(_make_record(symbol="B/USDT", zone_low=3, zone_high=4, status="ENTERED"))
        store.insert_zone(_make_record(symbol="C/USDT", zone_low=5, zone_high=6, status="INVALIDATED"))

        watchlist = store.list_watchlist(statuses=("WAITING", "ENTERED"))
        symbols = {r.symbol for r in watchlist}
        assert symbols == {"A/USDT", "B/USDT"}
        store.close()

    def test_list_pending_alerts_filters_grade_and_status(self):
        store = _temp_store()
        store.insert_zone(_make_record(symbol="A/USDT", zone_low=1, zone_high=2, status="ENTERED", grade="A+", alert_sent=False))
        store.insert_zone(_make_record(symbol="B/USDT", zone_low=3, zone_high=4, status="ENTERED", grade="B", alert_sent=False))
        store.insert_zone(_make_record(symbol="C/USDT", zone_low=5, zone_high=6, status="WAITING", grade="A+", alert_sent=False))

        pending = store.list_pending_alerts(min_grade=("A+", "A"))
        assert len(pending) == 1
        assert pending[0].symbol == "A/USDT"
        store.close()

    def test_mark_alert_sent(self):
        store = _temp_store()
        record = store.insert_zone(_make_record(status="ENTERED", grade="A+"))
        sent_at = datetime.now(timezone.utc)
        store.mark_alert_sent(record.id, sent_at)

        fetched = store.get_zone(record.id)
        assert fetched.alert_sent is True
        assert fetched.alert_time is not None
        store.close()


class TestSQLiteUniverse:
    def test_save_and_retrieve_snapshot(self):
        store = _temp_store()
        store.save_universe_snapshot(UniverseSnapshot(id=None, taken_at=datetime.now(timezone.utc), symbols=["BTC", "ETH"]))
        assert store.get_latest_universe_symbols() == ["BTC", "ETH"]
        store.close()

    def test_latest_snapshot_wins_when_multiple_saved(self):
        store = _temp_store()
        now = datetime.now(timezone.utc)
        store.save_universe_snapshot(UniverseSnapshot(id=None, taken_at=now, symbols=["BTC"]))
        store.save_universe_snapshot(UniverseSnapshot(id=None, taken_at=now + timedelta(minutes=1), symbols=["BTC", "ETH", "SOL"]))
        assert store.get_latest_universe_symbols() == ["BTC", "ETH", "SOL"]
        store.close()

    def test_save_universe_events(self):
        store = _temp_store()
        events = [
            UniverseEvent(id=None, occurred_at=datetime.now(timezone.utc), symbol="AVAX", event_type="ENTERED"),
            UniverseEvent(id=None, occurred_at=datetime.now(timezone.utc), symbol="SOL", event_type="EXITED"),
        ]
        store.save_universe_events(events)  # should not raise
        store.close()


class TestReconciliationAgainstRealSQLite:
    """Re-runs the key reconciliation scenarios from test_db.py, but
    against a real SQLite file instead of the in-memory fake, to confirm
    the two backends actually behave the same way, not just satisfy the
    same interface in theory."""

    def test_rescanning_updates_not_duplicates(self):
        store = _temp_store()
        r1 = reconcile_zone(store, _make_engine_zone())
        r2 = reconcile_zone(store, _make_engine_zone(current_price=115.0))
        assert r1.id == r2.id
        assert len(store.list_watchlist(statuses=("WAITING", "ENTERED"))) == 1
        store.close()

    def test_alert_dedup_across_real_db(self):
        store = _temp_store()
        record = reconcile_zone(store, _make_engine_zone(status=ZoneStatus.ENTERED))
        assert len(zones_needing_alert(store)) == 1

        confirm_alert_sent(store, record.id)
        assert zones_needing_alert(store) == []

        # rescan again - must still not reappear
        reconcile_zone(store, _make_engine_zone(status=ZoneStatus.ENTERED, current_price=120))
        assert zones_needing_alert(store) == []
        store.close()

    def test_universe_diff_across_real_db(self):
        store = _temp_store()
        update_universe(store, ["BTC", "ETH", "SOL"])
        events = update_universe(store, ["BTC", "ETH", "AVAX"])
        entered = {e.symbol for e in events if e.event_type == "ENTERED"}
        exited = {e.symbol for e in events if e.event_type == "EXITED"}
        assert entered == {"AVAX"}
        assert exited == {"SOL"}
        store.close()


if __name__ == "__main__":
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ModuleNotFoundError:
        print("pytest not installed; run via a plain test runner instead.")
