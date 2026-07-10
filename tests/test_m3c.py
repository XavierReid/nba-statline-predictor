"""Tests for M3c — CatchUpModifier and GarbageTimeModifier."""
import pytest

from app.services.modifiers.base import GameState, ModifierAdjustments, PlayerGameState
from app.services.modifiers.catch_up import CatchUpModifier
from app.services.modifiers.garbage_time import GarbageTimeModifier
from app.services.sim_config import DRAMA_M3, SimConfig


def _cfg(**kwargs) -> SimConfig:
    cfg = SimConfig(
        catch_up_clock_threshold=150,
        catch_up_max_deficit=15,
        garbage_time_margin=20,
        garbage_time_clock_threshold=600,
    )
    for k, v in kwargs.items():
        object.__setattr__(cfg, k, v)
    return cfg


def _state(
    home_score: int = 100,
    away_score: int = 100,
    quarter: int = 4,
    clock: float = 120.0,
) -> GameState:
    return GameState(
        home_score=home_score,
        away_score=away_score,
        quarter=quarter,
        clock_seconds=clock,
        possession_number=1,
    )


# ---------------------------------------------------------------------------
# CatchUpModifier
# ---------------------------------------------------------------------------

class TestCatchUpModifier:
    def _mod(self, **kwargs):
        return CatchUpModifier(_cfg(**kwargs))

    def test_no_effect_before_q4(self):
        mod = self._mod()
        adj = mod.get_adjustments(True, _state(quarter=3, clock=100.0))
        assert adj.three_rate_override == 0.0
        assert adj.pace_multiplier == 1.0

    def test_no_effect_when_clock_above_threshold(self):
        mod = self._mod()
        adj = mod.get_adjustments(True, _state(home_score=95, away_score=100, clock=200.0))
        assert adj.three_rate_override == 0.0
        assert adj.pace_multiplier == 1.0

    def test_no_effect_when_deficit_exceeds_max(self):
        mod = self._mod()
        # Home trails by 20 — above catch_up_max_deficit=15
        adj = mod.get_adjustments(True, _state(home_score=80, away_score=100, clock=90.0))
        assert adj.three_rate_override == 0.0

    def test_trailing_team_shifts_three_rate_small_deficit_urgent(self):
        mod = self._mod()
        # Home trails by 3, clock ≤ 60 → +0.08
        adj = mod.get_adjustments(True, _state(home_score=97, away_score=100, clock=45.0))
        assert adj.three_rate_override == pytest.approx(0.08)
        assert adj.tov_prob_delta == pytest.approx(0.02)
        assert adj.pace_multiplier == pytest.approx(0.75)

    def test_trailing_team_medium_deficit_urgent(self):
        mod = self._mod()
        # Home trails by 8, clock ≤ 60 → +0.14
        adj = mod.get_adjustments(True, _state(home_score=92, away_score=100, clock=50.0))
        assert adj.three_rate_override == pytest.approx(0.14)
        assert adj.pace_multiplier == pytest.approx(0.75)

    def test_trailing_team_large_deficit_non_urgent(self):
        mod = self._mod()
        # Home trails by 14, clock 61–150 → +0.12
        adj = mod.get_adjustments(True, _state(home_score=86, away_score=100, clock=100.0))
        assert adj.three_rate_override == pytest.approx(0.12)
        assert adj.pace_multiplier == pytest.approx(0.85)

    def test_leading_team_slows_pace_within_90s(self):
        mod = self._mod()
        # Home leads by 5, is_home=False (away trailing) → home team is leading
        # When is_home=True and home leads → home is offense of leading team
        adj = mod.get_adjustments(True, _state(home_score=105, away_score=100, clock=80.0))
        assert adj.pace_multiplier == pytest.approx(1.15)
        assert adj.shot_prob_delta == pytest.approx(-0.015)

    def test_leading_team_no_pace_change_above_90s(self):
        mod = self._mod()
        # Clock = 120 → leading-team pace guard only triggers ≤ 90s
        adj = mod.get_adjustments(True, _state(home_score=105, away_score=100, clock=120.0))
        # deficit = -5 (home leads) but clock > 90 → no leading-team pace adjustment
        assert adj.pace_multiplier == pytest.approx(1.0)
        assert adj.shot_prob_delta == pytest.approx(0.0)

    def test_away_team_trailing_gets_catch_up(self):
        mod = self._mod()
        # Away trails by 6, is_home=False
        adj = mod.get_adjustments(False, _state(home_score=106, away_score=100, clock=50.0))
        assert adj.three_rate_override == pytest.approx(0.14)
        assert adj.tov_prob_delta == pytest.approx(0.02)

    def test_update_is_noop(self):
        mod = self._mod()
        mod.update({}, True, _state())  # should not raise


