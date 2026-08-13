"""GET /simulations/{sim_id}/games/{game_id} — B1 game-detail endpoint.

Integration test hitting the seeded DB. Finds a completed SimulationRun and
uses one of its persisted games for the 200 case; skips cleanly when no
completed run exists (typical fresh DB).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models.simulation import SimulatedGame, SimulationRun

client = TestClient(app)


def _a_completed_sim():
    db = SessionLocal()
    try:
        sim = db.execute(
            select(SimulationRun).where(SimulationRun.status == "complete")
        ).scalars().first()
        if not sim:
            return None
        game = db.execute(
            select(SimulatedGame).where(SimulatedGame.simulation_id == sim.id)
        ).scalars().first()
        return (sim, game) if game else None
    finally:
        db.close()


def test_game_detail_404_for_unknown_sim():
    r = client.get("/simulations/999999/games/0022500001")
    assert r.status_code == 404


def test_game_detail_404_for_unknown_game():
    completed = _a_completed_sim()
    if not completed:
        pytest.skip("no completed SimulationRun in DB")
    sim, _ = completed
    r = client.get(f"/simulations/{sim.id}/games/0099999999")
    assert r.status_code == 404


def test_game_detail_shape_for_completed_run():
    completed = _a_completed_sim()
    if not completed:
        pytest.skip("no completed SimulationRun in DB")
    sim, sg = completed

    r = client.get(f"/simulations/{sim.id}/games/{sg.game_id}")
    assert r.status_code == 200

    data = r.json()
    # Matches SimulateGameResponse shape so the frontend can reuse LineScore /
    # BoxScore / PlayByPlay components verbatim.
    for key in [
        "season", "seed", "home_team", "away_team",
        "home_score", "away_score", "quarter_scores",
        "home_box", "away_box", "events",
    ]:
        assert key in data, f"missing {key}"

    assert data["season"] == sim.season
    assert isinstance(data["home_score"], int)
    assert isinstance(data["away_score"], int)
    assert "home" in data["quarter_scores"] and "away" in data["quarter_scores"]
    assert isinstance(data["home_box"], list)
    assert isinstance(data["events"], list)
    # Re-simulation is deterministic; the endpoint uses the stored seed. Historical
    # persisted runs may pre-date engine behavior changes, so we don't assert score
    # equality (that would fail whenever any sim-engine mechanism ships). The shape
    # assertions above are the durable contract; UAT covers the behavior contract.
