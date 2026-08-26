"""
Edge-case and input-validation coverage, added after a performance/
robustness review of the engine. Complements test_engine.py (which
covers detection correctness on realistic patterns) with the boundary
and malformed-input conditions a 100-coin unattended scanner will
eventually hit in production.

Run with: pytest tests/test_edge_cases.py -v
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import DEFAULT_CONFIG, Timeframe, detect_demand_zones, score_all_opportunities


def _mk(rows):
    ts0 = datetime(2024, 1, 1)
    return pd.DataFrame([
        {"timestamp": ts0 + timedelta(days=i), "open": o, "high": h, "low": l, "close": c, "volume": v}
        for i, (o, h, l, c, v) in enumerate(rows)
    ])


class TestBoundaryConditions:
    def test_empty_dataframe_returns_no_zones(self):
        df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        assert detect_demand_zones(df, "X/USDT", Timeframe.D1, DEFAULT_CONFIG) == []

    def test_below_minimum_length_returns_no_zones(self):
        df = _mk([(100 + i, 101 + i, 99 + i, 100.5 + i, 1000) for i in range(10)])
        assert detect_demand_zones(df, "X/USDT", Timeframe.D1, DEFAULT_CONFIG) == []

    def test_exactly_at_minimum_length_boundary(self):
        df = _mk([(100, 101, 99, 100.5, 1000) for _ in range(30)])
        assert isinstance(detect_demand_zones(df, "X/USDT", Timeframe.D1, DEFAULT_CONFIG), list)


class TestDegenerateMarketData:
    def test_flat_zero_range_market_does_not_crash(self):
        # every candle identical -> zero ATR, zero range; must not raise
        # ZeroDivisionError anywhere in the scoring/departure math
        df = _mk([(100, 100, 100, 100, 1000) for _ in range(60)])
        assert isinstance(detect_demand_zones(df, "FLAT/USDT", Timeframe.D1, DEFAULT_CONFIG), list)

    def test_zero_volume_throughout_does_not_crash(self):
        rows, price = [], 100.0
        for _ in range(60):
            rows.append((price, price + 1, price - 1, price + 0.5, 0))
            price += 0.5
        df = _mk(rows)
        assert isinstance(detect_demand_zones(df, "NOVOL/USDT", Timeframe.D1, DEFAULT_CONFIG), list)

    def test_flash_crash_extreme_wick_does_not_crash(self):
        rows = [(100, 101, 99, 100.5, 1000) for _ in range(30)]
        rows.append((100, 100.2, 0.01, 100.1, 50000))  # near-zero wick
        rows += [(100, 101, 99, 100.5, 1000) for _ in range(20)]
        df = _mk(rows)
        assert isinstance(detect_demand_zones(df, "CRASH/USDT", Timeframe.D1, DEFAULT_CONFIG), list)

    def test_monotonic_uptrend_with_no_pullback(self):
        rows = [(100 + i, 101 + i, 99 + i, 100.8 + i, 1000) for i in range(60)]
        df = _mk(rows)
        assert isinstance(detect_demand_zones(df, "MOON/USDT", Timeframe.D1, DEFAULT_CONFIG), list)

    def test_duplicate_timestamps_tolerated(self):
        # a data provider occasionally re-sends the current candle twice;
        # duplicates are non-decreasing so they pass the ordering check
        ts0 = datetime(2024, 1, 1)
        rows = []
        for i in range(60):
            ts = ts0 + timedelta(days=i // 2)
            rows.append({"timestamp": ts, "open": 100 + i, "high": 101 + i,
                         "low": 99 + i, "close": 100.5 + i, "volume": 1000})
        df = pd.DataFrame(rows)
        assert isinstance(detect_demand_zones(df, "DUP/USDT", Timeframe.D1, DEFAULT_CONFIG), list)


class TestInputValidation:
    """
    These document intentional fail-loud behavior: malformed OHLCV input
    must raise immediately rather than silently returning an empty or
    incorrect zone list. With 100 symbols scanning unattended, a silent
    failure here just looks like "no opportunities today" - nobody would
    notice the underlying data problem.
    """

    def test_missing_required_column_raises(self):
        df = _mk([(100, 101, 99, 100.5, 1000) for _ in range(40)]).drop(columns=["volume"])
        try:
            detect_demand_zones(df, "X/USDT", Timeframe.D1, DEFAULT_CONFIG)
            assert False, "expected ValueError for missing column"
        except ValueError as e:
            assert "volume" in str(e)

    def test_nan_price_data_raises(self):
        df = _mk([(100 + i, 101 + i, 99 + i, 100.5 + i, 1000) for i in range(60)])
        df.loc[15, "close"] = np.nan
        try:
            detect_demand_zones(df, "NAN/USDT", Timeframe.D1, DEFAULT_CONFIG)
            assert False, "expected ValueError for NaN price data"
        except ValueError:
            pass

    def test_nan_volume_only_is_tolerated(self):
        # volume gaps are common (a provider outage for one candle) and
        # shouldn't halt the whole symbol - only price NaNs are fatal
        df = _mk([(100 + i, 101 + i, 99 + i, 100.5 + i, 1000) for i in range(60)])
        df.loc[20, "volume"] = np.nan
        assert isinstance(detect_demand_zones(df, "NANVOL/USDT", Timeframe.D1, DEFAULT_CONFIG), list)

    def test_unsorted_timestamps_raises(self):
        rows = [(100 + i, 101 + i, 99 + i, 100.5 + i, 1000) for i in range(60)]
        df = _mk(rows).sample(frac=1, random_state=1).reset_index(drop=True)
        try:
            detect_demand_zones(df, "SHUFFLED/USDT", Timeframe.D1, DEFAULT_CONFIG)
            assert False, "expected ValueError for non-chronological data"
        except ValueError:
            pass


class TestScoringRobustness:
    def test_score_all_opportunities_empty_input(self):
        assert score_all_opportunities({}, DEFAULT_CONFIG) == []

    def test_score_all_opportunities_single_timeframe_only(self):
        df = _mk([(100 + i, 101 + i, 99 + i, 100.5 + i, 1000) for i in range(60)])
        zones = detect_demand_zones(df, "X/USDT", Timeframe.D1, DEFAULT_CONFIG)
        # should not require all three timeframes to be present
        scored = score_all_opportunities({Timeframe.D1: zones}, DEFAULT_CONFIG)
        assert isinstance(scored, list)


if __name__ == "__main__":
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ModuleNotFoundError:
        print("pytest not installed; run via a plain test runner instead.")
