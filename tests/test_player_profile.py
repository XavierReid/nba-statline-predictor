"""GET /players/{id}/profile — the player-detail modal's data source.

Integration test: hits the app against the seeded DB. Finds a real seeded player so it
doesn't hardcode an id; skips the 200 case if the DB has no data.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models.player_season_stats import PlayerSeasonStats

client = TestClient(app)


def _a_seeded_player():
    db = SessionLocal()
    try:
        return db.execute(
            select(PlayerSeasonStats).where(PlayerSeasonStats.season == "2025-26")
        ).scalars().first()
    finally:
        db.close()


def test_profile_404_for_unknown_player():
    r = client.get("/players/1/profile?season=2025-26")
    assert r.status_code == 404


def test_profile_404_for_unknown_season():
    row = _a_seeded_player()
    if row is None:
        pytest.skip("no seeded data")
    r = client.get(f"/players/{row.player_id}/profile?season=1970-71")
    assert r.status_code == 404


def test_profile_shape_for_seeded_player():
    row = _a_seeded_player()
    if row is None:
        pytest.skip("no seeded data")
    r = client.get(f"/players/{row.player_id}/profile?season=2025-26")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == row.player_id
    assert body["season"] == "2025-26"
    for k in ("gp", "min", "pts", "reb", "ast", "fg_pct", "fg3_pct", "ft_pct"):
        assert k in body["season_averages"]
    # ratings present (seeded season) and overall is display-only aliasing
    assert "overall" in body["ratings"]
    assert "clutch" in body["ratings"]
