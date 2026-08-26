"""
Watchlist reconciliation service.

Every scan, the engine re-detects zones from scratch (it has no memory
of previous runs). This service is what gives the system continuity:
matching each freshly-detected DemandZone against whatever's already
stored for that symbol/timeframe/price-area, updating the mutable
fields (score, status, current price...) on a match, and only inserting
a genuinely new row when there's no match. Alert state, creation date,
and any backtesting annotations are never touched by an update - only
by the explicit calls designed for them (mark_alert_sent, etc).

This is also where spec section 11's alert rule lives operationally:
"maintain an alert state so each zone does not generate duplicate
notifications" - because updates preserve alert_sent/alert_time, a zone
that already fired an alert simply won't reappear in list_pending_alerts()
on the next scan, even though it's still being rescanned and rescored.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from engine.models import DemandZone

from .models import TradedStatus, UniverseEvent, UniverseSnapshot, ZoneRecord
from .store import ZoneStore

logger = logging.getLogger(__name__)


def reconcile_zone(
    store: ZoneStore,
    zone: DemandZone,
    now: Optional[datetime] = None,
    btc_condition: Optional[str] = None,
) -> ZoneRecord:
    """
    Matches one freshly-scored engine zone against the store and either
    updates the existing record or inserts a new one. Returns the
    resulting ZoneRecord either way.
    """
    now = now or datetime.now(timezone.utc)
    timeframe_value = zone.timeframe.value if hasattr(zone.timeframe, "value") else str(zone.timeframe)

    existing = store.find_overlapping_zone(zone.symbol, timeframe_value, zone.zone_low, zone.zone_high)

    if existing is not None:
        existing.zone_low = zone.zone_low
        existing.zone_high = zone.zone_high
        existing.score = zone.score or 0.0
        existing.grade = zone.grade or "C"
        existing.freshness = zone.evidence.fresh.value
        existing.test_count = zone.evidence.test_count
        existing.departure_pct = zone.evidence.departure_pct
        existing.departure_atr_multiple = zone.evidence.departure_atr_multiple
        existing.volume_expansion_ratio = zone.evidence.volume_expansion_ratio
        existing.break_of_structure = zone.evidence.break_of_structure
        existing.liquidity_sweep = zone.evidence.liquidity_sweep
        existing.fibonacci_confluence = zone.evidence.fib_confluence
        existing.current_price = zone.current_price or 0.0
        existing.zone_entered = zone.status.value == "ENTERED"
        existing.status = zone.status.value
        existing.btc_condition = btc_condition
        existing.confluent_timeframes = [
            tf.value if hasattr(tf, "value") else str(tf) for tf in zone.confluent_timeframes
        ]
        existing.updated_at = now
        existing.last_seen_at = now
        # deliberately NOT touched: id, symbol, timeframe, creation_date,
        # alert_sent, alert_time, result, traded_skipped
        return store.update_zone(existing)

    record = ZoneRecord(
        id=None,
        symbol=zone.symbol,
        name=None,
        timeframe=timeframe_value,
        zone_low=zone.zone_low,
        zone_high=zone.zone_high,
        creation_date=zone.formed_at,
        score=zone.score or 0.0,
        grade=zone.grade or "C",
        freshness=zone.evidence.fresh.value,
        test_count=zone.evidence.test_count,
        departure_pct=zone.evidence.departure_pct,
        departure_atr_multiple=zone.evidence.departure_atr_multiple,
        volume_expansion_ratio=zone.evidence.volume_expansion_ratio,
        break_of_structure=zone.evidence.break_of_structure,
        liquidity_sweep=zone.evidence.liquidity_sweep,
        fibonacci_confluence=zone.evidence.fib_confluence,
        current_price=zone.current_price or 0.0,
        zone_entered=zone.status.value == "ENTERED",
        status=zone.status.value,
        btc_condition=btc_condition,
        confluent_timeframes=[
            tf.value if hasattr(tf, "value") else str(tf) for tf in zone.confluent_timeframes
        ],
        alert_sent=False,
        alert_time=None,
        result=None,
        traded_skipped=TradedStatus.UNDECIDED,
        updated_at=now,
        last_seen_at=now,
    )
    return store.insert_zone(record)


def reconcile_scan_results(
    store: ZoneStore,
    scored_zones: List[DemandZone],
    now: Optional[datetime] = None,
    btc_condition: Optional[str] = None,
) -> List[ZoneRecord]:
    """Reconciles every zone from one scan pass. Returns the resulting records."""
    return [reconcile_zone(store, z, now, btc_condition) for z in scored_zones]


def zones_needing_alert(store: ZoneStore) -> List[ZoneRecord]:
    """
    The list the Telegram layer (Phase 4) will poll: zones that have
    just entered their demand zone, are A/A+ grade, and haven't been
    alerted yet. Calling this repeatedly is always safe - it never
    mutates state, so it won't itself cause duplicate alerts.
    """
    return store.list_pending_alerts(min_grade=("A+", "A"))


def confirm_alert_sent(store: ZoneStore, zone_id: int, sent_at: Optional[datetime] = None) -> None:
    """
    Called by the Telegram layer only after a message has actually been
    successfully delivered - not before. If this were called
    speculatively before send confirmation and the send then failed,
    the alert would be silently lost (marked sent but never received).
    """
    store.mark_alert_sent(zone_id, sent_at or datetime.now(timezone.utc))


def update_universe(
    store: ZoneStore,
    current_symbols: List[str],
    now: Optional[datetime] = None,
) -> List[UniverseEvent]:
    """
    Persists the new universe snapshot and logs any entry/exit events
    versus whatever was previously stored (spec section 1: automatic
    universe updates). Returns the events logged this call.

    The very first snapshot ever taken has nothing to diff against - it's
    the baseline, not 100 coins simultaneously "entering." No events are
    logged in that case, only the snapshot itself.
    """
    now = now or datetime.now(timezone.utc)
    previous_symbols = set(store.get_latest_universe_symbols())
    is_first_snapshot = not previous_symbols
    current_set = set(current_symbols)

    events: List[UniverseEvent] = []
    if not is_first_snapshot:
        events = [
            UniverseEvent(id=None, occurred_at=now, symbol=s, event_type="ENTERED")
            for s in sorted(current_set - previous_symbols)
        ] + [
            UniverseEvent(id=None, occurred_at=now, symbol=s, event_type="EXITED")
            for s in sorted(previous_symbols - current_set)
        ]

    store.save_universe_snapshot(UniverseSnapshot(id=None, taken_at=now, symbols=list(current_symbols)))
    if events:
        store.save_universe_events(events)
        logger.info("Universe changed: %d entered, %d exited",
                    sum(1 for e in events if e.event_type == "ENTERED"),
                    sum(1 for e in events if e.event_type == "EXITED"))

    return events
