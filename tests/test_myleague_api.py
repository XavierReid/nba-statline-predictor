"""M-1b endpoint tests for the MyLeague HTTP surface.

Exercises the four routes:
  POST /myleague/
  POST /myleague/{id}/advance
  POST /myleague/{id}/events
  GET  /myleague/{id}

Covers happy paths + every error mapping (404 on missing, 422 on
retroactive/backward/malformed). Uses 2024-25 for schedule fixtures
(post-ingestion fix — see 064e318).
"""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.game import Game
from app.models.myleague import MyLeagueEvent, MyLeagueState
from app.models.simulation import SimulatedGame, SimulatedPlayerLine, SimulationRun
from sqlalchemy import select


client = TestClient(app)
SEASON = "2024-25"


def _cleanup(sim_id: int) -> None:
    """Manual FK-safe cleanup — same pattern as test_myleague_engine."""
    db = SessionLocal()
    db.rollback()
    game_ids = db.execute(
        select(SimulatedGame.id).where(SimulatedGame.simulation_id == sim_id)
    ).scalars().all()
    if game_ids:
        db.execute(
            SimulatedPlayerLine.__table__.delete()
            .where(SimulatedPlayerLine.simulated_game_id.in_(game_ids))
        )
    db.execute(SimulatedGame.__table__.delete().where(SimulatedGame.simulation_id == sim_id))
    st = db.execute(select(MyLeagueState).where(MyLeagueState.simulation_id == sim_id)).scalar_one_or_none()
    if st:
        db.execute(MyLeagueEvent.__table__.delete().where(MyLeagueEvent.myleague_state_id == st.id))
        db.execute(MyLeagueState.__table__.delete().where(MyLeagueState.id == st.id))
    db.execute(SimulationRun.__table__.delete().where(SimulationRun.id == sim_id))
    db.commit()
    db.close()


def _first_game_date() -> date:
    db = SessionLocal()
    try:
        row = db.execute(
            select(Game.game_date)
            .where(Game.game_date >= date(2024, 10, 1))
            .order_by(Game.game_date.asc())
        ).first()
        assert row is not None
        return row[0]
    finally:
        db.close()


def test_create_returns_201_and_base_state():
    resp = client.post("/myleague/", json={"season": SEASON, "seed": 42})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    try:
        assert body["season"] == SEASON
        assert body["root_seed"] == 42
        assert body["controlled_team_id"] is None
        assert body["games_completed"] == 0
    finally:
        _cleanup(body["simulation_id"])


def test_create_random_seed_when_omitted():
    resp = client.post("/myleague/", json={"season": SEASON})
    assert resp.status_code == 201
    body = resp.json()
    try:
        assert isinstance(body["root_seed"], int)
        assert body["root_seed"] > 0
    finally:
        _cleanup(body["simulation_id"])


def test_create_rejects_unknown_controlled_team():
    resp = client.post(
        "/myleague/",
        json={"season": SEASON, "controlled_team_id": 999_999_999},
    )
    assert resp.status_code == 422


def test_advance_moves_cursor_and_persists_games():
    created = client.post("/myleague/", json={"season": SEASON, "seed": 7}).json()
    sim_id = created["simulation_id"]
    try:
        day1 = _first_game_date()
        resp = client.post(f"/myleague/{sim_id}/advance", json={"target_date": day1.isoformat()})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["current_calendar_date"] == day1.isoformat()
        assert body["games_completed"] > 0
    finally:
        _cleanup(sim_id)


def test_advance_backward_returns_422():
    created = client.post("/myleague/", json={"season": SEASON, "seed": 7}).json()
    sim_id = created["simulation_id"]
    try:
        day1 = _first_game_date()
        client.post(f"/myleague/{sim_id}/advance", json={"target_date": day1.isoformat()})
        resp = client.post(
            f"/myleague/{sim_id}/advance",
            json={"target_date": (day1 - timedelta(days=5)).isoformat()},
        )
        assert resp.status_code == 422
    finally:
        _cleanup(sim_id)


def test_advance_missing_returns_404():
    resp = client.post("/myleague/99999999/advance", json={"target_date": "2025-01-01"})
    assert resp.status_code == 404


