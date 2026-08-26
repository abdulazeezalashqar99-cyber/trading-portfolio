"""
SQLite implementation of ZoneStore.

Chosen for the GitHub-Actions-scheduled deployment path (no VPS, no
hosted database account to manage): the state file lives in the repo
and is committed back after each scheduled run. At this system's scale
(order of 100 symbols x a handful of zones each, one row updated every
scan cycle) SQLite is a completely legitimate production choice, not
just a testing shim - unlike PostgresZoneStore, this one uses Python's
stdlib sqlite3, so it's exercised directly against a real embedded
database in tests/test_sqlite_store.py, not mocked.

Structurally mirrors memory_store.InMemoryZoneStore and
postgres_store.PostgresZoneStore method-for-method - same ZoneStore
interface, so service.py's reconciliation logic is unaffected by which
one is in use.

If you later move to Postgres (e.g. once running on a VPS anyway, or if
volume grows well beyond this system's Top-100 scope), PostgresZoneStore
implements the identical interface - swap the constructor, nothing else
in the codebase changes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .models import TradedStatus, UniverseEvent, UniverseSnapshot, ZoneRecord

SCHEMA_PATH = Path(__file__).parent / "schema_sqlite.sql"


def _dt_to_str(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _str_to_dt(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


class SQLiteZoneStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        with open(SCHEMA_PATH) as f:
            self._conn.executescript(f.read())
        self._conn.commit()

    def find_overlapping_zone(
        self, symbol: str, timeframe: str, zone_low: float, zone_high: float,
        exclude_statuses: tuple = ("INVALIDATED",),
    ) -> Optional[ZoneRecord]:
        placeholders = ",".join("?" * len(exclude_statuses))
        cur = self._conn.execute(
            f"""
            SELECT * FROM demand_zones
            WHERE symbol = ? AND timeframe = ?
              AND status NOT IN ({placeholders})
              AND NOT (zone_high < ? OR zone_low > ?)
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (symbol, timeframe, *exclude_statuses, zone_low, zone_high),
        )
        row = cur.fetchone()
        return _row_to_record(row) if row else None

    def insert_zone(self, record: ZoneRecord) -> ZoneRecord:
        params = _record_to_row_params(record)
        cur = self._conn.execute(
            """
            INSERT INTO demand_zones (
                symbol, name, timeframe, zone_low, zone_high, creation_date,
                score, grade, freshness, test_count, departure_pct,
                departure_atr_multiple, volume_expansion_ratio,
                break_of_structure, liquidity_sweep, fibonacci_confluence,
                current_price, zone_entered, status, btc_condition, confluent_timeframes,
                alert_sent, alert_time, result, traded_skipped,
                updated_at, last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            params[1:],  # skip id (autoincrement)
        )
        self._conn.commit()
        record.id = cur.lastrowid
        return record

    def update_zone(self, record: ZoneRecord) -> ZoneRecord:
        if record.id is None:
            raise ValueError("Cannot update a zone with no id")
        self._conn.execute(
            """
            UPDATE demand_zones SET
                zone_low=?, zone_high=?, score=?, grade=?, freshness=?,
                test_count=?, departure_pct=?, departure_atr_multiple=?,
                volume_expansion_ratio=?, break_of_structure=?, liquidity_sweep=?,
                fibonacci_confluence=?, current_price=?, zone_entered=?,
                status=?, btc_condition=?, confluent_timeframes=?, alert_sent=?, alert_time=?,
                result=?, traded_skipped=?, updated_at=?, last_seen_at=?
            WHERE id=?
            """,
            (
                record.zone_low, record.zone_high, record.score, record.grade, record.freshness,
                record.test_count, record.departure_pct, record.departure_atr_multiple,
                record.volume_expansion_ratio, int(record.break_of_structure), int(record.liquidity_sweep),
                record.fibonacci_confluence, record.current_price, int(record.zone_entered),
                record.status, record.btc_condition, json.dumps(record.confluent_timeframes),
                int(record.alert_sent), _dt_to_str(record.alert_time),
                record.result, _traded_value(record.traded_skipped), _dt_to_str(record.updated_at),
                _dt_to_str(record.last_seen_at), record.id,
            ),
        )
        self._conn.commit()
        return record

    def get_zone(self, zone_id: int) -> Optional[ZoneRecord]:
        cur = self._conn.execute("SELECT * FROM demand_zones WHERE id = ?", (zone_id,))
        row = cur.fetchone()
        return _row_to_record(row) if row else None

    def list_watchlist(self, statuses: tuple = ("WAITING", "ENTERED")) -> List[ZoneRecord]:
        placeholders = ",".join("?" * len(statuses))
        cur = self._conn.execute(
            f"SELECT * FROM demand_zones WHERE status IN ({placeholders}) ORDER BY score DESC",
            statuses,
        )
        return [_row_to_record(r) for r in cur.fetchall()]

    def list_pending_alerts(self, min_grade: tuple = ("A+", "A")) -> List[ZoneRecord]:
        placeholders = ",".join("?" * len(min_grade))
        cur = self._conn.execute(
            f"""
            SELECT * FROM demand_zones
            WHERE status = 'ENTERED' AND alert_sent = 0 AND grade IN ({placeholders})
            """,
            min_grade,
        )
        return [_row_to_record(r) for r in cur.fetchall()]

    def mark_alert_sent(self, zone_id: int, sent_at: datetime) -> None:
        self._conn.execute(
            "UPDATE demand_zones SET alert_sent = 1, alert_time = ? WHERE id = ?",
            (_dt_to_str(sent_at), zone_id),
        )
        self._conn.commit()

    def list_triggered_alerts(self) -> List[ZoneRecord]:
        cur = self._conn.execute("SELECT * FROM demand_zones WHERE alert_sent = 1 ORDER BY alert_time DESC")
        return [_row_to_record(r) for r in cur.fetchall()]

    def save_universe_snapshot(self, snapshot: UniverseSnapshot) -> UniverseSnapshot:
        cur = self._conn.execute(
            "INSERT INTO universe_snapshots (taken_at, symbols) VALUES (?, ?)",
            (_dt_to_str(snapshot.taken_at), json.dumps(snapshot.symbols)),
        )
        self._conn.commit()
        snapshot.id = cur.lastrowid
        return snapshot

    def get_latest_universe_symbols(self) -> List[str]:
        cur = self._conn.execute("SELECT symbols FROM universe_snapshots ORDER BY taken_at DESC LIMIT 1")
        row = cur.fetchone()
        return json.loads(row["symbols"]) if row else []

    def save_universe_events(self, events: List[UniverseEvent]) -> None:
        self._conn.executemany(
            "INSERT INTO universe_events (occurred_at, symbol, event_type) VALUES (?, ?, ?)",
            [(_dt_to_str(e.occurred_at), e.symbol, e.event_type) for e in events],
        )
        self._conn.commit()

    def get_setting(self, key: str) -> Optional[str]:
        cur = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()


def _traded_value(v) -> str:
    return v.value if isinstance(v, TradedStatus) else v


def _record_to_row_params(record: ZoneRecord) -> tuple:
    return (
        record.id, record.symbol, record.name, record.timeframe, record.zone_low, record.zone_high,
        _dt_to_str(record.creation_date), record.score, record.grade, record.freshness, record.test_count,
        record.departure_pct, record.departure_atr_multiple, record.volume_expansion_ratio,
        int(record.break_of_structure), int(record.liquidity_sweep), record.fibonacci_confluence,
        record.current_price, int(record.zone_entered), record.status, record.btc_condition,
        json.dumps(record.confluent_timeframes),
        int(record.alert_sent), _dt_to_str(record.alert_time), record.result, _traded_value(record.traded_skipped),
        _dt_to_str(record.updated_at), _dt_to_str(record.last_seen_at),
    )


def _row_to_record(row: sqlite3.Row) -> ZoneRecord:
    return ZoneRecord(
        id=row["id"], symbol=row["symbol"], name=row["name"], timeframe=row["timeframe"],
        zone_low=row["zone_low"], zone_high=row["zone_high"], creation_date=_str_to_dt(row["creation_date"]),
        score=row["score"], grade=row["grade"], freshness=row["freshness"], test_count=row["test_count"],
        departure_pct=row["departure_pct"], departure_atr_multiple=row["departure_atr_multiple"],
        volume_expansion_ratio=row["volume_expansion_ratio"], break_of_structure=bool(row["break_of_structure"]),
        liquidity_sweep=bool(row["liquidity_sweep"]), fibonacci_confluence=row["fibonacci_confluence"],
        current_price=row["current_price"], zone_entered=bool(row["zone_entered"]), status=row["status"],
        btc_condition=row["btc_condition"],
        confluent_timeframes=json.loads(row["confluent_timeframes"]) if row["confluent_timeframes"] else [],
        alert_sent=bool(row["alert_sent"]), alert_time=_str_to_dt(row["alert_time"]),
        result=row["result"], traded_skipped=TradedStatus(row["traded_skipped"]),
        updated_at=_str_to_dt(row["updated_at"]), last_seen_at=_str_to_dt(row["last_seen_at"]),
    )
