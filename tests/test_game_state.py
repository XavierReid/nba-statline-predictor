"""Tests for GameState — persistent simulation state (roadmap stage B)."""
from app.services.game_state import GameState


class TestGameState:
    def test_defaults(self):
        gs = GameState()
        assert gs.home_score == 0 and gs.away_score == 0
        assert gs.possession_number == 0 and gs.period_index == 0
        assert gs.game_clock == 0.0
        assert gs.home_conceded is False and gs.away_conceded is False
        assert gs.quarter_scores == {"home": [0, 0, 0, 0], "away": [0, 0, 0, 0]}

    def test_quarter_scores_are_independent_per_instance(self):
        a, b = GameState(), GameState()
        a.quarter_scores["home"][0] = 30
        assert b.quarter_scores["home"][0] == 0  # no shared mutable default

    def test_margin_home_perspective(self):
        gs = GameState(home_score=110, away_score=100)
        assert gs.margin == 10 and gs.abs_margin == 10
        assert gs.leading_is_home is True and gs.is_tied is False

    def test_margin_when_away_leads(self):
        gs = GameState(home_score=98, away_score=104)
        assert gs.margin == -6 and gs.abs_margin == 6
        assert gs.leading_is_home is False

    def test_is_tied(self):
        assert GameState(home_score=100, away_score=100).is_tied is True

    def test_is_final_period(self):
        assert GameState(period_index=2).is_final_period is False   # Q3
        assert GameState(period_index=3).is_final_period is True    # Q4
        assert GameState(period_index=4).is_final_period is True    # OT

    def test_offense_margin_perspective(self):
        gs = GameState(home_score=110, away_score=100)  # home +10
        assert gs.offense_margin(is_home=True) == 10
        assert gs.offense_margin(is_home=False) == -10

    def test_attribute_mutation(self):
        gs = GameState()
        gs.home_score += 3
        gs.possession_number += 1
        gs.home_conceded = True
        assert gs.home_score == 3 and gs.possession_number == 1
        assert gs.margin == 3 and gs.home_conceded is True
