"""Roster loading — fetch and normalize a team's top-10 players for simulation."""
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.player import Player
from app.models.player_attributes import PlayerAttributes
from app.models.player_tendencies import PlayerTendencies
from app.models.player_season_stats import PlayerSeasonStats


def player_variance(player: dict) -> float:
    """Derive per-game form factor σ from measurable proxies.

    The four tiers approximate behavioral archetypes using data we already have.
    When Player archetypes are added (Phase 3), archetype → σ mapping replaces
    these proxy conditions directly without changing the form factor interface.
    """
    passing = player.get("passing", 50)
    tov_rate = player.get("turnover_rate", 2.5)
    three_point = player.get("three_point", 50)
    usage = player.get("usage_rate", 0.20)
    overall = player.get("overall", 60)

    # Elite decision-maker: high-IQ, low-turnover playmakers (Jokić, LeBron)
    if passing >= 80 and tov_rate <= 2.0:
        return 0.02
    # Shooting specialist: spot-up shooters with high 3PT rating and low usage
    if three_point >= 80 and usage <= 0.20:
        return 0.05
    # Young/inconsistent: high-usage but lower overall (still developing)
    if overall < 60 and usage >= 0.25:
        return 0.04
    return 0.03


def load_roster(db: Session, team_id: int, season: str) -> list[dict]:
    """Load top 10 players by minutes for a team in a given season.

    Minutes are normalized so the 10 players sum to 240 (5 players × 48 min).
    Returns an empty list if no stats exist for that team/season combination.
    """
    rows = db.execute(
        select(Player, PlayerAttributes, PlayerTendencies, PlayerSeasonStats)
        .join(PlayerAttributes, PlayerAttributes.player_id == Player.id)
        .join(PlayerTendencies, PlayerTendencies.player_id == Player.id)
        .join(PlayerSeasonStats, PlayerSeasonStats.player_id == Player.id)
        .where(Player.team_id == team_id)
        .where(PlayerAttributes.season == season)
        .where(PlayerTendencies.season == season)
        .where(PlayerSeasonStats.season == season)
        .order_by(PlayerSeasonStats.minutes_per_game.desc())
        .limit(10)
    ).all()

    if not rows:
        return []

    players = []
    for p, a, t, s in rows:
        players.append({
            "id": p.id,
            "name": p.full_name,
            "position": p.position or "F",
            "minutes": s.minutes_per_game,
            "is_starter": False,
            # attributes (0-100 scale)
            "three_point": a.three_point,
            "mid_range": a.mid_range,
            "free_throw": a.free_throw,
            "close_shot": a.close_shot,
            "passing": a.passing,
            "steal": a.steal,
            "block": a.block,
            "perimeter_defense": a.perimeter_defense,
            "interior_defense": a.interior_defense,
            "offensive_rebound": a.offensive_rebound,
            "defensive_rebound": a.defensive_rebound,
            "overall": a.overall_rating,
            "clutch_rating": a.clutch_rating,
            # tendencies
            "usage_rate": t.usage_rate or 0.20,
            "three_point_rate": t.three_point_rate or 0.30,
            "shot_tendency": t.shot_tendency or 15.0,
            "assist_rate": s.assists or 1.0,
            "oreb_rate": t.oreb_rate or 0.05,
            "dreb_rate": t.dreb_rate or 0.10,
            "rebound_rate": t.rebound_rate or 5.0,
            "turnover_rate": t.turnover_rate or 2.0,
        })
        players[-1]["player_variance"] = player_variance(players[-1])

    for i, p in enumerate(players):
        p["is_starter"] = i < 5

    total = sum(p["minutes"] for p in players)
    if total > 0:
        for p in players:
            p["minutes"] = round(p["minutes"] / total * 240, 1)

    return players
