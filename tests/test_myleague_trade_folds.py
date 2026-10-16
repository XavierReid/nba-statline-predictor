"""M-6a increment 1 — pure folds for TRADE events and player-keyed availability.

Pure tests (no DB). Order of the file follows the design lock: the injured-player
trade tests come first, then the TRADE fold itself, then no-trade equivalence with the
pre-M-6 availability fold.

Design lock: project-session-m6-design-lock (2026-10-14).
"""
import copy
import random
from datetime import date, timedelta

import pytest

from app.services.myleague_state import (
    EVENT_SET_AVAILABLE,
    EVENT_SET_UNAVAILABLE,
    EVENT_TRADE,
    ALLOWED_EVENT_TYPES,
    AVAILABILITY_EVENT_TYPES,
    InconsistentTradeError,
    MalformedTradeError,
    MyLeagueEventPayload,
    apply_events,
    fold_team_overrides,
)

A, B, C = 1610612737, 1610612738, 1610612739
P1, P2, P3, P4 = 101, 102, 103, 104
D = date(2025, 1, 10)


def ev(event_type, d, **payload):
    return MyLeagueEventPayload(event_type=event_type, applied_at_date=d, payload=payload)


def out(d, team, player, **extra):
    return ev(EVENT_SET_UNAVAILABLE, d, team_id=team, player_id=player, **extra)


def back(d, team, player, **extra):
    return ev(EVENT_SET_AVAILABLE, d, team_id=team, player_id=player, **extra)


def trade(d, moves, trade_id="t1"):
    return ev(EVENT_TRADE, d, trade_id=trade_id,
              moves=[{"player_id": p, "from_team_id": f, "to_team_id": t} for p, f, t in moves])


# ---------------------------------------------------------------------------
# 1. Injured player traded: availability follows the player id (FIRST, per lock)
# ---------------------------------------------------------------------------

def test_injured_player_traded_stays_out_on_new_team():
    events = [
        out(D, A, P1, reason="injury", return_date="2025-01-25"),
        trade(D + timedelta(days=2), [(P1, A, B), (P2, B, A)]),
    ]
    assert apply_events(events, D + timedelta(days=1)) == {(A, P1)}
    after = apply_events(events, D + timedelta(days=2))
    assert after == {(B, P1)}          # still OUT, now attributed to the new team
    assert (A, P1) not in after


def test_original_recovery_event_still_clears_injury_after_trade():
    # The recovery was scheduled at injury time with the OLD team id. After the trade it must
    # still clear the OUT state, and must not drag the player back to the old team.
    events = [
        out(D, A, P1, reason="injury", return_date="2025-01-25"),
        trade(D + timedelta(days=2), [(P1, A, B), (P2, B, A)]),
        back(date(2025, 1, 25), A, P1, reason="recovered"),
    ]
    assert apply_events(events, date(2025, 1, 24)) == {(B, P1)}
    assert apply_events(events, date(2025, 1, 25)) == frozenset()


def test_recovery_event_does_not_move_player_back_to_old_team():
    events = [
        out(D, A, P1, reason="injury"),
        trade(D + timedelta(days=1), [(P1, A, B), (P2, B, A)]),
        back(D + timedelta(days=3), A, P1, reason="recovered"),
        out(D + timedelta(days=5), B, P1, reason="injury"),   # re-injured on the new team
    ]
    assert apply_events(events, D + timedelta(days=5)) == {(B, P1)}


def test_user_out_survives_injury_recovery_after_trade():
    events = [
        out(D, A, P1, reason="injury"),
        out(D + timedelta(days=1), A, P1, reason="user"),      # user override of the injury
        trade(D + timedelta(days=2), [(P1, A, B), (P2, B, A)]),
        back(D + timedelta(days=4), A, P1, reason="recovered"),  # stale auto-recovery: no-op
    ]
    assert apply_events(events, D + timedelta(days=4)) == {(B, P1)}


def test_trade_of_available_player_does_not_create_out_state():
    events = [trade(D, [(P1, A, B), (P2, B, A)])]
    assert apply_events(events, D) == frozenset()


def test_traded_away_then_player_goes_out_on_new_team():
    events = [
        trade(D, [(P1, A, B), (P2, B, A)]),
        out(D + timedelta(days=1), B, P1, reason="injury"),
    ]
    assert apply_events(events, D + timedelta(days=1)) == {(B, P1)}


# --- reverse direction: one player's change never alters another's status -------------

def test_becoming_available_does_not_change_other_players_status():
    events = [
        out(D, A, P1, reason="injury"),
        out(D, A, P2, reason="injury"),
        back(D + timedelta(days=3), A, P1, reason="recovered"),
    ]
    assert apply_events(events, D + timedelta(days=3)) == {(A, P2)}


