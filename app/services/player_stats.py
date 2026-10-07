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


_SIM_SUM_FIELDS = (
    ("mins", "minutes", 0.0), ("pts", "points", 0), ("reb", "rebounds", 0),
    ("ast", "assists", 0), ("stl", "steals", 0), ("blk", "blocks", 0),
    ("tov", "turnovers", 0), ("fgm", "fgm", 0), ("fga", "fga", 0),
    ("fg3m", "fg3m", 0), ("fg3a", "fg3a", 0), ("ftm", "ftm", 0), ("fta", "fta", 0),
)


def _sim_sum_columns() -> list:
    """Per-group totals of SimulatedPlayerLine — the sum-of-sums the contract requires."""
    return [func.count(SimulatedPlayerLine.id).label("gp")] + [
        func.coalesce(func.sum(getattr(SimulatedPlayerLine, attr)), zero).label(label)
        for label, attr, zero in _SIM_SUM_FIELDS
    ]


def _build_sim_block(
    rows: list, team_abbr_by_id: dict, team_gp: int,
) -> PlayerMyLeagueSim:
    """Totals-first: per-team blocks from each grouped row, overall block from the summed totals."""
    by_team_blocks: list[PlayerMyLeagueStatsBlock] = []
    tot_gp = tot_mins = tot_pts = tot_reb = tot_ast = tot_stl = tot_blk = tot_tov = 0
    tot_fgm = tot_fga = tot_fg3m = tot_fg3a = tot_ftm = tot_fta = 0
    for r in rows:
        by_team_blocks.append(PlayerMyLeagueStatsBlock(
            team_abbr=team_abbr_by_id.get(r.team_id, "?"),
            gp=r.gp,
            **_derive_rates(r.gp, r.mins, r.pts, r.reb, r.ast, r.stl, r.blk, r.tov),
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
    return PlayerMyLeagueSim(
        gp=tot_gp,
        team_gp=team_gp,
        **_derive_rates(tot_gp, tot_mins, tot_pts, tot_reb, tot_ast, tot_stl, tot_blk, tot_tov),
        fg_pct=_pct(tot_fgm, tot_fga),
        fg3_pct=_pct(tot_fg3m, tot_fg3a),
        ft_pct=_pct(tot_ftm, tot_fta),
        by_team=by_team_blocks,
    )


def _build_real_block(pss_rows: list) -> Optional[PlayerMyLeagueReal]:
    """Same-season PSS rows → one block. Sums across rows if the player was traded in real
    life (two team rows for one season); weights per-game averages by GP so cross-team
    real stats never blend as averages-of-averages. None when no games were played."""
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
    if r_gp <= 0:
        return None
    return PlayerMyLeagueReal(
        gp=r_gp,
        **_derive_rates(r_gp, r_mins, r_pts, r_reb, r_ast, r_stl, r_blk, r_tov),
        fg_pct=_pct(int(round(r_fgm)), int(round(r_fga))),
        fg3_pct=_pct(int(round(r_fg3m)), int(round(r_fg3a))),
        ft_pct=_pct(int(round(r_ftm)), int(round(r_fta))),
    )


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
    lines_by_team = db.execute(
        select(SimulatedPlayerLine.team_id, *_sim_sum_columns())
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

    # team_gp: games played by teams the player was rostered on in this
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

    pss_rows = db.execute(
        select(PlayerSeasonStats)
        .where(PlayerSeasonStats.player_id == player.id)
        .where(PlayerSeasonStats.season == sim.season)
    ).scalars().all()

    return PlayerMyLeagueStatsResponse(
        player_id=player.id,
        name=player.full_name,
        season=sim.season,
        sim=_build_sim_block(lines_by_team, team_abbr_by_id, team_gp),
        real=_build_real_block(pss_rows),
    )


def derive_bulk_player_stats(
    db: Session,
    sim: SimulationRun,
    player_ids: list[int],
) -> dict[int, tuple[Optional[PlayerMyLeagueSim], Optional[PlayerMyLeagueReal]]]:
    """Batch version — one grouped SQL per source (sim, real).

    Returns {player_id: (sim_block or None, real_block or None)}. Used by
    the M-3 team drill-in so a 15-player roster is one bulk query, not N
    per-player round-trips.

    A player_id absent from `player_ids` maps to nothing. A player_id
    present but with no sim lines yields sim block with gp=0 (and the
    caller decides whether to render sim or real). A player_id with no
    PSS row yields real=None.
    """
    if not player_ids:
        return {}

    # --- sim block per player, grouped by (player_id, team_id) so we
    # preserve by_team for future UI. Same math as derive_player_stats
    # but bulk.
    per_player_team = db.execute(
        select(SimulatedPlayerLine.player_id, SimulatedPlayerLine.team_id, *_sim_sum_columns())
        .join(SimulatedGame, SimulatedGame.id == SimulatedPlayerLine.simulated_game_id)
        .where(SimulatedGame.simulation_id == sim.id)
        .where(SimulatedPlayerLine.player_id.in_(player_ids))
        .group_by(SimulatedPlayerLine.player_id, SimulatedPlayerLine.team_id)
    ).all()

    # Group by player.
    by_player: dict[int, list] = {}
    all_team_ids: set[int] = set()
    for r in per_player_team:
        by_player.setdefault(r.player_id, []).append(r)
        all_team_ids.add(r.team_id)

    team_abbr_by_id = {
        t.id: t.abbreviation for t in db.execute(
            select(Team).where(Team.id.in_(all_team_ids))
        ).scalars()
    } if all_team_ids else {}

    # For team_gp, we need games-played-by-team for EACH team a roster
    # member is rostered on. For MVP where roster is single-team, every
    # player on this team shares the same team_gp. Bulk it: query games
    # played by any team the player was rostered on, in this sim.
    rostered_teams_per_player: dict[int, set[int]] = {}
    pss_rostered = db.execute(
        select(PlayerSeasonStats.player_id, PlayerSeasonStats.team_id)
        .where(PlayerSeasonStats.player_id.in_(player_ids))
        .where(PlayerSeasonStats.season == sim.season)
        .where(PlayerSeasonStats.team_id.isnot(None))
    ).all()
    for pid, tid in pss_rostered:
        rostered_teams_per_player.setdefault(pid, set()).add(tid)
    # Union observed sim teams (defensive; matches single-player path).
    for pid, rows in by_player.items():
        for r in rows:
            rostered_teams_per_player.setdefault(pid, set()).add(r.team_id)

    # Games-per-team in this sim (one bulk query).
    team_games_row = db.execute(
        select(
            Game.home_team_id.label("home_id"),
            Game.away_team_id.label("away_id"),
        )
        .join(SimulatedGame, SimulatedGame.game_id == Game.id)
        .where(SimulatedGame.simulation_id == sim.id)
    ).all()
    games_played_by_team: dict[int, int] = {}
    for tg in team_games_row:
        games_played_by_team[tg.home_id] = games_played_by_team.get(tg.home_id, 0) + 1
        games_played_by_team[tg.away_id] = games_played_by_team.get(tg.away_id, 0) + 1

    # --- real block per player, grouped from PSS.
    pss_rows = db.execute(
        select(PlayerSeasonStats)
        .where(PlayerSeasonStats.player_id.in_(player_ids))
        .where(PlayerSeasonStats.season == sim.season)
    ).scalars().all()
    real_by_player: dict[int, list[PlayerSeasonStats]] = {}
    for row in pss_rows:
        real_by_player.setdefault(row.player_id, []).append(row)

    result: dict[int, tuple[Optional[PlayerMyLeagueSim], Optional[PlayerMyLeagueReal]]] = {}
    for pid in player_ids:
        # team_gp: sum games-played by any team this player was rostered on.
        team_gp = sum(
            games_played_by_team.get(tid, 0)
            for tid in rostered_teams_per_player.get(pid, set())
        )
        result[pid] = (
            _build_sim_block(by_player.get(pid, []), team_abbr_by_id, team_gp),
            _build_real_block(real_by_player.get(pid, [])),
        )
    return result
