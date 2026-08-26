"""
Tests for engine/market_condition.py.

The key correctness property for this classifier isn't "always guesses
Strong on every up day" - a simple swing-structure classifier legitimately
returns Neutral on ambiguous/choppy data, and should. What actually
matters, since this label rides along on every alert as market context,
is that it never gives the *wrong* directional call - Weak is never
printed on a real uptrend, Strong is never printed on a real downtrend.
Tested across many random trials below rather than a single fixture,
since the fragility risk here is a bad string classifier that only
happens to work on one hand-picked example.

Run with: pytest tests/test_market_condition.py -v
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.market_condition import NEUTRAL, STRONG, WEAK, classify_btc_condition


def _make_trend(direction: str, n: int = 90, seed: int = 1, noise: float = 2.0) -> pd.DataFrame:
    """A realistic trending series: net drift in `direction`, with enough
    daily noise (relative to the drift) that actual swing highs/lows form
    - a perfectly smooth/monotonic series has zero interior swing points
    by definition, which isn't how real price action looks anyway."""
    rng = np.random.default_rng(seed)
    rows = []
    price = 100.0
    ts0 = datetime(2024, 1, 1)
    drift = 1.2 if direction == "up" else -1.2
    for i in range(n):
        step_noise = rng.normal(0, noise)
        o = price
        c = max(0.01, price + drift + step_noise)
        h = max(o, c) + abs(rng.normal(0, 0.5))
        l = min(o, c) - abs(rng.normal(0, 0.5))
        rows.append({"timestamp": ts0 + timedelta(days=i), "open": o, "high": h, "low": l, "close": c, "volume": 1000})
        price = c
    return pd.DataFrame(rows)


def _make_choppy(n: int = 60, seed: int = 1) -> pd.DataFrame:
    """Pure noise, zero net drift - a genuinely sideways market."""
    rng = np.random.default_rng(seed)
    rows = []
    price = 100.0
    ts0 = datetime(2024, 1, 1)
    for i in range(n):
        c = max(0.01, price + rng.normal(0, 2.0))
        o = price
        h = max(o, c) + 0.4
        l = min(o, c) - 0.4
        rows.append({"timestamp": ts0 + timedelta(days=i), "open": o, "high": h, "low": l, "close": c, "volume": 1000})
        price = c
    return pd.DataFrame(rows)


class TestDirectionalSafety:
    """The property that actually matters: never the wrong call."""

    def test_never_calls_uptrend_weak(self):
        wrong = 0
        for seed in range(30):
            df = _make_trend("up", seed=seed)
            if classify_btc_condition(df) == WEAK:
                wrong += 1
        assert wrong == 0, f"{wrong}/30 uptrends were misclassified as Weak"

    def test_never_calls_downtrend_strong(self):
        wrong = 0
        for seed in range(30):
            df = _make_trend("down", seed=seed + 5000)
            if classify_btc_condition(df) == STRONG:
                wrong += 1
        assert wrong == 0, f"{wrong}/30 downtrends were misclassified as Strong"

    def test_correctly_identifies_most_clear_trends(self):
        # not every trial needs to resolve to a directional call (Neutral
        # is an acceptable, conservative outcome) but the majority of
        # genuinely trending data should get a directional read
        correct = 0
        total = 40
        for seed in range(total // 2):
            up = classify_btc_condition(_make_trend("up", seed=seed))
            down = classify_btc_condition(_make_trend("down", seed=seed + 5000))
            correct += (up == STRONG) + (down == WEAK)
        assert correct / total >= 0.6, f"only {correct}/{total} clear trends got a directional read"


class TestChoppyMarket:
    def test_choppy_sideways_market_is_mostly_neutral(self):
        neutral_count = sum(
            classify_btc_condition(_make_choppy(seed=s)) == NEUTRAL for s in range(15)
        )
        assert neutral_count / 15 >= 0.8, "a genuinely sideways market should mostly read as Neutral"


class TestBoundaryConditions:
    def test_insufficient_history_returns_neutral(self):
        df = _make_trend("up", n=10)  # well under the minimum window
        assert classify_btc_condition(df) == NEUTRAL

    def test_does_not_crash_on_empty_dataframe(self):
        df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        assert classify_btc_condition(df) == NEUTRAL

    def test_does_not_crash_on_flat_market(self):
        rows = [{"timestamp": datetime(2024, 1, 1) + timedelta(days=i),
                  "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1000} for i in range(60)]
        df = pd.DataFrame(rows)
        assert classify_btc_condition(df) in (STRONG, NEUTRAL, WEAK)


if __name__ == "__main__":
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ModuleNotFoundError:
        print("pytest not installed; run via a plain test runner instead.")
