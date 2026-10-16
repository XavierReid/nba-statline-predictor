"""Resolve a team's roster as it stood on a specific date within a MyLeague.

Base membership comes from PlayerSeasonStats for the team+season. Availability and
trades are folded from the MyLeague event log up to `as_of_date`.

M-6a: TRADE events change team OWNERSHIP only. This module is the single owner of that
overlay: `trade_overlay_at_date` / `myleague_overlay_at` turn the event log into a
`RosterOverlay`, which the sim roster loader (`load_roster(overlay=...)`), the roster
display below, the next-game preview and the drill-in re-simulations all consume, so
every surface agrees on who is on which team on a given date.

Kept as a small standalone module so roster-membership logic (trades, waivers,
mid-season signings) has a single owner and doesn't leak into route/service layers.
"""
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.myleague import MyLeagueEvent, MyLeagueState
from app.models.player import Player
from app.models.player_season_stats import PlayerSeasonStats
from app.services.myleague_state import (
    AVAILABILITY_EVENT_TYPES,
    EVENT_SET_AVAILABLE,
    EVENT_SET_UNAVAILABLE,
    EVENT_TRADE,
    MyLeagueEventPayload,
    apply_events,
    fold_team_overrides,
    parse_trade,
)
from app.services.roster import RosterOverlay


@dataclass(frozen=True)
class RosterMember:
    player_id: int
    name: str
    position: str
    real_gp: int          # from PSS.games_played (for is_starter tiebreak + display)
    real_mpg: float       # for depth-chart ordering
    is_starter: bool      # top-5 by real MPG (roster tier baseline)
    is_available: bool    # folded from events at as_of_date
    out_reason: Optional[str] = None      # M-5b: 'injury' / 'user' / None when available
    out_return_date: Optional[date] = None  # M-5b: from injury event payload


EMPTY_OVERLAY = RosterOverlay()


def load_event_payloads(db: Session, myleague_state_id: int) -> List[MyLeagueEventPayload]:
    """The run's full event log in fold order (applied_at_date, then DB id)."""
    rows = db.execute(
        select(MyLeagueEvent)
        .where(MyLeagueEvent.myleague_state_id == myleague_state_id)
        .order_by(MyLeagueEvent.applied_at_date.asc(), MyLeagueEvent.id.asc())
    ).scalars().all()
    return [
        MyLeagueEventPayload(
            event_type=e.event_type,
            applied_at_date=e.applied_at_date,
            payload=e.payload_json or {},
        )
        for e in rows
    ]


def canonical_base_team_of(db: Session, season: str, player_ids: Iterable[int]) -> Dict[int, int]:
    """Pre-trade team for each player: the PlayerSeasonStats team row, the same source the
    roster display and the M-4 "is rostered" guard use. A player with several team rows
    (real-life midseason trade) resolves to the one with the most games, then the lowest
    team id; the aggregate TOT row (team 0) never counts."""
    ids = sorted(set(player_ids))
    if not ids:
        return {}
    rows = db.execute(
        select(PlayerSeasonStats.player_id, PlayerSeasonStats.team_id, PlayerSeasonStats.games_played)
        .where(PlayerSeasonStats.season == season)
        .where(PlayerSeasonStats.player_id.in_(ids))
        .where(PlayerSeasonStats.team_id.isnot(None))
        .where(PlayerSeasonStats.team_id != 0)
    ).all()
    best: Dict[int, tuple] = {}
    for pid, tid, gp in rows:
        rank = (-(gp or 0), tid)
        if pid not in best or rank < best[pid][0]:
            best[pid] = (rank, tid)
    return {pid: tid for pid, (_rank, tid) in best.items()}


def trade_overlay_at_date(
    db: Session,
    season: str,
    events: List[MyLeagueEventPayload],
    as_of_date: date,
    base_cache: Optional[Dict[int, int]] = None,
) -> RosterOverlay:
    """Ownership overlay for `as_of_date`, or EMPTY_OVERLAY when no trade has taken effect.

    The no-trade path touches nothing (no query, same object), so runs without trades are
    unchanged. `base_cache` lets a caller that asks for many dates (advance_to) reuse the
    base-membership lookups.
    """
    trades = [e for e in events if e.event_type == EVENT_TRADE and e.applied_at_date <= as_of_date]
    if not trades:
        return EMPTY_OVERLAY
    cache = base_cache if base_cache is not None else {}
    pids = {m.player_id for t in trades for m in parse_trade(t.payload)[1]}
    missing = pids - cache.keys()
    if missing:
        cache.update(canonical_base_team_of(db, season, missing))
    overrides = fold_team_overrides(events, as_of_date, {pid: cache[pid] for pid in pids if pid in cache})
    if not overrides:
        return EMPTY_OVERLAY
    return RosterOverlay(overrides=overrides, base_team_of={pid: cache[pid] for pid in overrides})


