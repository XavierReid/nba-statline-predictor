"""Roster loading — fetch and normalize a team's top-10 players for simulation.

Roster construction has a single owner (RosterProvider) with two modes:
  - CURRENT: the live roster (Player.team_id) — how the season's players sit on
    today's teams. This is what every calibration baseline was validated against.
  - HISTORICAL: season-accurate membership (PlayerSeasonStats.team_id) — reconstructing
    a completed season from its own data.
The possession engine is unaware of which mode produced a roster; it just gets a list
of players. `load_roster()` picks the mode from the season (see CURRENT_ROSTER_SEASONS).
"""
from abc import ABC, abstractmethod
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.player import Player
from app.models.player_attributes import PlayerAttributes
from app.models.player_tendencies import PlayerTendencies
from app.models.player_season_stats import PlayerSeasonStats

# League FT% — measured from ingested totals (2024-25: 0.780, 2025-26: 0.783).
# Used as the empirical-Bayes prior for per-player FT probability and the
# fallback for players without FT history.
LEAGUE_FT_PCT = 0.78
_FT_SHRINK_PRIOR_ATTEMPTS = 20.0  # low-volume shooters shrink toward league average

# Interior/mid make probability comes from OBSERVED zone FG% (like ft_prob), not a
# percentile→band round-trip — so a shot's difficulty reflects the era it was taken
# in (90s rim ~.55 vs modern ~.65) instead of a modern-anchored constant. The
# shrinkage prior is THAT season's league average for the zone (data-derived, not a
# hardcoded era table), so low-volume players regress to their own era's norm.
_ZONE_SHRINK_PRIOR_ATTEMPTS = 40.0
_ZONE_PRIOR_CACHE: dict = {}

# Seasons whose rosters the `players` table reflects (the live-roster snapshot).
# These use CURRENT mode so every existing calibration baseline stays byte-identical;
# every other season is treated as historical.
CURRENT_ROSTER_SEASONS = frozenset({"2025-26"})


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


class RosterProvider(ABC):
    """Owns roster construction. Subclasses differ ONLY in how team membership is
    resolved; everything else (ratings, tendencies, FT prob, minute normalization)
    is shared, so both modes produce identically-shaped player lists."""

    @abstractmethod
    def _team_membership(self, team_id: int):
        """SQLAlchemy filter selecting this team's players for the season."""

    def load(self, db: Session, team_id: int, season: str) -> list[dict]:
        """Load top 10 players by minutes for a team in a given season.

        Minutes are normalized so the 10 players sum to 240 (5 players × 48 min).
        Returns an empty list if no stats exist for that team/season combination.
        """
        rows = db.execute(
            select(Player, PlayerAttributes, PlayerTendencies, PlayerSeasonStats)
            .join(PlayerAttributes, PlayerAttributes.player_id == Player.id)
            .join(PlayerTendencies, PlayerTendencies.player_id == Player.id)
            .join(PlayerSeasonStats, PlayerSeasonStats.player_id == Player.id)
            .where(self._team_membership(team_id))
            .where(PlayerAttributes.season == season)
            .where(PlayerTendencies.season == season)
            .where(PlayerSeasonStats.season == season)
            .order_by(PlayerSeasonStats.minutes_per_game.desc())
            .limit(10)
        ).all()
        return _build_roster(rows, _league_zone_prior(db, season))


class CurrentRosterProvider(RosterProvider):
    """Live roster — the season's players as they sit on today's teams."""
    def _team_membership(self, team_id: int):
        return Player.team_id == team_id


class HistoricalRosterProvider(RosterProvider):
    """Season-accurate roster — membership from the season's own stats."""
    def _team_membership(self, team_id: int):
        return PlayerSeasonStats.team_id == team_id


def roster_provider_for(season: str) -> RosterProvider:
    if season in CURRENT_ROSTER_SEASONS:
        return CurrentRosterProvider()
    return HistoricalRosterProvider()


def load_roster(db: Session, team_id: int, season: str) -> list[dict]:
    """Public entry point — delegates to the roster provider for the season."""
    return roster_provider_for(season).load(db, team_id, season)


def _league_zone_prior(db: Session, season: str) -> dict:
    """Season league-average FG% for rim / paint / mid — the shrinkage prior.
    Cached per season (a full-season aggregation). None for a zone means the
    season carries no shot-location data (players then fall back to attr-based prob)."""
    if season in _ZONE_PRIOR_CACHE:
        return _ZONE_PRIOR_CACHE[season]
    rows = db.execute(
        select(PlayerSeasonStats).where(PlayerSeasonStats.season == season)
    ).scalars().all()

    def league_pct(fga_attr, fgm_fn):
        fga = sum((getattr(r, fga_attr) or 0.0) * (r.games_played or 0) for r in rows)
        fgm = sum(fgm_fn(r) * (r.games_played or 0) for r in rows)
        return fgm / fga if fga else None

    # Two 2pt zones, matching the accounting's interior/mid: rim = Restricted Area,
    # non-rim = paint(non-RA) + mid-range. non_rim_frac is the era-derived shrinkage
    # prior for each player's rim-vs-non-rim shot split (see _build_roster), so the
    # sim's 2pt shot mix reflects the era it is simulating instead of a 0.4 constant.
    S = lambda f: sum((getattr(r, f) or 0.0) * (r.games_played or 0) for r in rows)
    nonrim_a = S("paint_fga") + S("mid_fga")
    two_pt_a = S("ra_fga") + nonrim_a
    nonrim_m = S("paint_fgm") + sum(
        (r.mid_fga or 0.0) * (r.mid_fg_pct or 0.0) * (r.games_played or 0) for r in rows)
    prior = {
        "rim": league_pct("ra_fga", lambda r: r.ra_fgm or 0.0),
        "nonrim": nonrim_m / nonrim_a if nonrim_a else None,
        "nonrim_frac": nonrim_a / two_pt_a if two_pt_a else None,
        "three": league_pct("fg3a", lambda r: r.fg3m or 0.0),
    }
    _ZONE_PRIOR_CACHE[season] = prior
    return prior


