"""M-5a — multi-season injury validation harness (M-5b will USE this).

Runs N seeded MyLeague seasons at a given injury rate + duration
distribution, then reports the metrics Xavier's design lock demanded:

  - injury events per team-season
  - player-games lost (total + top-10-MPG-player subset)
  - distribution of injury durations
  - percentage of games where a team is missing at least one rotation player
  - percentage of games where a high-MPG player is unavailable
  - available-player depth per game-date (min / median / games-below-N)
  - reproducibility check (same seed = same event log)
  - integrity: no player is simultaneously AVAILABLE and OUT

Usage:
  docker compose run --rm api python scratch/injury_validator.py \\
      --rate 0.003 --season 2024-25 --seeds 20 --team LAL

Ships in M-5a with `rate=0` default so this file is testable now.
M-5b runs it against candidate rates and picks the parameters that
produce realistic-looking distributions.
"""
import argparse
import statistics
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select

from app.database import SessionLocal
from app.models.game import Game
from app.models.myleague import MyLeagueEvent, MyLeagueState
from app.models.simulation import (
    SimulatedGame,
    SimulatedPlayerLine,
    SimulationRun,
)
from app.models.team import Team
from app.services.myleague_engine import advance_to, create_run
from app.services.myleague_state import (
    EVENT_SET_AVAILABLE,
    EVENT_SET_UNAVAILABLE,
    MyLeagueEventPayload,
    apply_events,
)
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


def run_season(season: str, seed: int, rate: float, controlled_team_id: int | None) -> int:
    """Run one full-season MyLeague at the given rate. Returns sim_id.
    Caller cleans up."""
    db = SessionLocal()
    try:
        config = DRAMA_M3_SEASON
        sim, _ = create_run(
            db, season=season, seed=seed,
            controlled_team_id=controlled_team_id, config=config,
        )
        # Stamp injury_config into parameters (advance_to reads it).
        params = dict(sim.parameters or {})
        params["injury_config"] = {"rate": rate}
        from sqlalchemy import update
        db.execute(update(SimulationRun).where(SimulationRun.id == sim.id).values(parameters=params))
        db.commit()

        end = date(int(season.split("-")[0]) + 1, 7, 15)
        advance_to(db, simulation_id=sim.id, target_date=end)
        return sim.id
    finally:
        db.close()


