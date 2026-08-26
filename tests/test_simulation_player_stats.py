"""Tests for GET /simulations/{sim_id}/player/{player_id} — scope-agnostic
running-averages endpoint. Shares derivation with the /myleague route
via app.services.player_stats.

Covers the two additional scopes: team-scope batch, league-scope batch.
The 9 contract tests are exercised for MyLeague in
test_myleague_player_stats.py; this file targets the extension surface.
"""
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models.game import Game
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


def _team_id(db, abbr: str) -> int:
    return db.execute(select(Team.id).where(Team.abbreviation == abbr)).scalar_one()


def _pick_team_player(db, abbr: str) -> int:
    tid = _team_id(db, abbr)
    return db.execute(
        select(PlayerSeasonStats.player_id)
        .where(PlayerSeasonStats.season == SEASON, PlayerSeasonStats.team_id == tid)
        .limit(1)
    ).scalar_one()


def _team_game_ids(db, abbr: str, count: int) -> list[str]:
    tid = _team_id(db, abbr)
    return [
        g for (g,) in db.execute(
            select(Game.id)
            .where(Game.game_date >= date(2024, 10, 1))
            .where((Game.home_team_id == tid) | (Game.away_team_id == tid))
            .order_by(Game.game_date.asc())
            .limit(count)
        ).all()
    ]


def _make_sim(db, scope: str, team_id: int | None) -> int:
    sim = SimulationRun(
        season=SEASON, scope=scope, team_id=team_id if scope == "team" else None,
        seed=1, status="pending", parameters={}, games_completed=0,
    )
    db.add(sim); db.commit(); db.refresh(sim)
    return sim.id


def _seed_line(db, sim_id, game_id, team_id, player_id, **stats):
    defaults = dict(minutes=30.0, points=20, rebounds=5, assists=5,
                    steals=1, blocks=1, turnovers=2, fgm=8, fga=16,
                    fg3m=2, fg3a=5, ftm=2, fta=2)
    defaults.update(stats)
    sg = SimulatedGame(
        simulation_id=sim_id, game_id=game_id,
        home_score=100, away_score=95, went_to_ot=False,
        quarter_scores={"home": [25]*4, "away": [24]*4},
    )
    db.add(sg); db.flush()
    db.add(SimulatedPlayerLine(
        simulated_game_id=sg.id, player_id=player_id, team_id=team_id,
        personal_fouls=2, fouled_out=False, plus_minus=5, **defaults,
    ))
    db.commit()


def _cleanup(sim_id: int) -> None:
    db = SessionLocal()
    try:
        sg_ids = [i for (i,) in db.execute(
            select(SimulatedGame.id).where(SimulatedGame.simulation_id == sim_id)
        ).all()]
        if sg_ids:
            db.execute(
                SimulatedPlayerLine.__table__.delete()
                .where(SimulatedPlayerLine.simulated_game_id.in_(sg_ids))
            )
        db.execute(SimulatedGame.__table__.delete().where(SimulatedGame.simulation_id == sim_id))
        db.execute(SimulationRun.__table__.delete().where(SimulationRun.id == sim_id))
        db.commit()
    finally:
        db.close()


# ----- Team scope ------------------------------------------------------------

def test_team_scope_returns_sim_and_real_blocks():
    db = SessionLocal()
    try:
        lal_id = _team_id(db, "LAL")
        player_id = _pick_team_player(db, "LAL")
        game_ids = _team_game_ids(db, "LAL", 5)
        sim_id = _make_sim(db, "team", lal_id)
        for gid in game_ids:
            _seed_line(db, sim_id, gid, lal_id, player_id, points=25)
    finally:
        db.close()
    try:
        r = client.get(f"/simulations/{sim_id}/player/{player_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sim"]["gp"] == 5
        assert body["sim"]["ppg"] == 25.0
        # by_team has one team for team-scope
        assert len(body["sim"]["by_team"]) == 1
        assert body["sim"]["by_team"][0]["team_abbr"] == "LAL"
        # team_gp should equal games LAL played in the sim
        assert body["sim"]["team_gp"] == 5
        # Real block populated (this player has a real PSS row)
        assert body["real"] is not None
        assert body["real"]["gp"] > 0
    finally:
        _cleanup(sim_id)


