"""Single-prediction endpoint. The interesting one for interview demos."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Game, Player
from app.services.predictor import GameContext, StatHistory, predict_statline

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/{player_id}/{game_id}")
def predict(player_id: int, game_id: int, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    # TODO: implement these from real data.
    history = StatHistory(recent_avg=None, season_avg=None, vs_opponent_avg=None)
    is_home = (player.team_id == game.home_team_id) if player.team_id else False
    context = GameContext(is_home=is_home, rest_days=None, opponent_def_rating=None)

    result = predict_statline(history, context)
    if result is None:
        raise HTTPException(status_code=422, detail="No usable history for this player yet.")

    predicted, factors = result
    return {
        "player_id": player.id,
        "player_name": player.full_name,
        "game_id": game.id,
        "game_date": game.game_date.isoformat(),
        "is_home": is_home,
        "predicted": predicted,
        "factors": factors,
    }
