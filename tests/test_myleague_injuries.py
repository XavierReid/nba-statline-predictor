"""M-5a — injury pipeline infrastructure tests.

Covers the mechanism only. M-5b calibrates rate + duration
distributions against the multi-season validator; those metrics are
NOT unit-tested here.

Locked design (Xavier, 2026-08-29):
- Between-game draws only (post-game hook in advance_to)
- Only players who appeared in the game are injury-eligible
- Game-based duration (schedule-driven return date)
- Paired events with reason=injury / reason=recovered
- Override semantics: user-driven OUT between injury and scheduled
  recovery makes the recovery a no-op (fold-level check)
- Default rate=0.0 ships infrastructure only; NO live behavioral
  change without an explicit config
"""
from datetime import date, timedelta
import random

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models.game import Game
from app.models.myleague import MyLeagueEvent, MyLeagueState
from app.models.simulation import (
    SimulatedGame,
    SimulatedPlayerLine,
    SimulationRun,
)
from app.models.team import Team
from app.services.injuries import (
    InjuryConfig,
    compute_return_date,
    draw_injury_duration_games,
    generate_injuries_for_game,
)
from app.services.myleague_state import (
    EVENT_SET_AVAILABLE,
    EVENT_SET_UNAVAILABLE,
    MyLeagueEventPayload,
    apply_events,
)

client = TestClient(app)
SEASON = "2024-25"


def _tid(db, abbr):
    return db.execute(select(Team.id).where(Team.abbreviation == abbr)).scalar_one()


def _lal_id() -> int:
    db = SessionLocal()
    try:
        return _tid(db, "LAL")
    finally:
        db.close()


def _cleanup(sim_id):
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


# --- Duration draw ---------------------------------------------------------

def test_duration_draw_is_within_bucket_ranges():
    cfg = InjuryConfig(rate=0.0)
    rng = random.Random(42)
    for _ in range(1000):
        n = draw_injury_duration_games(rng, cfg)
        assert 1 <= n <= 25, f"draw out of expected range: {n}"


def test_duration_draw_is_reproducible():
    cfg = InjuryConfig()
    a = [draw_injury_duration_games(random.Random(7), cfg) for _ in range(50)]
    b = [draw_injury_duration_games(random.Random(7), cfg) for _ in range(50)]
    assert a == b


# --- Return-date computation ----------------------------------------------

def test_return_date_uses_actual_team_schedule():
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        game_dates = [gd for (gd,) in db.execute(
            select(Game.game_date)
            .where(Game.game_date >= date(2024, 10, 22))
            .where((Game.home_team_id == lal_id) | (Game.away_team_id == lal_id))
            .order_by(Game.game_date.asc())
            .limit(5)
        ).all()]
        return_date = compute_return_date(
            db, SEASON, lal_id, injury_effective_date=game_dates[0], games_missed=3,
        )
        assert return_date == game_dates[2] + timedelta(days=1)
    finally:
        db.close()


def test_return_date_clamps_to_season_end():
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        return_date = compute_return_date(
            db, SEASON, lal_id,
            injury_effective_date=date(2025, 4, 1),
            games_missed=100,
        )
        assert date(2025, 4, 1) < return_date < date(2025, 7, 15)
    finally:
        db.close()


# --- Generator + zero-rate default ---------------------------------------

def test_generator_yields_no_injuries_when_rate_is_zero():
    db = SessionLocal()
    try:
        injuries = generate_injuries_for_game(
            rng=random.Random(1),
            cfg=InjuryConfig(rate=0.0),
            db=db, season=SEASON, game_date=date(2024, 10, 22),
            home_team_id=_tid(db, "LAL"), away_team_id=_tid(db, "GSW"),
            home_appeared_ids=[1, 2, 3], away_appeared_ids=[4, 5, 6],
        )
        assert injuries == []
    finally:
        db.close()


def test_generator_only_draws_against_appeared_players():
    db = SessionLocal()
    try:
        injuries = generate_injuries_for_game(
            rng=random.Random(1),
            cfg=InjuryConfig(rate=1.0),
            db=db, season=SEASON, game_date=date(2024, 10, 22),
            home_team_id=_tid(db, "LAL"), away_team_id=_tid(db, "GSW"),
            home_appeared_ids=[1, 2], away_appeared_ids=[3, 4],
        )
        pids = {i.player_id for i in injuries}
        assert pids == {1, 2, 3, 4}
        assert 999 not in pids
    finally:
        db.close()


def test_out_from_date_is_day_after_game():
    db = SessionLocal()
    try:
        game_date = date(2024, 11, 15)
        injuries = generate_injuries_for_game(
            rng=random.Random(3),
            cfg=InjuryConfig(rate=1.0),
            db=db, season=SEASON, game_date=game_date,
            home_team_id=_tid(db, "LAL"), away_team_id=_tid(db, "GSW"),
            home_appeared_ids=[1], away_appeared_ids=[],
        )
        assert all(i.out_from_date == game_date + timedelta(days=1) for i in injuries)
    finally:
        db.close()


