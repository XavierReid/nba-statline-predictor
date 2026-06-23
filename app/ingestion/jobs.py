"""Nightly ingestion job — orchestrates pulls from nba_api and upserts into Postgres.

Designed to be idempotent: running twice for the same season should not duplicate data.
"""

import logging

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ingestion import nba_client
from app.models.game import Game, GameStatus
from app.models.player import Player
from app.models.player_attributes import PlayerAttributes, PlayerAttributeOverride
from app.models.player_season_stats import PlayerSeasonStats
from app.models.player_tendencies import PlayerTendencies
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


def ingest_season_stats(db: Session, season: str) -> int:
    """Upsert per-game averages for all players from LeagueDashPlayerStats."""
    from sqlalchemy import select
    rows = nba_client.fetch_season_stats(season)
    known_player_ids = {pid for (pid,) in db.execute(select(Player.id)).all()}
    count = 0
    skipped = 0
    for row in rows:
        pid = row['PLAYER_ID']
        if pid not in known_player_ids:
            skipped += 1
            continue
        existing = db.execute(
            select(PlayerSeasonStats).where(
                PlayerSeasonStats.player_id == pid,
                PlayerSeasonStats.season == season,
            )
        ).scalar_one_or_none()
        if existing:
            existing.games_played = row['GP']
            existing.minutes_per_game = row['MIN']
            existing.points = row['PTS']
            existing.rebounds = row['REB']
            existing.assists = row['AST']
            existing.steals = row['STL']
            existing.blocks = row['BLK']
            existing.turnovers = row['TOV']
            existing.fgm = row['FGM']
            existing.fga = row['FGA']
            existing.fg_pct = row['FG_PCT']
            existing.fg3m = row['FG3M']
            existing.fg3a = row['FG3A']
            existing.fg3_pct = row['FG3_PCT']
            existing.ftm = row['FTM']
            existing.fta = row['FTA']
            existing.ft_pct = row['FT_PCT']
            existing.plus_minus = row['PLUS_MINUS']
        else:
            db.add(PlayerSeasonStats(
                player_id=pid,
                season=season,
                team_id=row.get('TEAM_ID'),
                games_played=row['GP'],
                minutes_per_game=row['MIN'],
                points=row['PTS'],
                rebounds=row['REB'],
                assists=row['AST'],
                steals=row['STL'],
                blocks=row['BLK'],
                turnovers=row['TOV'],
                fgm=row['FGM'],
                fga=row['FGA'],
                fg_pct=row['FG_PCT'],
                fg3m=row['FG3M'],
                fg3a=row['FG3A'],
                fg3_pct=row['FG3_PCT'],
                ftm=row['FTM'],
                fta=row['FTA'],
                ft_pct=row['FT_PCT'],
                plus_minus=row['PLUS_MINUS'],
            ))
        count += 1
    log.info("ingest_season_stats: %d upserted, %d skipped (not in players table)", count, skipped)
    return count


def seed_player_attributes(db: Session, season: str) -> int:
    """Derive PlayerAttributes and PlayerTendencies from PlayerSeasonStats."""
    from sqlalchemy import select
    from app.services.rating_engine import (
        compute_ratings_for_attribute, compute_tendencies,
        apply_overrides, position_defaults, compute_overall, SKILL_CONFIGS,
    )

    all_stats = db.execute(
        select(PlayerSeasonStats).where(PlayerSeasonStats.season == season)
    ).scalars().all()

    if not all_stats:
        log.warning("No season stats found for season=%s — run ingest_season_stats first", season)
        return 0

    derived_attributes = list(SKILL_CONFIGS.keys())
    ratings_by_attr = {
        attr: compute_ratings_for_attribute(attr, all_stats, SKILL_CONFIGS[attr])
        for attr in derived_attributes
    }

    count = 0
    for stats in all_stats:
        pid = stats.player_id
        attr_vals = {attr: ratings_by_attr[attr][pid] for attr in derived_attributes}

        player = db.get(Player, pid)
        pos_defaults = position_defaults(player.position if player else None)
        full_attrs = {**pos_defaults, **attr_vals}

        overrides = db.execute(
            select(PlayerAttributeOverride).where(
                PlayerAttributeOverride.player_id == pid,
                PlayerAttributeOverride.season == season,
            )
        ).scalars().all()
        full_attrs = apply_overrides(full_attrs, overrides)
        full_attrs["overall_rating"] = compute_overall(full_attrs, player.position if player else None)
        attr_vals = full_attrs

        existing_attr = db.execute(
            select(PlayerAttributes).where(
                PlayerAttributes.player_id == pid,
                PlayerAttributes.season == season,
            )
        ).scalar_one_or_none()

        if existing_attr:
            for k, v in attr_vals.items():
                setattr(existing_attr, k, v)
        else:
            db.add(PlayerAttributes(player_id=pid, season=season, **attr_vals))

        tendencies = compute_tendencies(stats)
        existing_tend = db.execute(
            select(PlayerTendencies).where(
                PlayerTendencies.player_id == pid,
                PlayerTendencies.season == season,
            )
        ).scalar_one_or_none()

        if existing_tend:
            for k, v in tendencies.items():
                setattr(existing_tend, k, v)
        else:
            db.add(PlayerTendencies(player_id=pid, season=season, **tendencies))

        count += 1
    return count


def run_full_ingestion(season: str) -> dict[str, int]:
    """Top-level entrypoint called by scripts/run_ingestion.py."""
    log.info("Starting full ingestion for season=%s", season)
    counts: dict[str, int] = {}
    db = SessionLocal()
    try:
        counts["teams"] = ingest_teams(db)
        counts["players"] = ingest_active_players(db)
        counts["games"] = ingest_games_for_season(db, season)
        counts["season_stats"] = ingest_season_stats(db, season)
        db.commit()
        counts["attributes"] = seed_player_attributes(db, season)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    log.info("Ingestion complete: %s", counts)
    return counts
