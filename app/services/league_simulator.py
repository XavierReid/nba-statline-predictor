"""League simulation service (Session C-1).

Full 30-team, 1230-game NBA season sim. See:
- project-session-c-design-lock — the semantic + gate spec.
- season_simulator.run_season_simulation — team-scoped analog this mirrors.

Key invariants:
- One Game.id → one persisted SimulatedGame row per SimulationRun.
- Root seed + schedule + config → byte-identical results (reproducibility).
- Schedule integrity validated BEFORE the run starts. Refuses to run on
  a malformed season schedule.
"""
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.game import Game
from app.models.team import Team


EXPECTED_LEAGUE_GAMES = 1230
EXPECTED_TEAMS = 30
EXPECTED_TEAM_GAMES = 82


@dataclass
class ScheduleIntegrityResult:
    """Structured pass/fail report from validate_season_schedule.

    `ok` is True only when every check passed. `failures` is a list of
    human-readable strings describing what went wrong (empty on pass).
    """
    ok: bool
    n_games: int
    n_teams: int
    failures: List[str]
    per_team_game_counts: dict


def season_bounds(season: str) -> Tuple[date, date]:
    """A season 'YYYY-YY' spans Oct 1 of first year to Jul 15 of second."""
    yr = int(season.split("-")[0])
    return date(yr, 10, 1), date(yr + 1, 7, 15)


def validate_season_schedule(
    db: Session, season: str,
) -> ScheduleIntegrityResult:
    """Validate that the season's Game rows form a complete NBA schedule.

    Checks:
      - exactly EXPECTED_LEAGUE_GAMES games in the season window
      - exactly EXPECTED_TEAMS distinct teams
      - every team has exactly EXPECTED_TEAM_GAMES games
      - no duplicate Game.id (DB-enforced but re-verified in-memory)
      - no rows with null date / home_team_id / away_team_id
      - home_team_id != away_team_id for every game
    """
    start, end = season_bounds(season)
    games = db.execute(select(Game).where(and_(
        Game.game_date >= start, Game.game_date <= end,
    ))).scalars().all()
    failures: List[str] = []

    seen_ids: set = set()
    dup_ids: set = set()
    for g in games:
        if g.id in seen_ids:
            dup_ids.add(g.id)
        seen_ids.add(g.id)
    if dup_ids:
        failures.append(
            f"{len(dup_ids)} duplicate Game.id values: "
            f"{sorted(dup_ids)[:5]}{'...' if len(dup_ids) > 5 else ''}"
        )

    null_field_ids = [
        g.id for g in games
        if g.game_date is None or g.home_team_id is None or g.away_team_id is None
    ]
    if null_field_ids:
        failures.append(
            f"{len(null_field_ids)} games with null date/home/away: "
            f"{null_field_ids[:5]}{'...' if len(null_field_ids) > 5 else ''}"
        )

    self_plays = [g.id for g in games if g.home_team_id == g.away_team_id]
    if self_plays:
        failures.append(f"{len(self_plays)} self-play games: {self_plays[:5]}")

    if len(games) != EXPECTED_LEAGUE_GAMES:
        failures.append(
            f"expected {EXPECTED_LEAGUE_GAMES} games, got {len(games)}"
        )

    # Team counts (skip games with null teams to avoid double-error noise)
    per_team: dict = {}
    valid_games = [g for g in games if g.home_team_id and g.away_team_id]
    for g in valid_games:
        per_team[g.home_team_id] = per_team.get(g.home_team_id, 0) + 1
        per_team[g.away_team_id] = per_team.get(g.away_team_id, 0) + 1

    if len(per_team) != EXPECTED_TEAMS:
        failures.append(
            f"expected {EXPECTED_TEAMS} distinct teams, got {len(per_team)}"
        )

    off_count_teams = {
        tid: c for tid, c in per_team.items() if c != EXPECTED_TEAM_GAMES
    }
    if off_count_teams:
        failures.append(
            f"{len(off_count_teams)} teams have game count != "
            f"{EXPECTED_TEAM_GAMES}: {dict(list(off_count_teams.items())[:5])}"
        )

    return ScheduleIntegrityResult(
        ok=not failures,
        n_games=len(games),
        n_teams=len(per_team),
        failures=failures,
        per_team_game_counts=per_team,
    )