def test_recovery_of_traded_player_leaves_teammate_out():
    events = [
        out(D, A, P1, reason="injury"),
        out(D, B, P2, reason="injury"),
        trade(D + timedelta(days=1), [(P1, A, B), (P3, B, A)]),
        back(D + timedelta(days=4), A, P1, reason="recovered"),
    ]
    assert apply_events(events, D + timedelta(days=4)) == {(B, P2)}


def test_available_event_for_one_player_never_clears_a_different_player_on_same_team():
    events = [out(D, A, P1), out(D, A, P2), back(D + timedelta(days=1), A, P1)]
    assert apply_events(events, D + timedelta(days=1)) == {(A, P2)}


# ---------------------------------------------------------------------------
# 2. TRADE fold (team ownership only)
# ---------------------------------------------------------------------------

BASE = {P1: A, P2: B, P3: A, P4: C}


def test_simple_swap_overrides_only_moved_players():
    events = [trade(D, [(P1, A, B), (P2, B, A)])]
    assert fold_team_overrides(events, D, BASE) == {P1: B, P2: A}


def test_before_effective_date_nothing_applies_and_on_date_it_does():
    events = [trade(D, [(P1, A, B), (P2, B, A)])]
    assert fold_team_overrides(events, D - timedelta(days=1), BASE) == {}
    assert fold_team_overrides(events, D, BASE) == {P1: B, P2: A}


def test_multi_player_trade_applies_all_moves():
    events = [trade(D, [(P1, A, B), (P3, A, B), (P2, B, A)])]
    assert fold_team_overrides(events, D, BASE) == {P1: B, P3: B, P2: A}


def test_chained_trades_use_overlay_for_ownership_check():
    events = [
        trade(D, [(P1, A, B), (P2, B, A)], trade_id="t1"),
        trade(D + timedelta(days=5), [(P1, B, C), (P4, C, B)], trade_id="t2"),
    ]
    assert fold_team_overrides(events, D + timedelta(days=5), BASE) == {P1: C, P2: A, P4: B}


def test_round_trip_back_to_original_team_is_canonical_empty_override():
    events = [
        trade(D, [(P1, A, B), (P2, B, A)], trade_id="t1"),
        trade(D + timedelta(days=3), [(P1, B, A), (P2, A, B)], trade_id="t2"),
    ]
    assert fold_team_overrides(events, D + timedelta(days=3), BASE) == {}


def test_fold_is_input_order_preserving_within_a_date_and_date_ordered_overall():
    later = trade(D + timedelta(days=2), [(P1, B, C), (P4, C, B)], trade_id="t2")
    first = trade(D, [(P1, A, B), (P2, B, A)], trade_id="t1")
    # Caller passes events out of date order: the fold must sort by date (stable).
    assert fold_team_overrides([later, first], D + timedelta(days=2), BASE) == {P1: C, P2: A, P4: B}


def test_replay_is_idempotent_and_does_not_mutate_inputs():
    events = [trade(D, [(P1, A, B), (P2, B, A)])]
    base_copy = copy.deepcopy(BASE)
    events_copy = copy.deepcopy(events)
    first = fold_team_overrides(events, D, BASE)
    second = fold_team_overrides(events, D, BASE)
    assert first == second
    assert BASE == base_copy and events == events_copy


def test_duplicate_identical_trade_id_is_ignored_on_replay():
    t = trade(D, [(P1, A, B), (P2, B, A)])
    assert fold_team_overrides([t, t], D, BASE) == {P1: B, P2: A}


def test_same_trade_id_with_different_moves_is_rejected():
    t1 = trade(D, [(P1, A, B), (P2, B, A)], trade_id="dup")
    t2 = trade(D, [(P3, A, B), (P2, B, A)], trade_id="dup")
    with pytest.raises(MalformedTradeError):
        fold_team_overrides([t1, t2], D, BASE)