# ---------------------------------------------------------------------------
# GarbageTimeModifier
# ---------------------------------------------------------------------------

class TestGarbageTimeModifier:
    def _mod(self, **kwargs):
        return GarbageTimeModifier(_cfg(**kwargs))

    def test_no_effect_before_q3(self):
        mod = self._mod()
        adj = mod.get_adjustments(True, _state(quarter=2, home_score=120, away_score=95, clock=300.0))
        assert adj.shot_prob_delta == 0.0
        assert adj.three_rate_override == 0.0

    def test_no_effect_when_clock_above_threshold(self):
        mod = self._mod()
        # Q4 but 700s remaining → above 600s threshold
        adj = mod.get_adjustments(True, _state(quarter=4, home_score=120, away_score=95, clock=700.0))
        assert adj.three_rate_override == 0.0

    def test_no_effect_when_margin_below_threshold(self):
        mod = self._mod()
        # Margin = 19, threshold = 20
        adj = mod.get_adjustments(True, _state(quarter=4, home_score=119, away_score=100, clock=300.0))
        assert adj.three_rate_override == 0.0
        assert adj.shot_prob_delta == 0.0

    def test_trailing_team_offense_gets_garbage_time_adjustments(self):
        mod = self._mod()
        # Home leads by 25; away trailing, is_home=False (away on offense)
        adj = mod.get_adjustments(False, _state(quarter=4, home_score=125, away_score=100, clock=400.0))
        assert adj.three_rate_override == pytest.approx(0.08)
        # defense_penalty from soft leading-team defense benefits trailing team
        assert adj.defense_penalty_delta == pytest.approx(0.02)
        assert adj.tov_prob_delta == pytest.approx(0.01)
        assert adj.pace_multiplier == pytest.approx(1.0)  # no pace change

    def test_leading_team_offense_softens(self):
        mod = self._mod()
        # Home leads by 25; home on offense (is_home=True)
        adj = mod.get_adjustments(True, _state(quarter=4, home_score=125, away_score=100, clock=400.0))
        assert adj.shot_prob_delta == pytest.approx(-0.02)
        assert adj.three_rate_override == pytest.approx(0.0)
        assert adj.defense_penalty_delta == pytest.approx(0.0)  # defense penalty only on trailing team's turn

    def test_activates_in_q3(self):
        mod = self._mod()
        adj = mod.get_adjustments(True, _state(quarter=3, home_score=95, away_score=120, clock=300.0))
        # Away leads by 25 — home trailing in Q3
        assert adj.three_rate_override == pytest.approx(0.08)

    def test_exactly_at_margin_threshold(self):
        mod = self._mod()
        adj = mod.get_adjustments(False, _state(quarter=4, home_score=120, away_score=100, clock=400.0))
        assert adj.three_rate_override == pytest.approx(0.08)

    def test_update_is_noop(self):
        mod = self._mod()
        mod.update({}, True, _state())


# ---------------------------------------------------------------------------
# ModifierAdjustments additions
# ---------------------------------------------------------------------------

class TestModifierAdjustmentsM3c:
    def test_three_rate_override_additive(self):
        a = ModifierAdjustments(three_rate_override=0.08)
        b = ModifierAdjustments(three_rate_override=0.05)
        result = a + b
        assert result.three_rate_override == pytest.approx(0.13)

    def test_pace_multiplier_compounds(self):
        a = ModifierAdjustments(pace_multiplier=0.75)
        b = ModifierAdjustments(pace_multiplier=1.15)
        result = a + b
        assert result.pace_multiplier == pytest.approx(0.75 * 1.15)

    def test_default_pace_multiplier_is_neutral(self):
        a = ModifierAdjustments(pace_multiplier=0.85)
        b = ModifierAdjustments()  # pace_multiplier=1.0
        result = a + b
        assert result.pace_multiplier == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# DRAMA_M3 preset sanity
# ---------------------------------------------------------------------------

class TestDramaM3Preset:
    def test_drama_m3_has_all_m3c_toggles(self):
        # use_catch_up was superseded by use_team_objectives (gap 3.1); garbage time stays
        assert DRAMA_M3.use_team_objectives is True
        assert DRAMA_M3.use_catch_up is False
        assert DRAMA_M3.use_garbage_time is True

    def test_drama_m3_inherits_m2_toggles(self):
        assert DRAMA_M3.use_momentum is True
        assert DRAMA_M3.use_fatigue is True
        assert DRAMA_M3.use_clutch is True
