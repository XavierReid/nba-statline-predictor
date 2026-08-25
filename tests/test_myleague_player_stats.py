"""Tests for GET /myleague/{sim_id}/player/{player_id} — the running-
averages endpoint locked in project-myleague-stats-contract.

Nine required tests + a synthetic traded-player fixture that gates the
"aggregate = totals-first-then-derive" math contract. Sim rows are
inserted directly against real Game+Player+Team ids so we control the
numbers exactly (no dependency on the actual simulator's output).
"""
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models.game import Game
from app.models.myleague import MyLeagueState
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


def _lookup_ids(db, home_abbr: str, away_abbr: str, count: int) -> tuple[int, int, list[str]]:
    """Return (home_team_id, away_team_id, first-`count` game_ids in season).

    Any Game rows will do — the test seeds SimulatedGame rows against them.
    """
    home_id = db.execute(select(Team.id).where(Team.abbreviation == home_abbr)).scalar_one()
    away_id = db.execute(select(Team.id).where(Team.abbreviation == away_abbr)).scalar_one()
    # Pick games that actually involve home_id so team_gp assertions are meaningful.
    game_ids = [
        g for (g,) in db.execute(
            select(Game.id)
            .where(Game.game_date >= date(2024, 10, 1))
            .where((Game.home_team_id == home_id) | (Game.away_team_id == home_id))
            .order_by(Game.game_date.asc())
            .limit(count)
        ).all()
    ]
    return home_id, away_id, game_ids


def _new_myleague_run(db, controlled_team_id: int) -> int:
    """Insert a bare myleague-scope SimulationRun + MyLeagueState. Returns sim_id."""
    sim = SimulationRun(
        season=SEASON, scope="myleague", team_id=None,
        seed=1, status="running", parameters={}, games_completed=0,
    )
    db.add(sim); db.flush()
    db.add(MyLeagueState(
        simulation_id=sim.id,
        controlled_team_id=controlled_team_id,
        current_calendar_date=date(2024, 10, 21),
    ))
    db.commit()
    return sim.id


def _seed_line(
    db, sim_id: int, game_id: str, team_id: int, player_id: int,
    *, minutes=30.0, points=25, rebounds=5, assists=6,
    steals=1, blocks=1, turnovers=2, fgm=10, fga=20,
    fg3m=2, fg3a=6, ftm=3, fta=4,
):
    """Create one SimulatedGame + one SimulatedPlayerLine for the player."""
    sg = SimulatedGame(
        simulation_id=sim_id, game_id=game_id,
        home_score=100, away_score=95, went_to_ot=False,
        quarter_scores={"home": [25, 25, 25, 25], "away": [24, 24, 24, 23]},
    )
    db.add(sg); db.flush()
    db.add(SimulatedPlayerLine(
        simulated_game_id=sg.id, player_id=player_id, team_id=team_id,
        minutes=minutes, points=points, rebounds=rebounds, assists=assists,
        steals=steals, blocks=blocks, turnovers=turnovers, personal_fouls=2,
        fouled_out=False, fgm=fgm, fga=fga, fg3m=fg3m, fg3a=fg3a,
        ftm=ftm, fta=fta, plus_minus=5,
    ))
    db.commit()


def _seed_game_only(db, sim_id: int, game_id: str, home_team_id: int, away_team_id: int):
    """Create a SimulatedGame with no player line — models 'team played but this player didn't'."""
    db.add(SimulatedGame(
        simulation_id=sim_id, game_id=game_id,
        home_score=100, away_score=95, went_to_ot=False,
        quarter_scores={"home": [25, 25, 25, 25], "away": [24, 24, 24, 23]},
    ))
    db.commit()


def _pick_lal_player(db) -> int:
    """Any player whose PlayerSeasonStats row lists LAL for the season."""
    lal_id = db.execute(select(Team.id).where(Team.abbreviation == "LAL")).scalar_one()
    row = db.execute(
        select(PlayerSeasonStats.player_id)
        .where(PlayerSeasonStats.season == SEASON, PlayerSeasonStats.team_id == lal_id)
        .limit(1)
    ).scalar_one()
    return row


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
        db.execute(MyLeagueState.__table__.delete().where(MyLeagueState.simulation_id == sim_id))
        db.execute(SimulationRun.__table__.delete().where(SimulationRun.id == sim_id))
        db.commit()
    finally:
        db.close()


