"""
PostgreSQL implementation of ZoneStore, for production use.

Structurally mirrors memory_store.InMemoryZoneStore method-for-method,
so the two are behaviorally interchangeable as far as service.py is
concerned. The overlap-matching predicate here (`NOT (zone_high < %s OR
zone_low > %s)`) is the direct SQL equivalent of the same check in
InMemoryZoneStore.find_overlapping_zone.

Requires: pip install psycopg2-binary
Not exercised by the test suite in this delivery - this sandbox has
neither psycopg2 installed nor a Postgres server to connect to, and no
network to fix either. The reconciliation logic itself (which is where
the actual bugs would hide) is fully tested against InMemoryZoneStore in
tests/test_db.py; this file is comparatively thin, but before relying on
it in production, run tests/test_db.py's scenarios again against a real
Postgres instance (e.g. by writing a PostgresZoneStore-backed variant of
that suite once you have a database to point it at) to confirm the SQL
behaves the same as the in-memory reference implementation.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

try:
    import psycopg2
    import psycopg2.extras
except ImportError as e:  # pragma: no cover - exercised only when psycopg2 is actually missing
    raise ImportError(
        "psycopg2 is required for PostgresZoneStore. Install with: pip install psycopg2-binary"
    ) from e

from .models import TradedStatus, UniverseEvent, UniverseSnapshot, ZoneRecord


class PostgresZoneStore:
    def __init__(self, dsn: str):
        """dsn: a standard libpq connection string / DATABASE_URL."""
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True

    def close(self) -> None:
        self._conn.close()

    def find_overlapping_zone(
        self, symbol: str, timeframe: str, zone_low: float, zone_high: float,
        exclude_statuses: tuple = ("INVALIDATED",),
    ) -> Optional[ZoneRecord]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM demand_zones
                WHERE symbol = %s AND timeframe = %s
                  AND status != ALL(%s)
                  AND NOT (zone_high < %s OR zone_low > %s)
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (symbol, timeframe, list(exclude_statuses), zone_low, zone_high),
            )
            row = cur.fetchone()
            return _row_to_record(row) if row else None

    def insert_zone(self, record: ZoneRecord) -> ZoneRecord:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO demand_zones (
                    symbol, name, timeframe, zone_low, zone_high, creation_date,
                    score, grade, freshness, test_count, departure_pct,
                    departure_atr_multiple, volume_expansion_ratio,
                    break_of_structure, liquidity_sweep, fibonacci_confluence,
                    current_price, zone_entered, status, btc_condition, confluent_timeframes,
                    alert_sent, alert_time, result, traded_skipped,
                    updated_at, last_seen_at
                ) VALUES (
                    %(symbol)s, %(name)s, %(timeframe)s, %(zone_low)s, %(zone_high)s, %(creation_date)s,
                    %(score)s, %(grade)s, %(freshness)s, %(test_count)s, %(departure_pct)s,
                    %(departure_atr_multiple)s, %(volume_expansion_ratio)s,
                    %(break_of_structure)s, %(liquidity_sweep)s, %(fibonacci_confluence)s,
                    %(current_price)s, %(zone_entered)s, %(status)s, %(btc_condition)s, %(confluent_timeframes)s,
                    %(alert_sent)s, %(alert_time)s, %(result)s, %(traded_skipped)s,
                    %(updated_at)s, %(last_seen_at)s
                )
                RETURNING *
                """,
                _record_to_params(record),
            )
            return _row_to_record(cur.fetchone())

    def update_zone(self, record: ZoneRecord) -> ZoneRecord:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE demand_zones SET
                    zone_low = %(zone_low)s, zone_high = %(zone_high)s,
                    score = %(score)s, grade = %(grade)s, freshness = %(freshness)s,
                    test_count = %(test_count)s, departure_pct = %(departure_pct)s,
                    departure_atr_multiple = %(departure_atr_multiple)s,
                    volume_expansion_ratio = %(volume_expansion_ratio)s,
                    break_of_structure = %(break_of_structure)s,
                    liquidity_sweep = %(liquidity_sweep)s,
                    fibonacci_confluence = %(fibonacci_confluence)s,
                    current_price = %(current_price)s, zone_entered = %(zone_entered)s,
                    status = %(status)s, btc_condition = %(btc_condition)s, confluent_timeframes = %(confluent_timeframes)s,
                    alert_sent = %(alert_sent)s, alert_time = %(alert_time)s,
                    result = %(result)s, traded_skipped = %(traded_skipped)s,
                    updated_at = %(updated_at)s, last_seen_at = %(last_seen_at)s
                WHERE id = %(id)s
                RETURNING *
                """,
                _record_to_params(record),
            )
            return _row_to_record(cur.fetchone())

    def get_zone(self, zone_id: int) -> Optional[ZoneRecord]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM demand_zones WHERE id = %s", (zone_id,))
            row = cur.fetchone()
            return _row_to_record(row) if row else None

    def list_watchlist(self, statuses: tuple = ("WAITING", "ENTERED")) -> List[ZoneRecord]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM demand_zones WHERE status = ANY(%s) ORDER BY score DESC",
                (list(statuses),),
            )
            return [_row_to_record(r) for r in cur.fetchall()]

    def list_pending_alerts(self, min_grade: tuple = ("A+", "A")) -> List[ZoneRecord]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM demand_zones
                WHERE status = 'ENTERED' AND alert_sent = FALSE AND grade = ANY(%s)
                """,
                (list(min_grade),),
            )
            return [_row_to_record(r) for r in cur.fetchall()]

    def mark_alert_sent(self, zone_id: int, sent_at: datetime) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE demand_zones SET alert_sent = TRUE, alert_time = %s WHERE id = %s",
                (sent_at, zone_id),
            )

    def list_triggered_alerts(self) -> List[ZoneRecord]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM demand_zones WHERE alert_sent = TRUE ORDER BY alert_time DESC")
            return [_row_to_record(r) for r in cur.fetchall()]

    def save_universe_snapshot(self, snapshot: UniverseSnapshot) -> UniverseSnapshot:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO universe_snapshots (taken_at, symbols) VALUES (%s, %s) RETURNING *",
                (snapshot.taken_at, snapshot.symbols),
            )
            row = cur.fetchone()
            snapshot.id = row["id"]
            return snapshot

    def get_latest_universe_symbols(self) -> List[str]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT symbols FROM universe_snapshots ORDER BY taken_at DESC LIMIT 1")
            row = cur.fetchone()
            return list(row[0]) if row else []

    def save_universe_events(self, events: List[UniverseEvent]) -> None:
        with self._conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO universe_events (occurred_at, symbol, event_type) VALUES %s",
                [(e.occurred_at, e.symbol, e.event_type) for e in events],
            )

    def get_setting(self, key: str) -> Optional[str]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value),
            )


