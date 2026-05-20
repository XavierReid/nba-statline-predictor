"""Pydantic models for prediction API responses."""

from datetime import date
from pydantic import BaseModel, Field


class PredictedStatline(BaseModel):
    points: float
    rebounds: float
    assists: float
    steals: float
    blocks: float
    turnovers: float
    three_pointers_made: float


class PredictionFactors(BaseModel):
    """Transparent breakdown of how the prediction was computed."""
    recent_games_window: int
    recent_avg_points: float | None
    season_avg_points: float | None
    vs_opponent_avg_points: float | None
    home_away_adjustment: float
    rest_days: int | None
    rest_adjustment: float
    opponent_def_rating: float | None
    opponent_def_adjustment: float
    weights_used: dict[str, float]


class PredictionResponse(BaseModel):
    player_id: int
    player_name: str
    game_id: int
    game_date: date
    is_home: bool
    predicted: PredictedStatline
    factors: PredictionFactors = Field(
        ..., description="Per-stat factor breakdown — for now we expose the points-level factors as a representative sample."
    )
