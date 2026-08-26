"""
Storage interface for the watchlist/history layer.

Two implementations exist:
  - memory_store.InMemoryZoneStore - pure Python, no I/O. This is what
    the test suite runs against, and it's also legitimately useful for
    local development without standing up Postgres.
  - postgres_store.PostgresZoneStore - psycopg2-backed, for production.

Both implement this exact interface, so service.py's reconciliation
logic (zone matching, alert dedup) is written once against the
interface and behaves identically regardless of which store backs it.
Requires Python's typing.Protocol - structural typing, no inheritance
needed from either implementation.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Protocol

from .models import UniverseEvent, UniverseSnapshot, ZoneRecord


class ZoneStore(Protocol):
    def find_overlapping_zone(
        self, symbol: str, timeframe: str, zone_low: float, zone_high: float,
        exclude_statuses: tuple = ("INVALIDATED",),
    ) -> Optional[ZoneRecord]:
        """
        Finds an existing non-invalidated zone for this symbol/timeframe
        whose price range overlaps the given range - i.e. the "same" zone
        reappearing on a rescan, not a brand new one.
        """
        ...

    def insert_zone(self, record: ZoneRecord) -> ZoneRecord:
        """Inserts a new zone record, returns it with .id populated."""
        ...

    def update_zone(self, record: ZoneRecord) -> ZoneRecord:
        """Updates an existing zone record (matched by .id)."""
        ...

    def get_zone(self, zone_id: int) -> Optional[ZoneRecord]:
        ...

    def list_watchlist(self, statuses: tuple = ("WAITING", "ENTERED")) -> List[ZoneRecord]:
        ...

    def list_pending_alerts(self, min_grade: tuple = ("A+", "A")) -> List[ZoneRecord]:
        """Zones currently ENTERED, not yet alerted, at or above min_grade."""
        ...

    def mark_alert_sent(self, zone_id: int, sent_at: datetime) -> None:
        ...

    def list_triggered_alerts(self) -> List[ZoneRecord]:
        ...

    def save_universe_snapshot(self, snapshot: UniverseSnapshot) -> UniverseSnapshot:
        ...

    def get_latest_universe_symbols(self) -> List[str]:
        """Empty list if no snapshot has ever been saved."""
        ...

    def save_universe_events(self, events: List[UniverseEvent]) -> None:
        ...

    def get_setting(self, key: str) -> Optional[str]:
        """Small persistent key-value store, for cross-run cursors like the
        last-processed Telegram update_id - not zone or universe data, but
        needs the same cross-run persistence."""
        ...

    def set_setting(self, key: str, value: str) -> None:
        ...