def _record_to_params(record: ZoneRecord) -> dict:
    return {
        "id": record.id, "symbol": record.symbol, "name": record.name,
        "timeframe": record.timeframe, "zone_low": record.zone_low, "zone_high": record.zone_high,
        "creation_date": record.creation_date, "score": record.score, "grade": record.grade,
        "freshness": record.freshness, "test_count": record.test_count,
        "departure_pct": record.departure_pct, "departure_atr_multiple": record.departure_atr_multiple,
        "volume_expansion_ratio": record.volume_expansion_ratio,
        "break_of_structure": record.break_of_structure, "liquidity_sweep": record.liquidity_sweep,
        "fibonacci_confluence": record.fibonacci_confluence, "current_price": record.current_price,
        "zone_entered": record.zone_entered, "status": record.status, "btc_condition": record.btc_condition,
        "confluent_timeframes": record.confluent_timeframes,
        "alert_sent": record.alert_sent, "alert_time": record.alert_time, "result": record.result,
        "traded_skipped": record.traded_skipped.value if isinstance(record.traded_skipped, TradedStatus) else record.traded_skipped,
        "updated_at": record.updated_at, "last_seen_at": record.last_seen_at,
    }


def _row_to_record(row: dict) -> ZoneRecord:
    return ZoneRecord(
        id=row["id"], symbol=row["symbol"], name=row["name"], timeframe=row["timeframe"],
        zone_low=row["zone_low"], zone_high=row["zone_high"], creation_date=row["creation_date"],
        score=row["score"], grade=row["grade"], freshness=row["freshness"], test_count=row["test_count"],
        departure_pct=row["departure_pct"], departure_atr_multiple=row["departure_atr_multiple"],
        volume_expansion_ratio=row["volume_expansion_ratio"], break_of_structure=row["break_of_structure"],
        liquidity_sweep=row["liquidity_sweep"], fibonacci_confluence=row["fibonacci_confluence"],
        current_price=row["current_price"], zone_entered=row["zone_entered"], status=row["status"],
        btc_condition=row["btc_condition"], confluent_timeframes=list(row["confluent_timeframes"] or []),
        alert_sent=row["alert_sent"], alert_time=row["alert_time"],
        result=row["result"], traded_skipped=TradedStatus(row["traded_skipped"]),
        updated_at=row["updated_at"], last_seen_at=row["last_seen_at"],
    )
