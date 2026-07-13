"""Pydantic schemas for simulation endpoints."""
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.services.sim_config import DRAMA_M3, DRAMA_M3_NO_SUBTYPES, SimConfig


_PRESETS: dict = {
    "baseline": SimConfig(),
    "drama-m3": DRAMA_M3,
    "drama-m3-no-subtypes": DRAMA_M3_NO_SUBTYPES,
}


def resolve_config(req: Optional["SimConfigRequest"]) -> SimConfig:
    if req is None:
        return SimConfig()
    cfg = _PRESETS.get(req.preset)
    if cfg is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset '{req.preset}'. Valid options: {list(_PRESETS.keys())}",
        )
    if req.overrides:
        from dataclasses import replace
        overrides = {k: v for k, v in req.overrides.model_dump().items() if v is not None}
        cfg = replace(cfg, **overrides)
    return cfg


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class SimConfigOverrides(BaseModel):
    use_second_chance: Optional[bool] = None
    use_fast_break: Optional[bool] = None
    use_team_defense: Optional[bool] = None
    use_strategic_foul: Optional[bool] = None
    use_momentum: Optional[bool] = None
    use_fatigue: Optional[bool] = None
    use_foul_trouble: Optional[bool] = None
    use_clutch: Optional[bool] = None
    use_player_variance: Optional[bool] = None
    use_team_oreb: Optional[bool] = None
    use_catch_up: Optional[bool] = None
    use_garbage_time: Optional[bool] = None
    use_shot_subtypes: Optional[bool] = None
    use_contest_model: Optional[bool] = None
    use_positional_matchups: Optional[bool] = None
    use_foul_drawing: Optional[bool] = None
    foul_draw_scale: Optional[float] = Field(None, ge=0.1, le=2.0)
    use_endgame_pacing: Optional[bool] = None
    use_garbage_rotation: Optional[bool] = None
    use_lineup_quality: Optional[bool] = None
    use_team_objectives: Optional[bool] = None
    use_catch_up: Optional[bool] = None
    signal_gain: Optional[float] = Field(None, ge=0.5, le=3.0)
    oreb_chain_cap: Optional[int] = Field(None, ge=1, le=10)
    strategic_foul_probability: Optional[float] = Field(None, ge=0.0, le=1.0)
    momentum_max: Optional[float] = Field(None, ge=0.0, le=0.20)
    momentum_decay_rate: Optional[float] = Field(None, ge=0.0, le=1.0)


class SimConfigRequest(BaseModel):
    preset: str = Field("baseline", description="Named preset: 'baseline', 'drama-m3', or 'drama-m3-no-subtypes'")
    overrides: Optional[SimConfigOverrides] = Field(None, description="Override individual fields on top of the preset")


class SimulateGameRequest(BaseModel):
    home_team: str = Field(..., description="Team abbreviation, e.g. 'DEN'")
    away_team: str = Field(..., description="Team abbreviation, e.g. 'GSW'")
    season: str = Field(..., description="Season string, e.g. '2024-25'")
    seed: Optional[int] = Field(None, description="RNG seed for reproducibility. Omit for a random game.")
    config: Optional[SimConfigRequest] = Field(None, description="Simulation config. Omit for baseline.")
    include_pbp: bool = Field(False, description="Include full play-by-play in the response.")


class StepThroughRequest(BaseModel):
    home_team: str = Field(..., description="Team abbreviation, e.g. 'DEN'")
    away_team: str = Field(..., description="Team abbreviation, e.g. 'GSW'")
    season: str = Field(..., description="Season string, e.g. '2024-25'")
    seed: Optional[int] = Field(None, description="RNG seed for reproducibility.")
    steps: int = Field(4, ge=1, le=200, description="Number of steps to split the game into. Default 4 (quarters).")
    config: Optional[SimConfigRequest] = Field(None, description="Simulation config. Omit for baseline.")


class CreateSimulationRequest(BaseModel):
    team: str = Field(..., description="Team abbreviation, e.g. 'BOS'")
    season: str = Field(..., description="Season string, e.g. '2025-26'")
    seed: Optional[int] = Field(None, description="Master RNG seed. Omit for random.")
    config: Optional[SimConfigRequest] = Field(None, description="Simulation config. Omit for baseline.")


class StartSimulationRequest(BaseModel):
    config: Optional[SimConfigRequest] = Field(None, description="Simulation config. Omit for baseline.")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class PlayerLine(BaseModel):
    player_id: int
    name: str
    minutes: float
    points: int
    rebounds: int
    assists: int
    steals: int
    blocks: int
    turnovers: int
    personal_fouls: int
    plus_minus: int
    fgm: int
    fga: int
    fg3m: int
    fg3a: int
    ftm: int
    fta: int
    fouled_out: bool


class QuarterScores(BaseModel):
    home: list[int]
    away: list[int]


class PossessionEvent(BaseModel):
    possession: int
    game_clock_seconds: int
    quarter: int
    is_home: bool
    pts: int
    running_home_score: Optional[int] = None
    running_away_score: Optional[int] = None
    description: Optional[str] = None
    scorer: Optional[int] = None
    shot_type: Optional[str] = None
    made: Optional[bool] = None
    assisted_by: Optional[int] = None
    rebounded_by: Optional[int] = None
    is_oreb: Optional[bool] = None
    turnover_by: Optional[int] = None
    steal_by: Optional[int] = None
    block_by: Optional[int] = None
    fouled_by: Optional[int] = None
    fta: Optional[int] = None
    ftm: Optional[int] = None


class SimulateGameResponse(BaseModel):
    season: str
    seed: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    quarter_scores: QuarterScores
    home_box: list[PlayerLine]
    away_box: list[PlayerLine]
    events: Optional[list[PossessionEvent]] = None


class StepThroughResponse(BaseModel):
    token: str
    step: int
    total_steps: int
    complete: bool
    elapsed_minutes: float
    quarter: int
    season: str
    seed: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    home_box: list[PlayerLine]
    away_box: list[PlayerLine]


class SimulationCreatedResponse(BaseModel):
    id: int
    team: str
    season: str
    seed: int
    status: str


class SimulatedGameSummary(BaseModel):
    game_id: str
    game_date: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    went_to_ot: bool
    win: bool


class SimulationStatusResponse(BaseModel):
    id: int
    team: str
    season: str
    seed: int
    status: str
    games_completed: int
    total_games: int
    wins: Optional[int] = None
    losses: Optional[int] = None
    created_at: datetime
    completed_at: Optional[datetime]
    games: Optional[list[SimulatedGameSummary]] = None


class SimulationSummary(BaseModel):
    id: int
    team: str
    season: str
    status: str
    games_completed: int
    total_games: int
    wins: Optional[int] = None
    losses: Optional[int] = None
    created_at: datetime
    completed_at: Optional[datetime]
