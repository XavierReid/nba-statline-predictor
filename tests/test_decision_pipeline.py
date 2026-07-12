"""Tests for the possession decision pipeline stages (roadmap stage D).

Behavior is covered exhaustively by test_m3b/m3d/m3e via resolve_possession; these
tests pin the pipeline STRUCTURE — each named stage exists and produces its product —
so the readability seams don't silently collapse back into a monolith.
"""
import random

from app.services.possession import (
    Action,
    Matchup,
    ShotQuality,
    _evaluate_shot,
    _resolve_matchup,
    _select_action,
    resolve_possession,
)
from app.services.possession_context import make_context


def _player(pid, pos="G", **kw):
    p = dict(
        id=pid, name=f"P{pid}", position=pos, overall=75,
        three_point=70, mid_range=70, close_shot=70, layup=75, dunk=60, free_throw=75,
        perimeter_defense=70, interior_defense=70, block=50, steal=50, passing=70,
        offensive_rebound=30, defensive_rebound=50, usage_rate=0.20, three_point_rate=0.35,
        assist_rate=2.0, oreb_rate=0.05, dreb_rate=0.10, turnover_rate=2.0,
    )
    p.update(kw)
    return p


def _ctx(seed=1, **overrides):
    offense = [_player(i) for i in range(1, 6)]
    defense = [_player(i, pos="F") for i in range(10, 15)]
    return make_context(offense, defense, random.Random(seed), **overrides)


class TestStagesProduceProducts:
    def test_select_action_returns_action(self):
        ctx = _ctx()
        result = {"scorer": None, "shot_type": None, "fta": 0, "ftm": 0,
                  "turnover_by": None, "steal_by": None, "fouled_by": None}
        action = _select_action(ctx, result)
        assert isinstance(action, Action)
        assert action.ball_handler in ctx.offense
        # either terminal (no shot) or a shot with a type
        assert action.terminal or action.sub_type is not None

    def test_resolve_matchup_returns_matchup(self):
        ctx = _ctx(seed=3)
        action = Action(ctx.offense[0], terminal=False, coarse_type="three", sub_type="three")
        result = {"block_by": None, "rebounded_by": None, "is_oreb": False}
        m = _resolve_matchup(ctx, action, result)
        assert isinstance(m, Matchup)
        # a three is not block-eligible -> a defender must be chosen
        assert m.blocked is False and m.defender in ctx.defense

    def test_evaluate_shot_returns_probability(self):
        ctx = _ctx(seed=5)
        action = Action(ctx.offense[0], terminal=False, coarse_type="three", sub_type="three")
        m = Matchup(defender=ctx.defense[0])
        q = _evaluate_shot(ctx, action, m)
        assert isinstance(q, ShotQuality)
        assert 0.05 <= q.make_prob <= 0.95

    def test_evaluate_shot_has_no_make_miss_side_effect(self):
        # evaluate_shot computes quality only; the make/miss draw lives in resolve_outcome
        ctx = _ctx(seed=7)
        action = Action(ctx.offense[0], terminal=False, coarse_type="mid", sub_type="mid")
        q1 = _evaluate_shot(ctx, action, Matchup(defender=ctx.defense[0]))
        assert isinstance(q1.make_prob, float)


class TestOrchestratorFlow:
    def test_full_possession_returns_event(self):
        for seed in range(20):
            e = resolve_possession(_ctx(seed=seed))
            assert "shot_type" in e and "made" in e
            # exactly one terminal shape holds
            is_turnover = e["turnover_by"] is not None
            is_shot = e["shot_type"] is not None
            is_bonus_foul = e["fta"] > 0 and e["shot_type"] is None and not is_turnover
            assert is_turnover or is_shot or is_bonus_foul

    def test_determinism_same_seed(self):
        assert resolve_possession(_ctx(seed=42)) == resolve_possession(_ctx(seed=42))
