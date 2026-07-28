"""Tests for the canonical possession-accounting layer."""
from app.analysis.accounting import (
    ZONES, statistical_possessions, sim_accounting,
)


def test_statistical_possession_formula():
    # FGA - OREB + TOV + 0.44*FTA
    assert statistical_possessions(85, 10, 14, 25) == 85 - 10 + 14 + 0.44 * 25


def _game(shots, oreb=0, box=None):
    """Minimal simulate_game-shaped dict: events carry type/shot_type/made/is_oreb.
    Per PR #9 every typed event carries `type`; sim_accounting filters on it so AST/BLK
    context events (which also carry `shot_type` for display) don't inflate FGA."""
    events = [{"type": "SHOT", "shot_type": st, "made": made, "is_oreb": False} for st, made in shots]
    events += [{"type": "REB", "shot_type": None, "made": False, "is_oreb": True} for _ in range(oreb)]
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


def test_ast_and_blk_context_events_do_not_inflate_fga():
    """Regression fence — post-PR #9 AST and BLK events carry a `shot_type` field
    for display context ("P assists Q's mid-range jumper"). They must NOT be
    counted as FGAs. Prior to gating on `type == "SHOT"`, sim_accounting was
    triple-counting each assisted or blocked shot: SHOT + AST + (BLK) all share
    the same shot_type, so the FGA denominator inflated ~30% and FG% deflated
    ~15pp with no real behavior change."""
    box = {1: dict(fga=1, fgm=1, fg3a=0, fg3m=0, fta=0, ftm=0, tov=0, pts=2)}
    events = [
        {"type": "SHOT", "shot_type": "layup", "made": True, "is_oreb": False},
        # AST context event carries the parent's shot_type for the display layer.
        # It is NOT an attempt.
        {"type": "AST", "shot_type": "layup", "made": False, "is_oreb": False},
    ]
    acc = sim_accounting("t", [{"box_score": box, "events": events}])
    # 1 FGA, 1 FGM → FG% == 1.0. With the bug this was 1/2 == 0.5.
    assert acc.zones["interior"].fg_pct == 1.0
    assert acc.zones["interior"].fga_share == 1.0


def test_sim_oreb_counts_as_extension_not_possession():
    box = {1: dict(fga=10, fgm=5, fg3a=2, fg3m=1, fta=4, ftm=3, tov=2, pts=14)}
    g = _game([("layup", True)] * 5 + [("above_break_three", True)], oreb=3, box=box)
    acc = sim_accounting("t", [g])
    # per-team-game = totals / 2 (sim_accounting counts two teams per game);
    # statistical possessions subtract OREB: (10 - 3 + 2 + 0.44*4) / 2
    assert abs(acc.possessions - (10 - 3 + 2 + 0.44 * 4) / 2) < 1e-9
    assert acc.oreb_rate > 0
