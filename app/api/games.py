from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Game

router = APIRouter(prefix="/games", tags=["games"])


@router.get("/{game_id}")
def get_game(game_id: int, db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return {
        "id": game.id,
        "date": game.game_date.isoformat(),
        "home_team": game.home_team.abbreviation,
        "away_team": game.away_team.abbreviation,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "status": game.status.value,
    }
