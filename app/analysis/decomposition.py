"""Scoring decomposition — compare two PossessionAccounting objects and attribute
every point of the difference to a possession component.

The organizing question (the general calibration principle): *where do the extra
expected points come from?* The gap in points-per-team-game is split into a
possession-volume term plus per-component (interior / mid / three / free throw)
terms, each further separated into a volume effect (attempt rate) and an
efficiency effect (points per attempt). The parts sum to the whole, so once the
table balances you know exactly which component to investigate before touching
the shot model.
"""
import argparse
import os
import sys
import zlib
from dataclasses import replace
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.analysis.accounting import (
    ZONES, PossessionAccounting, real_accounting, sim_accounting,
)
from app.database import SessionLocal
from app.models.game import Game
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select


def simulate_schedule(db, season: str, config, sims_per_game: int) -> List[dict]:
    """Replay every real final game of a season; return simulate_game outputs."""
    year = season.split("-")[0][-2:]
    games = db.execute(
        select(Game).where(
            Game.id.like(f"002{year}%"), Game.status == "final",
            Game.home_score.isnot(None),
        )
    ).scalars().all()
    rosters = {}
    for t in db.execute(select(Team)).scalars().all():
        r = load_roster(db, t.id, season)
        if r:
            rosters[t.id] = r

    out = []
    for g in games:
        if g.home_team_id not in rosters or g.away_team_id not in rosters:
            continue
        seed = zlib.crc32(str(g.id).encode())
        for k in range(sims_per_game):
            out.append(simulate_game(
                rosters[g.home_team_id], rosters[g.away_team_id],
                seed=seed + k, season=season, config=config,
                home_team_id=g.home_team_id, away_team_id=g.away_team_id, db=db,
                capture_descriptions=True,  # populate events for the shot-zone split
            ))
    return out


def _ppp_parts(a: PossessionAccounting) -> dict:
    """PPP contribution of each component (sums to ~ppp; residual = uncategorized)."""
    parts = {z: a.zones[z].fga_per100 / 100 * a.zones[z].ppa for z in ZONES}
    parts["ft"] = a.ft_pct * a.fta_rate
    return parts


def compare(real: PossessionAccounting, sim: PossessionAccounting) -> None:
    w = 74
    print("\n" + "=" * w)
    print(f"  Possession decomposition — {sim.label}  vs  {real.label}")
    print("=" * w)
    print(f"  {'':16}{'real':>12}{'sim':>12}{'diff':>12}")
    def row(lbl, r, s, f="{:.3f}"):
        d = s - r
        ds = ("+" if d >= 0 else "") + f.format(d)
        print(f"  {lbl:16}{f.format(r):>12}{f.format(s):>12}{ds:>12}")
    row("possessions", real.possessions, sim.possessions, "{:.1f}")
    row("points/game", real.points_per_game, sim.points_per_game, "{:.1f}")
    row("PPP", real.ppp, sim.ppp)
    row("FTA rate", real.fta_rate, sim.fta_rate)
    row("FT%", real.ft_pct, sim.ft_pct)
    row("TOV rate", real.tov_rate, sim.tov_rate)
    row("OREB rate", real.oreb_rate, sim.oreb_rate)

    print(f"\n  {'zone':10}{'FGA share':>11}{'  '}{'FG%':>13}{'  '}{'pts/att':>13}")
    print(f"  {'':10}{'real / sim':>11}{'  '}{'real / sim':>13}{'  '}{'real / sim':>13}")
    for z in ZONES:
        r, s = real.zones[z], sim.zones[z]
        print(f"  {z:10}{r.fga_share:>5.2f} /{s.fga_share:>5.2f}  "
              f"{r.fg_pct:>6.3f} /{s.fg_pct:>6.3f}  {r.ppa:>6.3f} /{s.ppa:>6.3f}")
    print(f"  {'3pt split':10}corner {real.corner_share:.2f}/{sim.corner_share:.2f}"
          f"   above {real.above_break_share:.2f}/{sim.above_break_share:.2f}  (attempts only)")

    # --- shot-mix reconciliation: attempts are conserved, so a deficit in one zone
    # is an equal surplus in others. Answer literally where missing attempts went. ---
    deltas = {z: sim.zones[z].fga_share - real.zones[z].fga_share for z in ZONES}
    deficit = min(deltas, key=deltas.get)
    if deltas[deficit] < 0:
        surplus = {z: d for z, d in deltas.items() if d > 0}
        parts = ", ".join(f"{z} +{d*100:.0f}%" for z, d in sorted(surplus.items(), key=lambda x: -x[1]))
        print(f"\n  Shot-mix: sim under-attempts {deficit} by {-deltas[deficit]*100:.0f}% of FGA"
              f" — those attempts became: {parts}")

    # --- points attribution: gap = possession volume + per-component (vol + eff) ---
    gap = sim.points_per_game - real.points_per_game
    rp, sp = _ppp_parts(real), _ppp_parts(sim)
    print(f"\n  Points attribution (per team-game, sums to the {gap:+.1f} gap):")
    print(f"  {'component':12}{'volume':>10}{'efficiency':>12}{'total':>10}")
    vol_term = real.ppp * (sim.possessions - real.possessions)
    print(f"  {'possessions':12}{'':>10}{'':>12}{vol_term:>+10.1f}")
    running = vol_term
    labels = {"interior": "interior", "mid": "mid-range", "three": "three", "ft": "free throw"}
    for key in ("interior", "mid", "three", "ft"):
        if key == "ft":
            a_r, a_s = real.fta_rate, sim.fta_rate           # rate proxy
            p_r, p_s = real.ft_pct, sim.ft_pct
        else:
            a_r = real.zones[key].fga_per100 / 100
            a_s = sim.zones[key].fga_per100 / 100
            p_r, p_s = real.zones[key].ppa, sim.zones[key].ppa
        volume = (a_s - a_r) * p_r * sim.possessions
        eff = a_s * (p_s - p_r) * sim.possessions
        print(f"  {labels[key]:12}{volume:>+10.1f}{eff:>+12.1f}{volume + eff:>+10.1f}")
        running += volume + eff
    residual = gap - running
    print(f"  {'residual':12}{'':>10}{'':>12}{residual:>+10.1f}   (uncategorized shots)")
    print("=" * w + "\n")


def run(season: str, sims_per_game: int, config=DRAMA_M3) -> None:
    db = SessionLocal()
    real = real_accounting(db, season)
    sims = simulate_schedule(db, season, config, sims_per_game)
    sim = sim_accounting(f"{season} sim", sims)
    db.close()
    compare(real, sim)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2005-06")
    p.add_argument("--sims", type=int, default=1)
    args = p.parse_args()
    run(args.season, args.sims)
