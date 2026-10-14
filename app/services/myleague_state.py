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

M-6a (trades, design lock project-session-m6-design-lock):
  TRADE            { "trade_id": str|int,
                     "moves": [{"player_id", "from_team_id", "to_team_id"}, ...] }
  One atomic event between exactly two teams, any number of players. It changes
  team OWNERSHIP only; player identity and data are never touched. TRADE is folded
  here but is deliberately NOT in ALLOWED_EVENT_TYPES yet: the write path
  (myleague_engine.append_event) stays closed until its validation lands.
  v1 scope, on purpose and not by omission: there are no CPU-to-CPU trades and no
  CPU trade checks when a run has no controlled team.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, FrozenSet, List, Mapping, Optional, Tuple


# Event type constants — string values persisted in myleague_events.event_type.
EVENT_SET_UNAVAILABLE = "SET_UNAVAILABLE"
EVENT_SET_AVAILABLE = "SET_AVAILABLE"

ALLOWED_EVENT_TYPES = frozenset({EVENT_SET_UNAVAILABLE, EVENT_SET_AVAILABLE})

# Folded by the pure functions below but not yet accepted on the write path.
EVENT_TRADE = "TRADE"


class MalformedTradeError(ValueError):
    """A TRADE payload is structurally invalid. Rejected whole, never partially applied."""


class InconsistentTradeError(ValueError):
    """A TRADE contradicts the ownership implied by the base membership and earlier trades."""


@dataclass(frozen=True)
class TradeMove:
    player_id: int
    from_team_id: int
    to_team_id: int


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