# ---------- Required test 1 — 0 GP, team_gp == 0 -----------------------------

def test_zero_gp_season_fresh():
    """No games played anywhere: gp=0, team_gp=0. UI reads 'season just started'."""
    db = SessionLocal()
    try:
        lal_id = db.execute(select(Team.id).where(Team.abbreviation == "LAL")).scalar_one()
        player_id = _pick_lal_player(db)
        sim_id = _new_myleague_run(db, lal_id)
    finally:
        db.close()
    try:
        r = client.get(f"/myleague/{sim_id}/player/{player_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sim"]["gp"] == 0
        assert body["sim"]["team_gp"] == 0
        assert body["sim"]["ppg"] == 0.0
        assert body["sim"]["by_team"] == []
    finally:
        _cleanup(sim_id)


# ---------- Required test 2 — 0 GP, team_gp > 0 ------------------------------

def test_zero_gp_but_team_played_means_unavailable():
    """Team has played, this player has not: gp=0, team_gp>0. Distinct UI state."""
    db = SessionLocal()
    try:
        lal_id, opp_id, game_ids = _lookup_ids(db, "LAL", "GSW", 3)
        player_id = _pick_lal_player(db)
        sim_id = _new_myleague_run(db, lal_id)
        for gid in game_ids:
            _seed_game_only(db, sim_id, gid, lal_id, opp_id)
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/player/{player_id}").json()
        assert body["sim"]["gp"] == 0
        assert body["sim"]["team_gp"] >= 3
        # Numbers still zero — no dividing by team_gp.
        assert body["sim"]["ppg"] == 0.0
    finally:
        _cleanup(sim_id)


# ---------- Required test 3 — 1-9 GP (small-sample band) ---------------------

def test_small_sample_gp_reported_verbatim():
    """Backend just reports gp; the small-sample tag is a UI decision.
    Contract: gp is first-class, not string-formatted."""
    db = SessionLocal()
    try:
        lal_id, opp_id, game_ids = _lookup_ids(db, "LAL", "GSW", 5)
        player_id = _pick_lal_player(db)
        sim_id = _new_myleague_run(db, lal_id)
        for gid in game_ids:
            _seed_line(db, sim_id, gid, lal_id, player_id, points=20, rebounds=4, assists=5)
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/player/{player_id}").json()
        assert body["sim"]["gp"] == 5
        assert body["sim"]["ppg"] == 20.0
    finally:
        _cleanup(sim_id)


# ---------- Required test 4 — >=10 GP ----------------------------------------

def test_large_sample_uses_aggregate_math():
    """10+ games: rates match totals-derived. Vary per-game to catch avg bugs."""
    db = SessionLocal()
    try:
        lal_id, opp_id, game_ids = _lookup_ids(db, "LAL", "GSW", 10)
        player_id = _pick_lal_player(db)
        sim_id = _new_myleague_run(db, lal_id)
        for i, gid in enumerate(game_ids):
            _seed_line(
                db, sim_id, gid, lal_id, player_id,
                minutes=30 + i,          # 30..39 min → total 345, /10 = 34.5
                points=20 + i,           # 20..29    → total 245, /10 = 24.5
                fgm=8 + (i % 3),
                fga=18,
            )
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/player/{player_id}").json()
        assert body["sim"]["gp"] == 10
        assert body["sim"]["mpg"] == 34.5
        assert body["sim"]["ppg"] == 24.5
    finally:
        _cleanup(sim_id)


# ---------- Required test 5 — player missing from real season ----------------

def test_real_block_null_when_no_pss_row():
    """Player not in real season → real: null. Sim still populates."""
    db = SessionLocal()
    try:
        lal_id, opp_id, game_ids = _lookup_ids(db, "LAL", "GSW", 3)
        # Player who has no PSS row for SEASON. Use a rookie/off-season player.
        orphan = db.execute(
            select(Player.id).where(
                ~Player.id.in_(select(PlayerSeasonStats.player_id).where(PlayerSeasonStats.season == SEASON))
            ).limit(1)
        ).scalar()
        if orphan is None:
            import pytest
            pytest.skip("no orphan player available for this test")
        sim_id = _new_myleague_run(db, lal_id)
        for gid in game_ids:
            _seed_line(db, sim_id, gid, lal_id, orphan, points=10)
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/player/{orphan}").json()
        assert body["real"] is None
        assert body["sim"]["gp"] == 3
    finally:
        _cleanup(sim_id)


