"""
Tests for dashboard/generate_dashboard.py: confirms it actually writes
valid, parseable output files. The JS logic itself is validated
separately by extracting and running it under Node (see the project
notes) rather than in this Python suite - this suite covers the Python
side: does the generator produce a well-formed data.json and a
non-empty index.html.

Run with: pytest tests/test_dashboard_generation.py -v
"""

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.generate_dashboard import generate_dashboard
from dashboard.data_builder import ScanStats
from db.memory_store import InMemoryZoneStore
from db.models import TradedStatus, ZoneRecord


def _record(**overrides) -> ZoneRecord:
    defaults = dict(
        id=None, symbol="BTC/USDT", name=None, timeframe="1D",
        zone_low=100.0, zone_high=105.0, creation_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        score=92.0, grade="A+", freshness="fresh", test_count=0,
        departure_pct=20.0, departure_atr_multiple=4.0, volume_expansion_ratio=2.0,
        break_of_structure=True, liquidity_sweep=False, fibonacci_confluence=None,
        current_price=110.0, zone_entered=False, status="WAITING", btc_condition="Strong",
        confluent_timeframes=["3D"], alert_sent=False, alert_time=None, result=None,
        traded_skipped=TradedStatus.UNDECIDED, updated_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ZoneRecord(**defaults)


class TestGenerateDashboard:
    def test_writes_valid_json_and_html(self):
        store = InMemoryZoneStore()
        store.insert_zone(_record())

        with tempfile.TemporaryDirectory() as tmp:
            generate_dashboard(store, tmp)
            out = Path(tmp)

            assert (out / "index.html").exists()
            assert (out / "data.json").exists()

            html = (out / "index.html").read_text()
            assert "<html" in html
            assert len(html) > 500  # not an empty/truncated file

            data = json.loads((out / "data.json").read_text())  # must not raise
            assert "system_status" in data
            assert "a_plus_opportunities" in data
            assert len(data["a_plus_opportunities"]) == 1

    def test_creates_output_directory_if_missing(self):
        store = InMemoryZoneStore()
        with tempfile.TemporaryDirectory() as tmp:
            nested = str(Path(tmp) / "does" / "not" / "exist" / "yet")
            generate_dashboard(store, nested)
            assert (Path(nested) / "index.html").exists()

    def test_json_serializable_with_scan_stats(self):
        store = InMemoryZoneStore()
        stats = ScanStats(
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
            universe_size=100, coins_scanned=98, api_errors={}, alerts_sent_this_run=0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            generate_dashboard(store, tmp, scan_stats=stats)
            data = json.loads((Path(tmp) / "data.json").read_text())
            assert data["system_status"]["coins_scanned"] == 98

    def test_empty_store_produces_valid_empty_dashboard(self):
        store = InMemoryZoneStore()
        with tempfile.TemporaryDirectory() as tmp:
            generate_dashboard(store, tmp)
            data = json.loads((Path(tmp) / "data.json").read_text())
            assert data["a_plus_opportunities"] == []
            assert data["watchlist"] == []
            assert data["triggered_alerts"] == []

    def test_regenerating_overwrites_cleanly(self):
        store = InMemoryZoneStore()
        with tempfile.TemporaryDirectory() as tmp:
            generate_dashboard(store, tmp)
            store.insert_zone(_record(symbol="NEW/USDT", zone_low=1, zone_high=2))
            generate_dashboard(store, tmp)  # second run, same dir

            data = json.loads((Path(tmp) / "data.json").read_text())
            assert len(data["a_plus_opportunities"]) == 1
            assert data["a_plus_opportunities"][0]["symbol"] == "NEW/USDT"


if __name__ == "__main__":
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ModuleNotFoundError:
        print("pytest not installed; run via a plain test runner instead.")
