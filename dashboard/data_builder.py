"""
Dashboard data preparation.

Deliberately separated from generate_dashboard.py's HTML/file writing:
this module turns store contents into a plain dict matching spec section
15's required sections, and is fully unit-testable without touching the
filesystem or rendering anything. generate_dashboard.py just serializes
whatever this produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from db.models import ZoneRecord
from db.store import ZoneStore


@dataclass
class ScanStats:
    """Ephemeral per-run stats, supplied by the orchestration script -
    not persisted separately, since the dashboard is regenerated fresh
    every scan and the previous run's stats aren't meaningful afterward."""
    started_at: datetime
    finished_at: datetime
    universe_size: int
    coins_scanned: int
    api_errors: Dict[str, str] = field(default_factory=dict)  # provider -> error message
    alerts_sent_this_run: int = 0


def _zone_row(z: ZoneRecord) -> dict:
    distance_pct = 0.0
    if z.current_price > z.zone_high:
        distance_pct = ((z.current_price - z.zone_high) / z.zone_high) * 100

    return {
        "symbol": z.symbol,
        "timeframe": z.timeframe,
        "zone_low": z.zone_low,
        "zone_high": z.zone_high,
        "current_price": z.current_price,
        "distance_pct": round(distance_pct, 2),
        "score": z.score,
        "grade": z.grade,
        "status": z.status,
        "freshness": z.freshness,
        "confluent_timeframes": z.confluent_timeframes,
        "btc_condition": z.btc_condition,
        "alert_sent": z.alert_sent,
        "alert_time": z.alert_time.isoformat() if z.alert_time else None,
        "creation_date": z.creation_date.isoformat() if z.creation_date else None,
    }


def build_dashboard_data(store: ZoneStore, scan_stats: Optional[ScanStats] = None) -> dict:
    watchlist = store.list_watchlist(statuses=("WAITING", "ENTERED"))
    triggered = store.list_triggered_alerts()

    a_plus = sorted(
        (z for z in watchlist if z.grade == "A+"), key=lambda z: z.score, reverse=True
    )
    a_grade = sorted(
        (z for z in watchlist if z.grade == "A"), key=lambda z: z.score, reverse=True
    )

    now = scan_stats.finished_at if scan_stats else datetime.now(timezone.utc)

    system_status = {
        "last_scan_time": now.isoformat(),
        "coins_scanned": scan_stats.coins_scanned if scan_stats else None,
        "universe_size": scan_stats.universe_size if scan_stats else None,
        "active_zones": len(watchlist),
        "total_alerts_triggered": len(triggered),
        "alerts_sent_last_run": scan_stats.alerts_sent_this_run if scan_stats else None,
        "api_status": (
            {"coingecko": "ok", "binance": "ok", **{k: "error" for k in (scan_stats.api_errors if scan_stats else {})}}
        ),
        "api_errors": scan_stats.api_errors if scan_stats else {},
        "health": "DEGRADED" if (scan_stats and scan_stats.api_errors) else "OK",
    }

    return {
        "generated_at": now.isoformat(),
        "system_status": system_status,
        "a_plus_opportunities": [_zone_row(z) for z in a_plus],
        "a_opportunities": [_zone_row(z) for z in a_grade],
        "watchlist": [_zone_row(z) for z in sorted(watchlist, key=lambda z: z.score, reverse=True)],
        "triggered_alerts": [
            _zone_row(z) for z in sorted(triggered, key=lambda z: z.alert_time or now, reverse=True)
        ],
    }
