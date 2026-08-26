"""
Freshness classification.

Freshness is evaluated by walking forward from the candle where the zone
was formed and counting how many separate times price has traded back
into [zone_low, zone_high]. "Separate times" means the price must have
left the zone (closed above zone_high) between touches for it to count
as a new test, rather than counting every candle of one prolonged touch.

Implemented on raw numpy arrays rather than DataFrame.iterrows(): iterrows
reconstructs a pandas Series per row, which is roughly an order of
magnitude slower than plain array iteration and shows up as a measurable
hotspot once this runs across a full coin universe. See PERFORMANCE.md.
"""

from __future__ import annotations

import numpy as np

from .models import Freshness


def classify_freshness(
    low: np.ndarray,
    high: np.ndarray,
    close: np.ndarray,
    zone_low: float,
    zone_high: float,
    formed_at_index: int,
    invalidation_buffer_pct: float = 1.0,
) -> tuple[Freshness, int]:
    """
    Returns (freshness, test_count).

    Walks candles after formation:
      - a "test" starts the first time low <= zone_high (price re-enters)
      - the test ends once close > zone_high again (price has left)
      - if any candle closes below zone_low * (1 - buffer), the zone is
        invalidated (structure broken, no longer a valid demand zone)
    """
    after_low = low[formed_at_index + 1:]
    after_close = close[formed_at_index + 1:]
    if after_low.size == 0:
        return Freshness.FRESH, 0

    invalidation_price = zone_low * (1 - invalidation_buffer_pct / 100)

    invalidated_mask = after_close < invalidation_price
    first_invalidation = int(np.argmax(invalidated_mask)) if invalidated_mask.any() else None

    touching = after_low <= zone_high

    test_count = 0
    in_test = False
    limit = first_invalidation if first_invalidation is not None else after_low.size

    for i in range(limit):
        if touching[i] and not in_test:
            test_count += 1
            in_test = True
        elif not touching[i]:
            in_test = False

    if first_invalidation is not None:
        return Freshness.INVALIDATED, test_count

    if test_count == 0:
        return Freshness.FRESH, 0
    elif test_count == 1:
        return Freshness.TESTED_ONCE, 1
    else:
        return Freshness.MULTIPLE_TESTS, test_count
