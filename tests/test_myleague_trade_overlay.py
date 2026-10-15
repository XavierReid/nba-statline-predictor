"""M-6a increment 2 — the trade ownership overlay is consumed by every roster surface.

TRADE events are inserted straight into the event log here: the write path
(`append_event`) stays closed until increment 3. Covers design-lock gates G2 (a traded
player is on exactly one team on any date, from the effective date), G3 (an injured
player keeps his OUT status through a trade end-to-end) and "ownership only" (identity and
data never change), across: `load_roster`, `resolve_team_roster_at_date`, `advance_to`,
the sim's internal availability reload, and the API surfaces that re-derive rosters.
"""
from collections import defaultdict
from datetime import date, timedelta
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.schemas.simulations import DRAMA_M3_SEASON
from app.database import SessionLocal
from app.main import app
from app.models.game import Game
from app.models.myleague import MyLeagueEvent, MyLeagueState
from app.models.player_season_stats import PlayerSeasonStats
from app.models.simulation import SimulatedGame, SimulatedPlayerLine, SimulationRun
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services import myleague_engine
from app.services.myleague_engine import advance_to, create_run
from app.services.myleague_state import EVENT_SET_AVAILABLE, EVENT_SET_UNAVAILABLE, EVENT_TRADE
from app.services.roster import RosterOverlay, load_roster
from app.services.roster_at_date import resolve_team_roster_at_date
from tests.test_myleague_engine import _cleanup_run

SEASON = "2024-25"
SEED = 11
client = TestClient(app)


# ----------------------------------------------------------------------------- helpers

def _first_game_date(db) -> date:
    return db.execute(
        select(Game.game_date).where(Game.game_date >= date(2024, 10, 1)).order_by(Game.game_date.asc())
    ).first()[0]


def _top_player(db, team_id: int) -> int:
    return db.execute(
        select(PlayerSeasonStats.player_id)
        .where(PlayerSeasonStats.season == SEASON, PlayerSeasonStats.team_id == team_id)
        .order_by(PlayerSeasonStats.minutes_per_game.desc())
        .limit(1)
    ).scalar_one()


def _pick_pair(db, first_day: date, trade_day: date, window_end: date):
    """Two teams that each play >= 2 games before the trade date and >= 2 on/after it."""
    rows = db.execute(
        select(Game.game_date, Game.home_team_id, Game.away_team_id)
        .where(Game.game_date >= first_day, Game.game_date <= window_end)
    ).all()
    before, after = defaultdict(int), defaultdict(int)
    for d, h, a in rows:
        for t in (h, a):
            (before if d < trade_day else after)[t] += 1
    ok = sorted(t for t in before if before[t] >= 2 and after[t] >= 2)
    assert len(ok) >= 2, "schedule too sparse for the trade fixture"
    return ok[0], ok[1]


def _trade_payload(pa, a, pb, b, trade_id="t1"):
    return {"trade_id": trade_id, "moves": [
        {"player_id": pa, "from_team_id": a, "to_team_id": b},
        {"player_id": pb, "from_team_id": b, "to_team_id": a},
    ]}


def _add_event(db, sim_id, event_type, d, payload):
    st = db.execute(select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)).scalar_one()
    db.add(MyLeagueEvent(myleague_state_id=st.id, event_type=event_type,
                         applied_at_date=d, payload_json=payload))
    db.commit()


def _no_random_injuries(db, sim_id):
    sim = db.get(SimulationRun, sim_id)
    params = dict(sim.parameters or {})
    params["injury_config"] = {"rate": 0.0}
    db.execute(update(SimulationRun).where(SimulationRun.id == sim_id).values(parameters=params))
    db.commit()


