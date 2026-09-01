"""M-5b UAT — A/B falsification: does turning injuries on materially
change scoring / standings, or is the star-scoring gap a pre-existing
issue independent of M-5b?

Runs two full seasons on the same seed + season:
  A: injury_rate = 0 (baseline)
  B: injury_rate = 0.018 (M-5b calibrated)

Reports side-by-side metrics per Xavier's 2026-08-31 lock:
  - Player-level (Shai + Brunson + top-10 scorers): MPG, PPG,
    games-played, PPG-while-playing (separates missed-games from
    per-game production drop)
  - Team-level: OKC PPG + wins, league median/max PPG,
    teams-above-110/115/120 PPG
  - Injury-level: total episodes, player-games missed,
    episodes-per-player, gap between repeat episodes (immediate vs
    healthy interval)

Usage:
  docker compose exec -e PYTHONPATH=/app api python \\
      scratch/injury_ab_falsification.py --seed 1394 --season 2024-25
"""
import argparse
import statistics
from collections import defaultdict
from datetime import date

from sqlalchemy import select, update

from app.database import SessionLocal
from app.models.game import Game
from app.models.myleague import MyLeagueEvent, MyLeagueState
from app.models.player import Player
from app.models.player_season_stats import PlayerSeasonStats
from app.models.simulation import (
    SimulatedGame,
    SimulatedPlayerLine,
    SimulationRun,
)
from app.models.team import Team
from app.services.myleague_engine import advance_to, create_run
from app.services.sim_config import DRAMA_M3_SEASON


def _cleanup(sim_id: int) -> None:
    db = SessionLocal()
    try:
        sgs = [i for (i,) in db.execute(
            select(SimulatedGame.id).where(SimulatedGame.simulation_id == sim_id)
        ).all()]
        if sgs:
            db.execute(SimulatedPlayerLine.__table__.delete()
                       .where(SimulatedPlayerLine.simulated_game_id.in_(sgs)))
        db.execute(SimulatedGame.__table__.delete().where(SimulatedGame.simulation_id == sim_id))
        st = db.execute(select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)).scalar_one_or_none()
        if st:
            db.execute(MyLeagueEvent.__table__.delete().where(MyLeagueEvent.myleague_state_id == st.id))
            db.execute(MyLeagueState.__table__.delete().where(MyLeagueState.id == st.id))
        db.execute(SimulationRun.__table__.delete().where(SimulationRun.id == sim_id))
        db.commit()
    finally:
        db.close()


def run_season(season: str, seed: int, rate: float, controlled: int) -> int:
    db = SessionLocal()
    try:
        sim, _ = create_run(
            db, season=season, seed=seed,
            controlled_team_id=controlled, config=DRAMA_M3_SEASON,
        )
        params = dict(sim.parameters or {})
        params["injury_config"] = {"rate": rate}
        db.execute(update(SimulationRun).where(SimulationRun.id == sim.id).values(parameters=params))
        db.commit()
        end = date(int(season.split("-")[0]) + 1, 7, 15)
        advance_to(db, simulation_id=sim.id, target_date=end)
        return sim.id
    finally:
        db.close()


