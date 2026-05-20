"""Pure-function tests for the predictor — no DB required."""

from app.services.predictor import (
    GameContext,
    StatHistory,
    home_away_adjustment,
    normalize_weights,
    opponent_defense_adjustment,
    predict_statline,
    rest_adjustment,
)


def test_normalize_weights_all_present():
    w = normalize_weights(0.5, 0.3, 0.2, True, True, True)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["recent"] == 0.5


def test_normalize_weights_missing_vs_opponent():
    """Rookie facing a team for the first time: weight should redistribute."""
    w = normalize_weights(0.5, 0.3, 0.2, True, True, False)
    assert w["vs_opponent"] == 0
    assert abs(sum(w.values()) - 1.0) < 1e-9
    # Recent and season should grow proportionally
    assert w["recent"] > 0.5
    assert w["season"] > 0.3


def test_home_away_adjustment():
    assert home_away_adjustment(True) > 1.0
    assert home_away_adjustment(False) < 1.0


def test_rest_adjustment_back_to_back_penalty():
    assert rest_adjustment(0) < 1.0
    assert rest_adjustment(1) == 1.0
    assert rest_adjustment(3) > 1.0


def test_opponent_defense_adjustment_elite_defense_lowers_output():
    elite = opponent_defense_adjustment(105.0, league_avg=112.0)
    weak = opponent_defense_adjustment(118.0, league_avg=112.0)
    assert elite < 1.0 < weak


def test_predict_statline_returns_predicted_and_factors():
    history = StatHistory(
        recent_avg={"points": 28.0, "rebounds": 5.0, "assists": 6.0,
                    "steals": 1.0, "blocks": 0.5, "turnovers": 3.0,
                    "three_pointers_made": 4.0},
        season_avg={"points": 26.0, "rebounds": 5.0, "assists": 6.0,
                    "steals": 1.0, "blocks": 0.5, "turnovers": 3.0,
                    "three_pointers_made": 4.0},
        vs_opponent_avg={"points": 30.0, "rebounds": 5.0, "assists": 6.0,
                         "steals": 1.0, "blocks": 0.5, "turnovers": 3.0,
                         "three_pointers_made": 4.0},
    )
    context = GameContext(is_home=True, rest_days=2, opponent_def_rating=110.0)
    result = predict_statline(history, context)
    assert result is not None
    predicted, factors = result
    assert predicted["points"] > 0
    assert factors["weights_used"]["recent"] > 0
    assert factors["home_away_adjustment"] > 1.0


def test_predict_statline_returns_none_with_no_history():
    history = StatHistory(recent_avg=None, season_avg=None, vs_opponent_avg=None)
    context = GameContext(is_home=False, rest_days=None, opponent_def_rating=None)
    assert predict_statline(history, context) is None