def _ids(roster):
    return [p["id"] for p in roster]


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def fixture(db):
    """A fresh MyLeague run plus a two-team, two-star trade fixture."""
    first = _first_game_date(db)
    trade_day = first + timedelta(days=7)
    a, b = _pick_pair(db, first, trade_day, first + timedelta(days=21))
    pa, pb = _top_player(db, a), _top_player(db, b)
    sim, st = create_run(db, season=SEASON, seed=SEED, controlled_team_id=None)
    _no_random_injuries(db, sim.id)
    sim_id = sim.id   # read now: after a failed flush the ORM object can no longer be touched
    info = dict(sim_id=sim_id, first=first, trade_day=trade_day, a=a, b=b, pa=pa, pb=pb)
    yield info
    _cleanup_run(db, sim_id)   # rolls back first, so it also cleans up after a failing test


# ----------------------------------------------------- load_roster: ownership only

def test_empty_overlay_loads_byte_identically(db, fixture):
    a = fixture["a"]
    base = load_roster(db, a, SEASON, depth=15)
    assert load_roster(db, a, SEASON, depth=15, overlay=None) == base
    assert load_roster(db, a, SEASON, depth=15, overlay=RosterOverlay()) == base


def test_overlay_moves_ownership_and_never_changes_player_data(db, fixture):
    a, b, pa, pb = fixture["a"], fixture["b"], fixture["pa"], fixture["pb"]
    overlay = RosterOverlay(overrides={pa: b, pb: a}, base_team_of={pa: a, pb: b})
    depth = 40   # larger than any roster, so no depth cut hides a difference
    a_base, b_base = load_roster(db, a, SEASON, depth=depth), load_roster(db, b, SEASON, depth=depth)
    a_new = load_roster(db, a, SEASON, depth=depth, overlay=overlay)
    b_new = load_roster(db, b, SEASON, depth=depth, overlay=overlay)

    assert set(_ids(a_new)) == (set(_ids(a_base)) - {pa}) | {pb}
    assert set(_ids(b_new)) == (set(_ids(b_base)) - {pb}) | {pa}
    assert len(_ids(a_new)) == len(set(_ids(a_new)))                        # no duplicates
    assert not set(_ids(a_new)) & set(_ids(b_new))                          # one team each

    # Identity and data are untouched. Only roster-relative DERIVED fields may differ, because
    # the roster they are relative to did: the games-weighted minute share, the starter flag,
    # and avail_prob (calibrated across the roster to the season's real active-player count;
    # the player's own games_played, which it derives from, is asserted equal below).
    relative = {"minutes", "is_starter", "avail_prob"}
    by_id = {p["id"]: p for p in a_base + b_base}
    for p in a_new + b_new:
        want = {k: v for k, v in by_id[p["id"]].items() if k not in relative}
        got = {k: v for k, v in p.items() if k not in relative}
        assert got == want, f"player {p['id']} changed beyond ownership"


# ----------------------------------------------------------- roster display at a date

def test_display_roster_follows_the_trade_by_date(db, fixture):
    f = fixture
    _add_event(db, f["sim_id"], EVENT_TRADE, f["trade_day"],
               _trade_payload(f["pa"], f["a"], f["pb"], f["b"]))
    day_before = f["trade_day"] - timedelta(days=1)

    def members(team, d):
        return {m.player_id: m for m in resolve_team_roster_at_date(db, f["sim_id"], team, SEASON, d)}

    a0, b0 = members(f["a"], day_before), members(f["b"], day_before)
    a1, b1 = members(f["a"], f["trade_day"]), members(f["b"], f["trade_day"])
    assert f["pa"] in a0 and f["pb"] in b0                                   # before: unchanged
    assert f["pa"] in b1 and f["pa"] not in a1 and f["pb"] in a1 and f["pb"] not in b1
    assert set(a0) | set(b0) == set(a1) | set(b1)                            # conserved
    assert len(a0) == len(a1) and len(b0) == len(b1)
    for field in ("name", "position", "real_gp", "real_mpg"):                # data unchanged
        assert getattr(a0[f["pa"]], field) == getattr(b1[f["pa"]], field)


