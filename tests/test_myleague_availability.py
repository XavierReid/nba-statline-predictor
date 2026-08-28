"""M-4 tests — user-driven availability mutations.

Covers Xavier's locked invariants:
  1. Only controlled team can be mutated (server-side)
  2. Only rostered players can be marked (un)available
  3. Payload requires team_id + player_id
  4. Previously simulated games are immutable (existing retroactive
     guard; re-verified here for the availability flow)
  5. Availability change affects the next eligible game after the
     event, not simply UI state
  6. Deterministic fold on repeated/conflicting events
     (UNAVAILABLE → UNAVAILABLE → AVAILABLE)
  7. Same root seed + same event log preserves reproducibility of
     downstream sims

Also covers the end-to-end UAT gate: mark OUT → advance → verify
player absent from the resulting boxscore → mark AVAILABLE → advance
one more game → verify player present again.
"""
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models.game import Game
from app.models.myleague import MyLeagueEvent, MyLeagueState
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


def _create_run(controlled_abbr: str) -> int:
    r = client.post(
        "/myleague/",
        json={"season": SEASON, "seed": 42,
              "controlled_team_id": _lookup_team_id(controlled_abbr)},
    )
    return r.json()["simulation_id"]


def _lookup_team_id(abbr: str) -> int:
    db = SessionLocal()
    try:
        return _tid(db, abbr)
    finally:
        db.close()


def _lal_player() -> int:
    db = SessionLocal()
    try:
        lal_id = _tid(db, "LAL")
        return db.execute(
            select(PlayerSeasonStats.player_id)
            .where(PlayerSeasonStats.season == SEASON,
                   PlayerSeasonStats.team_id == lal_id)
            .limit(1)
        ).scalar_one()
    finally:
        db.close()


def _gsw_player() -> int:
    db = SessionLocal()
    try:
        gsw_id = _tid(db, "GSW")
        return db.execute(
            select(PlayerSeasonStats.player_id)
            .where(PlayerSeasonStats.season == SEASON,
                   PlayerSeasonStats.team_id == gsw_id)
            .limit(1)
        ).scalar_one()
    finally:
        db.close()


def _cleanup(sim_id: int) -> None:
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


def _cursor(sim_id: int) -> str:
    return client.get(f"/myleague/{sim_id}").json()["state"]["current_calendar_date"]


# --- Invariant 1: opponent-team mutation rejected (server-side) -------------

def test_opponent_team_mutation_rejected_server_side():
    """SET_UNAVAILABLE on a non-controlled team must 422 from the engine,
    not merely be hidden by the UI."""
    sim_id = _create_run("LAL")
    try:
        r = client.post(
            f"/myleague/{sim_id}/events",
            json={
                "event_type": "SET_UNAVAILABLE",
                "applied_at_date": _cursor(sim_id),
                "payload": {"team_id": _lookup_team_id("GSW"),
                            "player_id": _gsw_player()},
            },
        )
        assert r.status_code == 422
        assert "controlled" in r.text.lower() or "opponent" in r.text.lower()
    finally:
        _cleanup(sim_id)


# --- Invariant 2: player must actually be rostered ------------------------

def test_non_rostered_player_rejected():
    """LAL controlled, but the payload targets a GSW player (with LAL
    team_id spoofed) → 422 (roster-membership check)."""
    sim_id = _create_run("LAL")
    try:
        r = client.post(
            f"/myleague/{sim_id}/events",
            json={
                "event_type": "SET_UNAVAILABLE",
                "applied_at_date": _cursor(sim_id),
                "payload": {"team_id": _lookup_team_id("LAL"),
                            "player_id": _gsw_player()},
            },
        )
        assert r.status_code == 422
        assert "rostered" in r.text.lower() or "not" in r.text.lower()
    finally:
        _cleanup(sim_id)


# --- Invariant 3: missing payload fields → 422 -----------------------------

def test_missing_payload_fields_rejected():
    sim_id = _create_run("LAL")
    try:
        r = client.post(
            f"/myleague/{sim_id}/events",
            json={
                "event_type": "SET_UNAVAILABLE",
                "applied_at_date": _cursor(sim_id),
                "payload": {"team_id": _lookup_team_id("LAL")},
            },
        )
        assert r.status_code == 422
    finally:
        _cleanup(sim_id)


# --- Invariant 4: previously simulated games are immutable -----------------

def test_previously_simulated_games_immutable():
    """Advance past a date, then try to write an event with applied_at_date
    <= a completed game. Must 422 (retroactive-mutation guard)."""
    sim_id = _create_run("LAL")
    try:
        # Advance ~2 weeks so at least one LAL game is completed.
        cursor_before = _cursor(sim_id)
        y, m, d = [int(x) for x in cursor_before.split("-")]
        target = (date(y, m, d) + timedelta(days=14)).isoformat()
        client.post(f"/myleague/{sim_id}/advance", json={"target_date": target})
        # Now try to write an event at the ORIGINAL cursor.
        r = client.post(
            f"/myleague/{sim_id}/events",
            json={
                "event_type": "SET_UNAVAILABLE",
                "applied_at_date": cursor_before,
                "payload": {"team_id": _lookup_team_id("LAL"),
                            "player_id": _lal_player()},
            },
        )
        assert r.status_code == 422
        assert "retroactive" in r.text.lower() or "already-simulated" in r.text.lower()
    finally:
        _cleanup(sim_id)


