"""SeasonState — authoritative pure model for MyLeague stateful runs.

State at time T = fold(events with applied_at_date <= T, base). Pure:
no I/O, no DB, no RNG side effects. Persistence lives at the caller
(advance_to() in myleague_engine.py).

Design lock summary (M-1a):
- Event with applied_at_date=D affects games on D that have not yet been
  simulated + all subsequent games until superseded by a later event
  for the same target.
- No retroactive mutation: any attempt to insert an event with
  applied_at_date <= any completed game.game_date is rejected at the
  persistence layer (see myleague_engine.append_event).
- current_calendar_date is monotonic; advance_to refuses target < current.
- Off-days are legal: advance_to() over a range with no scheduled games
  moves the cursor forward without simulating anything.

Event contract (M-1a subset):
  SET_UNAVAILABLE  { "team_id": int, "player_id": int }
  SET_AVAILABLE    { "team_id": int, "player_id": int }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, FrozenSet, List, Optional, Set, Tuple


# Event type constants — string values persisted in myleague_events.event_type.
EVENT_SET_UNAVAILABLE = "SET_UNAVAILABLE"
EVENT_SET_AVAILABLE = "SET_AVAILABLE"

ALLOWED_EVENT_TYPES = frozenset({EVENT_SET_UNAVAILABLE, EVENT_SET_AVAILABLE})


@dataclass(frozen=True)
class MyLeagueEventPayload:
    """One event in the mutation log — enough to fold state."""
    event_type: str
    applied_at_date: date
    payload: dict


@dataclass
class SeasonState:
    """Persistent, mutable-over-time authoritative state for a MyLeague run.

    Not the DB row — a hydrated view over (SimulationRun + MyLeagueState +
    all events). Advance protocol produces a NEW SeasonState (rebuilt from
    DB after each advance_to call).
    """
    simulation_id: int
    season: str
    root_seed: int
    controlled_team_id: Optional[int]
    current_calendar_date: date
    # (team_id, player_id) pairs currently marked unavailable at
    # current_calendar_date. Computed by folding the event log.
    unavailable: FrozenSet[Tuple[int, int]] = field(default_factory=frozenset)


def apply_events(
    events: List[MyLeagueEventPayload],
    at_date: date,
) -> FrozenSet[Tuple[int, int]]:
    """Fold events with applied_at_date <= at_date into an availability set.

    Ordering rule (deterministic):
      1. applied_at_date ASC
      2. within a date: input order (caller sorts by DB id if needed)

    Later events supersede earlier ones for the same (team_id, player_id).
    Returns the set of (team_id, player_id) pairs currently OUT.

    M-5a reason-aware override semantics:
      - Every event carries an optional `reason` in its payload
        (default 'user' for legacy events). Auto-generated events
        carry 'injury' (paired with 'recovered').
      - A `reason='recovered'` SET_AVAILABLE only clears an OUT state
        that was set by `reason='injury'`. If the current OUT is user-
        driven (or any non-injury reason), the recovery is a no-op —
        so the auto-recovery event scheduled at injury time never
        unexpectedly overrides a user's manual OUT that landed
        between the injury and the scheduled return.
      - All other SET_AVAILABLE events unconditionally clear OUT.
    """
    # (team_id, player_id) → reason-of-current-OUT (str). Absence = AVAILABLE.
    out_reason: dict = {}
    for ev in sorted(events, key=lambda e: e.applied_at_date):
        if ev.applied_at_date > at_date:
            break
        if ev.event_type not in ALLOWED_EVENT_TYPES:
            # Forward-compat: unknown event types are ignored by the fold
            # rather than raising, so a partially-migrated schema doesn't
            # crash older code paths. Persistence layer validates on write.
            continue
        team_id = ev.payload.get("team_id")
        player_id = ev.payload.get("player_id")
        if team_id is None or player_id is None:
            continue
        key = (team_id, player_id)
        reason = ev.payload.get("reason", "user")
        if ev.event_type == EVENT_SET_UNAVAILABLE:
            out_reason[key] = reason
        elif ev.event_type == EVENT_SET_AVAILABLE:
            if reason == "recovered":
                # Only clear if the current OUT was set by injury —
                # a user-driven OUT (or any non-injury reason) that
                # landed between the injury and the scheduled recovery
                # takes precedence.
                if out_reason.get(key) == "injury":
                    out_reason.pop(key, None)
            else:
                out_reason.pop(key, None)
    return frozenset(out_reason.keys())


def build_state(
    *,
    simulation_id: int,
    season: str,
    root_seed: int,
    controlled_team_id: Optional[int],
    current_calendar_date: date,
    events: List[MyLeagueEventPayload],
) -> SeasonState:
    """Assemble a SeasonState from persisted pieces."""
    return SeasonState(
        simulation_id=simulation_id,
        season=season,
        root_seed=root_seed,
        controlled_team_id=controlled_team_id,
        current_calendar_date=current_calendar_date,
        unavailable=apply_events(events, current_calendar_date),
    )


def filter_available_players(
    players: List[dict],
    team_id: int,
    unavailable: FrozenSet[Tuple[int, int]],
) -> List[dict]:
    """Drop unavailable players from a roster list.

    Kept pure to make advance_to's roster resolution testable without a DB.
    """
    return [p for p in players if (team_id, p["id"]) not in unavailable]