def test_injured_player_traded_stays_out_and_recovery_still_clears_him(db, fixture):
    f = fixture
    injured_on = f["trade_day"] - timedelta(days=3)
    back_on = f["trade_day"] + timedelta(days=5)
    _add_event(db, f["sim_id"], EVENT_SET_UNAVAILABLE, injured_on,
               {"team_id": f["a"], "player_id": f["pa"], "reason": "injury",
                "return_date": back_on.isoformat()})
    _add_event(db, f["sim_id"], EVENT_TRADE, f["trade_day"],
               _trade_payload(f["pa"], f["a"], f["pb"], f["b"]))
    # the recovery was scheduled at injury time, so it still carries the OLD team id
    _add_event(db, f["sim_id"], EVENT_SET_AVAILABLE, back_on,
               {"team_id": f["a"], "player_id": f["pa"], "reason": "recovered"})

    def pa_on(team, d):
        return {m.player_id: m for m in
                resolve_team_roster_at_date(db, f["sim_id"], team, SEASON, d)}.get(f["pa"])

    during = pa_on(f["b"], f["trade_day"])
    assert during is not None and during.is_available is False               # OUT on the new team
    assert during.out_reason == "injury" and during.out_return_date == back_on
    assert pa_on(f["a"], f["trade_day"]) is None
    after = pa_on(f["b"], back_on)
    assert after is not None and after.is_available is True                  # recovery still clears


# --------------------------------------------------- advance_to end-to-end (G2, G3)

def _lines_by_date(db, sim_id, player_id):
    return db.execute(
        select(Game.game_date, SimulatedPlayerLine.team_id)
        .join(SimulatedGame, SimulatedGame.id == SimulatedPlayerLine.simulated_game_id)
        .join(Game, Game.id == SimulatedGame.game_id)
        .where(SimulatedGame.simulation_id == sim_id, SimulatedPlayerLine.player_id == player_id)
        .order_by(Game.game_date)
    ).all()


def test_traded_players_play_for_exactly_one_team_from_the_effective_date(db, fixture):
    f = fixture
    _add_event(db, f["sim_id"], EVENT_TRADE, f["trade_day"],
               _trade_payload(f["pa"], f["a"], f["pb"], f["b"]))
    advance_to(db, simulation_id=f["sim_id"], target_date=f["first"] + timedelta(days=21),
               config=DRAMA_M3_SEASON)
    for pid, old, new in ((f["pa"], f["a"], f["b"]), (f["pb"], f["b"], f["a"])):
        rows = _lines_by_date(db, f["sim_id"], pid)
        before = [t for d, t in rows if d < f["trade_day"]]
        after = [t for d, t in rows if d >= f["trade_day"]]
        assert before and after, f"player {pid} needs games on both sides of the trade"
        assert set(before) == {old}, f"player {pid} played for {set(before)} before the trade"
        assert set(after) == {new}, f"player {pid} played for {set(after)} after the trade"
        dates = [d for d, _ in rows]
        assert len(dates) == len(set(dates)), "a traded player has two lines on one date"


def test_injured_traded_player_misses_games_for_both_teams_until_recovery(db, fixture):
    f = fixture
    back_on = f["trade_day"] + timedelta(days=6)
    _add_event(db, f["sim_id"], EVENT_SET_UNAVAILABLE, f["trade_day"] - timedelta(days=2),
               {"team_id": f["a"], "player_id": f["pa"], "reason": "injury",
                "return_date": back_on.isoformat()})
    _add_event(db, f["sim_id"], EVENT_TRADE, f["trade_day"],
               _trade_payload(f["pa"], f["a"], f["pb"], f["b"]))
    _add_event(db, f["sim_id"], EVENT_SET_AVAILABLE, back_on,
               {"team_id": f["a"], "player_id": f["pa"], "reason": "recovered"})
    advance_to(db, simulation_id=f["sim_id"], target_date=f["first"] + timedelta(days=21),
               config=DRAMA_M3_SEASON)
    window = [(d, t) for d, t in _lines_by_date(db, f["sim_id"], f["pa"])
              if f["trade_day"] - timedelta(days=2) <= d < back_on]
    assert window == [], f"injured player played while OUT: {window}"
    resumed = [(d, t) for d, t in _lines_by_date(db, f["sim_id"], f["pa"]) if d >= back_on]
    assert resumed and {t for _, t in resumed} == {f["b"]}                   # back, on his new team