# ---------- Required test 6 — sim exists, no real reference (== test 5) ------
# Covered by test 5. Kept as a named contract check.

def test_both_states_render_when_sim_but_no_real():
    """Same as test 5 seen from the contract-mapping angle: both sim and
    real states are populated (real=null is a valid render state)."""
    db = SessionLocal()
    try:
        lal_id, opp_id, game_ids = _lookup_ids(db, "LAL", "GSW", 2)
        orphan = db.execute(
            select(Player.id).where(
                ~Player.id.in_(select(PlayerSeasonStats.player_id).where(PlayerSeasonStats.season == SEASON))
            ).limit(1)
        ).scalar()
        if orphan is None:
            import pytest
            pytest.skip("no orphan player available")
        sim_id = _new_myleague_run(db, lal_id)
        for gid in game_ids:
            _seed_line(db, sim_id, gid, lal_id, orphan)
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/player/{orphan}").json()
        assert "sim" in body and "real" in body
        assert body["sim"]["gp"] > 0
        assert body["real"] is None
    finally:
        _cleanup(sim_id)


# ---------- Required test 7 — traded aggregation matches sum(by_team) --------
# ---------- ALSO the implementation gate: totals-first math -----------------

def test_traded_player_aggregate_is_totals_first():
    """The math gate. Player logs 20 games with Team A + 10 games with Team B,
    intentionally different per-team rates. Aggregate rates must match the
    totals-derived calculation, NOT the average of the two team-level averages.

    Synthetic setup:
      Team A: 20 GP, 30 mpg,  20 ppg, fg 8/16 (50%)   → totals: 600 min, 400 pts, 160 fgm / 320 fga
      Team B: 10 GP, 40 mpg,  30 ppg, fg 10/12 (~83%) → totals: 400 min, 300 pts, 100 fgm / 120 fga
      Aggregate totals: 1000 min / 30 GP = 33.33 mpg,
                        700 pts / 30 GP = 23.33 ppg,
                        260 fgm / 440 fga = ~0.591 FG%
      WRONG average-of-averages would be (30+40)/2=35 mpg, (20+30)/2=25 ppg,
        (0.50+0.833)/2 = 0.667 FG% — assert these fail.
    """
    db = SessionLocal()
    try:
        team_a = db.execute(select(Team.id).where(Team.abbreviation == "LAL")).scalar_one()
        team_b = db.execute(select(Team.id).where(Team.abbreviation == "GSW")).scalar_one()
        player_id = _pick_lal_player(db)
        _, _, game_ids = _lookup_ids(db, "LAL", "GSW", 30)
        sim_id = _new_myleague_run(db, team_a)
        for gid in game_ids[:20]:
            _seed_line(
                db, sim_id, gid, team_a, player_id,
                minutes=30, points=20, rebounds=0, assists=0,
                steals=0, blocks=0, turnovers=0,
                fgm=8, fga=16, fg3m=0, fg3a=0, ftm=4, fta=4,
            )
        for gid in game_ids[20:30]:
            _seed_line(
                db, sim_id, gid, team_b, player_id,
                minutes=40, points=30, rebounds=0, assists=0,
                steals=0, blocks=0, turnovers=0,
                fgm=10, fga=12, fg3m=0, fg3a=0, ftm=10, fta=10,
            )
    finally:
        db.close()
    try:
        body = client.get(f"/myleague/{sim_id}/player/{player_id}").json()
        s = body["sim"]
        assert s["gp"] == 30
        # totals-first:
        assert abs(s["mpg"] - round(1000 / 30, 1)) < 1e-6         # 33.3
        assert abs(s["ppg"] - round(700 / 30, 1)) < 1e-6          # 23.3
        assert abs(s["fg_pct"] - round(260 / 440, 3)) < 1e-6      # 0.591
        # Confirm the wrong average-of-averages is NOT what we got:
        wrong_mpg = round((30 + 40) / 2, 1)   # 35.0
        wrong_ppg = round((20 + 30) / 2, 1)   # 25.0
        wrong_fgpct = round((0.5 + (10 / 12)) / 2, 3)  # 0.667
        assert s["mpg"] != wrong_mpg
        assert s["ppg"] != wrong_ppg
        assert s["fg_pct"] != wrong_fgpct
        # by_team preserves per-team info for future split UI
        assert len(s["by_team"]) == 2
        # Sum of per-team GP must equal aggregate GP
        assert sum(b["gp"] for b in s["by_team"]) == s["gp"]
    finally:
        _cleanup(sim_id)