@pytest.mark.parametrize("payload", [
    {},                                                                    # nothing
    {"trade_id": "x"},                                                     # no moves
    {"trade_id": "x", "moves": []},                                        # empty moves
    {"moves": [{"player_id": P1, "from_team_id": A, "to_team_id": B}]},    # no trade_id
    {"trade_id": "x", "moves": [{"player_id": P1, "from_team_id": A, "to_team_id": A}]},  # same team
    {"trade_id": "x", "moves": [{"player_id": P1, "from_team_id": A}]},    # missing field
    {"trade_id": "x", "moves": [{"player_id": "P1", "from_team_id": A, "to_team_id": B}]},  # bad type
    {"trade_id": "x", "moves": [{"player_id": P1, "from_team_id": A, "to_team_id": B},
                                {"player_id": P1, "from_team_id": A, "to_team_id": B}]},   # dup player
    {"trade_id": "x", "moves": [{"player_id": P1, "from_team_id": A, "to_team_id": B},
                                {"player_id": P4, "from_team_id": C, "to_team_id": A}]},   # 3 teams
    {"trade_id": "x", "moves": "nope"},                                    # wrong container
])
def test_malformed_trade_events_are_rejected_not_partially_applied(payload):
    bad = MyLeagueEventPayload(EVENT_TRADE, D, payload)
    with pytest.raises(MalformedTradeError):
        fold_team_overrides([bad], D, BASE)
    with pytest.raises(MalformedTradeError):
        apply_events([bad], D)


def test_one_bad_move_rejects_the_whole_multi_player_trade():
    good_then_bad = MyLeagueEventPayload(EVENT_TRADE, D, {"trade_id": "x", "moves": [
        {"player_id": P1, "from_team_id": A, "to_team_id": B},
        {"player_id": P2, "from_team_id": B, "to_team_id": B},       # malformed
    ]})
    with pytest.raises(MalformedTradeError):
        fold_team_overrides([good_then_bad], D, BASE)


def test_trade_from_wrong_team_is_inconsistent_history():
    with pytest.raises(InconsistentTradeError):
        fold_team_overrides([trade(D, [(P1, B, A), (P2, A, B)])], D, BASE)   # P1 is on A, not B


def test_trade_of_player_unknown_to_base_is_inconsistent_history():
    with pytest.raises(InconsistentTradeError):
        fold_team_overrides([trade(D, [(999, A, B), (P2, B, A)])], D, BASE)


def test_inconsistent_later_trade_does_not_leak_earlier_partial_result():
    events = [
        trade(D, [(P1, A, B), (P2, B, A)], trade_id="t1"),
        trade(D + timedelta(days=1), [(P1, A, C), (P4, C, A)], trade_id="t2"),   # P1 is on B now
    ]
    with pytest.raises(InconsistentTradeError):
        fold_team_overrides(events, D + timedelta(days=1), BASE)


def test_non_trade_events_are_ignored_by_the_ownership_fold():
    events = [out(D, A, P1, reason="injury"), trade(D, [(P1, A, B), (P2, B, A)])]
    assert fold_team_overrides(events, D, BASE) == {P1: B, P2: A}


def test_trade_may_exist_in_the_log_but_is_not_an_availability_event():
    # TRADE is loggable, but only append_trade_event writes it (the generic path is
    # availability-only); the availability fold must never treat it as one.
    assert EVENT_TRADE in ALLOWED_EVENT_TYPES
    assert EVENT_TRADE not in AVAILABILITY_EVENT_TYPES


# ---------------------------------------------------------------------------
# 3. No-trade equivalence with the pre-M-6 availability fold
# ---------------------------------------------------------------------------

def _legacy_apply_events(events, at_date):
    """Verbatim copy of the pre-M-6 fold (keyed by (team_id, player_id)); the oracle."""
    out_reason = {}
    for e in sorted(events, key=lambda e: e.applied_at_date):
        if e.applied_at_date > at_date:
            break
        if e.event_type not in {EVENT_SET_UNAVAILABLE, EVENT_SET_AVAILABLE}:
            continue
        team_id = e.payload.get("team_id")
        player_id = e.payload.get("player_id")
        if team_id is None or player_id is None:
            continue
        key = (team_id, player_id)
        reason = e.payload.get("reason", "user")
        if e.event_type == EVENT_SET_UNAVAILABLE:
            out_reason[key] = reason
        elif reason == "recovered":
            if out_reason.get(key) == "injury":
                out_reason.pop(key, None)
        else:
            out_reason.pop(key, None)
    return frozenset(out_reason.keys())


@pytest.mark.parametrize("seed", range(40))
def test_no_trade_histories_match_the_legacy_fold(seed):
    rng = random.Random(seed)
    team_of = {p: rng.choice([A, B, C]) for p in (P1, P2, P3, P4)}   # one stable team per player
    events = []
    for _ in range(rng.randint(1, 25)):
        p = rng.choice(list(team_of))
        d = D + timedelta(days=rng.randint(0, 20))
        if rng.random() < 0.55:
            events.append(out(d, team_of[p], p, reason=rng.choice(["user", "injury"])))
        else:
            events.append(back(d, team_of[p], p, reason=rng.choice(["user", "recovered"])))
    for day in range(0, 22, 3):
        at = D + timedelta(days=day)
        assert apply_events(events, at) == _legacy_apply_events(events, at), (seed, day)
