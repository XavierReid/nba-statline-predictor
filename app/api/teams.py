from fastapi import APIRouter, Depends
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.player_season_stats import PlayerSeasonStats
from app.models.team import Team
from app.services.franchise import team_identity

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("")
def list_teams(season: str, db: Session = Depends(get_db)):
    """Teams active in `season`, with season-accurate identity (e.g. 'SEA' for the
    2005-06 SuperSonics). Membership comes from PlayerSeasonStats; the current season's
    rosters key off Player.team_id instead, so fall back to the full teams table then."""
    ids = [tid for (tid,) in db.execute(
        select(distinct(PlayerSeasonStats.team_id)).where(
            PlayerSeasonStats.season == season,
            PlayerSeasonStats.team_id.isnot(None),
        )
    ).all()]
    q = select(Team).where(Team.id.in_(ids)) if ids else select(Team)
    out = []
    for t in db.execute(q).scalars().all():
        city, nick, abbr = team_identity(t.id, season, (t.city, t.nickname, t.abbreviation))
        out.append({"id": t.id, "abbreviation": abbr, "city": city, "nickname": nick})
    out.sort(key=lambda x: x["city"])
    return out