# --- Fold reason-aware semantics ----------------------------------------

def test_fold_recovered_no_ops_when_current_out_is_user_driven():
    """Xavier's override semantics: auto-recovery is a no-op when the
    current OUT was set by user (not injury). User's OUT wins."""
    team, player = 999, 42
    events = [
        MyLeagueEventPayload(
            event_type=EVENT_SET_UNAVAILABLE,
            applied_at_date=date(2025, 1, 1),
            payload={"team_id": team, "player_id": player, "reason": "injury"},
        ),
        MyLeagueEventPayload(
            event_type=EVENT_SET_UNAVAILABLE,
            applied_at_date=date(2025, 1, 5),
            payload={"team_id": team, "player_id": player, "reason": "user"},
        ),
        MyLeagueEventPayload(
            event_type=EVENT_SET_AVAILABLE,
            applied_at_date=date(2025, 1, 10),
            payload={"team_id": team, "player_id": player, "reason": "recovered"},
        ),
    ]
    unavailable = apply_events(events, date(2025, 1, 10))
    assert (team, player) in unavailable


def test_fold_recovered_clears_out_when_reason_is_injury():
    team, player = 999, 42
    events = [
        MyLeagueEventPayload(
            event_type=EVENT_SET_UNAVAILABLE,
            applied_at_date=date(2025, 1, 1),
            payload={"team_id": team, "player_id": player, "reason": "injury"},
        ),
        MyLeagueEventPayload(
            event_type=EVENT_SET_AVAILABLE,
            applied_at_date=date(2025, 1, 10),
            payload={"team_id": team, "player_id": player, "reason": "recovered"},
        ),
    ]
    assert (team, player) not in apply_events(events, date(2025, 1, 10))


def test_fold_user_available_always_clears_out_regardless_of_reason():
    """User-driven AVAILABLE always clears OUT, even injury-set OUT."""
    team, player = 999, 42
    events = [
        MyLeagueEventPayload(
            event_type=EVENT_SET_UNAVAILABLE,
            applied_at_date=date(2025, 1, 1),
            payload={"team_id": team, "player_id": player, "reason": "injury"},
        ),
        MyLeagueEventPayload(
            event_type=EVENT_SET_AVAILABLE,
            applied_at_date=date(2025, 1, 3),
            payload={"team_id": team, "player_id": player},   # no reason → 'user'
        ),
    ]
    assert (team, player) not in apply_events(events, date(2025, 1, 5))


def test_fold_legacy_events_without_reason_still_work():
    """Backward-compat: legacy events (no reason) fold identically to
    M-4 semantics."""
    team, player = 999, 42
    events = [
        MyLeagueEventPayload(
            event_type=EVENT_SET_UNAVAILABLE,
            applied_at_date=date(2025, 1, 1),
            payload={"team_id": team, "player_id": player},
        ),
    ]
    assert (team, player) in apply_events(events, date(2025, 1, 5))
    events.append(MyLeagueEventPayload(
        event_type=EVENT_SET_AVAILABLE,
        applied_at_date=date(2025, 1, 2),
        payload={"team_id": team, "player_id": player},
    ))
    assert (team, player) not in apply_events(events, date(2025, 1, 5))


# --- Advance path: zero-rate is byte-identical to no-injury -------------

def test_advance_with_rate_zero_produces_no_injury_events():
    """M-5a MVP integration: advance a MyLeague with default rate=0 —
    no injury events land in the log, downstream behavior unchanged."""
    r = client.post(
        "/myleague/",
        json={
            "season": SEASON, "seed": 42,
            "controlled_team_id": _lal_id(),
            "config": {"preset": "drama-m3-season"},
        },
    )
    sim_id = r.json()["simulation_id"]
    try:
        cursor = client.get(f"/myleague/{sim_id}").json()["state"]["current_calendar_date"]
        y, m, d = [int(x) for x in cursor.split("-")]
        target = (date(y, m, d) + timedelta(days=14)).isoformat()
        client.post(f"/myleague/{sim_id}/advance", json={"target_date": target})

        db = SessionLocal()
        try:
            st = db.execute(select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)).scalar_one()
            events = db.execute(
                select(MyLeagueEvent).where(MyLeagueEvent.myleague_state_id == st.id)
            ).scalars().all()
            injury_events = [e for e in events if (e.payload_json or {}).get("reason") in ("injury", "recovered")]
            assert injury_events == []
        finally:
            db.close()
    finally:
        _cleanup(sim_id)
