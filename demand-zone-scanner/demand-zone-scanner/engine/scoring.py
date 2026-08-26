"""
Scoring engine.

The 100-point scale in the spec splits into two halves:

  - "Quality" factors, evaluated on a single zone in isolation (60 pts):
      fresh/untested, departure & displacement strength, volume expansion,
      break of structure, liquidity sweep, fibonacci confluence.

  - "Confluence" factors, evaluated across timeframes for the same
      coin/price-area (40 pts): strong 3D demand, strong 1D demand,
      4H confirmation.

This split is what lets "3D Demand Zone -> 1D confirmation -> 4H
confirmation" (per the spec's architecture) translate into a higher score
than a single-timeframe zone with identical individual characteristics.
"""

from __future__ import annotations

from typing import Dict, List

from .config import DetectionConfig, DEFAULT_CONFIG, ScoringWeights
from .models import DemandZone, Freshness, Timeframe

GRADE_THRESHOLDS = [
    (90, "A+"),
    (80, "A"),
    (70, "B"),
]


def grade_for_score(score: float) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "C"


def score_zone_quality(zone: DemandZone, weights: ScoringWeights) -> Dict[str, float]:
    """Scores a single zone's own characteristics. Max = 60 with default weights."""
    e = zone.evidence
    breakdown: Dict[str, float] = {}

    if e.fresh == Freshness.FRESH:
        breakdown["fresh_untested"] = weights.fresh_untested
    elif e.fresh == Freshness.TESTED_ONCE:
        breakdown["fresh_untested"] = weights.fresh_untested * 0.4
    else:  # multiple_tests or invalidated
        breakdown["fresh_untested"] = 0.0

    # 3x ATR of departure = full marks; scales linearly below that
    departure_ratio = min(e.departure_atr_multiple / 3.0, 1.0)
    breakdown["departure_displacement"] = weights.departure_displacement * departure_ratio

    if e.volume_expansion_ratio >= 1.5:
        vol_ratio = min((e.volume_expansion_ratio - 1.0) / 1.5, 1.0)
        breakdown["volume_expansion"] = weights.volume_expansion * vol_ratio
    else:
        breakdown["volume_expansion"] = 0.0

    breakdown["break_of_structure"] = weights.break_of_structure if e.break_of_structure else 0.0
    breakdown["liquidity_sweep"] = weights.liquidity_sweep if e.liquidity_sweep else 0.0
    breakdown["fibonacci_confluence"] = weights.fibonacci_confluence if e.fib_confluence else 0.0

    return breakdown


def _zones_overlap(a: DemandZone, b: DemandZone, tolerance_pct: float = 5.0) -> bool:
    """Do two zones (typically from different timeframes) cover roughly the same price area?"""
    a_low, a_high = a.zone_low * (1 - tolerance_pct / 100), a.zone_high * (1 + tolerance_pct / 100)
    return not (b.zone_high < a_low or b.zone_low > a_high)


def score_opportunity(
    zone: DemandZone,
    other_timeframe_zones: Dict[Timeframe, List[DemandZone]],
    config: DetectionConfig = DEFAULT_CONFIG,
    overlap_tolerance_pct: float = 5.0,
) -> DemandZone:
    """
    Scores `zone` (the primary/highest-conviction timeframe zone for this
    opportunity) using its own quality plus confluence with zones detected
    on other timeframes for the same symbol. Mutates and returns `zone`
    with .score, .score_breakdown, .grade, .confluent_timeframes populated.
    """
    weights = config.weights
    breakdown = score_zone_quality(zone, weights)
    max_quality = (
        weights.fresh_untested + weights.departure_displacement + weights.volume_expansion
        + weights.break_of_structure + weights.liquidity_sweep + weights.fibonacci_confluence
    )
    quality_fraction = sum(breakdown.values()) / max_quality if max_quality else 0.0

    confluent: List[Timeframe] = []

    # award this zone's own timeframe weight, scaled by how strong its own quality is
    if zone.timeframe == Timeframe.D3:
        breakdown["strong_3d_demand"] = weights.strong_3d_demand * quality_fraction
    elif zone.timeframe == Timeframe.D1:
        breakdown["strong_1d_demand"] = weights.strong_1d_demand * quality_fraction
    elif zone.timeframe == Timeframe.H4:
        breakdown["confirmation_4h"] = weights.confirmation_4h * quality_fraction

    # check overlapping zones on other timeframes for confluence credit
    for tf, candidates in other_timeframe_zones.items():
        if tf == zone.timeframe:
            continue
        best_overlap = None
        for candidate in candidates:
            if _zones_overlap(zone, candidate, overlap_tolerance_pct):
                if best_overlap is None or candidate.evidence.departure_atr_multiple > best_overlap.evidence.departure_atr_multiple:
                    best_overlap = candidate
        if best_overlap is None:
            continue

        other_quality = score_zone_quality(best_overlap, weights)
        other_max = max_quality
        other_fraction = sum(other_quality.values()) / other_max if other_max else 0.0

        if tf == Timeframe.D3:
            breakdown["strong_3d_demand"] = weights.strong_3d_demand * other_fraction
            confluent.append(tf)
        elif tf == Timeframe.D1:
            breakdown["strong_1d_demand"] = weights.strong_1d_demand * other_fraction
            confluent.append(tf)
        elif tf == Timeframe.H4:
            breakdown["confirmation_4h"] = weights.confirmation_4h * other_fraction
            confluent.append(tf)

    total = sum(breakdown.values())
    zone.score = round(min(total, 100.0), 1)
    zone.score_breakdown = {k: round(v, 1) for k, v in breakdown.items()}
    zone.grade = grade_for_score(zone.score)
    zone.confluent_timeframes = confluent + [zone.timeframe]

    return zone


def score_all_opportunities(
    zones_by_timeframe: Dict[Timeframe, List[DemandZone]],
    config: DetectionConfig = DEFAULT_CONFIG,
) -> List[DemandZone]:
    """
    Convenience entry point: given all detected zones for one symbol across
    all timeframes, pick the highest-timeframe zone available (3D > 1D > 4H)
    per overlapping price area as the "primary" opportunity, score it with
    confluence from the others, and return the scored primary zones.

    This is what the scanner's per-symbol pipeline should call.
    """
    priority = [Timeframe.D3, Timeframe.D1, Timeframe.H4]
    scored: List[DemandZone] = []
    consumed: Dict[Timeframe, List[int]] = {tf: [] for tf in priority}

    for tf in priority:
        for idx, zone in enumerate(zones_by_timeframe.get(tf, [])):
            if idx in consumed[tf]:
                continue
            others = {
                other_tf: zlist for other_tf, zlist in zones_by_timeframe.items()
                if other_tf != tf
            }
            scored_zone = score_opportunity(zone, others, config)
            scored.append(scored_zone)
            consumed[tf].append(idx)

    scored.sort(key=lambda z: z.score or 0, reverse=True)
    return [z for z in scored if (z.score or 0) >= config.exclude_below_score]