def _is_id(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def parse_trade(payload) -> Tuple[object, Tuple[TradeMove, ...]]:
    """Validate a TRADE payload and return (trade_id, moves) in input order.

    Rejects anything malformed as a whole: missing/empty trade_id or moves, non-integer ids,
    a move to the same team, a player appearing twice, or a trade that does not involve
    exactly two distinct teams.
    """
    if not isinstance(payload, dict):
        raise MalformedTradeError("TRADE payload must be an object")
    trade_id = payload.get("trade_id")
    if trade_id is None or isinstance(trade_id, bool) or (
        not isinstance(trade_id, (str, int)) or trade_id == ""
    ):
        raise MalformedTradeError("TRADE requires a non-empty trade_id (str or int)")
    raw_moves = payload.get("moves")
    if not isinstance(raw_moves, (list, tuple)) or not raw_moves:
        raise MalformedTradeError("TRADE requires a non-empty list of moves")
    moves = []
    seen_players = set()
    teams = set()
    for m in raw_moves:
        if not isinstance(m, dict):
            raise MalformedTradeError(f"move must be an object, got {m!r}")
        pid, frm, to = m.get("player_id"), m.get("from_team_id"), m.get("to_team_id")
        if not (_is_id(pid) and _is_id(frm) and _is_id(to)):
            raise MalformedTradeError(f"move needs integer player_id/from_team_id/to_team_id: {m!r}")
        if frm == to:
            raise MalformedTradeError(f"move sends player {pid} to the team that already holds him")
        if pid in seen_players:
            raise MalformedTradeError(f"player {pid} appears in more than one move")
        seen_players.add(pid)
        teams.update((frm, to))
        moves.append(TradeMove(pid, frm, to))
    if len(teams) != 2:
        raise MalformedTradeError(f"TRADE must involve exactly two teams, got {sorted(teams)}")
    return trade_id, tuple(moves)


def _ordered_through(events: List[MyLeagueEventPayload], at_date: date):
    """Events with applied_at_date <= at_date: date ASC, input order within a date (stable)."""
    for ev in sorted(events, key=lambda e: e.applied_at_date):
        if ev.applied_at_date > at_date:
            break
        yield ev


def _register_trade(seen: dict, payload) -> Optional[Tuple[TradeMove, ...]]:
    """Parse a TRADE and de-duplicate by trade_id. Returns None for an identical replay,
    raises MalformedTradeError when a trade_id is reused with different moves."""
    trade_id, moves = parse_trade(payload)
    if trade_id in seen:
        if frozenset(seen[trade_id]) != frozenset(moves):
            raise MalformedTradeError(f"trade_id {trade_id!r} reused with different moves")
        return None
    seen[trade_id] = moves
    return moves


def fold_team_overrides(
    events: List[MyLeagueEventPayload],
    at_date: date,
    base_team_of: Mapping[int, int],
) -> Dict[int, int]:
    """Fold TRADE events up to at_date into {player_id: team_id} ownership overrides.

    `base_team_of` is the pre-trade membership (player_id -> team_id) and is never mutated.
    Only players whose team differs from the base appear in the result, so a round trip
    back to the original team yields no override. Pure and deterministic: replaying the same
    history gives the same answer. Every trade is validated against current ownership
    (base + earlier trades) BEFORE any of its moves are applied, and any failure raises
    instead of returning a partial overlay.
    """
    overrides: Dict[int, int] = {}
    seen: dict = {}
    for ev in _ordered_through(events, at_date):
        if ev.event_type != EVENT_TRADE:
            continue
        moves = _register_trade(seen, ev.payload)
        if moves is None:
            continue
        for m in moves:
            holder = overrides.get(m.player_id, base_team_of.get(m.player_id))
            if holder != m.from_team_id:
                raise InconsistentTradeError(
                    f"trade {ev.payload.get('trade_id')!r}: player {m.player_id} is held by "
                    f"{holder!r}, not {m.from_team_id}"
                )
        for m in moves:
            overrides[m.player_id] = m.to_team_id
    return {pid: team for pid, team in overrides.items() if base_team_of.get(pid) != team}


def apply_events(
    events: List[MyLeagueEventPayload],
    at_date: date,
) -> FrozenSet[Tuple[int, int]]:
    """Fold events with applied_at_date <= at_date into an availability set.

    Ordering rule (deterministic):
      1. applied_at_date ASC
      2. within a date: input order (caller sorts by DB id if needed)

    Availability state is keyed by PLAYER, since a player can only be on one team: a later
    event for the same player supersedes an earlier one whichever team id it carries. That
    is what lets a recovery event scheduled before a trade (old team id) still clear the OUT
    after it. The returned (team_id, player_id) pairs attribute each OUT player to the team
    he is on at at_date: the team named by his latest SET_UNAVAILABLE, moved by any later
    TRADE. A SET_AVAILABLE never changes a player's team attribution.

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
    out_reason: dict = {}   # player_id -> reason of the current OUT (absence = AVAILABLE)
    team_of: dict = {}      # player_id -> team he is currently attributed to (OUT players)
    seen_trades: dict = {}
    for ev in _ordered_through(events, at_date):
        if ev.event_type == EVENT_TRADE:
            moves = _register_trade(seen_trades, ev.payload)
            for m in moves or ():
                if m.player_id in team_of:
                    team_of[m.player_id] = m.to_team_id
            continue
        if ev.event_type not in ALLOWED_EVENT_TYPES:
            # Forward-compat: unknown event types are ignored by the fold
            # rather than raising, so a partially-migrated schema doesn't
            # crash older code paths. Persistence layer validates on write.
            continue
        team_id = ev.payload.get("team_id")
        player_id = ev.payload.get("player_id")
        if team_id is None or player_id is None:
            continue
        reason = ev.payload.get("reason", "user")
        if ev.event_type == EVENT_SET_UNAVAILABLE:
            out_reason[player_id] = reason
            team_of[player_id] = team_id
        elif ev.event_type == EVENT_SET_AVAILABLE:
            if reason == "recovered":
                # Only clear if the current OUT was set by injury —
                # a user-driven OUT (or any non-injury reason) that
                # landed between the injury and the scheduled recovery
                # takes precedence.
                if out_reason.get(player_id) == "injury":
                    out_reason.pop(player_id, None)
                    team_of.pop(player_id, None)
            else:
                out_reason.pop(player_id, None)
                team_of.pop(player_id, None)
    return frozenset((team_of[pid], pid) for pid in out_reason)


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
