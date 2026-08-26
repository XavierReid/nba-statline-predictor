"""Derive per-player running averages from a SimulationRun's persisted lines.

Scope-agnostic — the same derivation powers MyLeague, Season Sim (team-scope
batch), and Full League Sim modals. See project-myleague-stats-contract for
the locked contract; the math + team_gp semantics work identically for any
scope because Team / League / MyLeague all persist to SimulatedPlayerLine.

Reads only. No cache — fully derivable from the persisted event stream so a
rebuild/replay reproduces the same numbers.
"""
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas.myleague import (
    PlayerMyLeagueReal,
    PlayerMyLeagueSim,
    PlayerMyLeagueStatsBlock,
    PlayerMyLeagueStatsResponse,
)
from app.models.game import Game
from app.models.player import Player
from app.models.player_season_stats import PlayerSeasonStats
from app.models.simulation import (
    SimulatedGame,
    SimulatedPlayerLine,
    SimulationRun,
)
from app.models.team import Team


def _derive_rates(gp: int, mins, pts, reb, ast, stl, blk, tov) -> dict:
    """gp → per-game rates. gp==0 zeros everything (caller reports 0 GP)."""
    if gp <= 0:
        return dict(mpg=0.0, ppg=0.0, rpg=0.0, apg=0.0, spg=0.0, bpg=0.0, topg=0.0)
    return dict(
        mpg=round(mins / gp, 1),
        ppg=round(pts / gp, 1),
        rpg=round(reb / gp, 1),
        apg=round(ast / gp, 1),
        spg=round(stl / gp, 1),
        bpg=round(blk / gp, 1),
        topg=round(tov / gp, 1),
    )


def _pct(made: int, attempted: int) -> Optional[float]:
    """Percentage from aggregate makes/attempts. None on zero attempts.

    Contract mandates totals-first: never average team-level percentages.
    Zero attempts → None so UI can render "—" rather than 0.0%.
    """
    if attempted <= 0:
        return None
    return round(made / attempted, 3)


