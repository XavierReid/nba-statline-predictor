"""Tests for M2c modifiers: FatigueModifier, FoulTroubleModifier, ClutchModifier."""
import pytest

from app.services.modifiers.base import GameState, ModifierAdjustments, PlayerGameState
from app.services.modifiers.fatigue import FatigueModifier, FATIGUE_THRESHOLD_MINUTES, MAX_SHOT_PENALTY
from app.services.modifiers.foul_trouble import FoulTroubleModifier, FOUL_TROUBLE_THRESHOLD, MAX_DEFENSE_PENALTY
from app.services.modifiers.clutch import ClutchModifier, CLUTCH_CLOCK_THRESHOLD, CLUTCH_POINT_DIFF


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeCfg:
    pass


def _gs(
    home_score: int = 100,
    away_score: int = 100,
    quarter: int = 2,
    clock_seconds: float = 400.0,
    home_players: dict = None,
    away_players: dict = None,
) -> GameState:
    return GameState(
        home_score=home_score,
        away_score=away_score,
        quarter=quarter,
        clock_seconds=clock_seconds,
        possession_number=1,
        home_players=home_players or {},
        away_players=away_players or {},
    )


def _pgs(player_id: int = 1, minutes: float = 0.0, fouls: int = 0, clutch: int = 50) -> PlayerGameState:
    return PlayerGameState(player_id=player_id, minutes_played=minutes, fouls=fouls, clutch_rating=clutch)


# ---------------------------------------------------------------------------
# FatigueModifier
# ---------------------------------------------------------------------------

class TestFatigueModifier:
    def setup_method(self):
        self.mod = FatigueModifier(_FakeCfg())

    def test_no_penalty_below_threshold(self):
        gs = _gs(home_players={1: _pgs(1, minutes=20.0)})
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.shot_prob_delta == 0.0

    def test_penalty_above_threshold(self):
        gs = _gs(home_players={1: _pgs(1, minutes=FATIGUE_THRESHOLD_MINUTES + 5.0)})
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.shot_prob_delta < 0.0

    def test_penalty_grows_with_minutes(self):
        low_min = FATIGUE_THRESHOLD_MINUTES + 3.0
        high_min = FATIGUE_THRESHOLD_MINUTES + 10.0
        adj_low = self.mod.get_adjustments(is_home=True, game_state=_gs(home_players={1: _pgs(1, low_min)}))
        adj_high = self.mod.get_adjustments(is_home=True, game_state=_gs(home_players={1: _pgs(1, high_min)}))
        assert adj_high.shot_prob_delta < adj_low.shot_prob_delta

    def test_penalty_capped_at_max(self):
        gs = _gs(home_players={1: _pgs(1, minutes=60.0)})
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.shot_prob_delta >= MAX_SHOT_PENALTY  # penalty capped, not exceeded

    def test_no_penalty_empty_lineup(self):
        gs = _gs()
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.shot_prob_delta == 0.0

    def test_applies_to_offensive_side_only(self):
        # Home on offense, high away fatigue → no penalty to home offense
        gs = _gs(away_players={1: _pgs(1, minutes=45.0)})
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.shot_prob_delta == 0.0

    def test_update_is_noop(self):
        self.mod.update({}, is_home=True, game_state=_gs())  # should not raise


# ---------------------------------------------------------------------------
# FoulTroubleModifier
# ---------------------------------------------------------------------------

