"""Canonical possession accounting.

THE possession definition for all analysis. The engine internally counts
possession *events* (a halfcourt trip, a second-chance after an offensive
rebound, a fast break) because it needs them to run the clock — that is an
implementation detail. Every analysis, calibration report, historical comparison,
and regression test instead uses the STATISTICAL possession (the NBA estimator):

    possessions = FGA - OREB + TOV + 0.44 * FTA

An offensive rebound continues the same statistical possession, so OREB extension
shows up honestly as *more FGA per possession* rather than as extra possessions.
Fixing this in one place is the lesson of the 2026-07 pace episode: two possession
definitions coexisted, and comparing across them produced a phantom ~2.6% offset.

PossessionAccounting is the single object both real seasons and simulated runs
produce, so any comparison is apples-to-apples by construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.game import Game
from app.models.player_season_stats import PlayerSeasonStats


def statistical_possessions(fga: float, oreb: float, tov: float, fta: float) -> float:
    """The canonical NBA possession estimator. See module docstring."""
    return fga - oreb + tov + 0.44 * fta


# Comparison zones. Interior folds Restricted Area + Paint (the sim has no
# paint-non-RA sub-type); three folds corner + above-the-break for FG%/PPP (real
# make data is not split by three sub-zone) but tracks their attempt split.
ZONES = ("interior", "mid", "three")

# sim sub_type / coarse fallback -> comparison zone. floater is a short runner in
# the lane — it belongs with paint/mid (real "mid" folds paint-non-RA + mid-range).
_SIM_ZONE = {
    "dunk": "interior", "layup": "interior", "close": "interior",
    "mid_range": "mid", "mid": "mid", "floater": "mid",
    "corner_three": "three", "above_break_three": "three", "three": "three",
}
_ZONE_POINTS = {"interior": 2, "mid": 2, "three": 3}


@dataclass
class ZoneLine:
    fga_share: float   # fraction of all FGA
    fga_per100: float  # attempts per 100 statistical possessions
    fg_pct: float
    ppa: float         # points per attempt (field goals only)


@dataclass
class PossessionAccounting:
    """Everything needed to explain where a team's points came from, per
    statistical possession. Produced identically for real and simulated basketball."""
    label: str
    possessions: float           # per team-game
    points_per_game: float
    ppp: float
    zones: Dict[str, ZoneLine]
    corner_share: float          # of threes (attempts only)
    above_break_share: float
    fta_rate: float              # FTA per possession
    ft_pct: float
    tov_rate: float              # TOV per possession
    oreb_rate: float             # OREB per possession (extension rate)
    # raw per-team-game totals kept for auditing / richer future reports
    totals: Dict[str, float] = field(default_factory=dict)


def _build(label: str, tg: Dict[str, float],
           zone_fga: Dict[str, float], zone_fgm: Dict[str, float],
           corner_fga: float, above_fga: float) -> PossessionAccounting:
    """Assemble an accounting object from per-team-game totals `tg` and zone splits."""
    poss = statistical_possessions(tg["fga"], tg["oreb"], tg["tov"], tg["fta"])
    total_fga = sum(zone_fga.values()) or 1.0
    zones = {}
    for z in ZONES:
        a = zone_fga.get(z, 0.0)
        m = zone_fgm.get(z, 0.0)
        pct = m / a if a else 0.0
        zones[z] = ZoneLine(
            fga_share=a / total_fga,
            fga_per100=a / poss * 100 if poss else 0.0,
            fg_pct=pct,
            ppa=pct * _ZONE_POINTS[z],
        )
    three_fga = corner_fga + above_fga
    return PossessionAccounting(
        label=label,
        possessions=poss,
        points_per_game=tg["pts"],
        ppp=tg["pts"] / poss if poss else 0.0,
        zones=zones,
        corner_share=corner_fga / three_fga if three_fga else 0.0,
        above_break_share=above_fga / three_fga if three_fga else 0.0,
        fta_rate=tg["fta"] / poss if poss else 0.0,
        ft_pct=tg["ftm"] / tg["fta"] if tg["fta"] else 0.0,
        tov_rate=tg["tov"] / poss if poss else 0.0,
        oreb_rate=tg["oreb"] / poss if poss else 0.0,
        totals=tg,
    )


# --------------------------------------------------------------------------
# Real season accounting (from the database)
# --------------------------------------------------------------------------
def real_accounting(db: Session, season: str) -> PossessionAccounting:
    """Build accounting for a real season from ingested stats + game results.

    Per-team-game totals are season sums / (2 * final games). FGM is reconstructed
    from points (pts = 2*fg2m + 3*fg3m + ftm) so it needs no separate column, and
    OREB is estimated from oreb_pct applied to missed field goals.
    """
    year = season.split("-")[0][-2:]
    n_games = db.execute(
        select(Game).where(Game.id.like(f"002{year}%"), Game.status == "final")
    ).scalars().all()
    team_games = 2 * len(n_games)
    if not team_games:
        raise ValueError(f"no final games ingested for {season}")

    rows = db.execute(
        select(PlayerSeasonStats).where(PlayerSeasonStats.season == season)
    ).scalars().all()

    S = lambda f: sum((getattr(r, f) or 0.0) * (r.games_played or 0) for r in rows)
    fga, fg3a, fg3m = S("fga"), S("fg3a"), S("fg3m")
    fta, ftm, tov, pts = S("fta"), S("ftm"), S("turnovers"), S("points")
    fg2m = (pts - ftm - 3 * fg3m) / 2.0
    fgm = fg2m + fg3m

    # Zones mirror the sim's taxonomy (rim / mid-2 / three) AND are exhaustive so
    # they sum to total FGA (no uncategorized residual): interior = Restricted Area
    # (the sim's dunk+layup), three = all 3PA, mid = every other 2PA (paint-non-RA,
    # true mid-range, and any unclassified long two — the sim lumps these as mid_range).
    ra_fga, ra_fgm = S("ra_fga"), S("ra_fgm")
    mid_fga = (fga - fg3a) - ra_fga
    mid_fgm = (fgm - fg3m) - ra_fgm
    corner_fga = S("corner3_fga")

    # OREB via team OREB% applied to missed field goals (the rebound opportunities).
    # OREB% = OREB / (OREB + opp DREB) and opp DREB ~ our misses, so OREB ~ pct*misses.
    oreb_pct = _season_oreb_pct(db, season)
    oreb = oreb_pct * (fga - fgm)

    per = lambda v: v / team_games
    tg = {k: per(v) for k, v in dict(
        fga=fga, fgm=fgm, fg3a=fg3a, fg3m=fg3m, fta=fta, ftm=ftm,
        tov=tov, pts=pts, oreb=oreb).items()}

    zone_fga = {"interior": per(ra_fga), "mid": per(mid_fga), "three": per(fg3a)}
    zone_fgm = {"interior": per(ra_fgm), "mid": per(mid_fgm), "three": per(fg3m)}
    return _build(f"{season} real", tg, zone_fga, zone_fgm,
                  per(corner_fga), per(fg3a - corner_fga))


# League-average team OREB% when the season's team stats don't carry it (the
# Advanced endpoint omits it for in-progress seasons). Modern NBA ~0.27.
_DEFAULT_OREB_PCT = 0.27


def _season_oreb_pct(db: Session, season: str) -> float:
    from app.models.team_season_stats import TeamSeasonStats
    vals = [t.oreb_pct for t in db.execute(
        select(TeamSeasonStats).where(TeamSeasonStats.season == season)
    ).scalars().all() if t.oreb_pct is not None]
    return sum(vals) / len(vals) if vals else _DEFAULT_OREB_PCT


# --------------------------------------------------------------------------
# Simulated accounting (from simulate_game result dicts)
# --------------------------------------------------------------------------
def sim_accounting(label: str, games: Iterable[dict]) -> PossessionAccounting:
    """Build accounting from simulate_game outputs. Box scores give team totals;
    events give the shot-zone split and offensive-rebound (extension) count."""
    tg = dict(fga=0.0, fgm=0.0, fg3a=0.0, fg3m=0.0, fta=0.0, ftm=0.0,
              tov=0.0, pts=0.0, oreb=0.0)
    zone_fga = {z: 0.0 for z in ZONES}
    zone_fgm = {z: 0.0 for z in ZONES}
    corner_fga = above_fga = 0.0
    n = 0

    for g in games:
        n += 2  # two teams per game
        for p in g["box_score"].values():
            tg["fga"] += p["fga"]; tg["fgm"] += p["fgm"]
            tg["fg3a"] += p["fg3a"]; tg["fg3m"] += p["fg3m"]
            tg["fta"] += p["fta"]; tg["ftm"] += p["ftm"]
            tg["tov"] += p["tov"]; tg["pts"] += p["pts"]
        for e in g["events"]:
            st = e.get("shot_type")
            if st in _SIM_ZONE:
                z = _SIM_ZONE[st]
                zone_fga[z] += 1
                if e.get("made"):
                    zone_fgm[z] += 1
                if st == "corner_three":
                    corner_fga += 1
                elif st in ("above_break_three", "three"):
                    above_fga += 1
            if e.get("is_oreb"):
                tg["oreb"] += 1

    per = lambda v: v / n
    tg = {k: per(v) for k, v in tg.items()}
    zone_fga = {z: per(v) for z, v in zone_fga.items()}
    zone_fgm = {z: per(v) for z, v in zone_fgm.items()}
    return _build(label, tg, zone_fga, zone_fgm, per(corner_fga), per(above_fga))