# ---------- Required test 8 — stats update after Advance ---------------------
# We simulate "Advance" here as "another SPL row was persisted" since the
# derivation reads directly from the table — no cache to invalidate.

def test_stats_reflect_new_lines_after_advance():
    db = SessionLocal()
    try:
        lal_id, _, game_ids = _lookup_ids(db, "LAL", "GSW", 4)
        player_id = _pick_lal_player(db)
        sim_id = _new_myleague_run(db, lal_id)
        for gid in game_ids[:2]:
            _seed_line(db, sim_id, gid, lal_id, player_id, points=10)
    finally:
        db.close()
    try:
        b1 = client.get(f"/myleague/{sim_id}/player/{player_id}").json()
        assert b1["sim"]["gp"] == 2 and b1["sim"]["ppg"] == 10.0
        # "Advance" — add two more lines
        db = SessionLocal()
        try:
            for gid in game_ids[2:4]:
                _seed_line(db, sim_id, gid, lal_id, player_id, points=30)
        finally:
            db.close()
        b2 = client.get(f"/myleague/{sim_id}/player/{player_id}").json()
        assert b2["sim"]["gp"] == 4
        assert b2["sim"]["ppg"] == 20.0    # (10+10+30+30)/4
    finally:
        _cleanup(sim_id)


# ---------- Required test 9 — isolation between two MyLeague sim_ids ---------

def test_two_sim_ids_report_independent_sim_blocks():
    db = SessionLocal()
    try:
        lal_id, _, game_ids = _lookup_ids(db, "LAL", "GSW", 4)
        player_id = _pick_lal_player(db)
        sim_a = _new_myleague_run(db, lal_id)
        sim_b = _new_myleague_run(db, lal_id)
        for gid in game_ids[:2]:
            _seed_line(db, sim_a, gid, lal_id, player_id, points=10)
        for gid in game_ids[2:4]:
            _seed_line(db, sim_b, gid, lal_id, player_id, points=30)
    finally:
        db.close()
    try:
        a = client.get(f"/myleague/{sim_a}/player/{player_id}").json()
        b = client.get(f"/myleague/{sim_b}/player/{player_id}").json()
        assert a["sim"]["gp"] == 2 and a["sim"]["ppg"] == 10.0
        assert b["sim"]["gp"] == 2 and b["sim"]["ppg"] == 30.0
    finally:
        _cleanup(sim_a)
        _cleanup(sim_b)


# ---------- 404 mappings -----------------------------------------------------

def test_missing_sim_returns_404():
    r = client.get("/myleague/9999999/player/1")
    assert r.status_code == 404


def test_non_myleague_scope_returns_404():
    """Team-scope sim id must not resolve on the myleague route."""
    db = SessionLocal()
    try:
        lal_id = db.execute(select(Team.id).where(Team.abbreviation == "LAL")).scalar_one()
        sim = SimulationRun(
            season=SEASON, scope="team", team_id=lal_id, seed=1, status="pending",
            parameters={}, games_completed=0,
        )
        db.add(sim); db.commit(); db.refresh(sim); sid = sim.id
    finally:
        db.close()
    try:
        r = client.get(f"/myleague/{sid}/player/1")
        assert r.status_code == 404
    finally:
        db = SessionLocal()
        db.execute(SimulationRun.__table__.delete().where(SimulationRun.id == sid))
        db.commit(); db.close()