class TestFoulTroubleModifier:
    def setup_method(self):
        self.mod = FoulTroubleModifier(_FakeCfg())

    def test_no_penalty_without_foul_trouble(self):
        # Home on offense, away has no foul trouble
        gs = _gs(away_players={1: _pgs(1, fouls=2)})
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.defense_penalty_delta == 0.0

    def test_penalty_when_defender_in_foul_trouble(self):
        gs = _gs(away_players={1: _pgs(1, fouls=FOUL_TROUBLE_THRESHOLD)})
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.defense_penalty_delta > 0.0

    def test_penalty_scales_with_trouble_count(self):
        gs_one = _gs(away_players={1: _pgs(1, fouls=FOUL_TROUBLE_THRESHOLD)})
        gs_two = _gs(away_players={
            1: _pgs(1, fouls=FOUL_TROUBLE_THRESHOLD),
            2: _pgs(2, fouls=FOUL_TROUBLE_THRESHOLD),
        })
        adj_one = self.mod.get_adjustments(is_home=True, game_state=gs_one)
        adj_two = self.mod.get_adjustments(is_home=True, game_state=gs_two)
        assert adj_two.defense_penalty_delta > adj_one.defense_penalty_delta

    def test_q4_escalation(self):
        gs_q2 = _gs(quarter=2, away_players={1: _pgs(1, fouls=FOUL_TROUBLE_THRESHOLD)})
        gs_q4 = _gs(quarter=4, away_players={1: _pgs(1, fouls=FOUL_TROUBLE_THRESHOLD)})
        adj_q2 = self.mod.get_adjustments(is_home=True, game_state=gs_q2)
        adj_q4 = self.mod.get_adjustments(is_home=True, game_state=gs_q4)
        assert adj_q4.defense_penalty_delta > adj_q2.defense_penalty_delta

    def test_penalty_capped(self):
        gs = _gs(away_players={i: _pgs(i, fouls=6) for i in range(5)})
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.defense_penalty_delta <= MAX_DEFENSE_PENALTY * 1.25 + 1e-9

    def test_looks_at_defensive_team(self):
        # Away on offense (is_home=False) → look at HOME team's foul trouble
        gs = _gs(home_players={1: _pgs(1, fouls=FOUL_TROUBLE_THRESHOLD)})
        adj = self.mod.get_adjustments(is_home=False, game_state=gs)
        assert adj.defense_penalty_delta > 0.0

    def test_update_is_noop(self):
        self.mod.update({}, is_home=True, game_state=_gs())


# ---------------------------------------------------------------------------
# ClutchModifier
# ---------------------------------------------------------------------------

class TestClutchModifier:
    def setup_method(self):
        self.mod = ClutchModifier(_FakeCfg())

    def _clutch_gs(self, home_score: int = 100, away_score: int = 100, home_clutch: int = 75) -> GameState:
        return _gs(
            home_score=home_score,
            away_score=away_score,
            quarter=4,
            clock_seconds=60.0,
            home_players={1: _pgs(1, clutch=home_clutch)},
            away_players={2: _pgs(2, clutch=50)},
        )

    def test_no_adjustment_outside_clutch_window(self):
        gs = _gs(quarter=2, clock_seconds=400.0, home_players={1: _pgs(1, clutch=90)})
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.shot_prob_delta == 0.0
        assert adj.tov_prob_delta == 0.0

    def test_no_adjustment_blowout_game(self):
        # Within Q4 but not a close game
        gs = _gs(quarter=4, clock_seconds=60.0,
                 home_score=100, away_score=120,
                 home_players={1: _pgs(1, clutch=90)})
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.shot_prob_delta == 0.0

    def test_no_adjustment_too_much_clock_remaining(self):
        gs = _gs(quarter=4, clock_seconds=CLUTCH_CLOCK_THRESHOLD + 10,
                 home_score=100, away_score=101,
                 home_players={1: _pgs(1, clutch=90)})
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.shot_prob_delta == 0.0

    def test_positive_boost_above_average_clutch(self):
        gs = self._clutch_gs(home_clutch=80)
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.shot_prob_delta > 0.0
        assert adj.tov_prob_delta < 0.0

    def test_neutral_at_average_clutch(self):
        gs = self._clutch_gs(home_clutch=50)
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.shot_prob_delta == pytest.approx(0.0, abs=1e-9)

    def test_negative_below_average_clutch(self):
        gs = self._clutch_gs(home_clutch=20)
        adj = self.mod.get_adjustments(is_home=True, game_state=gs)
        assert adj.shot_prob_delta < 0.0
        assert adj.tov_prob_delta > 0.0

    def test_higher_clutch_rating_gives_larger_boost(self):
        gs_high = self._clutch_gs(home_clutch=90)
        gs_low = self._clutch_gs(home_clutch=60)
        adj_high = self.mod.get_adjustments(is_home=True, game_state=gs_high)
        adj_low = self.mod.get_adjustments(is_home=True, game_state=gs_low)
        assert adj_high.shot_prob_delta > adj_low.shot_prob_delta

    def test_away_team_clutch_applied_when_away_on_offense(self):
        gs = _gs(
            quarter=4, clock_seconds=60.0,
            home_score=100, away_score=100,
            home_players={1: _pgs(1, clutch=50)},
            away_players={2: _pgs(2, clutch=90)},
        )
        adj = self.mod.get_adjustments(is_home=False, game_state=gs)
        assert adj.shot_prob_delta > 0.0

    def test_update_is_noop(self):
        self.mod.update({}, is_home=True, game_state=_gs())
