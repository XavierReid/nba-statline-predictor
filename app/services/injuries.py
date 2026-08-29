"""M-5a — Random injury generator (infrastructure, calibration deferred).

Ships the injury pipeline shape so M-5b can turn it on and calibrate.
Default `rate = 0.0` means no live injuries — downstream behavior is
unchanged and existing tests keep passing byte-identically.

Design lock (Xavier, 2026-08-29):
  - Between-game draws (post-sim), not mid-game
  - Only players who APPEARED in the game are injury-eligible
  - Game-based duration (drawn via schedule lookup), not calendar days
  - Paired SET_UNAVAILABLE (reason=injury) + SET_AVAILABLE
    (reason=recovered) events
  - Override semantics: if user manually flips OUT after the injury,
    the auto-recovery is a no-op (see apply_events reason-aware fold)
  - All 30 teams league-wide (not just controlled)
  - Deterministic per game seed (RNG passed in, not module-global)
  - M-5a infrastructure only; M-5b turns rate on + calibrates against
    project-full-league-realism-audit-style validator

Written directly to the event table rather than through
myleague_engine.append_event because the engine's M-4 guards enforce
controlled_team_id matching — injuries must be able to fire for any
team. The injury generator is trusted: it always writes future-dated
events (game_date + 1), so the retroactive-mutation invariant holds
by construction.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional, Tuple
import random

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.game import Game
from app.models.myleague import MyLeagueEvent
from app.services.myleague_state import EVENT_SET_AVAILABLE, EVENT_SET_UNAVAILABLE


# --- Config ---------------------------------------------------------------

@dataclass(frozen=True)
class InjuryConfig:
    """Injury generation knobs. All game-based semantics per Xavier's
    2026-08-29 lock — durations are drawn as # of team-games missed,
    then the recovery date is computed from the actual schedule.

    Default `rate = 0.0` ships with no live injuries. M-5b calibrates.
    """
    rate: float = 0.0
    # Weighted duration buckets: (probability_weight, (min_games, max_games))
    # These are placeholder shapes; M-5b calibrates against real NBA data.
    duration_buckets: Tuple[Tuple[float, Tuple[int, int]], ...] = (
        (0.60, (1, 3)),      # day-to-day (60%)
        (0.25, (4, 8)),      # short-term (25%)
        (0.15, (10, 25)),    # long-term (15%)
    )


# --- Duration + return-date helpers ---------------------------------------

def draw_injury_duration_games(rng: random.Random, cfg: InjuryConfig) -> int:
    """Discrete # of team-games missed. Weighted pick over buckets,
    uniform within a bucket."""
    r = rng.random()
    cum = 0.0
    for weight, (lo, hi) in cfg.duration_buckets:
        cum += weight
        if r <= cum:
            return rng.randint(lo, hi)
    # Fallback (weights should sum to 1.0 but be safe).
    lo, hi = cfg.duration_buckets[-1][1]
    return rng.randint(lo, hi)


def compute_return_date(
    db: Session,
    season: str,
    team_id: int,
    injury_effective_date: date,
    games_missed: int,
) -> date:
    """Schedule-driven return date: look at the team's scheduled games
    starting from `injury_effective_date`, count `games_missed` of
    them, return the day AFTER the last missed game.

    If the team has fewer than `games_missed` games remaining in the
    season, return the day after their last scheduled game.
    """
    from app.services.league_simulator import season_bounds
    start, end = season_bounds(season)
    rows = db.execute(
        select(Game.game_date)
        .where(Game.game_date >= start, Game.game_date <= end)
        .where(Game.game_date >= injury_effective_date)
        .where(or_(Game.home_team_id == team_id, Game.away_team_id == team_id))
        .order_by(Game.game_date.asc(), Game.id.asc())
        .limit(games_missed)
    ).scalars().all()
    if not rows:
        return injury_effective_date + timedelta(days=1)
    last_missed = rows[-1]
    return last_missed + timedelta(days=1)


# --- Injury generator -----------------------------------------------------

@dataclass(frozen=True)
class GeneratedInjury:
    team_id: int
    player_id: int
    out_from_date: date
    games_missed: int
    return_date: date


def generate_injuries_for_game(
    rng: random.Random,
    cfg: InjuryConfig,
    db: Session,
    season: str,
    game_date: date,
    home_team_id: int,
    away_team_id: int,
    home_appeared_ids: List[int],
    away_appeared_ids: List[int],
) -> List[GeneratedInjury]:
    """Roll per-player injury draws for the players who appeared.
    Returns descriptors with `out_from_date = game_date + 1` (satisfies
    the M-1a retroactive-guard by construction) and a `return_date`
    computed from the team's actual schedule.

    Deterministic per-`rng` — pass the game seed's RNG.
    """
    if cfg.rate <= 0.0:
        return []
    out_from = game_date + timedelta(days=1)
    injuries: List[GeneratedInjury] = []
    for team_id, appeared_ids in (
        (home_team_id, home_appeared_ids),
        (away_team_id, away_appeared_ids),
    ):
        for player_id in appeared_ids:
            if rng.random() < cfg.rate:
                games_missed = draw_injury_duration_games(rng, cfg)
                return_date = compute_return_date(
                    db, season, team_id, out_from, games_missed,
                )
                injuries.append(GeneratedInjury(
                    team_id=team_id,
                    player_id=player_id,
                    out_from_date=out_from,
                    games_missed=games_missed,
                    return_date=return_date,
                ))
    return injuries


def write_injury_events(
    db: Session,
    state_row_id: int,
    injury: GeneratedInjury,
) -> None:
    """Write the paired SET_UNAVAILABLE (reason=injury) +
    SET_AVAILABLE (reason=recovered) events for one injury.

    Direct DB write, bypassing myleague_engine.append_event, because
    the engine's M-4 controlled-team guard would reject events for any
    team other than the user's franchise. Injuries are league-wide and
    trusted by construction: out_from = game_date + 1 satisfies the
    retroactive guard; the injury generator only fires on rostered
    players who appeared.
    """
    db.add(MyLeagueEvent(
        myleague_state_id=state_row_id,
        event_type=EVENT_SET_UNAVAILABLE,
        applied_at_date=injury.out_from_date,
        payload_json={
            "team_id": injury.team_id,
            "player_id": injury.player_id,
            "reason": "injury",
            "games_missed": injury.games_missed,
            "return_date": injury.return_date.isoformat(),
        },
    ))
    db.add(MyLeagueEvent(
        myleague_state_id=state_row_id,
        event_type=EVENT_SET_AVAILABLE,
        applied_at_date=injury.return_date,
        payload_json={
            "team_id": injury.team_id,
            "player_id": injury.player_id,
            "reason": "recovered",
        },
    ))
