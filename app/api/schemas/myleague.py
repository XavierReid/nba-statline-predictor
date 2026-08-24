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
    total_games: int                        # season's scheduled game count
    status: str                             # 'running' | 'complete' | ...


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


class UpcomingGameRow(BaseModel):
    """Next scheduled game for the controlled team (M-2 preview).

    Same shape as RecentGameRow minus scores/OT since these haven't been
    simulated yet. Only populated when the run has a controlled team.
    """
    game_id: str
    game_date: date
    home_team: str
    away_team: str


class PreviewRosterPlayer(BaseModel):
    """One player in a NextGameCard rotation preview — top-8 by MPG."""
    player_id: int
    name: str
    position: str
    mpg: float
    is_starter: bool


class NextGamePreview(BaseModel):
    """Full pre-game context for the controlled team's next game (M-2).

    Populated only when there's an unsimulated game ahead for the
    controlled team. Series counts reflect only games between the two
    teams that have ALREADY been simulated in this run.
    """
    game_id: str
    game_date: date
    is_home: bool                           # controlled team is home
    opponent_abbr: str
    matchup_index: int                      # Nth meeting this season (1-based)
    matchup_total: int                      # total meetings scheduled
    series_wins_controlled: int             # games won so far in this series
    series_wins_opponent: int
    controlled_roster: list[PreviewRosterPlayer]
    opponent_roster: list[PreviewRosterPlayer]


class MyLeagueSummaryResponse(BaseModel):
    """GET /myleague/{id} — full hydration for the front-page dashboard.

    Combines base state + league standings (30 rows) + last-N completed
    games + next-N upcoming games for the controlled team. Deliberately
    caps at 10 recent + 5 upcoming; a full team drill-in would use a
    dedicated endpoint (deferred).
    """
    state: MyLeagueStateResponse
    standings: list[StandingsRow]
    recent_games: list[RecentGameRow]
    upcoming_games: list[UpcomingGameRow]
    next_game_preview: Optional[NextGamePreview] = None
