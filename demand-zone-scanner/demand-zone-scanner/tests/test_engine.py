"""
Tests use synthetic OHLCV data with a hand-built, unambiguous demand-zone
pattern (downtrend -> tight base -> strong bullish displacement with volume
expansion -> price never returns) so we can assert the engine finds it and
scores it highly, plus a negative case (choppy/no clean zone) that should
NOT produce a high-scoring result.

Run with: pytest tests/test_engine.py -v
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import (
    DEFAULT_CONFIG,
    Timeframe,
    detect_demand_zones,
    score_all_opportunities,
)


def _candles_to_df(rows):
    ts0 = datetime(2024, 1, 1)
    return pd.DataFrame([
        {
            "timestamp": ts0 + timedelta(days=i),
            "open": o, "high": h, "low": l, "close": c, "volume": v,
        }
        for i, (o, h, l, c, v) in enumerate(rows)
    ])


def _build_clean_demand_zone_df() -> pd.DataFrame:
    rows = []

    # 1. uptrend into a genuine local peak (an unambiguous swing high,
    #    higher than every candle within the swing lookback window on
    #    both sides) so BOS / fib-confluence have a real reference point
    price = 88.0
    for _ in range(5):
        o = price
        c = price + 2.4
        h = c + 0.3
        l = o - 0.3
        rows.append((o, h, l, c, 1000))
        price = c
    # peak candle: clearly the highest high in its neighborhood
    rows.append((price, price + 3.0, price - 0.3, price + 1.0, 1200))
    price = price + 1.0

    # 2. downtrend away from the peak, down toward the base
    for _ in range(9):
        o = price
        c = price - 2.6
        h = o + 0.3
        l = c - 0.3
        rows.append((o, h, l, c, 1000))
        price = c

    # 3. liquidity sweep + base: a tight consolidation candle wicking below
    #    the prior swing low then closing back inside
    base_low = price - 3  # wick sweeps below recent structure
    rows.append((price, price + 0.4, base_low, price - 0.2, 900))
    base_open = price - 0.2
    rows.append((base_open, base_open + 0.6, base_open - 0.6, base_open + 0.1, 950))

    zone_low = min(base_low, base_open - 0.6)
    zone_high = max(price + 0.4, base_open + 0.6)

    # 4. strong bullish displacement with volume expansion, breaking
    #    structure above the earlier swing high
    disp_price = base_open + 0.1
    for _ in range(5):
        o = disp_price
        c = disp_price + 4
        h = c + 0.3
        l = o - 0.2
        rows.append((o, h, l, c, 5000))  # 5x the ~1000 baseline volume
        disp_price = c

    # 5. price continues higher and never returns to the zone (fresh)
    for _ in range(15):
        o = disp_price
        c = disp_price + 1
        h = c + 0.5
        l = o - 0.5
        rows.append((o, h, l, c, 1500))
        disp_price = c

    return _candles_to_df(rows), zone_low, zone_high


def _build_choppy_df() -> pd.DataFrame:
    rows = []
    price = 100.0
    for i in range(45):
        # sideways chop, no clean displacement or volume expansion
        o = price
        c = price + (1 if i % 2 == 0 else -1)
        h = max(o, c) + 0.3
        l = min(o, c) - 0.3
        rows.append((o, h, l, c, 1000))
        price = c
    return _candles_to_df(rows)


class TestZoneDetection:
    def test_detects_clean_demand_zone(self):
        df, expected_low, expected_high = _build_clean_demand_zone_df()
        zones = detect_demand_zones(df, "TEST/USDT", Timeframe.D1, DEFAULT_CONFIG)

        assert len(zones) >= 1, "should detect at least one demand zone"
        zone = zones[-1]  # the clean zone we built is the last one formed
        assert zone.zone_low <= expected_low + 1
        assert zone.zone_high >= expected_high - 1
        assert zone.evidence.volume_expansion_ratio >= 1.5
        assert zone.evidence.departure_atr_multiple > 0

    def test_choppy_market_produces_no_strong_zones(self):
        df = _build_choppy_df()
        zones = detect_demand_zones(df, "CHOP/USDT", Timeframe.D1, DEFAULT_CONFIG)
        # choppy data may produce weak candidate zones, but none should
        # score at A/A+ once scored
        scored = score_all_opportunities({Timeframe.D1: zones}, DEFAULT_CONFIG)
        assert all(z.score < 80 for z in scored)


class TestScoring:
    def test_clean_zone_scores_highly_with_confluence(self):
        df, _, _ = _build_clean_demand_zone_df()
        d1_zones = detect_demand_zones(df, "TEST/USDT", Timeframe.D1, DEFAULT_CONFIG)
        d3_zones = detect_demand_zones(df, "TEST/USDT", Timeframe.D3, DEFAULT_CONFIG)

        scored = score_all_opportunities(
            {Timeframe.D1: d1_zones, Timeframe.D3: d3_zones}, DEFAULT_CONFIG
        )
        assert len(scored) >= 1
        top = scored[0]
        assert top.score >= 70, f"expected at least B grade, got {top.score}"
        assert top.grade in ("A+", "A", "B")

    def test_score_is_bounded_0_to_100(self):
        df, _, _ = _build_clean_demand_zone_df()
        zones = detect_demand_zones(df, "TEST/USDT", Timeframe.D1, DEFAULT_CONFIG)
        scored = score_all_opportunities({Timeframe.D1: zones}, DEFAULT_CONFIG)
        for z in scored:
            assert 0 <= z.score <= 100


if __name__ == "__main__":
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ModuleNotFoundError:
        print("pytest not installed; run via a plain test runner instead.")
