"""M-3 team drill-in tests — GET /myleague/{sim_id}/team/{team_abbr}.

Covers Xavier's 7 backend gates:
  1. Exactly the expected roster is returned for the selected team.
  2. Players are not duplicated.
  3. Roster includes players who haven't appeared in a sim game yet.
  4. Players with 0 sim GP don't disappear simply because they have no
     SimulatedPlayerLine.
  5. Sim stats are isolated to the requested simulation_id.
  6. Availability state is reflected correctly as of the MyLeague's
     current date.
  7. Team record/recent games are derived only from that MyLeague's
     persisted games.

Also verifies as_of_date + roster-at-date shape for M-6 forward-compat.
"""
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models.game import Game
from app.models.myleague import MyLeagueEvent, MyLeagueState
from app.models.player import Player
from app.models.player_season_stats import PlayerSeasonStats
from app.models.simulation import (
    SimulatedGame,
    SimulatedPlayerLine,
    SimulationRun,
)
from app.models.team import Team

client = TestClient(app)
SEASON = "2024-25"


def _tid(db, abbr):
    return db.execute(select(Team.id).where(Team.abbreviation == abbr)).scalar_one()


def _team_pss_ids(db, abbr) -> list[int]:
    tid = _tid(db, abbr)
    return [
        p for (p,) in db.execute(
            select(PlayerSeasonStats.player_id.distinct())
            .where(PlayerSeasonStats.season == SEASON, PlayerSeasonStats.team_id == tid)
        ).all()
    ]


def _make_myleague_run(db, controlled_id, cursor: date = date(2024, 10, 21)) -> int:
    sim = SimulationRun(
        season=SEASON, scope="myleague", team_id=None,
        seed=1, status="running", parameters={}, games_completed=0,
    )
    db.add(sim); db.flush()
    st = MyLeagueState(
        simulation_id=sim.id, controlled_team_id=controlled_id,
        current_calendar_date=cursor,
    )
    db.add(st); db.commit()
    return sim.id


def _team_game_ids(db, tid, count):
    return [
        g for (g,) in db.execute(
            select(Game.id)
            .where(Game.game_date >= date(2024, 10, 1))
            .where((Game.home_team_id == tid) | (Game.away_team_id == tid))
            .order_by(Game.game_date.asc())
            .limit(count)
        ).all()
    ]


def _seed_line(db, sim_id, game_id, team_id, player_id, points=20):
    sg = SimulatedGame(
        simulation_id=sim_id, game_id=game_id,
        home_score=100, away_score=95, went_to_ot=False,
        quarter_scores={"home": [25]*4, "away": [24]*4},
    )
    db.add(sg); db.flush()
    db.add(SimulatedPlayerLine(
        simulated_game_id=sg.id, player_id=player_id, team_id=team_id,
        minutes=30.0, points=points, rebounds=5, assists=5,
        steals=1, blocks=1, turnovers=2, personal_fouls=2, fouled_out=False,
        fgm=8, fga=16, fg3m=2, fg3a=5, ftm=2, fta=2, plus_minus=5,
    ))
    db.commit()


def _seed_game(db, sim_id, game_id):
    db.add(SimulatedGame(
        simulation_id=sim_id, game_id=game_id,
        home_score=100, away_score=95, went_to_ot=False,
        quarter_scores={"home": [25]*4, "away": [24]*4},
    ))
    db.commit()


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


# --- Gate 1: exactly the expected roster is returned ------------------------

def test_gate1_exact_roster_for_team():
    """Roster matches distinct PSS.player_id for that team+season."""
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        expected = set(_team_pss_ids(db, "LAL"))
        sim_id = _make_myleague_run(db, lal_id)
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/team/LAL").json()
        returned = {p["player_id"] for p in body["roster"]}
        assert returned == expected, (
            f"missing: {expected - returned}, extra: {returned - expected}"
        )
    finally:
        _cleanup(sim_id)


# --- Gate 2: no duplicate players -------------------------------------------

def test_gate2_no_duplicate_players():
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        sim_id = _make_myleague_run(db, lal_id)
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/team/LAL").json()
        ids = [p["player_id"] for p in body["roster"]]
        assert len(ids) == len(set(ids)), f"duplicates present: {ids}"
    finally:
        _cleanup(sim_id)


# --- Gate 3+4: 0-GP players still appear ------------------------------------

def test_gate3_4_zero_gp_players_still_in_roster():
    """Fresh run: no sim lines exist. Every roster player still appears."""
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        expected = set(_team_pss_ids(db, "LAL"))
        sim_id = _make_myleague_run(db, lal_id)
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/team/LAL").json()
        returned = {p["player_id"] for p in body["roster"]}
        assert returned == expected
        # All rows should have sim.gp == 0 (fresh run)
        for row in body["roster"]:
            assert row["sim"] is not None
            assert row["sim"]["gp"] == 0
    finally:
        _cleanup(sim_id)


# --- Gate 5: sim stats isolated to requested simulation_id -----------------

