-- Production schema for the Demand Zone Scanner.
-- Apply with: psql -U <user> -d <db> -f db/schema.sql

CREATE TABLE IF NOT EXISTS demand_zones (
    id                      BIGSERIAL PRIMARY KEY,
    symbol                  TEXT NOT NULL,
    name                    TEXT,
    timeframe               TEXT NOT NULL CHECK (timeframe IN ('3D', '1D', '4H')),

    zone_low                DOUBLE PRECISION NOT NULL,
    zone_high               DOUBLE PRECISION NOT NULL,
    creation_date           TIMESTAMPTZ NOT NULL,

    score                   DOUBLE PRECISION NOT NULL,
    grade                   TEXT NOT NULL CHECK (grade IN ('A+', 'A', 'B', 'C')),
    freshness               TEXT NOT NULL,
    test_count              INTEGER NOT NULL DEFAULT 0,
    departure_pct           DOUBLE PRECISION NOT NULL DEFAULT 0,
    departure_atr_multiple  DOUBLE PRECISION NOT NULL DEFAULT 0,
    volume_expansion_ratio  DOUBLE PRECISION NOT NULL DEFAULT 1,
    break_of_structure      BOOLEAN NOT NULL DEFAULT FALSE,
    liquidity_sweep         BOOLEAN NOT NULL DEFAULT FALSE,
    fibonacci_confluence    DOUBLE PRECISION,

    current_price           DOUBLE PRECISION NOT NULL,
    zone_entered            BOOLEAN NOT NULL DEFAULT FALSE,
    status                  TEXT NOT NULL CHECK (status IN ('WAITING', 'ENTERED', 'INVALIDATED', 'STALE')),
    btc_condition            TEXT,
    confluent_timeframes     TEXT[],

    alert_sent              BOOLEAN NOT NULL DEFAULT FALSE,
    alert_time               TIMESTAMPTZ,

    result                  TEXT,
    traded_skipped           TEXT NOT NULL DEFAULT 'UNDECIDED' CHECK (traded_skipped IN ('UNDECIDED', 'TRADED', 'SKIPPED')),

    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- overlap-matching (find_overlapping_zone) and watchlist/alert queries
-- both filter by symbol+timeframe+status, so this composite index covers
-- the hot path for every scan.
CREATE INDEX IF NOT EXISTS idx_demand_zones_symbol_tf_status
    ON demand_zones (symbol, timeframe, status);

CREATE INDEX IF NOT EXISTS idx_demand_zones_pending_alerts
    ON demand_zones (status, alert_sent, grade)
    WHERE status = 'ENTERED' AND alert_sent = FALSE;


CREATE TABLE IF NOT EXISTS universe_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    taken_at    TIMESTAMPTZ NOT NULL,
    symbols     TEXT[] NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_universe_snapshots_taken_at
    ON universe_snapshots (taken_at DESC);


CREATE TABLE IF NOT EXISTS universe_events (
    id            BIGSERIAL PRIMARY KEY,
    occurred_at   TIMESTAMPTZ NOT NULL,
    symbol        TEXT NOT NULL,
    event_type    TEXT NOT NULL CHECK (event_type IN ('ENTERED', 'EXITED'))
);

CREATE INDEX IF NOT EXISTS idx_universe_events_occurred_at
    ON universe_events (occurred_at DESC);


CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
