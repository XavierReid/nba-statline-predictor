"""Tests for the BehaviorPipeline + ObjectiveModifier (roadmap stage C)."""
from app.services.behavior.objective import ObjectiveModifier
from app.services.behavior.pipeline import BehaviorPipeline
from app.services.late_game import derive_objective, objective_adjustments
from app.services.modifiers.base import GameSnapshot
from app.services.sim_config import DRAMA_M3, SimConfig


def _snap(home=100, away=100, quarter=4, clock=300.0, home_conceded=False, away_conceded=False):
    return GameSnapshot(
        home_score=home, away_score=away, quarter=quarter, clock_seconds=clock,
        possession_number=1, home_conceded=home_conceded, away_conceded=away_conceded,
    )


class TestPipelineBuild:
    def test_empty_without_clock(self):
        p = BehaviorPipeline(SimConfig(use_momentum=True, use_clock=False), [], [])
        assert p.is_empty

    def test_drama_m3_has_sources(self):
        p = BehaviorPipeline(DRAMA_M3, [{"id": 1, "overall": 75}], [{"id": 2, "overall": 75}])
        assert not p.is_empty

    def test_baseline_empty(self):
        assert BehaviorPipeline(SimConfig(), [], []).is_empty

    def test_adjustments_of_empty_is_neutral(self):
        p = BehaviorPipeline(SimConfig(), [], [])
        adj = p.adjustments(True, _snap())
        assert adj.shot_prob_delta == 0.0 and adj.pace_multiplier == 1.0


class TestPipelineCombines:
    def test_objective_included_and_applied(self):
        # A protecting leader in Q4 should produce a non-neutral combined adjustment
        cfg = SimConfig(use_clock=True, use_team_objectives=True)
        p = BehaviorPipeline(cfg, [], [])
        adj = p.adjustments(True, _snap(home=112, away=100, clock=120.0))  # +12, protecting
        assert adj.shot_prob_delta < 0.0  # PROTECT efficiency cost

    def test_conceded_offense_is_neutral(self):
        cfg = SimConfig(use_clock=True, use_team_objectives=True)
        p = BehaviorPipeline(cfg, [], [])
        adj = p.adjustments(True, _snap(home=125, away=100, clock=120.0, home_conceded=True))
        assert adj.shot_prob_delta == 0.0 and adj.pace_multiplier == 1.0


class TestObjectiveModifier:
    def test_matches_direct_derive_translate(self):
        cfg = SimConfig()
        snap = _snap(home=110, away=100, quarter=4, clock=200.0)  # home +10, protecting
        mod = ObjectiveModifier(cfg)
        got = mod.get_adjustments(True, snap)
        obj, intensity = derive_objective(True, 10, 200.0, 3, cfg)  # q_idx = quarter-1
        expected = objective_adjustments(obj, intensity, cfg)
        assert got.shot_prob_delta == expected.shot_prob_delta
        assert got.pace_multiplier == expected.pace_multiplier
        assert got.three_rate_override == expected.three_rate_override

    def test_trailing_offense_chases(self):
        cfg = SimConfig()
        # away on offense (is_home=False), away trailing by 12
        adj = ObjectiveModifier(cfg).get_adjustments(False, _snap(home=112, away=100, clock=200.0))
        assert adj.pace_multiplier < 1.0  # CHASE hurries

    def test_conceded_gate(self):
        cfg = SimConfig()
        adj = ObjectiveModifier(cfg).get_adjustments(True, _snap(home=125, away=100, home_conceded=True))
        assert adj.shot_prob_delta == 0.0 and adj.pace_multiplier == 1.0

    def test_update_is_noop(self):
        ObjectiveModifier(SimConfig()).update({}, True, _snap())  # should not raise
