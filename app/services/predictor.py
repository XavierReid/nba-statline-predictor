"""The heuristic predictor.

Pure functions — no I/O, no DB. Take stat histories in, return predicted statlines out.
This makes the predictor trivially unit-testable.

The data-loading happens in app/api/predictions.py, which calls into here.
"""

from dataclasses import dataclass

from app.config import settings


# Stats we predict. Add more as desired (e.g., minutes, fouls).
STATS = ("points", "rebounds", "assists", "steals", "blocks", "turnovers", "three_pointers_made")


@dataclass
class StatHistory:
    """Aggregate stat averages for a player over different historical windows."""
    recent_avg: dict[str, float] | None    # avg over last N games (None if no games)
    season_avg: dict[str, float] | None    # avg over current season
    vs_opponent_avg: dict[str, float] | None  # career avg vs the upcoming opponent


@dataclass
class GameContext:
    """Context for the upcoming game that adjusts the baseline prediction."""
    is_home: bool
    rest_days: int | None              # days since player's last game; None if unknown
    opponent_def_rating: float | None  # opponent's current defensive rating
    league_avg_def_rating: float = 112.0  # rough league average baseline


def normalize_weights(w_recent: float, w_season: float, w_vs_opp: float,
                      have_recent: bool, have_season: bool, have_vs_opp: bool) -> dict[str, float]:
    """Re-distribute weights when one or more components are missing.

    Example: if a player has no career history vs this opponent (rookie, new team),
    we throw away that component and re-normalize the other two.
    """
    raw = {
        "recent": w_recent if have_recent else 0.0,
        "season": w_season if have_season else 0.0,
        "vs_opponent": w_vs_opp if have_vs_opp else 0.0,
    }
    total = sum(raw.values())
    if total == 0:
        # No history at all — caller should handle this and probably return None.
        return raw
    return {k: v / total for k, v in raw.items()}


def home_away_adjustment(is_home: bool) -> float:
    return settings.predictor_home_adj if is_home else settings.predictor_away_adj


def rest_adjustment(rest_days: int | None) -> float:
    if rest_days is None:
        return 1.0
    if rest_days == 0:
        return settings.predictor_rest_back_to_back
    if rest_days == 1:
        return settings.predictor_rest_one_day
    return settings.predictor_rest_two_plus


def opponent_defense_adjustment(opp_def_rating: float | None,
                                league_avg: float = 112.0) -> float:
    """Better opponent defense → lower predicted output.

    Multiplier = league_avg / opp_def_rating.
    A team allowing 105 pts/100 (great defense) → 112/105 = 1.067 inverse → we want to *reduce*,
    so we use 1 / (that), i.e. opp_def_rating / league_avg < 1 for elite defenses.
    """
    if opp_def_rating is None or opp_def_rating <= 0:
        return 1.0
    return opp_def_rating / league_avg


def predict_statline(history: StatHistory, context: GameContext) -> tuple[dict[str, float], dict] | None:
    """Compute the predicted statline + factor breakdown.

    Returns (predicted_dict, factors_dict) or None if there is no usable history.
    """
    weights = normalize_weights(
        settings.predictor_w_recent,
        settings.predictor_w_season,
        settings.predictor_w_vs_opponent,
        have_recent=history.recent_avg is not None,
        have_season=history.season_avg is not None,
        have_vs_opp=history.vs_opponent_avg is not None,
    )

    if sum(weights.values()) == 0:
        return None  # no history at all

    home_away_mult = home_away_adjustment(context.is_home)
    rest_mult = rest_adjustment(context.rest_days)
    def_mult = opponent_defense_adjustment(context.opponent_def_rating, context.league_avg_def_rating)

    predicted: dict[str, float] = {}
    for stat in STATS:
        baseline = (
            weights["recent"]      * (history.recent_avg.get(stat, 0.0)      if history.recent_avg      else 0.0)
            + weights["season"]    * (history.season_avg.get(stat, 0.0)      if history.season_avg      else 0.0)
            + weights["vs_opponent"] * (history.vs_opponent_avg.get(stat, 0.0) if history.vs_opponent_avg else 0.0)
        )
        predicted[stat] = round(baseline * home_away_mult * rest_mult * def_mult, 2)

    factors = {
        "recent_games_window": settings.predictor_recent_games_window,
        "recent_avg_points": history.recent_avg.get("points") if history.recent_avg else None,
        "season_avg_points": history.season_avg.get("points") if history.season_avg else None,
        "vs_opponent_avg_points": history.vs_opponent_avg.get("points") if history.vs_opponent_avg else None,
        "home_away_adjustment": home_away_mult,
        "rest_days": context.rest_days,
        "rest_adjustment": rest_mult,
        "opponent_def_rating": context.opponent_def_rating,
        "opponent_def_adjustment": def_mult,
        "weights_used": weights,
    }

    return predicted, factors