def derive_player_stats(
    db: Session, sim: SimulationRun, player: Player,
) -> PlayerMyLeagueStatsResponse:
    """Sim vs. real block for one player in one SimulationRun.

    Sim block is DERIVED at request time from SimulatedPlayerLine rows —
    no cache, fully replayable. Aggregate rates are totals-first-then-
    derive; never averages-of-averages. `by_team` preserves per-team
    splits so a future "career split" is a UI change, not a backend
    rewrite. Real block is the same-season PSS row(s), unchanged.

    Works for any scope — team-scope sims have a single team_id in
    by_team, league / myleague may have more.
    """
    # --- Sim block: aggregate SimulatedPlayerLine → totals → derive rates.
    # Query per-team totals in ONE round-trip; the aggregate row is a
    # sum-of-sums, matching what the contract requires (totals-first).
    lines_by_team = db.execute(
        select(
            SimulatedPlayerLine.team_id,
            func.count(SimulatedPlayerLine.id).label("gp"),
            func.coalesce(func.sum(SimulatedPlayerLine.minutes), 0.0).label("mins"),
            func.coalesce(func.sum(SimulatedPlayerLine.points), 0).label("pts"),
            func.coalesce(func.sum(SimulatedPlayerLine.rebounds), 0).label("reb"),
            func.coalesce(func.sum(SimulatedPlayerLine.assists), 0).label("ast"),
            func.coalesce(func.sum(SimulatedPlayerLine.steals), 0).label("stl"),
            func.coalesce(func.sum(SimulatedPlayerLine.blocks), 0).label("blk"),
            func.coalesce(func.sum(SimulatedPlayerLine.turnovers), 0).label("tov"),
            func.coalesce(func.sum(SimulatedPlayerLine.fgm), 0).label("fgm"),
            func.coalesce(func.sum(SimulatedPlayerLine.fga), 0).label("fga"),
            func.coalesce(func.sum(SimulatedPlayerLine.fg3m), 0).label("fg3m"),
            func.coalesce(func.sum(SimulatedPlayerLine.fg3a), 0).label("fg3a"),
            func.coalesce(func.sum(SimulatedPlayerLine.ftm), 0).label("ftm"),
            func.coalesce(func.sum(SimulatedPlayerLine.fta), 0).label("fta"),
        )
        .join(SimulatedGame, SimulatedGame.id == SimulatedPlayerLine.simulated_game_id)
        .where(SimulatedGame.simulation_id == sim.id)
        .where(SimulatedPlayerLine.player_id == player.id)
        .group_by(SimulatedPlayerLine.team_id)
    ).all()

    team_abbr_by_id = {
        t.id: t.abbreviation for t in db.execute(
            select(Team).where(Team.id.in_([r.team_id for r in lines_by_team]))
        ).scalars()
    } if lines_by_team else {}

    by_team_blocks: list[PlayerMyLeagueStatsBlock] = []
    tot_gp = tot_mins = tot_pts = tot_reb = tot_ast = tot_stl = tot_blk = tot_tov = 0
    tot_fgm = tot_fga = tot_fg3m = tot_fg3a = tot_ftm = tot_fta = 0
    for r in lines_by_team:
        rates = _derive_rates(r.gp, r.mins, r.pts, r.reb, r.ast, r.stl, r.blk, r.tov)
        by_team_blocks.append(PlayerMyLeagueStatsBlock(
            team_abbr=team_abbr_by_id.get(r.team_id, "?"),
            gp=r.gp,
            **rates,
            fg_pct=_pct(r.fgm, r.fga),
            fg3_pct=_pct(r.fg3m, r.fg3a),
            ft_pct=_pct(r.ftm, r.fta),
        ))
        tot_gp += r.gp
        tot_mins += float(r.mins); tot_pts += r.pts; tot_reb += r.reb
        tot_ast += r.ast; tot_stl += r.stl; tot_blk += r.blk; tot_tov += r.tov
        tot_fgm += r.fgm; tot_fga += r.fga
        tot_fg3m += r.fg3m; tot_fg3a += r.fg3a
        tot_ftm += r.ftm; tot_fta += r.fta

    # --- team_gp: games played by teams the player was rostered on in this
    # sim. Team scope: rostered_team_ids is the one team_id on the sim
    # (via PSS). League / MyLeague: any team(s) the player was on for the
    # season. Trade-period filtering is future work when M-6 trades ship.
    rostered_team_ids = [
        tid for (tid,) in db.execute(
            select(PlayerSeasonStats.team_id)
            .where(PlayerSeasonStats.player_id == player.id)
            .where(PlayerSeasonStats.season == sim.season)
            .where(PlayerSeasonStats.team_id.isnot(None))
            .distinct()
        ).all()
    ]
    # Union any teams the player has ACTUALLY appeared for in this sim
    # (defensive — if rostering diverges from what PSS says, prefer the
    # observed truth so team_gp is never smaller than gp).
    rostered_team_ids = list({*rostered_team_ids, *(r.team_id for r in lines_by_team)})
    team_gp = 0
    if rostered_team_ids:
        team_gp = db.execute(
            select(func.count(SimulatedGame.id))
            .join(Game, Game.id == SimulatedGame.game_id)
            .where(SimulatedGame.simulation_id == sim.id)
            .where(
                (Game.home_team_id.in_(rostered_team_ids))
                | (Game.away_team_id.in_(rostered_team_ids))
            )
        ).scalar() or 0

    sim_rates = _derive_rates(
        tot_gp, tot_mins, tot_pts, tot_reb, tot_ast, tot_stl, tot_blk, tot_tov,
    )
    sim_block = PlayerMyLeagueSim(
        gp=tot_gp,
        team_gp=team_gp,
        **sim_rates,
        fg_pct=_pct(tot_fgm, tot_fga),
        fg3_pct=_pct(tot_fg3m, tot_fg3a),
        ft_pct=_pct(tot_ftm, tot_fta),
        by_team=by_team_blocks,
    )

    # --- Real block: same-season PSS. Sum across rows if the player was
    # traded in real life (two team rows for one season) — same totals-
    # first policy so cross-team real stats never blend as averages.
    pss_rows = db.execute(
        select(PlayerSeasonStats)
        .where(PlayerSeasonStats.player_id == player.id)
        .where(PlayerSeasonStats.season == sim.season)
    ).scalars().all()
    real_block: Optional[PlayerMyLeagueReal] = None
    if pss_rows:
        r_gp = 0
        r_mins = r_pts = r_reb = r_ast = r_stl = r_blk = r_tov = 0.0
        r_fgm = r_fga = r_fg3m = r_fg3a = r_ftm = r_fta = 0.0
        for row in pss_rows:
            gp = row.games_played or 0
            if gp <= 0:
                continue
            r_gp += gp
            r_mins += (row.minutes_per_game or 0.0) * gp
            r_pts += (row.points or 0.0) * gp
            r_reb += (row.rebounds or 0.0) * gp
            r_ast += (row.assists or 0.0) * gp
            r_stl += (row.steals or 0.0) * gp
            r_blk += (row.blocks or 0.0) * gp
            r_tov += (row.turnovers or 0.0) * gp
            r_fgm += (row.fgm or 0.0) * gp
            r_fga += (row.fga or 0.0) * gp
            r_fg3m += (row.fg3m or 0.0) * gp
            r_fg3a += (row.fg3a or 0.0) * gp
            r_ftm += (row.ftm or 0.0) * gp
            r_fta += (row.fta or 0.0) * gp
        if r_gp > 0:
            real_rates = _derive_rates(r_gp, r_mins, r_pts, r_reb, r_ast, r_stl, r_blk, r_tov)
            real_block = PlayerMyLeagueReal(
                gp=r_gp,
                **real_rates,
                fg_pct=_pct(int(round(r_fgm)), int(round(r_fga))),
                fg3_pct=_pct(int(round(r_fg3m)), int(round(r_fg3a))),
                ft_pct=_pct(int(round(r_ftm)), int(round(r_fta))),
            )

    return PlayerMyLeagueStatsResponse(
        player_id=player.id,
        name=player.full_name,
        season=sim.season,
        sim=sim_block,
        real=real_block,
    )
