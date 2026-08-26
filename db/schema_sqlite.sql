-- SQLite schema for the Demand Zone Scanner (GitHub Actions deployment path).
-- Mirrors schema.sql (the Postgres version) with SQLite-compatible types:
-- booleans as INTEGER 0/1, timestamps as ISO8601 TEXT, no native array type
-- (universe symbols stored as a JSON text blob instead).
-- Applied automatically by SQLiteZoneStore on connect - no separate
-- migration step needed for this store.

CREATE TABLE IF NOT EXISTS demand_zones (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                  TEXT NOT NULL,
    name                    TEXT,
    timeframe               TEXT NOT NULL,

    zone_low                REAL NOT NULL,
    zone_high               REAL NOT NULL,
    creation_date           TEXT NOT NULL,

    score                   REAL NOT NULL,
    grade                   TEXT NOT NULL,
    freshness               TEXT NOT NULL,
    test_count              INTEGER NOT NULL DEFAULT 0,
    departure_pct           REAL NOT NULL DEFAULT 0,
    departure_atr_multiple  REAL NOT NULL DEFAULT 0,
    volume_expansion_ratio  REAL NOT NULL DEFAULT 1,
    break_of_structure      INTEGER NOT NULL DEFAULT 0,
    liquidity_sweep         INTEGER NOT NULL DEFAULT 0,
    fibonacci_confluence    REAL,

    current_price           REAL NOT NULL,
    zone_entered            INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL,
    btc_condition           TEXT,
    confluent_timeframes    TEXT,  -- JSON array, e.g. ["3D","1D"]

    alert_sent              INTEGER NOT NULL DEFAULT 0,
    alert_time              TEXT,

    result                  TEXT,
    traded_skipped          TEXT NOT NULL DEFAULT 'UNDECIDED',

    updated_at              TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_demand_zones_symbol_tf_status
    ON demand_zones (symbol, timeframe, status);

CREATE INDEX IF NOT EXISTS idx_demand_zones_pending_alerts
    ON demand_zones (status, alert_sent, grade);


CREATE TABLE IF NOT EXISTS universe_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at    TEXT NOT NULL,
    symbols     TEXT NOT NULL  -- JSON array
);

CREATE INDEX IF NOT EXISTS idx_universe_snapshots_taken_at
    ON universe_snapshots (taken_at DESC);


CREATE TABLE IF NOT EXISTS universe_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at   TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    event_type    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_universe_events_occurred_at
    ON universe_events (occurred_at DESC);


CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
