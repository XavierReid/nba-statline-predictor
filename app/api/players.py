from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Player

router = APIRouter(prefix="/players", tags=["players"])


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


@router.get("/{player_id}/history")
def get_player_history(player_id: int, vs_team: Optional[int] = None, limit: int = 20,
                       db: Session = Depends(get_db)):
    """Recent box scores for a player. Optional filter by opponent team_id."""
    # TODO: implement filtering by vs_team and joining games for opponent context.
    return {"player_id": player_id, "vs_team": vs_team, "limit": limit, "results": []}