def test_a_trade_that_has_not_taken_effect_changes_nothing(db, fixture):
    """Same seed, one run with a trade dated AFTER the advance window and one without: the
    persisted games must be identical (a pending trade must not perturb the sim)."""
    f = fixture
    target = f["first"] + timedelta(days=5)
    clean, _ = create_run(db, season=SEASON, seed=SEED, controlled_team_id=None)
    clean_id = clean.id
    _no_random_injuries(db, clean_id)
    try:
        _add_event(db, f["sim_id"], EVENT_TRADE, f["first"] + timedelta(days=40),
                   _trade_payload(f["pa"], f["a"], f["pb"], f["b"]))
        advance_to(db, simulation_id=f["sim_id"], target_date=target, config=DRAMA_M3_SEASON)
        advance_to(db, simulation_id=clean_id, target_date=target, config=DRAMA_M3_SEASON)

        def snap(sim_id):
            return db.execute(
                select(SimulatedGame.game_id, SimulatedGame.home_score, SimulatedGame.away_score,
                       SimulatedGame.went_to_ot)
                .where(SimulatedGame.simulation_id == sim_id).order_by(SimulatedGame.game_id)
            ).all(), db.execute(
                select(SimulatedPlayerLine.player_id, SimulatedPlayerLine.team_id,
                       SimulatedPlayerLine.points, SimulatedPlayerLine.minutes)
                .join(SimulatedGame, SimulatedGame.id == SimulatedPlayerLine.simulated_game_id)
                .where(SimulatedGame.simulation_id == sim_id)
                .order_by(SimulatedGame.game_id, SimulatedPlayerLine.player_id)
            ).all()

        assert snap(f["sim_id"]) == snap(clean_id)
        assert snap(clean_id)[0], "no games simulated"
    finally:
        _cleanup_run(db, clean_id)


def test_engine_hands_each_game_the_overlay_in_force_on_its_date(db, fixture):
    """Wiring check on the real call: simulate_game receives no overlay before the trade date
    and the right one from it. (A 15-deep roster never triggers the sim's own reload, so this
    cannot be seen from outputs alone; a thin roster would silently get the wrong players.)"""
    f = fixture
    _add_event(db, f["sim_id"], EVENT_TRADE, f["trade_day"],
               _trade_payload(f["pa"], f["a"], f["pb"], f["b"]))
    seen = []
    real = myleague_engine.simulate_game

    def spy(*args, **kwargs):
        seen.append((kwargs.get("home_team_id"), kwargs.get("away_team_id"), kwargs.get("roster_overlay")))
        return real(*args, **kwargs)

    with mock.patch.object(myleague_engine, "simulate_game", side_effect=spy):
        advance_to(db, simulation_id=f["sim_id"], target_date=f["first"] + timedelta(days=14),
                   config=DRAMA_M3_SEASON)
    assert seen, "no games simulated"
    # Map each call back to its date through the persisted order (calls are in schedule order).
    ordered = db.execute(
        select(Game.game_date).join(SimulatedGame, SimulatedGame.game_id == Game.id)
        .where(SimulatedGame.simulation_id == f["sim_id"])
        .order_by(Game.game_date.asc(), Game.id.asc())
    ).scalars().all()
    assert len(ordered) == len(seen)
    want = {f["pa"]: f["b"], f["pb"]: f["a"]}
    for game_date, (_, _, overlay) in zip(ordered, seen):
        if game_date < f["trade_day"]:
            assert overlay is None
        else:
            assert overlay is not None and dict(overlay.overrides) == want
            assert dict(overlay.base_team_of) == {f["pa"]: f["a"], f["pb"]: f["b"]}
    assert any(d < f["trade_day"] for d in ordered) and any(d >= f["trade_day"] for d in ordered)