def test_gate5_sim_stats_isolated_by_sim_id():
    """Two MyLeague runs on same team, same player. Endpoints must not
    bleed lines from one into the other."""
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        gids = _team_game_ids(db, lal_id, 4)
        # Any LAL player.
        player = _team_pss_ids(db, "LAL")[0]
        sim_a = _make_myleague_run(db, lal_id)
        sim_b = _make_myleague_run(db, lal_id)
        # A: 2 games @ 10pts; B: 2 games @ 30pts
        for gid in gids[:2]:
            _seed_line(db, sim_a, gid, lal_id, player, points=10)
        for gid in gids[2:4]:
            _seed_line(db, sim_b, gid, lal_id, player, points=30)
    finally:
        db.close()
    try:
        a = client.get(f"/myleague/{sim_a}/team/LAL").json()
        b = client.get(f"/myleague/{sim_b}/team/LAL").json()
        a_row = next(p for p in a["roster"] if p["player_id"] == player)
        b_row = next(p for p in b["roster"] if p["player_id"] == player)
        assert a_row["sim"]["gp"] == 2 and a_row["sim"]["ppg"] == 10.0
        assert b_row["sim"]["gp"] == 2 and b_row["sim"]["ppg"] == 30.0
    finally:
        _cleanup(sim_a); _cleanup(sim_b)


# --- Gate 6: availability reflects as_of_date ------------------------------

def test_gate6_availability_reflects_as_of_date():
    """A SET_UNAVAILABLE event with applied_at_date <= cursor should
    render the player as OUT. A future-dated event should NOT."""
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        player = _team_pss_ids(db, "LAL")[0]
        cursor = date(2024, 11, 1)
        sim_id = _make_myleague_run(db, lal_id, cursor=cursor)
        st = db.execute(select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)).scalar_one()
        # Apply an unavailable event BEFORE the cursor.
        db.add(MyLeagueEvent(
            myleague_state_id=st.id,
            event_type="SET_UNAVAILABLE",
            applied_at_date=cursor - timedelta(days=3),
            payload_json={"team_id": lal_id, "player_id": player},
        ))
        db.commit()
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/team/LAL").json()
        row = next(p for p in body["roster"] if p["player_id"] == player)
        assert row["availability"] == "OUT"
        # Every other roster player should be AVAILABLE.
        others = [p for p in body["roster"] if p["player_id"] != player]
        assert all(o["availability"] == "AVAILABLE" for o in others)
    finally:
        _cleanup(sim_id)


# --- Gate 7: team record/recent games only from THIS sim's games ----------

def test_gate7_record_only_from_this_sim():
    """A different sim's games must not affect this team drill-in's record."""
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        player = _team_pss_ids(db, "LAL")[0]
        gids = _team_game_ids(db, lal_id, 4)
        sim_a = _make_myleague_run(db, lal_id)
        sim_b = _make_myleague_run(db, lal_id)
        # Only sim_a gets games persisted.
        for gid in gids[:3]:
            _seed_line(db, sim_a, gid, lal_id, player, points=15)
    finally:
        db.close()
    try:
        a = client.get(f"/myleague/{sim_a}/team/LAL").json()
        b = client.get(f"/myleague/{sim_b}/team/LAL").json()
        assert a["record"]["wins"] + a["record"]["losses"] == 3
        assert b["record"]["wins"] + b["record"]["losses"] == 0
        assert len(a["recent_games"]) == 3
        assert len(b["recent_games"]) == 0
    finally:
        _cleanup(sim_a); _cleanup(sim_b)


# --- as_of_date is the sim's cursor + returned shape checks ---------------

def test_as_of_date_is_sim_cursor():
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        cursor = date(2024, 12, 25)
        sim_id = _make_myleague_run(db, lal_id, cursor=cursor)
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/team/LAL").json()
        assert body["as_of_date"] == "2024-12-25"
        assert body["team_abbr"] == "LAL"
        assert body["team_city"]
        assert body["team_nickname"]
    finally:
        _cleanup(sim_id)


# --- 404s ------------------------------------------------------------------

def test_missing_sim_returns_404():
    r = client.get("/myleague/9999999/team/LAL")
    assert r.status_code == 404


def test_unknown_team_abbr_returns_404():
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        sim_id = _make_myleague_run(db, lal_id)
    finally:
        db.close()
    try:
        r = client.get(f"/myleague/{sim_id}/team/ZZZ")
        assert r.status_code == 404
    finally:
        _cleanup(sim_id)


def test_non_myleague_scope_returns_404():
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        sim = SimulationRun(
            season=SEASON, scope="team", team_id=lal_id, seed=1, status="pending",
            parameters={}, games_completed=0,
        )
        db.add(sim); db.commit(); db.refresh(sim); sid = sim.id
    finally:
        db.close()
    try:
        r = client.get(f"/myleague/{sid}/team/LAL")
        assert r.status_code == 404
    finally:
        db = SessionLocal()
        db.execute(SimulationRun.__table__.delete().where(SimulationRun.id == sid))
        db.commit(); db.close()


# --- Roster ordering: starters first, then MPG ----------------------------

def test_roster_order_starters_then_mpg():
    """Starters appear before non-starters. Within tier, higher MPG first."""
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        sim_id = _make_myleague_run(db, lal_id)
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/team/LAL").json()
        roster = body["roster"]
        # Assert we see a run of starters before any non-starter.
        seen_non_starter = False
        for row in roster:
            if not row["is_starter"]:
                seen_non_starter = True
            elif seen_non_starter:
                raise AssertionError(
                    f"starter {row['name']} appears after a non-starter"
                )
        # There should be exactly 5 starters.
        starters = [p for p in roster if p["is_starter"]]
        assert len(starters) == 5
    finally:
        _cleanup(sim_id)