# --- Invariant 5 + 7 + E2E gate: mark OUT → advance → verify → mark ------
# ------ AVAILABLE → advance → verify. This is the mutation → fold → sim ---
# ------ pipeline gate. -----------------------------------------------------

def test_out_survives_availability_reload_in_simulate_game():
    """Regression for the M-4 sim-bug: when use_availability=True and the
    roster passed to simulate_game is shorter than depth (because OUT
    players got filtered out), simulate_game's inner reload used to pull
    the FULL roster and silently re-include the OUT players. Ship the
    fix: advance_to must pass unavailable_player_ids so simulate_game
    re-applies the fold after reload.

    Uses the drama-m3-season preset because that's what actually
    triggers the reload path in production."""
    lal_id = _lookup_team_id("LAL")
    player = _lal_player()
    r = client.post(
        "/myleague/",
        json={
            "season": SEASON, "seed": 42, "controlled_team_id": lal_id,
            "config": {"preset": "drama-m3-season"},
        },
    )
    sim_id = r.json()["simulation_id"]
    try:
        # Mark OUT at the cursor (frontend semantics: applied_at_date = cursor + 1).
        y, m, d = [int(x) for x in _cursor(sim_id).split("-")]
        eff = (date(y, m, d) + timedelta(days=1)).isoformat()
        r = client.post(
            f"/myleague/{sim_id}/events",
            json={
                "event_type": "SET_UNAVAILABLE",
                "applied_at_date": eff,
                "payload": {"team_id": lal_id, "player_id": player},
            },
        )
        assert r.status_code == 201, r.text

        # Advance to LAL's first upcoming game.
        summary = client.get(f"/myleague/{sim_id}").json()
        upcoming = summary["upcoming_games"]
        assert len(upcoming) > 0
        first_lal = next(g for g in upcoming if "LAL" in (g["home_team"], g["away_team"]))
        client.post(f"/myleague/{sim_id}/advance",
                    json={"target_date": first_lal["game_date"]})

        # Verify: no SimulatedPlayerLine for `player` in the game just played.
        # WITHOUT the fix, simulate_game's reload re-included the player and
        # he'd have a line row.
        db = SessionLocal()
        try:
            game_id_row = db.execute(
                select(SimulatedGame.id)
                .where(SimulatedGame.simulation_id == sim_id)
                .where(SimulatedGame.game_id == first_lal["game_id"])
            ).scalar_one_or_none()
            assert game_id_row is not None
            line = db.execute(
                select(SimulatedPlayerLine.id)
                .where(SimulatedPlayerLine.simulated_game_id == game_id_row)
                .where(SimulatedPlayerLine.player_id == player)
            ).scalar_one_or_none()
            assert line is None, (
                "OUT event was silently re-included by simulate_game's reload"
            )
        finally:
            db.close()
    finally:
        _cleanup(sim_id)