def myleague_overlay_at(db: Session, sim_id: int, season: str, at_date: date) -> RosterOverlay:
    """Overlay for a MyLeague run on a date (EMPTY_OVERLAY for runs with no state/trades)."""
    state_row = db.execute(
        select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)
    ).scalar_one_or_none()
    if state_row is None:
        return EMPTY_OVERLAY
    return trade_overlay_at_date(db, season, load_event_payloads(db, state_row.id), at_date)


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
    cols = (
        PlayerSeasonStats.player_id,
        PlayerSeasonStats.games_played,
        PlayerSeasonStats.minutes_per_game,
        Player.full_name,
        Player.position,
    )
    rows = db.execute(
        select(*cols)
        .join(Player, Player.id == PlayerSeasonStats.player_id)
        .where(PlayerSeasonStats.team_id == team_id)
        .where(PlayerSeasonStats.season == season)
    ).all()

    state_row = db.execute(
        select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)
    ).scalar_one_or_none()
    events = load_event_payloads(db, state_row.id) if state_row is not None else []

    # Trades: drop every player an overlay moves, then add the ones moved HERE (using the
    # season-stats row of the team they started on). Identity and stats are untouched.
    overlay = trade_overlay_at_date(db, season, events, as_of_date)
    if overlay:
        rows = [r for r in rows if r.player_id not in overlay.overrides]
        moved_in = [
            and_(PlayerSeasonStats.player_id == pid,
                 PlayerSeasonStats.team_id == overlay.base_team_of[pid])
            for pid, owner in sorted(overlay.overrides.items()) if owner == team_id
        ]
        if moved_in:
            rows = rows + db.execute(
                select(*cols)
                .join(Player, Player.id == PlayerSeasonStats.player_id)
                .where(PlayerSeasonStats.season == season)
                .where(or_(*moved_in))
            ).all()

    # Starters = top 5 by real-season MPG (with GP tiebreak). Deterministic.
    ranked = sorted(
        rows,
        key=lambda r: (r.minutes_per_game or 0.0, r.games_played or 0),
        reverse=True,
    )
    starter_ids = {r.player_id for r in ranked[:5]}

    # Fold availability events. `out_meta` (player_id -> (reason, return_date_str)) keeps the
    # LATEST OUT event's reason + return date for players still OUT at as_of_date, so the
    # M-5b chip subtitle can show "OUT (injury) · returns 11/03" without a separate lookup.
    # Keyed by player (a player is on one team), mirroring apply_events, so an injured
    # player who is traded keeps his OUT status and his recovery still clears it.
    unavailable = apply_events(events, as_of_date)
    out_meta: dict = {}
    for e in events:
        if e.applied_at_date > as_of_date:
            break
        if e.event_type not in AVAILABILITY_EVENT_TYPES:
            continue
        pid = e.payload.get("player_id")
        if e.payload.get("team_id") is None or pid is None:
            continue
        if e.event_type == EVENT_SET_UNAVAILABLE:
            out_meta[pid] = (e.payload.get("reason", "user"), e.payload.get("return_date"))
        elif e.event_type == EVENT_SET_AVAILABLE:
            # Only clear meta if this AVAILABLE event actually cleared the OUT —
            # mirrors apply_events' reason-aware semantics (recovered-noop for user OUT).
            current = out_meta.get(pid)
            if e.payload.get("reason", "user") == "recovered":
                if current and current[0] == "injury":
                    out_meta.pop(pid, None)
            else:
                out_meta.pop(pid, None)

    members: List[RosterMember] = []
    seen: set = set()
    for r in rows:
        # Dedupe defensively — the query shouldn't return the same
        # player_id twice for one team+season, but the "no duplicates"
        # invariant demands we not trust that.
        if r.player_id in seen:
            continue
        seen.add(r.player_id)
        meta = out_meta.get(r.player_id)
        out_reason = meta[0] if meta else None
        out_return = None
        if meta and meta[1]:
            try:
                out_return = date.fromisoformat(meta[1])
            except (ValueError, TypeError):
                out_return = None
        members.append(RosterMember(
            player_id=r.player_id,
            name=r.full_name or f"Player {r.player_id}",
            position=r.position or "?",
            real_gp=int(r.games_played or 0),
            real_mpg=float(r.minutes_per_game or 0.0),
            is_starter=r.player_id in starter_ids,
            is_available=(team_id, r.player_id) not in unavailable,
            out_reason=out_reason,
            out_return_date=out_return,
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
