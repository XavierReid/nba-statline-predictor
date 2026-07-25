from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Player
from app.models.player_attributes import PlayerAttributes
from app.models.player_season_stats import PlayerSeasonStats
from app.models.team import Team
from app.services.franchise import team_identity

router = APIRouter(prefix="/players", tags=["players"])

# Curated ratings surfaced in the player-detail modal (0-100). overall_rating is DISPLAY-ONLY
# here (guardrail #1 — never a sim input); the modal renames it `overall`/`clutch` for the UI.
_RATING_KEYS = [
    "overall_rating", "three_point", "mid_range", "layup", "passing", "ball_handle",
    "perimeter_defense", "interior_defense", "offensive_rebound", "defensive_rebound",
    "clutch_rating",
]
_RATING_ALIAS = {"overall_rating": "overall", "clutch_rating": "clutch"}


@router.get("/{player_id}")
def get_player(player_id: int, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return {
        "id": player.id,
        "full_name": player.full_name,
        "team": player.team.abbreviation if player.team else None,
        "position": player.position,
    }


@router.get("/{player_id}/profile")
def get_player_profile(player_id: int, season: str, db: Session = Depends(get_db)):
    """Player/season profile for the detail modal: real season averages + derived ratings.

    Deliberately does NOT return the game line or play-by-play — those are game-specific and
    already on the simulation response the frontend holds. Keeps the API boundary clean:
    profile = player/season data, simulation response = game data.
    """
    player = db.get(Player, player_id)
    stats = db.execute(
        select(PlayerSeasonStats).where(
            PlayerSeasonStats.player_id == player_id,
            PlayerSeasonStats.season == season,
        )
    ).scalar_one_or_none()
    if player is None or stats is None:
        raise HTTPException(status_code=404, detail=f"No profile for player {player_id} in {season}")

    attrs = db.execute(
        select(PlayerAttributes).where(
            PlayerAttributes.player_id == player_id,
            PlayerAttributes.season == season,
        )
    ).scalar_one_or_none()

    team_id = stats.team_id or player.team_id
    team_abbr = None
    if team_id:
        team = db.get(Team, team_id)
        if team:
            _, _, team_abbr = team_identity(team.id, season, (team.city, team.nickname, team.abbreviation))

    def rnd(x: Optional[float]) -> Optional[float]:
        return round(x, 3) if x is not None else None

    return {
        "id": player.id,
        "full_name": player.full_name,
        "position": player.position,
        "team": team_abbr,
        "season": season,
        "season_averages": {
            "gp": stats.games_played,
            "min": stats.minutes_per_game,
            "pts": stats.points,
            "reb": stats.rebounds,
            "ast": stats.assists,
            "stl": stats.steals,
            "blk": stats.blocks,
            "tov": stats.turnovers,
            "fg_pct": rnd(stats.fg_pct),
            "fg3_pct": rnd(stats.fg3_pct),
            "ft_pct": rnd(stats.ft_pct),
        },
        "ratings": {
            _RATING_ALIAS.get(k, k): getattr(attrs, k) for k in _RATING_KEYS
        } if attrs else {},
    }


@router.get("/{player_id}/history")
def get_player_history(player_id: int, vs_team: Optional[int] = None, limit: int = 20,
                       db: Session = Depends(get_db)):
    """Recent box scores for a player. Optional filter by opponent team_id."""
    # TODO: implement filtering by vs_team and joining games for opponent context.
    return {"player_id": player_id, "vs_team": vs_team, "limit": limit, "results": []}
