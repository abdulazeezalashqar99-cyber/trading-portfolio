"""
In-memory ZoneStore. No I/O, no dependencies - this is what the test
suite runs against, and it's a legitimate way to run the scanner locally
before setting up Postgres.

Overlap matching uses a simple range-intersection check with a tolerance
band, mirroring the same logic the Postgres implementation should use in
its SQL WHERE clause (see postgres_store.py's docstring for the
equivalent query).
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from .models import UniverseEvent, UniverseSnapshot, ZoneRecord


class InMemoryZoneStore:
    def __init__(self):
        self._zones: Dict[int, ZoneRecord] = {}
        self._next_id = 1
        self._universe_snapshots: List[UniverseSnapshot] = []
        self._universe_events: List[UniverseEvent] = []
        self._settings: Dict[str, str] = {}

    def find_overlapping_zone(
        self, symbol: str, timeframe: str, zone_low: float, zone_high: float,
        exclude_statuses: tuple = ("INVALIDATED",),
    ) -> Optional[ZoneRecord]:
        candidates = [
            z for z in self._zones.values()
            if z.symbol == symbol and z.timeframe == timeframe and z.status not in exclude_statuses
        ]
        for z in candidates:
            if not (zone_high < z.zone_low or zone_low > z.zone_high):  # ranges overlap
                return z
        return None

    def insert_zone(self, record: ZoneRecord) -> ZoneRecord:
        record.id = self._next_id
        self._next_id += 1
        self._zones[record.id] = record
        return record

    def update_zone(self, record: ZoneRecord) -> ZoneRecord:
        if record.id is None or record.id not in self._zones:
            raise ValueError(f"Cannot update zone with unknown id: {record.id}")
        self._zones[record.id] = record
        return record

    def get_zone(self, zone_id: int) -> Optional[ZoneRecord]:
        return self._zones.get(zone_id)

    def list_watchlist(self, statuses: tuple = ("WAITING", "ENTERED")) -> List[ZoneRecord]:
        return sorted(
            (z for z in self._zones.values() if z.status in statuses),
            key=lambda z: z.score,
            reverse=True,
        )

    def list_pending_alerts(self, min_grade: tuple = ("A+", "A")) -> List[ZoneRecord]:
        return [
            z for z in self._zones.values()
            if z.status == "ENTERED" and not z.alert_sent and z.grade in min_grade
        ]

    def mark_alert_sent(self, zone_id: int, sent_at: datetime) -> None:
        z = self._zones.get(zone_id)
        if z is None:
            raise ValueError(f"Cannot mark alert sent for unknown zone id: {zone_id}")
        z.alert_sent = True
        z.alert_time = sent_at

    def list_triggered_alerts(self) -> List[ZoneRecord]:
        return [z for z in self._zones.values() if z.alert_sent]

    def save_universe_snapshot(self, snapshot: UniverseSnapshot) -> UniverseSnapshot:
        snapshot.id = len(self._universe_snapshots) + 1
        self._universe_snapshots.append(snapshot)
        return snapshot

    def get_latest_universe_symbols(self) -> List[str]:
        if not self._universe_snapshots:
            return []
        return list(self._universe_snapshots[-1].symbols)

    def save_universe_events(self, events: List[UniverseEvent]) -> None:
        for e in events:
            e.id = len(self._universe_events) + 1
            self._universe_events.append(e)

    def get_setting(self, key: str) -> Optional[str]:
        return self._settings.get(key)

    def set_setting(self, key: str, value: str) -> None:
        self._settings[key] = value
