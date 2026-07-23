"""Canonical player possession accounting.

The per-player analog of accounting.py: every field-goal attempt, free-throw trip,
turnover, rebound and assist is attributable to an individual, so a player's line
divergence from real is explained by a behavioral OWNER rather than merely observed.
The line report is one consumer; the primary artifact is the tier decomposition —
individual players are noisy, tiers expose structural bias (e.g. "the engine shifts
X% of team usage from stars to rotation regardless of who the star is").

Δ Points  = minutes + shot volume (usage share) + shot efficiency + free-throw rate
Δ Assists = team makes + assist rate (attribution) + assist share (ball-handler)

Tiers are assigned from REAL within-team usage rank, so a player's sim line is
compared against real within the role the player actually held.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Dict, List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.game import Game
from app.models.player_season_stats import PlayerSeasonStats
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3

TIERS = ["star", "primary", "secondary", "rotation", "bench"]
# within-team usage rank (0 = highest) -> tier; 10-man rotation
_TIER_BY_RANK = ["star", "primary", "secondary", "secondary",
                 "rotation", "rotation", "rotation", "bench", "bench", "bench"]


def _usage(fga: float, fta: float, tov: float) -> float:
    """Used possessions — the possession-allocation currency."""
    return fga + 0.44 * fta + tov


@dataclass
class PlayerAccount:
    player_id: int
    name: str
    team_id: int
    tier: str
    minutes: float          # per game
    pts: float
    reb: float
    ast: float
    tov: float
    fga: float
    fgm: float
    fg3m: float
    fta: float
    ftm: float
    usage: float            # per-game used possessions
    usage_share: float      # share of team used possessions
    ast_share: float        # share of team assists

    @property
    def fg_pts(self) -> float:
        return 2 * self.fgm + self.fg3m  # 2*(2ptM+3ptM)+3ptM


def _finalize(rows: List[dict]) -> Dict[int, PlayerAccount]:
    """rows: per-player per-game dicts for one team. Assign tiers by usage rank
    and compute within-team shares."""
    team_usage = sum(r["usage"] for r in rows) or 1.0
    team_ast = sum(r["ast"] for r in rows) or 1.0
    ranked = sorted(rows, key=lambda r: -r["usage"])
    out: Dict[int, PlayerAccount] = {}
    for rank, r in enumerate(ranked):
        out[r["player_id"]] = PlayerAccount(
            player_id=r["player_id"], name=r["name"], team_id=r["team_id"],
            tier=_TIER_BY_RANK[min(rank, 9)],
            minutes=r["minutes"], pts=r["pts"], reb=r["reb"], ast=r["ast"], tov=r["tov"],
            fga=r["fga"], fgm=r["fgm"], fg3m=r["fg3m"], fta=r["fta"], ftm=r["ftm"],
            usage=r["usage"], usage_share=r["usage"] / team_usage,
            ast_share=r["ast"] / team_ast,
        )
    return out


def real_accounts(db: Session, season: str) -> Dict[int, PlayerAccount]:
    """Per-game real accounts for the 10-man rotation of every team."""
    accounts: Dict[int, PlayerAccount] = {}
    for team in db.execute(select(Team)).scalars().all():
        roster = load_roster(db, team.id, season)
        if not roster:
            continue
        pids = [p["id"] for p in roster]
        stats = {s.player_id: s for s in db.execute(
            select(PlayerSeasonStats).where(
                PlayerSeasonStats.season == season,
                PlayerSeasonStats.player_id.in_(pids))
        ).scalars().all()}
        rows = []
        for p in roster:
            s = stats.get(p["id"])
            if not s:
                continue
            rows.append(dict(
                player_id=p["id"], name=p["name"], team_id=team.id,
                minutes=s.minutes_per_game or 0.0, pts=s.points or 0.0,
                reb=s.rebounds or 0.0, ast=s.assists or 0.0, tov=s.turnovers or 0.0,
                fga=s.fga or 0.0, fgm=s.fgm or 0.0, fg3m=s.fg3m or 0.0,
                fta=s.fta or 0.0, ftm=s.ftm or 0.0,
                usage=_usage(s.fga or 0.0, s.fta or 0.0, s.turnovers or 0.0),
            ))
        accounts.update(_finalize(rows))
    return accounts


def sim_accounts(db: Session, season: str, tiers: Dict[int, str],
                 config=DRAMA_M3, sims_per_game: int = 1, max_games=None) -> Dict[int, PlayerAccount]:
    """Per-game sim accounts from a schedule replay. `tiers` (from real_accounts)
    are inherited so a player is compared within the role he actually held.
    max_games samples the schedule (every Nth game) for fast gamma sweeps."""
    year = season.split("-")[0][-2:]
    games = db.execute(select(Game).where(
        Game.id.like(f"002{year}%"), Game.status == "final", Game.home_score.isnot(None))
    ).scalars().all()
    if max_games and len(games) > max_games:
        step = len(games) // max_games
        games = games[::step]
    rosters = {}
    _depth = getattr(config, "roster_depth", 10)
    for t in db.execute(select(Team)).scalars().all():
        r = load_roster(db, t.id, season, depth=_depth)
        if r:
            rosters[t.id] = r

    # per-player totals + appearances, and team membership
    tot: Dict[int, dict] = {}
    team_of: Dict[int, int] = {}
    name_of: Dict[int, str] = {}
    for tid, r in rosters.items():
        for p in r:
            team_of[p["id"]] = tid
            name_of[p["id"]] = p["name"]

    for g in games:
        if g.home_team_id not in rosters or g.away_team_id not in rosters:
            continue
        seed = zlib.crc32(str(g.id).encode())
        for k in range(sims_per_game):
            res = simulate_game(
                rosters[g.home_team_id], rosters[g.away_team_id],
                seed=seed + k, season=season, config=config,
                home_team_id=g.home_team_id, away_team_id=g.away_team_id, db=db)
            for pid, b in res["box_score"].items():
                a = tot.setdefault(pid, {k: 0.0 for k in
                    ("g", "min", "pts", "reb", "ast", "tov", "fga", "fgm", "fg3m", "fta", "ftm")})
                a["g"] += 1
                for k2 in ("min", "pts", "reb", "ast", "tov", "fga", "fgm", "fg3m", "fta", "ftm"):
                    a[k2] += b[k2 if k2 != "min" else "min"]

    # group by team, per-game averages
    by_team: Dict[int, List[dict]] = {}
    for pid, a in tot.items():
        g = a["g"] or 1
        by_team.setdefault(team_of.get(pid, -1), []).append(dict(
            player_id=pid, name=name_of.get(pid, str(pid)), team_id=team_of.get(pid, -1),
            minutes=a["min"] / g, pts=a["pts"] / g, reb=a["reb"] / g, ast=a["ast"] / g,
            tov=a["tov"] / g, fga=a["fga"] / g, fgm=a["fgm"] / g, fg3m=a["fg3m"] / g,
            fta=a["fta"] / g, ftm=a["ftm"] / g,
            usage=_usage(a["fga"] / g, a["fta"] / g, a["tov"] / g),
        ))

    accounts: Dict[int, PlayerAccount] = {}
    for rows in by_team.values():
        built = _finalize(rows)
        # override tier with the real one (compare within real role)
        for pid, acc in built.items():
            acc.tier = tiers.get(pid, acc.tier)
        accounts.update(built)
    return accounts


# --------------------------------------------------------------------------
# Decompositions (behavioral owners)
# --------------------------------------------------------------------------
def points_waterfall(real: PlayerAccount, sim: PlayerAccount) -> Dict[str, float]:
    """Δ points = minutes + shot volume (usage) + shot efficiency + FT rate.
    Sequential substitution real->sim so the terms sum exactly to Δ points.
    PTS = M * (FGA/M) * (FG_pts/FGA) + M * (FTM/M)."""
    def parts(a: PlayerAccount):
        m = a.minutes or 1e-9
        fga_pm = a.fga / m
        pts_per_fga = (a.fg_pts / a.fga) if a.fga else 0.0
        ftm_pm = a.ftm / m
        return m, fga_pm, pts_per_fga, ftm_pm

    mr, vr, er, fr = parts(real)
    ms, vs, es, fs = parts(sim)
    p0 = mr * vr * er + mr * fr
    p1 = ms * vr * er + ms * fr          # minutes
    p2 = ms * vs * er + ms * fr          # shot volume (usage)
    p3 = ms * vs * es + ms * fr          # shot efficiency
    p4 = ms * vs * es + ms * fs          # FT rate
    return {"minutes": p1 - p0, "usage": p2 - p1,
            "efficiency": p3 - p2, "ft": p4 - p3, "total": p4 - p0}


def assists_waterfall(real: PlayerAccount, sim: PlayerAccount,
                      team_fgm_real: float, team_fgm_sim: float,
                      team_ast_real: float, team_ast_sim: float) -> Dict[str, float]:
    """Δ assists = team makes + assist rate (attribution) + assist share (ball-handler).
    AST = team_FGM * (team_AST/team_FGM) * (player_AST/team_AST)."""
    def parts(fgm, ast, share):
        ratio = (ast / fgm) if fgm else 0.0
        return fgm, ratio, share
    mr, rr, sr = parts(team_fgm_real, team_ast_real, real.ast_share)
    ms, rs, ss = parts(team_fgm_sim, team_ast_sim, sim.ast_share)
    a0 = mr * rr * sr
    a1 = ms * rr * sr    # team makes
    a2 = ms * rs * sr    # assist rate (attribution)
    a3 = ms * rs * ss    # assist share (ball-handler)
    return {"team_makes": a1 - a0, "attribution": a2 - a1,
            "ball_handler": a3 - a2, "total": a3 - a0}


# --------------------------------------------------------------------------
# Tier report — the primary artifact
# --------------------------------------------------------------------------
def _team_totals(accounts: Dict[int, PlayerAccount]) -> Dict[int, Tuple[float, float, float]]:
    """team_id -> (usage, fgm, ast) per game, summed over the roster."""
    out: Dict[int, list] = {}
    for a in accounts.values():
        t = out.setdefault(a.team_id, [0.0, 0.0, 0.0])
        t[0] += a.usage; t[1] += a.fgm; t[2] += a.ast
    return {k: tuple(v) for k, v in out.items()}


def tier_report(real: Dict[int, PlayerAccount], sim: Dict[int, PlayerAccount], season: str) -> None:
    rt, st = _team_totals(real), _team_totals(sim)
    # group matched players by tier
    tiers: Dict[str, list] = {t: [] for t in TIERS}
    for pid, r in real.items():
        s = sim.get(pid)
        if s:
            tiers[r.tier].append((r, s))

    W = 96
    print("\n" + "=" * W)
    print(f"  Player possession accounting — tier reconciliation  ({season})")
    print("=" * W)
    hdr = f"  {'tier':<10}{'n':>4}{'min r/s':>13}{'usg% r/s':>14}{'PTS r/s':>13}{'AST r/s':>13}{'REB r/s':>13}{'TOV r/s':>12}"
    print(hdr)
    for t in TIERS:
        pairs = tiers[t]
        if not pairs:
            continue
        n = len(pairs)
        avg = lambda f: (sum(f(r) for r, s in pairs) / n, sum(f(s) for r, s in pairs) / n)
        mn = avg(lambda a: a.minutes)
        # tier's share of team usage: sum usage_share within tier, avg over teams
        ur = sum(r.usage_share for r, s in pairs) / n * n / len({r.team_id for r, s in pairs})
        us = sum(s.usage_share for r, s in pairs) / n * n / len({r.team_id for r, s in pairs})
        pt, at, rb, tv = avg(lambda a: a.pts), avg(lambda a: a.ast), avg(lambda a: a.reb), avg(lambda a: a.tov)
        print(f"  {t:<10}{n:>4}{mn[0]:>6.1f}/{mn[1]:>5.1f}{ur*100:>7.0f}/{us*100:>5.0f}"
              f"{pt[0]:>7.1f}/{pt[1]:>4.1f}{at[0]:>7.1f}/{at[1]:>4.1f}"
              f"{rb[0]:>7.1f}/{rb[1]:>4.1f}{tv[0]:>6.1f}/{tv[1]:>4.1f}")

    print("\n  Δ POINTS per player, by tier (minutes / usage / efficiency / ft = total):")
    for t in TIERS:
        pairs = tiers[t]
        if not pairs:
            continue
        agg = {k: 0.0 for k in ("minutes", "usage", "efficiency", "ft", "total")}
        for r, s in pairs:
            for k, v in points_waterfall(r, s).items():
                agg[k] += v
        n = len(pairs)
        print(f"    {t:<10}{agg['minutes']/n:>+7.2f}{agg['usage']/n:>+7.2f}"
              f"{agg['efficiency']/n:>+7.2f}{agg['ft']/n:>+7.2f}  = {agg['total']/n:>+6.2f}")

    print("\n  Δ ASSISTS per player, by tier (team-makes / attribution / ball-handler = total):")
    for t in TIERS:
        pairs = tiers[t]
        if not pairs:
            continue
        agg = {k: 0.0 for k in ("team_makes", "attribution", "ball_handler", "total")}
        for r, s in pairs:
            rtt, stt = rt.get(r.team_id), st.get(s.team_id)
            if not rtt or not stt:
                continue
            for k, v in assists_waterfall(r, s, rtt[1], stt[1], rtt[2], stt[2]).items():
                agg[k] += v
        n = len(pairs)
        print(f"    {t:<10}{agg['team_makes']/n:>+7.2f}{agg['attribution']/n:>+7.2f}"
              f"{agg['ball_handler']/n:>+7.2f}  = {agg['total']/n:>+6.2f}")

    # attribution question: league assist rate on makes
    ast_r = sum(v[2] for v in rt.values()); fgm_r = sum(v[1] for v in rt.values())
    ast_s = sum(v[2] for v in st.values()); fgm_s = sum(v[1] for v in st.values())
    print(f"\n  Assist rate (team AST/FGM):  real {ast_r/fgm_r:.3f}  sim {ast_s/fgm_s:.3f}  "
          f"({'attribution gap' if ast_s < ast_r*0.95 else 'ok'})")
    print("=" * W + "\n")


# playmaker buckets by REAL assist rank within team (usage-tiers wash these out)
_PM_BY_RANK = ["primary", "secondary", "tertiary", "tertiary",
               "minimal", "minimal", "minimal", "minimal", "minimal", "minimal"]


def playmaker_report(real: Dict[int, PlayerAccount], sim: Dict[int, PlayerAccount], season: str) -> None:
    """Assist lens: bucket by real assist rank so lead creators aren't averaged away.
    Separates allocation (assist SHARE of team) from attribution (team AST/FGM)."""
    # rank real players by assists within team -> playmaker bucket
    by_team: Dict[int, list] = {}
    for a in real.values():
        by_team.setdefault(a.team_id, []).append(a)
    bucket_of: Dict[int, str] = {}
    for rows in by_team.values():
        for rank, a in enumerate(sorted(rows, key=lambda x: -x.ast)):
            bucket_of[a.player_id] = _PM_BY_RANK[min(rank, 9)]

    groups: Dict[str, list] = {b: [] for b in ("primary", "secondary", "tertiary", "minimal")}
    for pid, r in real.items():
        s = sim.get(pid)
        if s:
            groups[bucket_of[pid]].append((r, s))

    W = 74
    print("\n" + "=" * W)
    print(f"  Assist / playmaker lens — by real assist rank  ({season})")
    print("=" * W)
    print(f"  {'bucket':<12}{'n':>4}{'AST/g r/s':>16}{'AST share r/s':>18}{'AST/36 r/s':>16}")
    for b in ("primary", "secondary", "tertiary", "minimal"):
        pairs = groups[b]
        if not pairs:
            continue
        n = len(pairs)
        ar = sum(r.ast for r, s in pairs) / n
        as_ = sum(s.ast for r, s in pairs) / n
        shr = sum(r.ast_share for r, s in pairs) / n
        shs = sum(s.ast_share for r, s in pairs) / n
        p36r = sum(r.ast / (r.minutes or 1) * 36 for r, s in pairs) / n
        p36s = sum(s.ast / (s.minutes or 1) * 36 for r, s in pairs) / n
        print(f"  {b:<12}{n:>4}{ar:>8.1f}/{as_:>5.1f}{shr*100:>10.0f}%/{shs*100:>4.0f}%"
              f"{p36r:>9.1f}/{p36s:>4.1f}")
    print("=" * W + "\n")


def run(season: str, sims_per_game: int = 1, gamma: float = None, max_games=None,
        playmaker: bool = False) -> None:
    from dataclasses import replace
    from app.database import SessionLocal
    config = replace(DRAMA_M3, usage_concentration=gamma) if gamma is not None else DRAMA_M3
    db = SessionLocal()
    real = real_accounts(db, season)
    sim = sim_accounts(db, season, {pid: a.tier for pid, a in real.items()},
                       config, sims_per_game, max_games)
    db.close()
    if gamma is not None:
        print(f"  (usage_concentration gamma={gamma})")
    if playmaker:
        playmaker_report(real, sim, season)
    else:
        tier_report(real, sim, season)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2016-17")
    p.add_argument("--sims", type=int, default=1)
    p.add_argument("--gamma", type=float, default=None)
    p.add_argument("--max-games", type=int, default=None)
    p.add_argument("--playmaker", action="store_true")
    args = p.parse_args()
    run(args.season, args.sims, args.gamma, args.max_games, args.playmaker)
