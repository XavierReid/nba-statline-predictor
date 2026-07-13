"""Tests for the canonical possession-accounting layer."""
from app.analysis.accounting import (
    ZONES, statistical_possessions, sim_accounting,
)


def test_statistical_possession_formula():
    # FGA - OREB + TOV + 0.44*FTA
    assert statistical_possessions(85, 10, 14, 25) == 85 - 10 + 14 + 0.44 * 25


def _game(shots, oreb=0, box=None):
    """Minimal simulate_game-shaped dict: events carry shot_type/made/is_oreb."""
    events = [{"shot_type": st, "made": made, "is_oreb": False} for st, made in shots]
    events += [{"shot_type": None, "made": False, "is_oreb": True} for _ in range(oreb)]
    return {"box_score": box or {}, "events": events}


def test_sim_zone_mapping_and_makes():
    # one team-game worth: 2 rim (1 make), 1 mid miss, 1 above-break make
    box = {1: dict(fga=4, fgm=2, fg3a=1, fg3m=1, fta=0, ftm=0, tov=0, pts=7)}
    g = _game([("layup", True), ("dunk", False), ("mid_range", False),
               ("above_break_three", True)], box=box)
    acc = sim_accounting("t", [g])
    assert acc.zones["interior"].fg_pct == 0.5   # 1 of 2
    assert acc.zones["mid"].fg_pct == 0.0
    assert acc.zones["three"].fg_pct == 1.0
    assert acc.above_break_share == 1.0

    # attempt shares sum to 1 across the three zones
    assert abs(sum(acc.zones[z].fga_share for z in ZONES) - 1.0) < 1e-9


def test_sim_oreb_counts_as_extension_not_possession():
    box = {1: dict(fga=10, fgm=5, fg3a=2, fg3m=1, fta=4, ftm=3, tov=2, pts=14)}
    g = _game([("layup", True)] * 5 + [("above_break_three", True)], oreb=3, box=box)
    acc = sim_accounting("t", [g])
    # per-team-game = totals / 2 (sim_accounting counts two teams per game);
    # statistical possessions subtract OREB: (10 - 3 + 2 + 0.44*4) / 2
    assert abs(acc.possessions - (10 - 3 + 2 + 0.44 * 4) / 2) < 1e-9
    assert acc.oreb_rate > 0