class ScheduleIntegrityError(RuntimeError):
    """Raised when validate_season_schedule fails and we refuse to run."""

    def __init__(self, result: ScheduleIntegrityResult):
        self.result = result
        summary = "; ".join(result.failures)
        super().__init__(
            f"schedule integrity check failed for the season: {summary}"
        )


# ---------------------------------------------------------------------------
# Standings computation (derived state — no persistence)
# ---------------------------------------------------------------------------

@dataclass
class TeamStanding:
    rank: int
    team_id: int
    team_abbr: str
    wins: int
    losses: int
    pct: float
    gb: float
    streak: str  # e.g. "W3", "L2", "-" if no games


def compute_standings(
    game_rows: List[Tuple[str, int, int, int, int, str, str]],
) -> List[TeamStanding]:
    """Compute standings from a list of finalized game tuples.

    Row shape: (game_id, home_team_id, away_team_id, home_score, away_score,
    home_team_abbr, away_team_abbr). Rows should be sorted in chronological
    order — the streak computation reads them in the order given (game_id
    is chronological in NBA numbering, so callers ordering by game_id are
    safe).

    Callers should use compute_standings_from_sim below; this pure function
    is designed for the synthetic-standings test.
    """
    # Aggregate wins/losses per team AND record the W/L sequence (per team,
    # in the given order) for streak computation.
    stats: dict = {}  # team_id -> {"abbr": str, "wins": int, "losses": int, "results": list[str]}
    for game_id, home_id, away_id, home_score, away_score, home_abbr, away_abbr in game_rows:
        stats.setdefault(home_id, {"abbr": home_abbr, "wins": 0, "losses": 0, "results": []})
        stats.setdefault(away_id, {"abbr": away_abbr, "wins": 0, "losses": 0, "results": []})
        home_won = home_score > away_score
        if home_won:
            stats[home_id]["wins"] += 1
            stats[home_id]["results"].append("W")
            stats[away_id]["losses"] += 1
            stats[away_id]["results"].append("L")
        else:
            stats[home_id]["losses"] += 1
            stats[home_id]["results"].append("L")
            stats[away_id]["wins"] += 1
            stats[away_id]["results"].append("W")

    # Sort with tie-breakers: W desc, L asc, team_id asc.
    entries = sorted(
        stats.items(),
        key=lambda kv: (-kv[1]["wins"], kv[1]["losses"], kv[0]),
    )
    if not entries:
        return []

    # GB relative to the leader (rank 1).
    leader_w = entries[0][1]["wins"]
    leader_l = entries[0][1]["losses"]

    rows: List[TeamStanding] = []
    for rank_idx, (team_id, s) in enumerate(entries, start=1):
        gp = s["wins"] + s["losses"]
        pct = round(s["wins"] / gp, 3) if gp > 0 else 0.0
        gb = round(
            ((leader_w - s["wins"]) + (s["losses"] - leader_l)) / 2.0, 1
        )
        rows.append(TeamStanding(
            rank=rank_idx, team_id=team_id, team_abbr=s["abbr"],
            wins=s["wins"], losses=s["losses"],
            pct=pct, gb=gb,
            streak=_current_streak(s["results"]),
        ))
    return rows


def _current_streak(results: List[str]) -> str:
    """Trailing streak from a chronologically-ordered W/L list.

    Empty → "-". Otherwise counts consecutive same-letter results from
    the end and returns "W3" / "L2" style.
    """
    if not results:
        return "-"
    last = results[-1]
    n = 0
    for r in reversed(results):
        if r == last:
            n += 1
        else:
            break
    return f"{last}{n}"


# ---------------------------------------------------------------------------
# League simulation background task
# ---------------------------------------------------------------------------
import logging  # noqa: E402
from datetime import datetime  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.simulation import SimulatedGame, SimulationRun  # noqa: E402
from app.services.game_simulator import load_roster, simulate_game  # noqa: E402
from app.services.season_simulator import _game_seed, _persist_game  # noqa: E402

log = logging.getLogger(__name__)


def _fetch_league_schedule(db: Session, season: str) -> List[Game]:
    """All Game rows in the season window, ordered by date (stable tie-break by id)."""
    from sqlalchemy import asc
    start, end = season_bounds(season)
    return list(db.execute(
        select(Game)
        .where(Game.game_date >= start, Game.game_date <= end)
        .order_by(asc(Game.game_date), asc(Game.id))
    ).scalars().all())