def test_mark_out_advance_verify_then_available_e2e():
    """End-to-end: set player OUT → advance one game → boxscore doesn't
    include them → set AVAILABLE → advance another game → boxscore does."""
    sim_id = _create_run("LAL")
    try:
        player = _lal_player()
        lal_id = _lookup_team_id("LAL")

        # Mark OUT at the current cursor.
        r = client.post(
            f"/myleague/{sim_id}/events",
            json={
                "event_type": "SET_UNAVAILABLE",
                "applied_at_date": _cursor(sim_id),
                "payload": {"team_id": lal_id, "player_id": player},
            },
        )
        assert r.status_code == 201

        # Advance to LAL's first upcoming game. Get its date via /myleague.
        summary = client.get(f"/myleague/{sim_id}").json()
        upcoming = summary["upcoming_games"]
        assert len(upcoming) > 0
        # Take the earliest LAL game.
        lal_next = upcoming[0]
        assert lal_next["home_team"] == "LAL" or lal_next["away_team"] == "LAL"
        client.post(
            f"/myleague/{sim_id}/advance",
            json={"target_date": lal_next["game_date"]},
        )

        # Verify: no SimulatedPlayerLine for `player` in the game just played.
        db = SessionLocal()
        try:
            game_id_row = db.execute(
                select(SimulatedGame.id)
                .where(SimulatedGame.simulation_id == sim_id)
                .where(SimulatedGame.game_id == lal_next["game_id"])
            ).scalar_one_or_none()
            assert game_id_row is not None, "advance didn't persist the game"
            line = db.execute(
                select(SimulatedPlayerLine.id)
                .where(SimulatedPlayerLine.simulated_game_id == game_id_row)
                .where(SimulatedPlayerLine.player_id == player)
            ).scalar_one_or_none()
            assert line is None, "player marked OUT still appeared in the game"
        finally:
            db.close()

        # Mark AVAILABLE at cursor+1 (same rule the frontend uses — the
        # engine's retroactive guard rejects same-day mutations because
        # the cursor after advance IS the date of the just-completed
        # game).
        y2, m2, d2 = [int(x) for x in _cursor(sim_id).split("-")]
        eff2 = (date(y2, m2, d2) + timedelta(days=1)).isoformat()
        r = client.post(
            f"/myleague/{sim_id}/events",
            json={
                "event_type": "SET_AVAILABLE",
                "applied_at_date": eff2,
                "payload": {"team_id": lal_id, "player_id": player},
            },
        )
        assert r.status_code == 201

        # Advance to LAL's next upcoming game.
        summary = client.get(f"/myleague/{sim_id}").json()
        upcoming = summary["upcoming_games"]
        assert len(upcoming) > 0
        lal_next2 = upcoming[0]
        client.post(
            f"/myleague/{sim_id}/advance",
            json={"target_date": lal_next2["game_date"]},
        )

        # Verify: SimulatedPlayerLine for `player` DOES appear.
        db = SessionLocal()
        try:
            game_id_row = db.execute(
                select(SimulatedGame.id)
                .where(SimulatedGame.simulation_id == sim_id)
                .where(SimulatedGame.game_id == lal_next2["game_id"])
            ).scalar_one_or_none()
            assert game_id_row is not None
            line = db.execute(
                select(SimulatedPlayerLine.id)
                .where(SimulatedPlayerLine.simulated_game_id == game_id_row)
                .where(SimulatedPlayerLine.player_id == player)
            ).scalar_one_or_none()
            assert line is not None, (
                "player marked AVAILABLE was still absent from the next game"
            )
        finally:
            db.close()
    finally:
        _cleanup(sim_id)


# --- Invariant 6: repeated/conflicting events fold deterministically ------

def test_repeated_and_conflicting_events_fold_deterministic():
    """UNAVAILABLE → UNAVAILABLE → AVAILABLE at the same cursor:
    the final fold must show the player as AVAILABLE (last-write-wins
    with the AVAILABLE event superseding)."""
    sim_id = _create_run("LAL")
    try:
        player = _lal_player()
        lal_id = _lookup_team_id("LAL")
        cursor = _cursor(sim_id)

        for et in ("SET_UNAVAILABLE", "SET_UNAVAILABLE", "SET_AVAILABLE"):
            r = client.post(
                f"/myleague/{sim_id}/events",
                json={
                    "event_type": et,
                    "applied_at_date": cursor,
                    "payload": {"team_id": lal_id, "player_id": player},
                },
            )
            assert r.status_code == 201, r.text

        # Fetch the team drill-in — the availability chip should say Avail.
        body = client.get(f"/myleague/{sim_id}/team/LAL").json()
        row = next(p for p in body["roster"] if p["player_id"] == player)
        assert row["availability"] == "AVAILABLE"
    finally:
        _cleanup(sim_id)


# --- Invariant 7: reproducibility of downstream sims ---------------------

def test_root_seed_plus_events_are_reproducible():
    """Two runs with same seed + same event log produce identical sim
    outputs (via existing deterministic seeding). Assert by comparing
    persisted home/away scores of the games played after the event."""
    seed = 424242
    lal_id = _lookup_team_id("LAL")
    player = _lal_player()

    def _run_once() -> list[tuple[str, int, int]]:
        r = client.post(
            "/myleague/",
            json={"season": SEASON, "seed": seed, "controlled_team_id": lal_id},
        )
        sid = r.json()["simulation_id"]
        try:
            # Apply the same event.
            client.post(
                f"/myleague/{sid}/events",
                json={
                    "event_type": "SET_UNAVAILABLE",
                    "applied_at_date": _cursor(sid),
                    "payload": {"team_id": lal_id, "player_id": player},
                },
            )
            # Advance ~2 weeks.
            cursor_before = _cursor(sid)
            y, m, d = [int(x) for x in cursor_before.split("-")]
            target = (date(y, m, d) + timedelta(days=14)).isoformat()
            client.post(f"/myleague/{sid}/advance", json={"target_date": target})
            # Collect (game_id, home_score, away_score) for all persisted games.
            db = SessionLocal()
            try:
                rows = db.execute(
                    select(SimulatedGame.game_id, SimulatedGame.home_score,
                           SimulatedGame.away_score)
                    .where(SimulatedGame.simulation_id == sid)
                    .order_by(SimulatedGame.game_id.asc())
                ).all()
                return [(g, h, a) for g, h, a in rows]
            finally:
                db.close()
        finally:
            _cleanup(sid)

    a = _run_once()
    b = _run_once()
    assert a == b, "same seed + same event log produced different sim outputs"