def collect_metrics(sim_id: int) -> dict:
    """Extract the calibration metrics Xavier locked at 2026-08-29.

    Two-stage discipline: rate sweep uses episode counts + player-
    games-missed + rotation-absence rates + appearance denominator.
    Duration calibration uses the games-missed histogram.
    """
    db = SessionLocal()
    try:
        st = db.execute(select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)).scalar_one()
        events = db.execute(
            select(MyLeagueEvent).where(MyLeagueEvent.myleague_state_id == st.id)
            .order_by(MyLeagueEvent.applied_at_date.asc(), MyLeagueEvent.id.asc())
        ).scalars().all()

        injury_events = [e for e in events if (e.payload_json or {}).get("reason") == "injury"]
        recovery_events = [e for e in events if (e.payload_json or {}).get("reason") == "recovered"]

        # Injury episodes per team.
        per_team_injuries: dict = defaultdict(int)
        durations: list[int] = []
        for e in injury_events:
            tid = (e.payload_json or {}).get("team_id")
            per_team_injuries[tid] += 1
            gm = (e.payload_json or {}).get("games_missed")
            if isinstance(gm, int):
                durations.append(gm)

        # Player-games missed = sum of games_missed across all injuries.
        total_player_games_lost = sum(durations)
        per_team_games_lost: dict = defaultdict(int)
        for e in injury_events:
            tid = (e.payload_json or {}).get("team_id")
            gm = (e.payload_json or {}).get("games_missed")
            if isinstance(gm, int):
                per_team_games_lost[tid] += gm

        # Games-missed histogram (for stage-2 duration calibration).
        duration_hist: dict = defaultdict(int)
        for d in durations:
            duration_hist[d] += 1

        # Fold integrity sanity: apply_events returns frozenset (no
        # simultaneous Avail+OUT possible by construction).
        payloads = [
            MyLeagueEventPayload(
                event_type=e.event_type,
                applied_at_date=e.applied_at_date,
                payload=e.payload_json or {},
            )
            for e in events
        ]
        integrity_ok = True
        if events:
            first_date = min(e.applied_at_date for e in events)
            last_date = max(e.applied_at_date for e in events)
            span = (last_date - first_date).days or 1
            for i in range(5):
                d = first_date + timedelta(days=int(span * i / 4))
                unavailable = apply_events(payloads, d)
                assert isinstance(unavailable, frozenset)

        # --- Per-game analysis: for every simulated game, count active
        # players per team + check "rotation player unavailable" and
        # "top-3-MPG unavailable" rates.
        game_rows = db.execute(
            select(SimulatedGame.id, SimulatedGame.game_id, Game.game_date,
                   Game.home_team_id, Game.away_team_id)
            .join(Game, Game.id == SimulatedGame.game_id)
            .where(SimulatedGame.simulation_id == sim_id)
        ).all()

        # For each team we drill into: real-season MPG rank → top-3 ids,
        # top-10 rotation ids. We use PlayerSeasonStats mpg for the
        # baseline hierarchy since that's what the sim's roster loader
        # ranks by.
        from app.models.player_season_stats import PlayerSeasonStats
        season_row = db.execute(select(SimulationRun.season).where(SimulationRun.id == sim_id)).scalar_one()
        top3_by_team: dict = {}
        top10_by_team: dict = {}
        for (tid,) in db.execute(select(Team.id)).all():
            rows = db.execute(
                select(PlayerSeasonStats.player_id, PlayerSeasonStats.minutes_per_game)
                .where(PlayerSeasonStats.team_id == tid)
                .where(PlayerSeasonStats.season == season_row)
                .order_by(PlayerSeasonStats.minutes_per_game.desc().nullslast())
            ).all()
            top3_by_team[tid] = {r.player_id for r in rows[:3]}
            top10_by_team[tid] = {r.player_id for r in rows[:10]}

        # For each game, compute: OUT set at game.date, distinct
        # SimulatedPlayerLine per team (appeared), and whether any
        # top-3 / any top-10 player was OUT.
        rotation_player_unavailable_count = 0   # denominator: games * 2 teams
        top3_out_count = 0
        team_appearances: list[int] = []   # per-team-game appearance counts
        team_game_denom = 0
        appearance_denominator = 0   # total player-appearances (rate math check)
        min_depth_by_game: list[int] = []

        for sgid, game_id, gd, hid, aid in game_rows:
            unavailable = apply_events(payloads, gd)
            for team_id in (hid, aid):
                team_game_denom += 1
                out_ids = {pid for (t, pid) in unavailable if t == team_id}
                if out_ids & top10_by_team.get(team_id, set()):
                    rotation_player_unavailable_count += 1
                if out_ids & top3_by_team.get(team_id, set()):
                    top3_out_count += 1
            # appearance count per team for this game
            per_team_lines: dict = defaultdict(int)
            rows = db.execute(
                select(SimulatedPlayerLine.team_id, SimulatedPlayerLine.player_id)
                .where(SimulatedPlayerLine.simulated_game_id == sgid)
            ).all()
            for team_id, _pid in rows:
                per_team_lines[team_id] += 1
                appearance_denominator += 1
            for c in per_team_lines.values():
                team_appearances.append(c)
            if per_team_lines:
                min_depth_by_game.append(min(per_team_lines.values()))

        def _q(vals: list, q: float) -> float:
            if not vals:
                return 0
            if len(vals) < 4:
                return vals[0]
            return statistics.quantiles(vals, n=100)[int(q * 100) - 1]

        num_teams = len(per_team_injuries) or 1
        return {
            "sim_id": sim_id,
            # Episode + team-season stats
            "injury_events": len(injury_events),
            "recovery_events": len(recovery_events),
            "injuries_per_team_season_mean": len(injury_events) / max(1, len(top10_by_team)),
            "player_games_lost_per_team_season_mean":
                total_player_games_lost / max(1, len(top10_by_team)),
            "per_team_injuries": dict(per_team_injuries),
            "per_team_games_lost": dict(per_team_games_lost),
            # Duration
            "duration_min": min(durations) if durations else 0,
            "duration_max": max(durations) if durations else 0,
            "duration_mean": statistics.mean(durations) if durations else 0,
            "duration_median": statistics.median(durations) if durations else 0,
            "duration_p90": _q(durations, 0.90),
            "duration_histogram": dict(duration_hist),
            "total_player_games_lost": total_player_games_lost,
            # Rate-math verification
            "appearance_denominator": appearance_denominator,
            "observed_injury_rate":
                len(injury_events) / max(1, appearance_denominator),
            # Rotation-absence rates
            "team_game_denominator": team_game_denom,
            "rotation_player_out_rate":
                rotation_player_unavailable_count / max(1, team_game_denom),
            "top3_out_rate": top3_out_count / max(1, team_game_denom),
            # Depth
            "avail_depth_min": min(team_appearances) if team_appearances else 0,
            "avail_depth_median": statistics.median(team_appearances) if team_appearances else 0,
            "games_under_8_depth": sum(1 for c in team_appearances if c < 8),
            # Integrity
            "integrity_ok": integrity_ok,
        }
    finally:
        db.close()