def test_events_forward_accepted_and_persisted():
    created = client.post("/myleague/", json={"season": SEASON, "seed": 7}).json()
    sim_id = created["simulation_id"]
    try:
        future = _first_game_date() + timedelta(days=30)
        resp = client.post(
            f"/myleague/{sim_id}/events",
            json={
                "event_type": "SET_UNAVAILABLE",
                "applied_at_date": future.isoformat(),
                "payload": {"team_id": 1610612747, "player_id": 2544},  # LAL / LeBron
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["event_type"] == "SET_UNAVAILABLE"
        assert body["applied_at_date"] == future.isoformat()
        assert body["payload"]["player_id"] == 2544
    finally:
        _cleanup(sim_id)


def test_events_retroactive_returns_422():
    created = client.post("/myleague/", json={"season": SEASON, "seed": 7}).json()
    sim_id = created["simulation_id"]
    try:
        day1 = _first_game_date()
        client.post(f"/myleague/{sim_id}/advance", json={"target_date": day1.isoformat()})
        resp = client.post(
            f"/myleague/{sim_id}/events",
            json={
                "event_type": "SET_UNAVAILABLE",
                "applied_at_date": day1.isoformat(),  # SAME day as completed game
                "payload": {"team_id": 1610612747, "player_id": 2544},
            },
        )
        assert resp.status_code == 422
    finally:
        _cleanup(sim_id)


def test_events_unknown_type_returns_422():
    created = client.post("/myleague/", json={"season": SEASON, "seed": 7}).json()
    sim_id = created["simulation_id"]
    try:
        resp = client.post(
            f"/myleague/{sim_id}/events",
            json={
                "event_type": "NOT_A_REAL_EVENT",
                "applied_at_date": "2025-04-01",
                "payload": {},
            },
        )
        assert resp.status_code == 422
    finally:
        _cleanup(sim_id)


def test_get_returns_state_standings_and_recent_games():
    created = client.post("/myleague/", json={"season": SEASON, "seed": 7}).json()
    sim_id = created["simulation_id"]
    try:
        day1 = _first_game_date()
        client.post(f"/myleague/{sim_id}/advance", json={"target_date": day1.isoformat()})
        resp = client.get(f"/myleague/{sim_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "state" in body
        assert body["state"]["current_calendar_date"] == day1.isoformat()
        assert isinstance(body["standings"], list)
        assert isinstance(body["recent_games"], list)
        # After advancing to day 1, at least one game exists.
        assert len(body["recent_games"]) > 0
        # And every team involved should appear in standings.
        assert len(body["standings"]) > 0
    finally:
        _cleanup(sim_id)


def test_get_missing_returns_404():
    resp = client.get("/myleague/99999999")
    assert resp.status_code == 404


def test_get_rejects_non_myleague_scope_as_404():
    """A team- or league-scope sim id must return 404 from /myleague/{id},
    not silently masquerade as a myleague run."""
    db = SessionLocal()
    try:
        sim = SimulationRun(
            season=SEASON, scope="league", team_id=None,
            status="pending", seed=1, games_completed=0,
        )
        db.add(sim); db.commit(); db.refresh(sim)
        sim_id = sim.id
    finally:
        db.close()
    try:
        resp = client.get(f"/myleague/{sim_id}")
        assert resp.status_code == 404
    finally:
        _cleanup(sim_id)


def _lal_id() -> int:
    db = SessionLocal()
    try:
        from app.models.team import Team as _T
        return db.execute(select(_T.id).where(_T.abbreviation == "LAL")).scalar_one()
    finally:
        db.close()


def test_next_game_preview_null_without_controlled_team():
    """Runs with no controlled team never get a preview."""
    created = client.post("/myleague/", json={"season": SEASON, "seed": 1}).json()
    sim_id = created["simulation_id"]
    try:
        body = client.get(f"/myleague/{sim_id}").json()
        assert body["next_game_preview"] is None
    finally:
        _cleanup(sim_id)


def test_next_game_preview_populated_with_expected_shape():
    """With a controlled team + upcoming games, the preview is present
    and every documented field is populated with a sensible value."""
    created = client.post(
        "/myleague/",
        json={"season": SEASON, "seed": 1, "controlled_team_id": _lal_id()},
    ).json()
    sim_id = created["simulation_id"]
    try:
        body = client.get(f"/myleague/{sim_id}").json()
        p = body["next_game_preview"]
        assert p is not None, "preview should be populated with a controlled team + games ahead"
        # Shape
        for field in (
            "game_id", "game_date", "is_home", "opponent_abbr",
            "matchup_index", "matchup_total",
            "series_wins_controlled", "series_wins_opponent",
            "controlled_roster", "opponent_roster",
        ):
            assert field in p, f"preview missing field {field}"
        # Value sanity
        assert isinstance(p["is_home"], bool)
        assert p["opponent_abbr"] != "LAL"
        assert 1 <= p["matchup_index"] <= p["matchup_total"]
        assert p["series_wins_controlled"] == 0 and p["series_wins_opponent"] == 0
        assert 1 <= len(p["controlled_roster"]) <= 8
        assert 1 <= len(p["opponent_roster"]) <= 8
        for roster in (p["controlled_roster"], p["opponent_roster"]):
            for r in roster:
                assert r["player_id"] > 0
                assert r["name"]
                assert r["mpg"] >= 0
    finally:
        _cleanup(sim_id)


def test_next_game_preview_series_updates_after_playing():
    """After the controlled team plays its first meeting vs an opponent,
    subsequent previews against a later meeting reflect the series score."""
    created = client.post(
        "/myleague/",
        json={"season": SEASON, "seed": 1, "controlled_team_id": _lal_id()},
    ).json()
    sim_id = created["simulation_id"]
    try:
        # Advance past the season's first ~10 days so the controlled team has
        # definitely played at least one game.
        from datetime import timedelta as _td
        target = _first_game_date() + _td(days=14)
        client.post(f"/myleague/{sim_id}/advance", json={"target_date": target.isoformat()})
        body = client.get(f"/myleague/{sim_id}").json()
        p = body["next_game_preview"]
        # If LAL has more meetings ahead, the preview should exist. If the
        # remaining matchup is against a team LAL has already played, the
        # series wins for BOTH sides should sum to matchup_index - 1.
        if p is not None and p["matchup_index"] > 1:
            total = p["series_wins_controlled"] + p["series_wins_opponent"]
            assert total == p["matchup_index"] - 1, (
                f"series wins ({p['series_wins_controlled']}-{p['series_wins_opponent']}) "
                f"should sum to matchup_index-1 ({p['matchup_index'] - 1})"
            )
    finally:
        _cleanup(sim_id)