# --------------------------------------- the sim's internal availability reload path

def test_sim_reload_path_honors_the_overlay(db, fixture):
    """With availability on, simulate_game reloads a too-short roster from the DB itself. That
    reload must apply the overlay or it silently puts traded-away players back."""
    a, b, pa, pb = fixture["a"], fixture["b"], fixture["pa"], fixture["pb"]
    overlay = RosterOverlay(overrides={pa: b, pb: a}, base_team_of={pa: a, pb: b})
    cfg = DRAMA_M3_SEASON
    short_a = load_roster(db, a, SEASON, depth=10)       # shorter than cfg.roster_depth => reload
    short_b = load_roster(db, b, SEASON, depth=10)
    assert len(short_a) < cfg.roster_depth

    plain = simulate_game(short_a, short_b, seed=5, season=SEASON, config=cfg, db=db,
                          home_team_id=a, away_team_id=b)
    moved = simulate_game(short_a, short_b, seed=5, season=SEASON, config=cfg, db=db,
                          home_team_id=a, away_team_id=b, roster_overlay=overlay)
    assert pa in _ids(plain["home_pool"]) and pb in _ids(plain["away_pool"])
    assert pa in _ids(moved["away_pool"]) and pa not in _ids(moved["home_pool"])
    assert pb in _ids(moved["home_pool"]) and pb not in _ids(moved["away_pool"])
    assert not set(_ids(moved["home_pool"])) & set(_ids(moved["away_pool"]))


# ------------------------------------------------------------------ API surfaces

def test_team_drill_in_and_game_replay_agree_with_the_persisted_trade(db, fixture):
    f = fixture
    _add_event(db, f["sim_id"], EVENT_TRADE, f["trade_day"],
               _trade_payload(f["pa"], f["a"], f["pb"], f["b"]))
    advance_to(db, simulation_id=f["sim_id"], target_date=f["first"] + timedelta(days=21),
               config=DRAMA_M3_SEASON)
    abbr = {t.id: t.abbreviation for t in db.execute(select(Team)).scalars()}

    # Team drill-in is "as of" the day after the cursor, which is past the trade.
    for team, expect_in, expect_out in ((f["b"], f["pa"], f["pb"]), (f["a"], f["pb"], f["pa"])):
        body = client.get(f"/myleague/{f['sim_id']}/team/{abbr[team]}").json()
        ids = [p["player_id"] for p in body["roster"]]
        assert expect_in in ids and expect_out not in ids
        assert len(ids) == len(set(ids))

    # The replay of a post-trade game must reproduce the persisted lines' team attribution.
    game = db.execute(
        select(SimulatedGame.game_id, SimulatedPlayerLine.team_id, Game.home_team_id)
        .join(SimulatedPlayerLine, SimulatedPlayerLine.simulated_game_id == SimulatedGame.id)
        .join(Game, Game.id == SimulatedGame.game_id)
        .where(SimulatedGame.simulation_id == f["sim_id"],
               SimulatedPlayerLine.player_id == f["pa"], Game.game_date >= f["trade_day"])
        .order_by(Game.game_date)
    ).first()
    assert game is not None, "traded player has no post-trade persisted line"
    game_id, line_team, home_team = game
    assert line_team == f["b"]
    detail = client.get(f"/simulations/{f['sim_id']}/games/{game_id}").json()
    side = "home_box" if line_team == home_team else "away_box"
    other = "away_box" if side == "home_box" else "home_box"
    assert f["pa"] in [p["player_id"] for p in detail[side]]
    assert f["pa"] not in [p["player_id"] for p in detail[other]]
