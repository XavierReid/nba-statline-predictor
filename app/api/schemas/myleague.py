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
    """One player in a NextGameCard rotation preview — top-8 by MPG.

    ppg/rpg/apg populated from PlayerSeasonStats when available; None for
    players with no real-season row (rookies etc.). These are real-season
    reference stats; the MyLeague running-averages story is deferred to a
    dedicated design session.
    """
    player_id: int
    name: str
    position: str
    mpg: float
    is_starter: bool
    ppg: Optional[float] = None
    rpg: Optional[float] = None
    apg: Optional[float] = None


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


class PlayerMyLeagueStatsBlock(BaseModel):
    """Per-team split for a MyLeague player.

    Sim aggregate is `PlayerMyLeagueStats` — this is one team's slice.
    Rate stats derived from that team's totals (totals-first, never
    average-of-averages).
    """
    team_abbr: str
    gp: int
    mpg: float
    ppg: float
    rpg: float
    apg: float
    spg: float
    bpg: float
    topg: float
    fg_pct: Optional[float] = None      # None on zero attempts
    fg3_pct: Optional[float] = None
    ft_pct: Optional[float] = None


class PlayerMyLeagueSim(BaseModel):
    """Sim-side block for GET /myleague/{sim_id}/player/{player_id}.

    Derived from SimulatedPlayerLine rows at request time — no cache,
    fully replayable. `by_team` preserves per-team splits so a future
    "career-in-MyLeague split view" is a UI change, not a backend
    rewrite. Aggregate rates are computed totals-first, never as an
    average of team-level averages.
    """
    gp: int                             # distinct games this player appeared in
    team_gp: int                        # games played by teams this player was rostered on
    mpg: float
    ppg: float
    rpg: float
    apg: float
    spg: float
    bpg: float
    topg: float
    fg_pct: Optional[float] = None
    fg3_pct: Optional[float] = None
    ft_pct: Optional[float] = None
    by_team: list[PlayerMyLeagueStatsBlock]


class PlayerMyLeagueReal(BaseModel):
    """Real-season reference — same season as the MyLeague, no substitution."""
    gp: int
    mpg: float
    ppg: float
    rpg: float
    apg: float
    spg: float
    bpg: float
    topg: float
    fg_pct: Optional[float] = None
    fg3_pct: Optional[float] = None
    ft_pct: Optional[float] = None


class PlayerMyLeagueStatsResponse(BaseModel):
    """Response for GET /myleague/{sim_id}/player/{player_id}.

    Sim is the primary reality; real is reference/context. Never blended
    — the UI decides ordering/emphasis but the data contract keeps them
    separate. `real` is null when the player has no PlayerSeasonStats
    row for the MyLeague's season (rookies, retired, un-ingested).
    """
    player_id: int
    name: str
    season: str
    sim: PlayerMyLeagueSim
    real: Optional[PlayerMyLeagueReal] = None


class TeamDrillInRecord(BaseModel):
    """Team record + splits derived from the sim's persisted games."""
    wins: int
    losses: int
    pct: float
    streak: str          # "W3", "L2", "-" if no games
    home_wins: int
    home_losses: int
    away_wins: int
    away_losses: int
    ppg_scored: float
    ppg_allowed: float


class TeamDrillInRosterPlayer(BaseModel):
    """One row in the M-3 team roster panel.

    Per the statistics contract lock: MyLeague stats are primary when
    meaningful sim history exists; real reference is used when the
    player has no sim GP yet. The UI reads `sim` and `real` and picks
    which world to render per row (never blends silently).

    Availability is folded from the MyLeague event log as of the sim's
    current_calendar_date. M-4 turns it into an interactive control.
    """
    player_id: int
    name: str
    position: str
    is_starter: bool           # derived from real-season MPG rank (top 5)
    availability: str          # "AVAILABLE" | "OUT"
    # M-5b: reason + return date when player is OUT (nulls otherwise).
    # `out_reason` = 'injury' / 'user'; `out_return_date` populated only
    # for injury OUTs (from the paired recovery event's date).
    out_reason: Optional[str] = None
    out_return_date: Optional[date] = None
    sim: Optional[PlayerMyLeagueSim] = None
    real: Optional[PlayerMyLeagueReal] = None


class TeamDrillInResponse(BaseModel):
    """GET /myleague/{sim_id}/team/{team_abbr} — the M-3 read-only surface.

    Roster is centerpiece; record + recent_games provide framing.
    Roster-at-date semantics: as_of_date = the sim's current cursor;
    when M-6 trades ship, the underlying roster resolution will filter
    by trade-event dates. MVP is time-invariant (no trades).
    """
    team_id: int
    team_abbr: str
    team_city: str
    team_nickname: str
    as_of_date: date
    record: TeamDrillInRecord
    roster: list[TeamDrillInRosterPlayer]
    recent_games: list[RecentGameRow]


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
