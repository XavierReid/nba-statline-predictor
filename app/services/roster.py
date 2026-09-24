"""Roster loading — fetch and normalize a team's top players (by minutes) for simulation.

`load_roster(depth=...)` returns the top `depth` players (default 10; the availability
model loads deeper and draws a per-game active set — see app/services/availability.py).

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

# Per-zone fraction of missed shots drawing shooting fouls in the sim.
# Used ONLY when the pre-negation transform is enabled (SimConfig.use_pre_negation_probs).
# Re-measured 2026-09-16 (Probe #9e) via scratch/probe_9e_remeasure_constants.py on the
# current shipped sim: full-league 2024-25, 1230 games, 223k shots.
#   rim (layup+dunk):     0.3537   (was 0.24 pre-shooter_draw)
#   nonrim (mid+floater): 0.2320 measured — LEFT AT 0.19 because the aggregate masks
#                                 a shot-class split (floater 0.259 vs mid_range 0.230);
#                                 raising it regresses non-elite guard mid-range FG%.
#                                 Split banked as Probe #10.
#   three (both types):   0.0239   (was 0.05 pre-shooter_draw)
# These are SIM-measured rates because the transform inverts SIM's own PR#8 negation, not
# real's. If sim foul-drawing rates drift, these constants would need re-measurement.
_ZONE_FOUL_MISS_RATE = {
    "rim": 0.354,
    "nonrim": 0.190,   # aggregate, used when Probe #10 flag OFF (byte-identity path)
    "paint": 0.259,    # measured Probe #9e (floater sub-type f_miss aggregate)
    "midrange": 0.230, # measured Probe #9e (mid_range sub-type f_miss aggregate)
    "three": 0.024,
}


def _pre_negation_prob(obs_post_neg, f_miss: float):
    """Convert a real POST-negation zone FG% (`ra_fgm/ra_fga` etc — real NBA excludes
    fouled-miss FGAs per PR #8 accounting) into the corresponding PRE-negation make
    probability the sim should use as its shot-outcome base. Session 2 causal closure
    (2026-07-27) proved this to within 0.2pp both eras.

    Identity:  observed = p / (p + (1-p)(1-f))  ->  p = obs*(1-f) / (1 - obs*f)

    Passthrough on None / boundary values so callers can pipe the shrunk output
    straight through without null-checking.
    """
    if obs_post_neg is None or obs_post_neg <= 0.0 or obs_post_neg >= 1.0:
        return obs_post_neg
    return round(obs_post_neg * (1.0 - f_miss) / (1.0 - obs_post_neg * f_miss), 4)

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

    def load(self, db: Session, team_id: int, season: str, depth: int = 10,
             pre_negation: bool = False, paint_mid_split: bool = False) -> list[dict]:
        """Load the top `depth` players by minutes for a team in a given season.

        Minutes are games-weighted (see _build_roster) so each player keeps their real
        per-game share of 240 (5 players × 48 min). Default depth 10 = the fixed-roster
        model. The availability model (gap 3.4) loads deeper (e.g. 14) and draws per-game
        availability from games_played; loading deeper WITHOUT availability over-benched
        stars in garbage time (the reverted 15-player experiment). Empty list if no stats.

        `pre_negation=True` applies the pre-negation transform to per-player zone FG%
        values (see `_pre_negation_prob`) — an experimental toggle for Session 3 of
        the make-model residual arc. Default False keeps byte-identical behavior.
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
            .limit(depth)
        ).all()
        roster = _build_roster(rows, _league_zone_prior(db, season),
                               pre_negation=pre_negation, paint_mid_split=paint_mid_split)
        # Calibrate each player's per-game availability to the season's real active count
        # (game logs, when ingested). Harmless when availability is off — an unused field.
        from app.services.availability import season_active_target, calibrate_avail_prob
        calibrate_avail_prob(roster, season_active_target(db, season))
        return roster


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


