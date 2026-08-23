"""Pydantic schemas for the MyLeague HTTP surface (M-1b).

Thin wrappers over the engine layer in app/services/myleague_engine.py.
Request shapes mirror engine function signatures; response shapes hydrate
enough state for the UI to render a between-games surface without further
round-trips (state + standings + recent games).
"""
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel

from app.api.schemas.simulations import SimConfigRequest, StandingsRow


# --- Requests ---------------------------------------------------------------

class CreateMyLeagueRequest(BaseModel):
    season: str
    seed: Optional[int] = None                  # random if omitted
    controlled_team_id: Optional[int] = None    # None = God mode (future)
    config: Optional[SimConfigRequest] = None


class AdvanceRequest(BaseModel):
    target_date: date


class AppendEventRequest(BaseModel):
    event_type: str                             # SET_UNAVAILABLE / SET_AVAILABLE
    applied_at_date: date
    payload: dict[str, Any]                     # {team_id, player_id}


# --- Responses --------------------------------------------------------------

class MyLeagueStateResponse(BaseModel):
    """Base state block — always present in every MyLeague response."""
    simulation_id: int
    season: str
    root_seed: int
    controlled_team_id: Optional[int]
    controlled_team_abbr: Optional[str]     # NULL only if no controlled team
    current_calendar_date: date
    games_completed: int


class MyLeagueEventResponse(BaseModel):
    id: int
    event_type: str
    applied_at_date: date
    payload: dict[str, Any]
    created_at: datetime


class RecentGameRow(BaseModel):
    """Compact summary for the between-games surface's "recent results" list."""
    game_id: str
    game_date: date
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    went_to_ot: bool


class MyLeagueSummaryResponse(BaseModel):
    """GET /myleague/{id} — full hydration for the front-page dashboard.

    Combines base state + league standings (30 rows) + last-N completed
    games. Deliberately caps at 10 recent games; a full team drill-in
    would use a dedicated endpoint (out of scope for M-1b).
    """
    state: MyLeagueStateResponse
    standings: list[StandingsRow]
    recent_games: list[RecentGameRow]