def _mark_failed(db: Session, sim: SimulationRun, reason: str) -> None:
    from sqlalchemy import update
    log.error("LeagueSimulation %d failed: %s", sim.id, reason)
    db.execute(
        update(SimulationRun)
        .where(SimulationRun.id == sim.id)
        .values(status="failed",
                completed_at=datetime.utcnow(),
                parameters={**(sim.parameters or {}), "failure_reason": reason})
    )
    db.commit()


def run_league_simulation(simulation_id: int, config: Optional["SimConfig"] = None) -> None:
    """Background task: simulate every game in the season and persist results.

    Enforces:
      - scope='league' + team_id IS NULL (SimulationRun invariant, DB-checked)
      - schedule integrity validation before starting (refuses malformed schedules)
      - deterministic per-game seeds derived from sim.seed via _game_seed
      - idempotent per-game persist via existing UniqueConstraint(sim_id, game_id)
      - cancellation checked between games

    Resume-after-cancel is NOT supported (explicit non-goal in C-1). A cancelled
    run stays cancelled; restart = new SimulationRun.
    """
    from sqlalchemy import update
    db = SessionLocal()
    sim = None
    try:
        sim = db.get(SimulationRun, simulation_id)
        if not sim:
            log.error("SimulationRun %d not found", simulation_id)
            return
        if sim.scope != "league":
            _mark_failed(db, sim, f"expected scope='league', got {sim.scope!r}")
            return
        if sim.team_id is not None:
            _mark_failed(db, sim, f"league scope requires team_id IS NULL, got {sim.team_id}")
            return

        # Schedule integrity gate — refuse to run on a malformed schedule.
        integrity = validate_season_schedule(db, sim.season)
        if not integrity.ok:
            _mark_failed(
                db, sim,
                f"schedule integrity failed: {'; '.join(integrity.failures)}",
            )
            return

        schedule = _fetch_league_schedule(db, sim.season)
        db.execute(
            update(SimulationRun)
            .where(SimulationRun.id == simulation_id)
            .values(parameters={**(sim.parameters or {}), "total_games": len(schedule)})
        )
        db.commit()

        # Roster cache spans all 30 teams; loaded once per team per run.
        roster_cache: dict = {}
        roster_depth = getattr(config, "roster_depth", 10) if config else 10
        roster_pre_negation = (
            getattr(config, "use_pre_negation_probs", True) if config else True
        )

        def get_roster(team_id: int):
            if team_id not in roster_cache:
                roster_cache[team_id] = load_roster(
                    db, team_id, sim.season,
                    depth=roster_depth, pre_negation=roster_pre_negation,
                )
            return roster_cache[team_id]

        completed = 0
        for game in schedule:
            db.refresh(sim)
            if sim.status == "cancelled":
                log.info("LeagueSimulation %d cancelled at game %d/%d",
                         simulation_id, completed, len(schedule))
                return

            home_players = get_roster(game.home_team_id)
            away_players = get_roster(game.away_team_id)
            if not home_players or not away_players:
                log.warning("Missing roster for game %s — skipping", game.id)
                continue

            seed = _game_seed(sim.seed, game.id)
            result = simulate_game(
                home_players, away_players, seed=seed, season=sim.season,
                config=config, db=db,
                home_team_id=game.home_team_id, away_team_id=game.away_team_id,
            )
            _persist_game(db, simulation_id, game, result, home_players, away_players)

            completed += 1
            db.execute(
                update(SimulationRun)
                .where(SimulationRun.id == simulation_id)
                .values(games_completed=completed)
            )
            db.commit()

        db.execute(
            update(SimulationRun)
            .where(SimulationRun.id == simulation_id)
            .values(status="complete", completed_at=datetime.utcnow())
        )
        db.commit()
        log.info("LeagueSimulation %d complete: %d games", simulation_id, completed)

    except Exception as e:
        log.exception("LeagueSimulation %d crashed", simulation_id)
        if sim is not None:
            try:
                _mark_failed(db, sim, f"{type(e).__name__}: {e}")
            except Exception:
                pass
        raise
    finally:
        db.close()