def load_roster(db: Session, team_id: int, season: str, depth: int = 10,
                pre_negation: bool = False, paint_mid_split: bool = False) -> list[dict]:
    """Public entry point — delegates to the roster provider for the season.

    `pre_negation=True` applies the pre-negation transform to per-player zone FG%s.
    `paint_mid_split=True` (Probe #10) also splits the aggregate `nonrim` zone into
    per-player `paint_fg_prob` / `midrange_fg_prob` / `paint_shot_rate`, with
    pre-negation applied to each sub-zone independently.
    """
    return roster_provider_for(season).load(db, team_id, season, depth,
                                            pre_negation=pre_negation,
                                            paint_mid_split=paint_mid_split)


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
    paint_a = S("paint_fga")
    paint_m = S("paint_fgm")
    mid_a = S("mid_fga")
    mid_m = sum((r.mid_fga or 0.0) * (r.mid_fg_pct or 0.0) * (r.games_played or 0) for r in rows)
    nonrim_a = paint_a + mid_a
    two_pt_a = S("ra_fga") + nonrim_a
    nonrim_m = paint_m + mid_m
    prior = {
        "rim": league_pct("ra_fga", lambda r: r.ra_fgm or 0.0),
        # Aggregate nonrim — used when Probe #10 flag OFF (byte-identity path).
        "nonrim": nonrim_m / nonrim_a if nonrim_a else None,
        "nonrim_frac": nonrim_a / two_pt_a if two_pt_a else None,
        # Split zones — used when Probe #10 flag ON. Independent priors so a player's
        # sparse paint sample doesn't leak into their midrange baseline (or vice versa).
        "paint": paint_m / paint_a if paint_a else None,
        "midrange": mid_m / mid_a if mid_a else None,
        # `paint_share_of_nonrim` is the league prior for `paint_shot_rate` — shrunk
        # per-player against this to stabilize thin samples.
        "paint_share_of_nonrim": paint_a / nonrim_a if nonrim_a else None,
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


def _build_roster(rows, zone_prior: Optional[dict] = None, pre_negation: bool = False,
                  paint_mid_split: bool = False) -> list[dict]:
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
            "games_played": s.games_played or 0,
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
        # Turnover ECONOMY (per used possession), not per-36. TOV/36 is a volume stat
        # inflated by usage — reading it as a per-possession rate gave stars an
        # inverted turnover economy (gap 3.4b). Real TOV/used-poss is ~flat (~0.12-0.14,
        # slightly lower for stars). Drives the unforced-turnover event in possession.py.
        # Foul propensity as a PER-MINUTE rate (guardrail #7): the weighted foul-
        # attribution draw picks among on-court defenders, so weighting by PF/min gives
        # each player expected fouls ~ (PF/min x minutes on court) ~ their measured PF.
        # This is what stops the uniform draw from funneling fouls onto whoever plays the
        # most minutes (stars), who in reality foul the LEAST per minute. Falls back to the
        # league mean (~0.09/min = ~22 team PF / 240 min) when PF isn't ingested for a season.
        mpg = s.minutes_per_game or 0.0
        players[-1]["foul_rate"] = round((s.pf_per_game / mpg), 4) if (s.pf_per_game and mpg > 0) else 0.09
        used_poss = (s.fga or 0.0) + 0.44 * (s.fta or 0.0) + (s.turnovers or 0.0)
        if used_poss > 0:
            players[-1]["tov_per_poss"] = round((s.turnovers or 0.0) / used_poss, 4)
        # Observed zone make probabilities (rim/paint/mid) — the shot's era-embedded
        # difficulty. Absent when the season has no shot-location data; _evaluate_shot
        # then falls back to the attribute-derived band.
        gp = s.games_played or 0
        # Aggregate nonrim — always computed for byte-identity when paint_mid_split OFF.
        paint_pg_fga, paint_pg_fgm = (s.paint_fga or 0.0), (s.paint_fgm or 0.0)
        mid_pg_fga = (s.mid_fga or 0.0)
        mid_pg_fgm = mid_pg_fga * (s.mid_fg_pct or 0.0)
        nonrim_fga = paint_pg_fga + mid_pg_fga
        nonrim_fgm = paint_pg_fgm + mid_pg_fgm
        rim = _shrunk_zone_prob(s.ra_fgm, s.ra_fga, gp, zone_prior["rim"])
        nonrim = _shrunk_zone_prob(nonrim_fgm, nonrim_fga, gp, zone_prior["nonrim"])
        three = _shrunk_zone_prob(s.fg3m, s.fg3a, gp, zone_prior["three"])
        # Probe #10 split — always computed (cheap; presence gated by caller reads).
        # Independent shrinkage: paint and midrange each use their own prior + attempts,
        # so a sparse paint sample doesn't drag midrange toward paint average.
        paint = _shrunk_zone_prob(paint_pg_fgm, paint_pg_fga, gp, zone_prior.get("paint"))
        midrange_pg_fgm = mid_pg_fga * (s.mid_fg_pct or 0.0)  # rebuild for clarity
        midrange = _shrunk_zone_prob(midrange_pg_fgm, mid_pg_fga, gp, zone_prior.get("midrange"))
        if pre_negation:
            # Invert sim's PR#8 negation once at load time so the sim treats these
            # values as raw make probs (not double-counted post-neg data). See
            # `_pre_negation_prob` doc + Session 2 causal proof.
            rim = _pre_negation_prob(rim, _ZONE_FOUL_MISS_RATE["rim"])
            three = _pre_negation_prob(three, _ZONE_FOUL_MISS_RATE["three"])
            if paint_mid_split:
                # Split path: each 2P sub-zone gets its own f_miss inversion.
                paint = _pre_negation_prob(paint, _ZONE_FOUL_MISS_RATE["paint"])
                midrange = _pre_negation_prob(midrange, _ZONE_FOUL_MISS_RATE["midrange"])
                # nonrim is unused on the read side under the split; skip the inversion
                # (keeps `nonrim_fg_prob` as an unused raw-shrunk value for compat).
            else:
                nonrim = _pre_negation_prob(nonrim, _ZONE_FOUL_MISS_RATE["nonrim"])
        if rim is not None:
            players[-1]["rim_fg_prob"] = rim
        if nonrim is not None:
            players[-1]["nonrim_fg_prob"] = nonrim
        if paint is not None:
            players[-1]["paint_fg_prob"] = paint
        if midrange is not None:
            players[-1]["midrange_fg_prob"] = midrange
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
        # Probe #10: per-player share of nonrim attempts that are paint (non-RA).
        # Shrunk against league `paint_share_of_nonrim` so thin-sample players regress
        # to the league mean. Read by `_select_sub_type` only under the split flag —
        # positioned as the SOLE random draw for paint vs midrange within nonrim
        # (no layered floater_rate on top).
        paint_att = paint_pg_fga * gp
        nonrim_att_total = nonrim_fga * gp
        prior_share = zone_prior.get("paint_share_of_nonrim")
        if nonrim_att_total and prior_share is not None:
            players[-1]["paint_shot_rate"] = round(
                (paint_att + prior_share * _ZONE_SHRINK_PRIOR_ATTEMPTS)
                / (nonrim_att_total + _ZONE_SHRINK_PRIOR_ATTEMPTS), 4)
        # Only include when real data exists — M3d sub-type selection falls back
        # to positional defaults via .get() when the key is absent.
        if t.corner_three_rate is not None:
            players[-1]["corner_three_rate"] = t.corner_three_rate
        players[-1]["player_variance"] = player_variance(players[-1])

    for i, p in enumerate(players):
        p["is_starter"] = i < 5
        p["mpg"] = p["minutes"]   # raw per-game-played minutes (availability model, gap 3.4)

    # Games-weight real per-game minutes (MPG × games_played) so each player keeps their real
    # SHARE of the 240 team-minutes. Raw MPG is per game PLAYED, so it sums above 240 for the
    # top players; the old normalize-raw-MPG-to-240 therefore shaved every star ~5% (e.g.
    # Westbrook 34.6→32.9). Games-weighting corrects for missed games (a 40-game 34-MPG player
    # contributes less than an 82-game 34-MPG one) and restores real starter minutes.
    weights = [p["minutes"] * p["games_played"] for p in players]
    total = sum(weights)
    if total > 0:
        for p, w in zip(players, weights):
            p["minutes"] = round(w / total * 240, 1)

    return players