def collect_metrics(sim_id: int) -> dict:
    db = SessionLocal()
    try:
        # --- Per-player: MPG, PPG, gp, ppg-while-playing.
        player_stats: dict = defaultdict(lambda: {
            "min": 0.0, "pts": 0, "gp": 0, "team_id": None,
        })
        rows = db.execute(
            select(
                SimulatedPlayerLine.player_id,
                SimulatedPlayerLine.team_id,
                SimulatedPlayerLine.minutes,
                SimulatedPlayerLine.points,
            )
            .join(SimulatedGame, SimulatedGame.id == SimulatedPlayerLine.simulated_game_id)
            .where(SimulatedGame.simulation_id == sim_id)
        ).all()
        for pid, tid, mins, pts in rows:
            s = player_stats[pid]
            s["team_id"] = tid
            s["min"] += mins
            s["pts"] += pts
            s["gp"] += 1

        # Round-derived per-player output.
        derived = {}
        for pid, s in player_stats.items():
            gp = s["gp"]
            if gp == 0:
                continue
            derived[pid] = {
                "team_id": s["team_id"],
                "gp": gp,
                "mpg": round(s["min"] / gp, 1),
                "ppg": round(s["pts"] / gp, 1),   # ppg-while-playing
            }

        # --- League-level: team wins, PPG, team totals.
        # For each SimulatedGame, tally per-team score + record W/L.
        team_stats: dict = defaultdict(lambda: {
            "gp": 0, "pts": 0, "wins": 0, "losses": 0,
        })
        sg_rows = db.execute(
            select(
                SimulatedGame.game_id, SimulatedGame.home_score,
                SimulatedGame.away_score, Game.home_team_id, Game.away_team_id,
            )
            .join(Game, Game.id == SimulatedGame.game_id)
            .where(SimulatedGame.simulation_id == sim_id)
        ).all()
        for game_id, hs, ascore, hid, aid in sg_rows:
            for tid, own, opp in ((hid, hs, ascore), (aid, ascore, hs)):
                team_stats[tid]["gp"] += 1
                team_stats[tid]["pts"] += own
                if own > opp:
                    team_stats[tid]["wins"] += 1
                else:
                    team_stats[tid]["losses"] += 1

        # Derived team output.
        team_derived = {}
        for tid, s in team_stats.items():
            gp = s["gp"]
            if gp == 0:
                continue
            team_derived[tid] = {
                "wins": s["wins"], "losses": s["losses"],
                "ppg": round(s["pts"] / gp, 1),
            }

        # --- Injury stats.
        st_row = db.execute(select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)).scalar_one_or_none()
        injury_events = []
        if st_row:
            events = db.execute(
                select(MyLeagueEvent).where(MyLeagueEvent.myleague_state_id == st_row.id)
                .order_by(MyLeagueEvent.applied_at_date.asc(), MyLeagueEvent.id.asc())
            ).scalars().all()
            injury_events = [e for e in events if (e.payload_json or {}).get("reason") == "injury"]

        # Repeat-injury analysis: episodes per player + gaps between them.
        per_player_episodes: dict = defaultdict(list)
        for e in injury_events:
            pid = (e.payload_json or {}).get("player_id")
            gm = (e.payload_json or {}).get("games_missed") or 0
            per_player_episodes[pid].append({
                "start": e.applied_at_date,
                "games_missed": gm,
            })
        immediate_repeat_count = 0
        healthy_repeat_gaps = []
        for pid, eps in per_player_episodes.items():
            if len(eps) < 2:
                continue
            eps.sort(key=lambda x: x["start"])
            for prev, curr in zip(eps, eps[1:]):
                # crude gap in days
                gap_days = (curr["start"] - prev["start"]).days - prev["games_missed"]
                if gap_days <= 3:
                    immediate_repeat_count += 1
                else:
                    healthy_repeat_gaps.append(gap_days)

        return {
            "sim_id": sim_id,
            "player_stats": derived,
            "team_stats": team_derived,
            "injury_events": len(injury_events),
            "total_player_games_missed": sum(
                (e.payload_json or {}).get("games_missed", 0)
                for e in injury_events
            ),
            "players_with_multiple_injuries": sum(
                1 for eps in per_player_episodes.values() if len(eps) >= 2
            ),
            "immediate_repeat_injuries": immediate_repeat_count,
            "healthy_gap_repeats_median": (
                statistics.median(healthy_repeat_gaps) if healthy_repeat_gaps else 0
            ),
        }
    finally:
        db.close()


def _get_pid(name_substr: str) -> int | None:
    db = SessionLocal()
    try:
        return db.execute(
            select(Player.id).where(Player.full_name.ilike(f"%{name_substr}%")).limit(1)
        ).scalar()
    finally:
        db.close()


def _get_tid(abbr: str) -> int:
    db = SessionLocal()
    try:
        return db.execute(select(Team.id).where(Team.abbreviation == abbr)).scalar_one()
    finally:
        db.close()


def _abbr(tid: int | None) -> str:
    if tid is None:
        return "?"
    db = SessionLocal()
    try:
        r = db.execute(select(Team.abbreviation).where(Team.id == tid)).scalar()
        return r or "?"
    finally:
        db.close()


def _player_name(pid: int) -> str:
    db = SessionLocal()
    try:
        r = db.execute(select(Player.full_name).where(Player.id == pid)).scalar()
        return r or f"#{pid}"
    finally:
        db.close()