def test_team_scope_zero_gp_but_team_played():
    """Season is 5 games in for the team but this player didn't play any of them."""
    db = SessionLocal()
    try:
        lal_id = _team_id(db, "LAL")
        player_id = _pick_team_player(db, "LAL")
        game_ids = _team_game_ids(db, "LAL", 4)
        sim_id = _make_sim(db, "team", lal_id)
        # Insert SimulatedGame rows WITHOUT a line for this player
        for gid in game_ids:
            db_ = SessionLocal()
            try:
                db_.add(SimulatedGame(
                    simulation_id=sim_id, game_id=gid,
                    home_score=100, away_score=95, went_to_ot=False,
                    quarter_scores={"home": [25]*4, "away": [24]*4},
                ))
                db_.commit()
            finally:
                db_.close()
    finally:
        db.close()
    try:
        body = client.get(f"/simulations/{sim_id}/player/{player_id}").json()
        assert body["sim"]["gp"] == 0
        assert body["sim"]["team_gp"] == 4     # LAL played, player didn't
    finally:
        _cleanup(sim_id)


# ----- League scope ----------------------------------------------------------

def test_league_scope_uses_same_derivation():
    """League scope: same derivation, same output. Just a different sim.scope."""
    db = SessionLocal()
    try:
        lal_id = _team_id(db, "LAL")
        player_id = _pick_team_player(db, "LAL")
        game_ids = _team_game_ids(db, "LAL", 3)
        sim_id = _make_sim(db, "league", None)
        for gid in game_ids:
            _seed_line(db, sim_id, gid, lal_id, player_id, points=30)
    finally:
        db.close()
    try:
        body = client.get(f"/simulations/{sim_id}/player/{player_id}").json()
        assert body["sim"]["gp"] == 3
        assert body["sim"]["ppg"] == 30.0
        assert len(body["sim"]["by_team"]) == 1
    finally:
        _cleanup(sim_id)


# ----- Cross-scope consistency ----------------------------------------------

def test_myleague_route_and_season_route_return_identical_body():
    """Backwards-compat check: the /myleague path and /simulations path must
    return the same body for a MyLeague-scope sim, since they share the same
    derivation. If they diverge, one of the wrappers has drifted."""
    from app.models.myleague import MyLeagueState
    db = SessionLocal()
    try:
        lal_id = _team_id(db, "LAL")
        player_id = _pick_team_player(db, "LAL")
        game_ids = _team_game_ids(db, "LAL", 4)
        sim_id = _make_sim(db, "myleague", None)
        db.add(MyLeagueState(
            simulation_id=sim_id, controlled_team_id=lal_id,
            current_calendar_date=date(2024, 10, 21),
        ))
        db.commit()
        for gid in game_ids:
            _seed_line(db, sim_id, gid, lal_id, player_id, points=15)
    finally:
        db.close()
    try:
        a = client.get(f"/myleague/{sim_id}/player/{player_id}").json()
        b = client.get(f"/simulations/{sim_id}/player/{player_id}").json()
        assert a == b
    finally:
        db = SessionLocal()
        db.execute(MyLeagueState.__table__.delete().where(MyLeagueState.simulation_id == sim_id))
        db.commit(); db.close()
        _cleanup(sim_id)


# ----- 404 mappings ----------------------------------------------------------

def test_missing_sim_returns_404():
    r = client.get("/simulations/9999999/player/1")
    assert r.status_code == 404


def test_missing_player_returns_404():
    db = SessionLocal()
    try:
        lal_id = _team_id(db, "LAL")
        sim_id = _make_sim(db, "team", lal_id)
    finally:
        db.close()
    try:
        r = client.get(f"/simulations/{sim_id}/player/999999999")
        assert r.status_code == 404
    finally:
        _cleanup(sim_id)
