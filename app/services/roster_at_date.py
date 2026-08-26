"""Resolve a team's roster as it stood on a specific date within a MyLeague.

MVP (no trades): the roster is time-invariant — it comes from
PlayerSeasonStats for the team+season. Availability is folded from the
MyLeague event log up to `as_of_date`.

M-6 (trades) will extend this by filtering the base roster set by
trade-event applied_at_date. The endpoint contract already exposes
as_of_date so the frontend stays stable across that change.

Kept as a small standalone module so future roster-membership logic
(trades, waivers, mid-season signings) has a single owner and doesn't
leak into route/service layers.
"""
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.myleague import MyLeagueEvent, MyLeagueState
from app.models.player import Player
from app.models.player_season_stats import PlayerSeasonStats
from app.services.myleague_state import MyLeagueEventPayload, apply_events


@dataclass(frozen=True)
class RosterMember:
    player_id: int
    name: str
    position: str
    real_gp: int          # from PSS.games_played (for is_starter tiebreak + display)
    real_mpg: float       # for depth-chart ordering
    is_starter: bool      # top-5 by real MPG (roster tier baseline)
    is_available: bool    # folded from events at as_of_date


def resolve_team_roster_at_date(
    db: Session,
    sim_id: int,
    team_id: int,
    season: str,
    as_of_date: date,
) -> List[RosterMember]:
    """Return the team's roster as of `as_of_date` in the MyLeague run.

    MVP: base membership = PSS.team_id == team_id for the season. This
    surfaces every player who has a PSS row for the team, including
    those who haven't appeared in any sim game yet (invariant: "roster
    includes players who haven't appeared in a sim game yet").

    Availability comes from the MyLeagueEvent log folded at as_of_date
    via app.services.myleague_state.apply_events.
    """
    # Base roster: all players with a PSS row for team+season.
    # LEFT JOIN Player so a player row without a name (shouldn't happen)
    # still surfaces with a placeholder rather than being silently dropped.
    rows = db.execute(
        select(
            PlayerSeasonStats.player_id,
            PlayerSeasonStats.games_played,
            PlayerSeasonStats.minutes_per_game,
            Player.full_name,
            Player.position,
        )
        .join(Player, Player.id == PlayerSeasonStats.player_id)
        .where(PlayerSeasonStats.team_id == team_id)
        .where(PlayerSeasonStats.season == season)
    ).all()

    # Starters = top 5 by real-season MPG (with GP tiebreak). Deterministic.
    ranked = sorted(
        rows,
        key=lambda r: (r.minutes_per_game or 0.0, r.games_played or 0),
        reverse=True,
    )
    starter_ids = {r.player_id for r in ranked[:5]}

    # Fold availability events.
    state_row = db.execute(
        select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)
    ).scalar_one_or_none()
    unavailable: frozenset = frozenset()
    if state_row is not None:
        event_rows = db.execute(
            select(MyLeagueEvent)
            .where(MyLeagueEvent.myleague_state_id == state_row.id)
            .order_by(MyLeagueEvent.applied_at_date.asc(), MyLeagueEvent.id.asc())
        ).scalars().all()
        payloads = [
            MyLeagueEventPayload(
                event_type=e.event_type,
                applied_at_date=e.applied_at_date,
                payload=e.payload_json or {},
            )
            for e in event_rows
        ]
        unavailable = apply_events(payloads, as_of_date)

    members: List[RosterMember] = []
    seen: set = set()
    for r in rows:
        # Dedupe defensively — the query shouldn't return the same
        # player_id twice for one team+season, but the "no duplicates"
        # invariant demands we not trust that.
        if r.player_id in seen:
            continue
        seen.add(r.player_id)
        members.append(RosterMember(
            player_id=r.player_id,
            name=r.full_name or f"Player {r.player_id}",
            position=r.position or "?",
            real_gp=int(r.games_played or 0),
            real_mpg=float(r.minutes_per_game or 0.0),
            is_starter=r.player_id in starter_ids,
            is_available=(team_id, r.player_id) not in unavailable,
        ))
    return members


def sort_roster_depth_chart(members: List[RosterMember]) -> List[RosterMember]:
    """Depth-chart order: starters first, then rotation/bench by MPG.

    Within each tier, higher real MPG comes first. This is a *display*
    ordering — the caller shouldn't rely on it for correctness.
    """
    return sorted(
        members,
        key=lambda m: (not m.is_starter, -m.real_mpg, -m.real_gp),
    )
