"""M-6a increment 3 — `append_trade_event`: validation and the write path for TRADE events.

Design lock: project-session-m6-design-lock (2026-10-14, confirmed defaults):
  - deadline = first game date + 2/3 of the season's ACTUAL date range, deterministic,
    with boundary tests for the last eligible and first ineligible date
  - v1 has NO CPU-to-CPU trades and NO CPU checks when no team is controlled (intentional)
  - each team keeps >= 10 players after every trade, both teams validated atomically
  - a rejected trade writes nothing; a player cannot be traded twice on one date
The CPU's accept/reject decision is NOT here (increment 4); this is the structural gate.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models.game import Game
from app.models.myleague import MyLeagueEvent, MyLeagueState
from app.models.player_season_stats import PlayerSeasonStats
from app.services.myleague_engine import (
    MIN_ROSTER_AFTER_TRADE,
    MyLeagueError,
    RetroactiveEventError,
    TradeRejected,
    advance_to,
    append_event,
    append_trade_event,
    create_run,
    trade_deadline,
)
from app.services.myleague_state import EVENT_SET_UNAVAILABLE, EVENT_TRADE, ALLOWED_EVENT_TYPES
from app.services.roster_at_date import resolve_team_roster_at_date
from app.api.schemas.simulations import DRAMA_M3_SEASON
from tests.test_myleague_engine import _cleanup_run
from tests.test_myleague_trade_overlay import _first_game_date, _lines_by_date, _no_random_injuries

SEASON = "2024-25"


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def _roster_ids(db, team_id):
    """Players of a team, least-used first (so moving them disturbs the sim the least)."""
    return [p for (p,) in db.execute(
        select(PlayerSeasonStats.player_id)
        .where(PlayerSeasonStats.season == SEASON, PlayerSeasonStats.team_id == team_id)
        .order_by(PlayerSeasonStats.minutes_per_game.asc(), PlayerSeasonStats.player_id)
    ).all()]


def _teams(db):
    """Three teams with comfortably more than the minimum roster."""
    counts = db.execute(
        select(PlayerSeasonStats.team_id, func.count(PlayerSeasonStats.player_id.distinct()))
        .where(PlayerSeasonStats.season == SEASON, PlayerSeasonStats.team_id.isnot(None),
               PlayerSeasonStats.team_id != 0)
        .group_by(PlayerSeasonStats.team_id).order_by(PlayerSeasonStats.team_id)
    ).all()
    big = [t for t, n in counts if n >= MIN_ROSTER_AFTER_TRADE + 3]
    assert len(big) >= 3
    return big[0], big[1], big[2]


def _trade(a, b, a_gives, b_gives, trade_id="t1"):
    moves = [{"player_id": p, "from_team_id": a, "to_team_id": b} for p in a_gives]
    moves += [{"player_id": p, "from_team_id": b, "to_team_id": a} for p in b_gives]
    return {"trade_id": trade_id, "moves": moves}


def _n_events(db, sim_id):
    st = db.execute(select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)).scalar_one()
    return db.execute(select(func.count(MyLeagueEvent.id))
                      .where(MyLeagueEvent.myleague_state_id == st.id)).scalar()


@pytest.fixture
def world(db):
    """A fresh god-mode run plus three teams and a mid-window trade date."""
    first = _first_game_date(db)
    a, b, c = _teams(db)
    sim, _ = create_run(db, season=SEASON, seed=21, controlled_team_id=None)
    sim_id = sim.id
    _no_random_injuries(db, sim_id)
    day = first + timedelta(days=10)
    yield dict(sim_id=sim_id, first=first, a=a, b=b, c=c, day=day,
               pa=_roster_ids(db, a), pb=_roster_ids(db, b), pc=_roster_ids(db, c))
    _cleanup_run(db, sim_id)


def _controlled_world(db, controlled_team):
    sim, _ = create_run(db, season=SEASON, seed=22, controlled_team_id=controlled_team)
    _no_random_injuries(db, sim.id)
    return sim.id


def _rejects(db, sim_id, code, day, payload):
    before = _n_events(db, sim_id)
    with pytest.raises(TradeRejected) as exc:
        append_trade_event(db, simulation_id=sim_id, applied_at_date=day, payload=payload)
    assert exc.value.code == code, f"expected {code}, got {exc.value.code}: {exc.value}"
    assert _n_events(db, sim_id) == before, "a rejected trade must not write anything"


# ------------------------------------------------------------------- the write path

def test_trade_is_loggable_but_not_writable_through_the_generic_event_path(db, world):
    assert EVENT_TRADE in ALLOWED_EVENT_TYPES            # may exist in the log
    before = _n_events(db, world["sim_id"])
    with pytest.raises(MyLeagueError, match="append_trade_event"):   # the message names the right door
        append_event(db, simulation_id=world["sim_id"], event_type=EVENT_TRADE,
                     applied_at_date=world["day"],
                     payload=_trade(world["a"], world["b"], world["pa"][:1], world["pb"][:1]))
    assert _n_events(db, world["sim_id"]) == before     # CPU-unchecked trades can't sneak in


def test_valid_trade_is_written_and_visible_on_the_rosters(db, world):
    w = world
    pa, pb = w["pa"][0], w["pb"][0]
    ev = append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=w["day"],
                            payload=_trade(w["a"], w["b"], [pa], [pb]))
    assert ev.event_type == EVENT_TRADE and ev.applied_at_date == w["day"]
    assert ev.payload_json["trade_id"] == "t1" and len(ev.payload_json["moves"]) == 2
    on_b = {m.player_id for m in resolve_team_roster_at_date(db, w["sim_id"], w["b"], SEASON, w["day"])}
    on_a = {m.player_id for m in resolve_team_roster_at_date(db, w["sim_id"], w["a"], SEASON, w["day"])}
    assert pa in on_b and pb in on_a and pa not in on_a and pb not in on_b


def test_validated_trade_flows_through_advance_to(db, world):
    w = world
    pa = w["pa"][-1]                                     # his team's most-used player
    pb = w["pb"][-1]
    d = w["first"] + timedelta(days=6)
    append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=d,
                       payload=_trade(w["a"], w["b"], [pa], [pb]))
    advance_to(db, simulation_id=w["sim_id"], target_date=w["first"] + timedelta(days=16),
               config=DRAMA_M3_SEASON)
    rows = _lines_by_date(db, w["sim_id"], pa)
    before = {t for dt, t in rows if dt < d}
    after = {t for dt, t in rows if dt >= d}
    assert after == {w["b"]}, f"after the trade he must play only for the new team, got {after}"
    assert before <= {w["a"]}


# ----------------------------------------------------------------- malformed / ids

@pytest.mark.parametrize("bad", [
    {}, {"trade_id": "x"}, {"trade_id": "x", "moves": []},
    {"moves": [{"player_id": 1, "from_team_id": 2, "to_team_id": 3}]},
    {"trade_id": "x", "moves": [{"player_id": 1, "from_team_id": 2, "to_team_id": 2}]},
    {"trade_id": "x", "moves": "nope"},
])
def test_malformed_payload_is_rejected_and_writes_nothing(db, world, bad):
    _rejects(db, world["sim_id"], "MALFORMED", world["day"], bad)


def test_reusing_a_trade_id_is_rejected(db, world):
    w = world
    append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=w["day"],
                       payload=_trade(w["a"], w["b"], [w["pa"][0]], [w["pb"][0]], trade_id="same"))
    _rejects(db, w["sim_id"], "DUPLICATE_TRADE_ID", w["day"] + timedelta(days=2),
             _trade(w["a"], w["c"], [w["pa"][1]], [w["pc"][0]], trade_id="same"))


# ------------------------------------------------------------------- the deadline

def test_deadline_is_two_thirds_of_the_actual_schedule_span_and_deterministic(db):
    rows = db.execute(select(func.min(Game.game_date), func.max(Game.game_date))
                      .where(Game.game_date >= date(2024, 10, 1), Game.game_date <= date(2025, 7, 15))).one()
    first, last = rows
    expected = first + timedelta(days=(2 * (last - first).days) // 3)
    assert trade_deadline(db, SEASON) == expected
    assert trade_deadline(db, SEASON) == trade_deadline(db, SEASON)


def test_deadline_boundary_last_eligible_date_passes_first_ineligible_fails(db, world):
    w = world
    deadline = trade_deadline(db, SEASON)
    _rejects(db, w["sim_id"], "AFTER_DEADLINE", deadline + timedelta(days=1),
             _trade(w["a"], w["b"], [w["pa"][0]], [w["pb"][0]]))
    ev = append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=deadline,
                            payload=_trade(w["a"], w["b"], [w["pa"][0]], [w["pb"][0]]))
    assert ev.applied_at_date == deadline


# ----------------------------------------------------------- history immutability

def test_trade_cannot_land_on_or_before_a_completed_game_date(db, world):
    w = world
    cursor = advance_to(db, simulation_id=w["sim_id"], target_date=w["first"] + timedelta(days=5),
                        config=DRAMA_M3_SEASON).current_calendar_date
    payload = _trade(w["a"], w["b"], [w["pa"][0]], [w["pb"][0]])
    before = _n_events(db, w["sim_id"])
    with pytest.raises(RetroactiveEventError):
        append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=cursor, payload=payload)
    assert _n_events(db, w["sim_id"]) == before
    ev = append_trade_event(db, simulation_id=w["sim_id"],
                            applied_at_date=cursor + timedelta(days=1), payload=payload)
    assert ev.applied_at_date == cursor + timedelta(days=1)


# ----------------------------------------------------- controlled team (v1 scope)

def test_with_a_controlled_team_the_trade_must_involve_it(db, world):
    w = world
    sim_id = _controlled_world(db, w["a"])
    try:
        # CPU-to-CPU (B for C) is deliberately out of scope for v1.
        _rejects(db, sim_id, "NOT_CONTROLLED_TEAM", w["day"],
                 _trade(w["b"], w["c"], [w["pb"][0]], [w["pc"][0]]))
        ev = append_trade_event(db, simulation_id=sim_id, applied_at_date=w["day"],
                                payload=_trade(w["a"], w["b"], [w["pa"][0]], [w["pb"][0]]))
        assert ev.event_type == EVENT_TRADE
    finally:
        _cleanup_run(db, sim_id)


def test_god_mode_run_allows_any_two_teams(db, world):
    w = world
    ev = append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=w["day"],
                            payload=_trade(w["b"], w["c"], [w["pb"][0]], [w["pc"][0]]))
    assert ev.event_type == EVENT_TRADE


# ----------------------------------------------------------------------- ownership

def test_player_must_actually_be_held_by_the_from_team(db, world):
    w = world
    _rejects(db, w["sim_id"], "OWNERSHIP", w["day"],
             _trade(w["a"], w["b"], [w["pb"][0]], [w["pa"][0]]))      # both sent from the wrong side


def test_unknown_player_is_rejected(db, world):
    w = world
    _rejects(db, w["sim_id"], "OWNERSHIP", w["day"],
             _trade(w["a"], w["b"], [999_999_999], [w["pb"][0]]))


def test_chained_ownership_follows_earlier_trades(db, world):
    w = world
    p = w["pa"][0]
    d1, d2 = w["day"], w["day"] + timedelta(days=3)
    append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=d1,
                       payload=_trade(w["a"], w["b"], [p], [w["pb"][0]], trade_id="t1"))
    # p now belongs to B: A can no longer send him
    _rejects(db, w["sim_id"], "OWNERSHIP", d2,
             _trade(w["a"], w["c"], [p], [w["pc"][0]], trade_id="t2"))
    ev = append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=d2,
                            payload=_trade(w["b"], w["c"], [p], [w["pc"][0]], trade_id="t3"))
    assert ev.applied_at_date == d2


def test_an_earlier_trade_that_breaks_a_scheduled_later_one_is_rejected(db, world):
    w = world
    p = w["pa"][0]
    later = w["day"] + timedelta(days=5)
    append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=later,
                       payload=_trade(w["a"], w["b"], [p], [w["pb"][0]], trade_id="later"))
    # Sending p to C earlier would leave the scheduled A->B move referring to a player A no longer has.
    _rejects(db, w["sim_id"], "OWNERSHIP", w["day"],
             _trade(w["a"], w["c"], [p], [w["pc"][0]], trade_id="earlier"))


def test_a_player_cannot_be_traded_twice_on_one_date(db, world):
    w = world
    p = w["pa"][0]
    append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=w["day"],
                       payload=_trade(w["a"], w["b"], [p], [w["pb"][0]], trade_id="t1"))
    _rejects(db, w["sim_id"], "SAME_DAY_RETRADE", w["day"],
             _trade(w["b"], w["c"], [p], [w["pc"][0]], trade_id="t2"))


# ------------------------------------------------------ minimum roster (atomic, both)

def _size(db, team_id):
    return len(_roster_ids(db, team_id))


def test_roster_minimum_boundary_exactly_ten_passes_nine_fails(db, world):
    w = world
    n = _size(db, w["a"])
    give_ok = w["pa"][: n - MIN_ROSTER_AFTER_TRADE]                  # leaves A with exactly 10
    give_bad = w["pa"][: n - MIN_ROSTER_AFTER_TRADE + 1]             # leaves A with 9
    _rejects(db, w["sim_id"], "ROSTER_MINIMUM", w["day"], _trade(w["a"], w["b"], give_bad, []))
    ev = append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=w["day"],
                            payload=_trade(w["a"], w["b"], give_ok, []))
    assert ev.event_type == EVENT_TRADE


def test_roster_minimum_is_checked_for_both_teams_not_just_the_proposer(db, world):
    w = world
    n_b = _size(db, w["b"])
    # A is fine (it only receives); B would drop to 9, so the whole trade must be refused.
    _rejects(db, w["sim_id"], "ROSTER_MINIMUM", w["day"],
             _trade(w["a"], w["b"], [], w["pb"][: n_b - MIN_ROSTER_AFTER_TRADE + 1]))


def test_roster_minimum_counts_earlier_trades(db, world):
    w = world
    n = _size(db, w["a"])
    first_give = w["pa"][: n - MIN_ROSTER_AFTER_TRADE]               # A at exactly 10
    append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=w["day"],
                       payload=_trade(w["a"], w["b"], first_give, [], trade_id="t1"))
    _rejects(db, w["sim_id"], "ROSTER_MINIMUM", w["day"] + timedelta(days=2),
             _trade(w["a"], w["c"], [w["pa"][len(first_give)]], [], trade_id="t2"))


def test_roster_minimum_also_protects_against_later_scheduled_trades(db, world):
    """A trade can be fine on its own date and still break the minimum once a trade that is
    already scheduled for a LATER date takes effect: both dates must be checked."""
    w = world
    n = _size(db, w["a"])
    scheduled = w["pa"][: n - MIN_ROSTER_AFTER_TRADE]                    # A at exactly 10 from day+5
    later = w["day"] + timedelta(days=5)
    append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=later,
                       payload=_trade(w["a"], w["b"], scheduled, [], trade_id="later"))
    # On `day` A would still have n-1 >= 10, but by `later` it would be 9.
    _rejects(db, w["sim_id"], "ROSTER_MINIMUM", w["day"],
             _trade(w["a"], w["c"], [w["pa"][len(scheduled)]], [], trade_id="earlier"))


# ------------------------------------------- availability events honor trades now

def test_availability_event_checks_rostering_against_the_post_trade_team(db, world):
    w = world
    p = w["pa"][0]
    append_trade_event(db, simulation_id=w["sim_id"], applied_at_date=w["day"],
                       payload=_trade(w["a"], w["b"], [p], [w["pb"][0]]))
    after = w["day"] + timedelta(days=1)
    ev = append_event(db, simulation_id=w["sim_id"], event_type=EVENT_SET_UNAVAILABLE,
                      applied_at_date=after, payload={"team_id": w["b"], "player_id": p})
    assert ev.payload_json["team_id"] == w["b"]                       # on the new team: ok
    with pytest.raises(MyLeagueError):
        append_event(db, simulation_id=w["sim_id"], event_type=EVENT_SET_UNAVAILABLE,
                     applied_at_date=after, payload={"team_id": w["a"], "player_id": p})   # old team
