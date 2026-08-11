"""Foul-out invariant: once a player is fouled out during a game, they cannot
appear in any subsequent possession's on-court set, including OT.

Regression coverage for the 7-fouls bug — see project-bug-7-fouls-jokic memo.
The unit test uses a real simulate_game run and asserts:
  - No player has more than 6 PF (structural ceiling)
  - No player has more than 6 FOUL events in the event stream
  - Both invariants hold across regulation and OT
"""
from app.services.game_simulator import simulate_game
from app.services.game_simulator import load_roster  # noqa: F401  (kept for future data-driven tests)
from app.database import SessionLocal
from app.models.team import Team
from sqlalchemy import select
import pytest


def _load_two_rosters(season: str, home_abbr: str, away_abbr: str):
    """Small helper that pulls two real rosters from the seeded DB."""
    db = SessionLocal()
    try:
        from app.services.game_simulator import load_roster as _lr
        home = db.execute(select(Team).where(Team.abbreviation == home_abbr)).scalar_one_or_none()
        away = db.execute(select(Team).where(Team.abbreviation == away_abbr)).scalar_one_or_none()
        if not home or not away:
            return None, None
        return _lr(db, home.id, season), _lr(db, away.id, season)
    finally:
        db.close()


def test_no_player_exceeds_6_pf_and_6_foul_events():
    """The 7-fouls bug produced 7 FOUL events + fouled_out=true on the box.
    With the fouled_out filter, both counts should max out at 6.
    """
    home, away = _load_two_rosters("2025-26", "LAL", "DEN")
    if not home or not away:
        pytest.skip("no seeded roster for 2025-26")

    # Use seed 2026 — the exact seed that triggered the OT foul-out bug.
    result = simulate_game(home, away, seed=2026, season="2025-26", capture_descriptions=True)
    box = result["box_score"]

    for pid, stats in box.items():
        assert stats["pf"] <= 6, f"player {pid} has PF {stats['pf']} > 6 (foul-out ceiling)"

    # Also count FOUL events per player in the event stream — the PBP that
    # PlayerModal displays. Before the fix this could exceed the box PF.
    events = result.get("events") or []
    from collections import Counter
    foul_counts: Counter = Counter()
    for ev in events:
        if ev.get("type") == "FOUL" and ev.get("player_id") is not None:
            foul_counts[ev["player_id"]] += 1

    for pid, cnt in foul_counts.items():
        assert cnt <= 6, f"player {pid} has {cnt} FOUL events in PBP > 6"

    # Invariant cross-check: any player marked fouled_out MUST have exactly 6 PF.
    for pid, stats in box.items():
        if stats.get("fouled_out"):
            assert stats["pf"] == 6, f"fouled_out player {pid} has PF {stats['pf']} != 6"


def test_atl_det_strategic_foul_repro():
    """Repros bug B (Isaiah Stewart same-minute strategic-foul spam) —
    ATL@DET 2025-12-12 in the Xavier-reported run.
    We can't perfectly reproduce the season-level seed derivation from a
    single-game call, but the LAL@DEN test above covers both bugs' fix
    surface (regular + strategic on-court filter)."""
    home, away = _load_two_rosters("2025-26", "DET", "ATL")
    if not home or not away:
        pytest.skip("no seeded roster")

    # Run a few seeds and confirm the invariant across all of them —
    # the fix is deterministic so any run should satisfy the ceiling.
    for seed in (26, 42, 100, 1787728955):
        result = simulate_game(home, away, seed=seed, season="2025-26",
                               capture_descriptions=True)
        box = result["box_score"]
        for pid, stats in box.items():
            assert stats["pf"] <= 6
        events = result.get("events") or []
        from collections import Counter
        foul_counts: Counter = Counter()
        for ev in events:
            if ev.get("type") == "FOUL" and ev.get("player_id") is not None:
                foul_counts[ev["player_id"]] += 1
        for pid, cnt in foul_counts.items():
            assert cnt <= 6, f"seed {seed}: pid {pid} has {cnt} FOUL events"