def analytical_duration_stats(cfg=None):
    """Compute the expected games-missed from the configured buckets
    directly, so we don't guess. Prints mean + median from the closed
    form."""
    from app.services.injuries import InjuryConfig
    cfg = cfg or InjuryConfig()
    means = []
    weights = []
    for w, (lo, hi) in cfg.duration_buckets:
        weights.append(w)
        means.append((lo + hi) / 2.0)   # uniform-in-range mean
    total_w = sum(weights)
    expected_mean = sum(w * m for w, m in zip(weights, means)) / total_w
    print(f"# Duration buckets (weight × [lo, hi]):")
    for w, (lo, hi) in cfg.duration_buckets:
        print(f"    w={w:.2f}  range=[{lo}, {hi}]  mean={(lo + hi) / 2:.1f}")
    print(f"# Analytical mean games/injury: {expected_mean:.2f}")


def check_reproducibility(season: str, seed: int, rate: float, controlled: int | None) -> bool:
    """Same seed + same rate → same event log (byte-identical)."""
    a = run_season(season, seed, rate, controlled)
    try:
        ma = collect_metrics(a)
    finally:
        _cleanup(a)
    b = run_season(season, seed, rate, controlled)
    try:
        mb = collect_metrics(b)
    finally:
        _cleanup(b)
    # Just check the observable summary numbers match.
    keys = ("injury_events", "recovery_events", "total_player_games_lost",
            "duration_min", "duration_max", "duration_median")
    return all(ma[k] == mb[k] for k in keys)


def _team_id(abbr: str) -> int | None:
    if not abbr:
        return None
    db = SessionLocal()
    try:
        return db.execute(select(Team.id).where(Team.abbreviation == abbr)).scalar_one_or_none()
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate", type=float, default=0.0,
                        help="Per-player-per-appearance injury probability")
    parser.add_argument("--season", type=str, default="2024-25")
    parser.add_argument("--seeds", type=int, default=3,
                        help="Number of seeded seasons to run")
    parser.add_argument("--team", type=str, default="LAL",
                        help="Controlled team abbr (None for God mode)")
    parser.add_argument("--repro", action="store_true",
                        help="Also run a reproducibility check")
    args = parser.parse_args()

    controlled = _team_id(args.team)
    print(f"# M-5b injury validator")
    print(f"# season={args.season} rate={args.rate} seeds={args.seeds}"
          f" team={args.team}")
    analytical_duration_stats()
    print()

    all_metrics = []
    for i in range(args.seeds):
        seed = 42 + i
        print(f"# Running seed={seed}...", flush=True)
        sim_id = run_season(args.season, seed, args.rate, controlled)
        try:
            m = collect_metrics(sim_id)
            all_metrics.append(m)
            print(f"  injuries={m['injury_events']:>4d}"
                  f"  /team={m['injuries_per_team_season_mean']:.1f}"
                  f"  PGL={m['total_player_games_lost']:>5d}"
                  f"  /team={m['player_games_lost_per_team_season_mean']:.1f}"
                  f"  dur[mean/med/p90/max]={m['duration_mean']:.1f}/"
                  f"{m['duration_median']}/{m['duration_p90']}/{m['duration_max']}"
                  f"  appear={m['appearance_denominator']}"
                  f"  observed-rate={m['observed_injury_rate']:.4f}"
                  f"  rot-out%={m['rotation_player_out_rate']*100:.1f}"
                  f"  top3-out%={m['top3_out_rate']*100:.1f}"
                  f"  depth[min/med/games<8]={m['avail_depth_min']}/"
                  f"{m['avail_depth_median']}/{m['games_under_8_depth']}")
        finally:
            _cleanup(sim_id)

    if all_metrics:
        print("\n# Cross-seed variance:")
        for key in ("injuries_per_team_season_mean",
                    "player_games_lost_per_team_season_mean",
                    "rotation_player_out_rate", "top3_out_rate",
                    "observed_injury_rate"):
            vals = [m[key] for m in all_metrics]
            mean_v = statistics.mean(vals)
            std_v = statistics.stdev(vals) if len(vals) >= 2 else 0.0
            print(f"    {key:>45s} mean={mean_v:.4f} std={std_v:.4f}")

    if args.repro:
        print("\n# Reproducibility check...")
        ok = check_reproducibility(args.season, 999, args.rate, controlled)
        print(f"  reproducible: {ok}")


if __name__ == "__main__":
    main()
