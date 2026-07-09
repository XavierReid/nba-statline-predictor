"""Lineup quality — emergent per-possession quality of the five on the floor.

Team-level season stats (def_rating, pace) describe the *normal rotation*; they
cannot see who is actually playing. This module compares the current lineup to
the team's expected-rotation baseline so quality fluctuates as lineups change —
bench-heavy units genuinely defend worse, defensive closing groups better.
Garbage time exposed the need, but the abstraction is general.

Only the defense factor is live today. Future dimensions (offense, rebounding,
spacing, transition) plug into compute_lineup_quality and return 1.0 until
implemented — callers never change.
"""
from typing import Dict, List

# How strongly a lineup's defensive rating gap (vs the rotation baseline) moves
# opponent shot probability. Same 0.5 dampening convention as the team-level
# def_rating factor: a lineup 10 rating points below baseline => x1.05 opponent
# shot-prob factor (~+5 pts/100 poss — bench-vs-starter defensive gap scale).
# Initial constant; verify via the lineup-factor distribution instrumentation
# before treating as calibrated.
_DEF_GAP_SENSITIVITY = 0.5


def lineup_defensive_rating(players: List[dict]) -> float:
    """Mean of the five defenders' blended individual defense ratings."""
    if not players:
        return 50.0
    return sum(
        (p["perimeter_defense"] + p["interior_defense"]) / 2.0 for p in players
    ) / len(players)


def rotation_baseline(players: List[dict]) -> float:
    """Minutes-weighted defensive rating of the team's expected rotation.

    Weighted by planned minutes — NOT a flat roster average — so rarely-used
    end-of-bench players don't distort the baseline. Normal lineups therefore
    center on 1.0 by construction.
    """
    total_min = sum(p["minutes"] for p in players)
    if total_min <= 0:
        return lineup_defensive_rating(players)
    return sum(
        (p["perimeter_defense"] + p["interior_defense"]) / 2.0 * p["minutes"]
        for p in players
    ) / total_min


def compute_lineup_quality(lineup: List[dict], baseline: float) -> Dict[str, float]:
    """Quality factors for the current five, relative to the rotation baseline.

    defense: multiplier on OPPONENT shot probability — >1.0 means this lineup
    defends worse than the team's normal rotation.
    """
    gap = lineup_defensive_rating(lineup) - baseline
    return {
        "defense": 1.0 - (gap / 100.0) * _DEF_GAP_SENSITIVITY,
    }
