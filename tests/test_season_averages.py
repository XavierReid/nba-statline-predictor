"""GET /simulations/{sim_id}/averages — B4 sim-vs-real averages endpoint.

Integration test hitting the seeded DB. Requires at least one completed
SimulationRun; skips cleanly when none exists.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models.simulation import SimulationRun

client = TestClient(app)


def _a_completed_sim():
    db = SessionLocal()
    try:
        return db.execute(
            select(SimulationRun).where(SimulationRun.status == "complete")
        ).scalars().first()
    finally:
        db.close()


def test_averages_404_for_unknown_sim():
    r = client.get("/simulations/999999/averages")
    assert r.status_code == 404


def test_averages_shape_for_completed_run():
    sim = _a_completed_sim()
    if not sim:
        pytest.skip("no completed SimulationRun in DB")

    r = client.get(f"/simulations/{sim.id}/averages")
    assert r.status_code == 200
    data = r.json()

    # Top-level shape
    for key in ["sim_id", "team", "season", "team_totals", "players"]:
        assert key in data
    assert data["sim_id"] == sim.id

    # team_totals: sim always populated, real may be an empty dict if not ingested
    assert "sim" in data["team_totals"]
    assert "real" in data["team_totals"]
    sim_totals = data["team_totals"]["sim"]
    for key in ["gp", "ppg", "opp_ppg", "fga", "fta", "pf", "tov"]:
        assert key in sim_totals

    # At least one player row
    assert isinstance(data["players"], list)
    assert len(data["players"]) > 0

    # Player row shape
    p0 = data["players"][0]
    assert "player_id" in p0 and "name" in p0
    assert "sim" in p0
    for key in ["gp", "mpg", "ppg", "rpg", "apg", "spg", "bpg", "topg"]:
        assert key in p0["sim"]
    # `real` may be None for rookies / missing anchors — allowed.
    assert p0["real"] is None or isinstance(p0["real"], dict)

    # Players sorted by sim MPG descending
    mpgs = [p["sim"]["mpg"] for p in data["players"]]
    assert mpgs == sorted(mpgs, reverse=True)