def print_comparison(a: dict, b: dict, focus_players: list[int], focus_teams: list[int]) -> None:
    print()
    print("=" * 78)
    print(f"{'METRIC':<40s} {'A (rate=0)':>18s} {'B (rate=0.018)':>18s}")
    print("=" * 78)

    def _row(label, va, vb, fmt="{:.1f}"):
        sa = fmt.format(va) if va is not None else "—"
        sb = fmt.format(vb) if vb is not None else "—"
        print(f"{label:<40s} {sa:>18s} {sb:>18s}")

    # --- Focus players (Shai, Brunson).
    for pid in focus_players:
        name = _player_name(pid)
        pa = a["player_stats"].get(pid)
        pb = b["player_stats"].get(pid)
        print(f"\n[Player] {name}")
        _row("  GP", pa["gp"] if pa else 0, pb["gp"] if pb else 0, "{:d}")
        _row("  MPG (while playing)", pa["mpg"] if pa else 0, pb["mpg"] if pb else 0)
        _row("  PPG (while playing)", pa["ppg"] if pa else 0, pb["ppg"] if pb else 0)

    # --- Focus teams (OKC).
    for tid in focus_teams:
        abbr = _abbr(tid)
        ta = a["team_stats"].get(tid, {})
        tb = b["team_stats"].get(tid, {})
        print(f"\n[Team] {abbr}")
        _row("  Wins", ta.get("wins", 0), tb.get("wins", 0), "{:d}")
        _row("  Losses", ta.get("losses", 0), tb.get("losses", 0), "{:d}")
        _row("  PPG", ta.get("ppg", 0.0), tb.get("ppg", 0.0))

    # --- League distributions.
    def _ppg_summary(m):
        vals = [v["ppg"] for v in m["team_stats"].values()]
        vals.sort()
        return {
            "median": statistics.median(vals) if vals else 0,
            "max": max(vals) if vals else 0,
            "over110": sum(1 for v in vals if v > 110),
            "over115": sum(1 for v in vals if v > 115),
            "over120": sum(1 for v in vals if v > 120),
        }

    la = _ppg_summary(a); lb = _ppg_summary(b)
    print(f"\n[League team-PPG distribution]")
    _row("  Median team PPG", la["median"], lb["median"])
    _row("  Max team PPG", la["max"], lb["max"])
    _row("  Teams > 110 PPG", la["over110"], lb["over110"], "{:d}")
    _row("  Teams > 115 PPG", la["over115"], lb["over115"], "{:d}")
    _row("  Teams > 120 PPG", la["over120"], lb["over120"], "{:d}")

    # --- Standings distribution.
    def _win_summary(m):
        wins = sorted((v["wins"] for v in m["team_stats"].values()), reverse=True)
        return {
            "top": wins[:5] if wins else [],
            "bot": wins[-5:] if wins else [],
            "over50": sum(1 for w in wins if w >= 50),
            "over55": sum(1 for w in wins if w >= 55),
            "over60": sum(1 for w in wins if w >= 60),
            "median": statistics.median(wins) if wins else 0,
        }

    wa = _win_summary(a); wb = _win_summary(b)
    print(f"\n[Standings distribution]")
    _row("  Median wins", wa["median"], wb["median"])
    _row("  Teams >= 50 wins", wa["over50"], wb["over50"], "{:d}")
    _row("  Teams >= 55 wins", wa["over55"], wb["over55"], "{:d}")
    _row("  Teams >= 60 wins", wa["over60"], wb["over60"], "{:d}")
    _row("  Top-5 wins", str(wa["top"]), str(wb["top"]), "{}")
    _row("  Bottom-5 wins", str(wa["bot"]), str(wb["bot"]), "{}")

    # --- Top-10 scorer PPG (aggregate across league).
    def _top10_ppg(m):
        vals = sorted((p["ppg"] for p in m["player_stats"].values()), reverse=True)[:10]
        return statistics.mean(vals) if vals else 0

    _row("Top-10 scorer avg PPG", _top10_ppg(a), _top10_ppg(b))

    # --- League-average player MPG (rotation guys only, gp >= 20).
    def _mpg_avg(m, gp_threshold=20):
        vals = [p["mpg"] for p in m["player_stats"].values() if p["gp"] >= gp_threshold]
        return statistics.mean(vals) if vals else 0

    _row("League avg MPG (gp>=20)", _mpg_avg(a), _mpg_avg(b))

    # --- Injuries.
    print(f"\n[Injuries]")
    _row("  Injury episodes", a["injury_events"], b["injury_events"], "{:d}")
    _row("  Player-games missed", a["total_player_games_missed"],
         b["total_player_games_missed"], "{:d}")
    _row("  Players w/ >=2 injuries", a["players_with_multiple_injuries"],
         b["players_with_multiple_injuries"], "{:d}")
    _row("  Immediate repeats (<=3d gap)", a["immediate_repeat_injuries"],
         b["immediate_repeat_injuries"], "{:d}")
    _row("  Healthy-gap median (days)", a["healthy_gap_repeats_median"],
         b["healthy_gap_repeats_median"], "{:.0f}")

    print("=" * 78)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=1394)
    ap.add_argument("--season", type=str, default="2024-25")
    ap.add_argument("--rate-b", type=float, default=0.018)
    ap.add_argument("--team", type=str, default="OKC")
    args = ap.parse_args()

    controlled = _get_tid(args.team)
    shai_pid = _get_pid("Gilgeous-Alexander")
    brunson_pid = _get_pid("Brunson")
    focus_players = [pid for pid in (shai_pid, brunson_pid) if pid]

    print(f"# A/B falsification — seed={args.seed} season={args.season}")
    print(f"# A: rate=0.0")
    print(f"# B: rate={args.rate_b}")
    print(f"# Controlled team (both): {args.team}")

    print("\n# Running A (baseline)...", flush=True)
    sim_a = run_season(args.season, args.seed, 0.0, controlled)
    try:
        ma = collect_metrics(sim_a)
        print("\n# Running B (M-5b)...", flush=True)
        sim_b = run_season(args.season, args.seed, args.rate_b, controlled)
        try:
            mb = collect_metrics(sim_b)
            print_comparison(ma, mb, focus_players, [controlled])
        finally:
            _cleanup(sim_b)
    finally:
        _cleanup(sim_a)


if __name__ == "__main__":
    main()
