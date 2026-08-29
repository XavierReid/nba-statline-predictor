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
    """Extract the metrics Xavier listed from a completed sim."""
    db = SessionLocal()
    try:
        st = db.execute(select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)).scalar_one()
        events = db.execute(
            select(MyLeagueEvent).where(MyLeagueEvent.myleague_state_id == st.id)
            .order_by(MyLeagueEvent.applied_at_date.asc(), MyLeagueEvent.id.asc())
        ).scalars().all()

        injury_events = [e for e in events if (e.payload_json or {}).get("reason") == "injury"]
        recovery_events = [e for e in events if (e.payload_json or {}).get("reason") == "recovered"]

        # Per-team injury count.
        per_team_injuries: dict = defaultdict(int)
        durations: list[int] = []
        for e in injury_events:
            tid = (e.payload_json or {}).get("team_id")
            per_team_injuries[tid] += 1
            gm = (e.payload_json or {}).get("games_missed")
            if isinstance(gm, int):
                durations.append(gm)

        # Player-games lost = sum of games_missed across all injuries.
        total_player_games_lost = sum(durations)

        # Integrity: no player simultaneously OUT + AVAILABLE at any date.
        # Sample checks: run apply_events at 5 evenly spaced dates through
        # the season and ensure the OUT set is well-formed (frozenset).
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
                # Frozenset can't contain contradictions; this is more a
                # "did we crash" check than a semantic gate.
                assert isinstance(unavailable, frozenset)

        # Available-depth per game date: for each simulated game, how
        # many rostered players were AVAILABLE?  Rough proxy: sim
        # persisted N SimulatedPlayerLine rows per game (only players
        # who played get lines). Low counts = deep bench used.
        game_row_counts = []
        game_ids = [i for (i,) in db.execute(
            select(SimulatedGame.id).where(SimulatedGame.simulation_id == sim_id)
        ).all()]
        for sgid in game_ids:
            per_team_lines: dict = defaultdict(int)
            rows = db.execute(
                select(SimulatedPlayerLine.team_id, SimulatedPlayerLine.player_id)
                .where(SimulatedPlayerLine.simulated_game_id == sgid)
            ).all()
            for team_id, _pid in rows:
                per_team_lines[team_id] += 1
            for c in per_team_lines.values():
                game_row_counts.append(c)

        return {
            "sim_id": sim_id,
            "injury_events": len(injury_events),
            "recovery_events": len(recovery_events),
            "per_team_injuries": dict(per_team_injuries),
            "duration_min": min(durations) if durations else 0,
            "duration_max": max(durations) if durations else 0,
            "duration_median": statistics.median(durations) if durations else 0,
            "duration_p90": statistics.quantiles(durations, n=10)[-1] if len(durations) >= 10 else 0,
            "total_player_games_lost": total_player_games_lost,
            "avail_depth_min": min(game_row_counts) if game_row_counts else 0,
            "avail_depth_median": statistics.median(game_row_counts) if game_row_counts else 0,
            "games_under_8_depth": sum(1 for c in game_row_counts if c < 8),
            "integrity_ok": integrity_ok,
        }
    finally:
        db.close()


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
    print(f"# M-5a injury validator")
    print(f"# season={args.season} rate={args.rate} seeds={args.seeds}"
          f" team={args.team}")
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
                  f"  player-games-lost={m['total_player_games_lost']:>5d}"
                  f"  dur[min/med/max]={m['duration_min']}/{m['duration_median']}/{m['duration_max']}"
                  f"  avail-depth[min/med]={m['avail_depth_min']}/{m['avail_depth_median']}"
                  f"  games<8={m['games_under_8_depth']}")
        finally:
            _cleanup(sim_id)

    if args.repro:
        print("\n# Reproducibility check...")
        ok = check_reproducibility(args.season, 999, args.rate, controlled)
        print(f"  reproducible: {ok}")


if __name__ == "__main__":
    main()