def _shrunk_zone_prob(fgm_pg, fga_pg, gp, prior_fg) -> Optional[float]:
    if not fga_pg or prior_fg is None:
        return None
    att = fga_pg * gp
    made = (fgm_pg or 0.0) * gp
    return round((made + prior_fg * _ZONE_SHRINK_PRIOR_ATTEMPTS)
                 / (att + _ZONE_SHRINK_PRIOR_ATTEMPTS), 4)


def _build_roster(rows, zone_prior: Optional[dict] = None) -> list[dict]:
    if not rows:
        return []
    zone_prior = zone_prior or {"rim": None, "paint": None, "mid": None}

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
            "layup": a.layup,
            "dunk": a.dunk,
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
            # `is not None` (not `or`): a real 0.0 three rate — a non-shooter, ~27% of
            # players in pre-spacing eras — must stay 0.0, not fall through to the default.
            "three_point_rate": t.three_point_rate if t.three_point_rate is not None else 0.30,
            "shot_tendency": t.shot_tendency or 15.0,
            "assist_rate": s.assists or 1.0,
            "oreb_rate": t.oreb_rate or 0.05,
            "dreb_rate": t.dreb_rate or 0.10,
            "rebound_rate": t.rebound_rate or 5.0,
            "turnover_rate": t.turnover_rate or 2.0,
            "foul_drawing_rate": t.foul_drawing_rate,
        })
        # FT probability straight from observation — FT% is one of the few skills
        # where the observation IS the probability. The old rating round-trip
        # (real FT% -> percentile rating -> attr_to_prob 0.60-0.95) ran the league
        # at ~0.85 vs 0.78 real. Shrinkage keeps 2-for-2 bench players honest.
        fta_total = (s.fta or 0) * (s.games_played or 0)
        ftm_total = (s.ftm or 0) * (s.games_played or 0)
        players[-1]["ft_prob"] = round(
            (ftm_total + LEAGUE_FT_PCT * _FT_SHRINK_PRIOR_ATTEMPTS)
            / (fta_total + _FT_SHRINK_PRIOR_ATTEMPTS), 4)
        # Observed zone make probabilities (rim/paint/mid) — the shot's era-embedded
        # difficulty. Absent when the season has no shot-location data; _evaluate_shot
        # then falls back to the attribute-derived band.
        gp = s.games_played or 0
        nonrim_fga = (s.paint_fga or 0.0) + (s.mid_fga or 0.0)
        nonrim_fgm = (s.paint_fgm or 0.0) + (s.mid_fga or 0.0) * (s.mid_fg_pct or 0.0)
        rim = _shrunk_zone_prob(s.ra_fgm, s.ra_fga, gp, zone_prior["rim"])
        nonrim = _shrunk_zone_prob(nonrim_fgm, nonrim_fga, gp, zone_prior["nonrim"])
        three = _shrunk_zone_prob(s.fg3m, s.fg3a, gp, zone_prior["three"])
        if rim is not None:
            players[-1]["rim_fg_prob"] = rim
        if nonrim is not None:
            players[-1]["nonrim_fg_prob"] = nonrim
        if three is not None:
            players[-1]["three_fg_prob"] = three
        # Non-rim (paint+mid) share of this player's 2pt attempts (observed) —
        # replaces the hardcoded 0.4 mid/interior split in shot selection. Shrunk
        # toward the era's league share so low-volume players regress to their norm.
        two_pt_att = ((s.ra_fga or 0.0) + nonrim_fga) * gp
        if two_pt_att and zone_prior["nonrim_frac"] is not None:
            players[-1]["mid_shot_rate"] = round(
                (nonrim_fga * gp + zone_prior["nonrim_frac"] * _ZONE_SHRINK_PRIOR_ATTEMPTS)
                / (two_pt_att + _ZONE_SHRINK_PRIOR_ATTEMPTS), 4)
        # Only include when real data exists — M3d sub-type selection falls back
        # to positional defaults via .get() when the key is absent.
        if t.corner_three_rate is not None:
            players[-1]["corner_three_rate"] = t.corner_three_rate
        players[-1]["player_variance"] = player_variance(players[-1])

    for i, p in enumerate(players):
        p["is_starter"] = i < 5

    total = sum(p["minutes"] for p in players)
    if total > 0:
        for p in players:
            p["minutes"] = round(p["minutes"] / total * 240, 1)

    return players
