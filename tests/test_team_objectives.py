"""Tests for Q4 team objectives (gap 3.1) — the first Behavior-Engine citizen."""
from app.services.late_game import (
    TeamObjective,
    derive_objective,
    objective_adjustments,
)
from app.services.sim_config import DRAMA_M3, SimConfig

CFG = SimConfig()


class TestDeriveObjective:
    def test_neutral_before_q4(self):
        obj, i = derive_objective(True, 15, 400.0, 2, CFG)
        assert obj is TeamObjective.NEUTRAL and i == 0.0

    def test_neutral_on_tiny_margin(self):
        obj, i = derive_objective(True, 2, 400.0, 3, CFG)  # below objective_min_margin
        assert obj is TeamObjective.NEUTRAL

    def test_leader_protects(self):
        obj, i = derive_objective(True, 12, 300.0, 3, CFG)
        assert obj is TeamObjective.PROTECT and i > 0

    def test_trailer_chases(self):
        obj, i = derive_objective(False, 12, 300.0, 3, CFG)
        assert obj is TeamObjective.CHASE and i > 0

    def test_intensity_rises_with_margin(self):
        _, small = derive_objective(True, 6, 300.0, 3, CFG)
        _, big = derive_objective(True, 18, 300.0, 3, CFG)
        assert big > small

    def test_intensity_rises_as_quarter_elapses(self):
        _, early = derive_objective(True, 12, 700.0, 3, CFG)
        _, late = derive_objective(True, 12, 60.0, 3, CFG)
        assert late > early

    def test_intensity_capped_at_full_margin(self):
        _, at_full = derive_objective(True, CFG.objective_full_margin, 0.0, 3, CFG)
        _, beyond = derive_objective(True, CFG.objective_full_margin + 15, 0.0, 3, CFG)
        assert at_full == beyond == 1.0

    def test_active_in_ot(self):
        obj, i = derive_objective(True, 10, 150.0, 4, CFG)
        assert obj is TeamObjective.PROTECT and i > 0


class TestObjectiveAdjustments:
    def test_neutral_is_noop(self):
        adj = objective_adjustments(TeamObjective.NEUTRAL, 0.0, CFG)
        assert adj.three_rate_override == 0.0 and adj.pace_multiplier == 1.0

    def test_protect_reduces_threes_and_slows(self):
        adj = objective_adjustments(TeamObjective.PROTECT, 1.0, CFG)
        assert adj.three_rate_override < 0      # conservative selection
        assert adj.pace_multiplier > 1.0        # milk clock

    def test_chase_hurries_and_is_efficiency_neutral(self):
        # CHASE is tempo-only (efficiency-neutral urgency): no efficiency penalty,
        # and no three shift by default (it costs efficiency in this shot model).
        adj = objective_adjustments(TeamObjective.CHASE, 1.0, CFG)
        assert adj.pace_multiplier < 1.0        # faster
        assert adj.shot_prob_delta == 0.0       # efficiency-neutral
        assert adj.tov_prob_delta == 0.0        # not the old catch-up turnover penalty

    def test_protect_pays_explicit_efficiency_cost(self):
        # Behavior-only selection backfired (fewer threes -> more rim -> higher PPP);
        # the compression lever is an explicit clock-priority shot-quality cost.
        adj = objective_adjustments(TeamObjective.PROTECT, 1.0, CFG)
        assert adj.shot_prob_delta < 0.0

    def test_intensity_scales_magnitude(self):
        half = objective_adjustments(TeamObjective.PROTECT, 0.5, CFG)
        full = objective_adjustments(TeamObjective.PROTECT, 1.0, CFG)
        assert abs(full.three_rate_override) > abs(half.three_rate_override)


class TestConfig:
    def test_drama_m3_uses_objectives_not_catch_up(self):
        assert DRAMA_M3.use_team_objectives is True
        assert DRAMA_M3.use_catch_up is False

    def test_default_off(self):
        assert SimConfig().use_team_objectives is False
