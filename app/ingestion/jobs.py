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
from app.models.team_season_stats import TeamSeasonStats

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
            existing.usg_pct = row.get('USG_PCT')
            existing.ast_pct = row.get('AST_PCT')
            existing.oreb_pct = row.get('OREB_PCT')
            existing.dreb_pct = row.get('DREB_PCT')
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
                usg_pct=row.get('USG_PCT'),
                ast_pct=row.get('AST_PCT'),
                oreb_pct=row.get('OREB_PCT'),
                dreb_pct=row.get('DREB_PCT'),
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

    # Aggregate season-total possessions + minutes per team for accurate usage_rate.
    # Stats are stored as per-game averages, so multiply by games_played.
    # team_totals[team_id] = (season_total_possessions, season_total_player_minutes)
    team_totals: dict = {}
    for s in all_stats:
        if s.team_id is None:
            continue
        gp = s.games_played or 1
        poss = ((s.fga or 0) + 0.44 * (s.fta or 0) + (s.turnovers or 0)) * gp
        mins = (s.minutes_per_game or 0) * gp
        prev_poss, prev_mins = team_totals.get(s.team_id, (0.0, 0.0))
        team_totals[s.team_id] = (prev_poss + poss, prev_mins + mins)

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

        tendencies = compute_tendencies(stats, team_totals=team_totals)
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

    # --- Clutch rating pass ---
    # Fetch separately since it's a different endpoint with smaller sample sizes.
    _seed_clutch_ratings(db, season, all_stats)

    return count


def _seed_clutch_ratings(db: Session, season: str, all_stats: list) -> None:
    """Derive and upsert clutch_rating for all players with sufficient clutch data.

    Players below the minimum FGA volume in clutch situations keep the default (50).
    Ratings are computed via the same percentile curve used for other attributes.
    """
    from sqlalchemy import select
    from app.services.rating_engine import percentile_to_rating

    try:
        clutch_rows = nba_client.fetch_clutch_stats(season)
    except Exception as exc:
        log.warning("Clutch stats fetch failed for %s: %s — skipping clutch_rating", season, exc)
        return

    # Minimum volume: 1.0 FGA/clutch-game AND 15 clutch games.
    # Both filters needed: FGA alone lets 3-game hot streaks through; GP alone
    # lets sub-1-attempt role players through. Combined keeps ~100 meaningful performers.
    eligible = [
        r for r in clutch_rows
        if (r.get('fga') or 0) >= 1.0 and (r.get('gp') or 0) >= 15
    ]

    if len(eligible) < 20:
        log.warning("Too few eligible clutch players (%d) for %s — skipping", len(eligible), season)
        return

    # Compute equal-weight composite: (fg_pct_pct + ft_pct_pct + (1 - tov_rate_pct)) / 3
    # tov is per-game; normalise to per-FGA to make it a rate.
    def safe(val, default: float) -> float:
        return float(val) if val is not None else default

    fg_pcts = [safe(r['fg_pct'], 0.0) for r in eligible]
    ft_pcts = [safe(r['ft_pct'], 0.0) for r in eligible]
    # TOV rate = tov / (fga + 0.44 * fta) approximation; use tov / fga as proxy
    tov_rates = [safe(r['tov'], 0.0) / max(safe(r['fga'], 1.0), 0.1) for r in eligible]

    def pct_rank(values: list, val: float) -> float:
        return sum(1 for v in values if v < val) / len(values) * 100.0

    ratings: dict = {}
    for row in eligible:
        fg = safe(row['fg_pct'], 0.0)
        ft = safe(row['ft_pct'], 0.0)
        tov_r = safe(row['tov'], 0.0) / max(safe(row['fga'], 1.0), 0.1)

        composite = (
            pct_rank(fg_pcts, fg)
            + pct_rank(ft_pcts, ft)
            + (100.0 - pct_rank(tov_rates, tov_r))
        ) / 3.0
        ratings[row['player_id']] = percentile_to_rating(composite)

    # Upsert into existing PlayerAttributes rows only — don't create new rows here.
    all_pids = {s.player_id for s in all_stats}
    for pid, rating in ratings.items():
        if pid not in all_pids:
            continue
        attr = db.execute(
            select(PlayerAttributes).where(
                PlayerAttributes.player_id == pid,
                PlayerAttributes.season == season,
            )
        ).scalar_one_or_none()
        if attr:
            attr.clutch_rating = rating

    log.info("Seeded clutch_rating for %d players in %s", len(ratings), season)


def ingest_team_season_stats(db: Session, season: str) -> int:
    """Upsert pace, off_rating, def_rating, net_rating per team for a season."""
    rows = nba_client.fetch_team_season_stats(season)
    from sqlalchemy import select
    for row in rows:
        existing = db.execute(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == row['team_id'],
                TeamSeasonStats.season == season,
            )
        ).scalar_one_or_none()
        if existing:
            existing.pace = row['pace']
            existing.off_rating = row['off_rating']
            existing.def_rating = row['def_rating']
            existing.net_rating = row['net_rating']
        else:
            db.add(TeamSeasonStats(season=season, **row))
    return len(rows)


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
        counts["team_season_stats"] = ingest_team_season_stats(db, season)
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
