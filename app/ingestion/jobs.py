"""Nightly ingestion job — orchestrates pulls from nba_api and upserts into Postgres.

Designed to be idempotent: running twice for the same season should not duplicate data.
"""

import logging

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ingestion import nba_client
from app.models.game import Game, GameStatus
from app.models.player import Player
from app.models.team import Team

log = logging.getLogger(__name__)


def ingest_teams(db: Session) -> int:
    """Upsert teams. Returns count inserted/updated."""
    teams = nba_client.fetch_all_teams()
    for team in teams:
        existing = db.get(Team, team['id'])
        if existing is not None:
            existing.city = team['city']
            existing.abbreviation = team['abbreviation']
            existing.nickname = team['nickname']
        else:
            db.add(Team(
                id=team['id'],
                city=team['city'],
                abbreviation=team['abbreviation'],
                nickname=team['nickname'],
                conference=None,
                division=None))
        
    return len(teams)


def ingest_active_players(db: Session) -> int:
    """Upsert active players."""
    players = nba_client.fetch_all_active_players()
    for player in players:
        existing = db.get(Player, player['PLAYER_ID'])
        if existing is not None:
            existing.full_name = player['PLAYER']
            existing.team_id = player['TeamID']
            existing.position = player['POSITION']
        else:
            db.add(Player(
                id=player['PLAYER_ID'],
                full_name=player['PLAYER'],
                team_id=player['TeamID'],
                position=player['POSITION']))
        
    return len(players)



def ingest_games_for_season(db: Session, season: str) -> int:
    """Upsert games for a season. Returns count inserted/updated."""
    games = nba_client.fetch_games_for_season(season)
    for game in games:
        existing = db.get(Game, game['id'])
        if existing is not None:
            existing.home_score = game['home_score']
            existing.away_score = game['away_score']
            existing.status = GameStatus(game['status'])
        else:
            db.add(Game(
                id=game['id'],
                game_date=game['game_date'],
                home_team_id=game['home_team_id'],
                away_team_id=game['away_team_id'],
                home_score=game['home_score'],
                away_score=game['away_score'],
                status=GameStatus(game['status']),
            ))
    return len(games)


def run_full_ingestion(season: str) -> dict[str, int]:
    """Top-level entrypoint called by scripts/run_ingestion.py."""
    log.info("Starting full ingestion for season=%s", season)
    counts: dict[str, int] = {}
    db = SessionLocal()
    try:
        counts["teams"] = ingest_teams(db)
        counts["players"] = ingest_active_players(db)
        counts["games"] = ingest_games_for_season(db, season)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    log.info("Ingestion complete: %s", counts)
    return counts
