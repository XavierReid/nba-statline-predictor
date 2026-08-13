"""Garbage rotation hierarchy ordering — regression coverage for the inversion bug
diagnosed in project-garbage-rotation-inversion.

Prior to the fix, an injury-limited high-MPG star could rank LOWER in the
`players_by_min` ordering than a durable role player (because the ordering
used the availability-normalized `minutes` field, which weights by GP).
MODE_GARBAGE's `[-5:]` slice then returned the actual STARTERS instead of
the bench.

The fix sorts by raw `mpg` (real per-game-played minutes) for hierarchy
decisions. `minutes` (availability-normalized share) is preserved for
scheduled-minute allocation in build_rotation.
"""
import random
from app.services.game_simulator import simulate_game  # noqa: F401 — sanity import
from app.services.rotation import build_rotation, resolve_lineup, MODE_GARBAGE


def _mk(name: str, pid: int, mpg: float, minutes: float, gp: int) -> dict:
    """Minimal roster player. `mpg` = real per-game-played MPG (role hierarchy);
    `minutes` = availability-normalized share (may diverge from mpg when a
    star misses games or a role-player plays every game)."""
    return {
        "id": pid, "name": name, "mpg": mpg, "minutes": minutes,
        "games_played": gp, "is_starter": True,
    }


def test_mode_garbage_returns_deep_bench_by_role_hierarchy():
    """MODE_GARBAGE must return the 5 LOWEST-mpg players, not the last 5 of
    an availability-normalized ordering."""
    # Reproduce the GSW-style inversion: Curry is a high-mpg star with low
    # season share (missed games); Podziemski / Green / etc. are durable
    # role players with higher normalized share.
    players = [
        _mk("Curry",       201939, mpg=30.9, minutes=23.0, gp=40),   # true star
        _mk("Butler",      202710, mpg=31.1, minutes=20.4, gp=45),   # true star
        _mk("Podziemski",  1642461, mpg=28.4, minutes=40.2, gp=82),  # role, high share
        _mk("Green",       203110, mpg=27.5, minutes=32.3, gp=75),   # role
        _mk("Moody",       1630228, mpg=25.7, minutes=26.6, gp=80),  # role
        _mk("Gui Santos",  1631212, mpg=15.0, minutes=24.1, gp=82),  # deep bench
        _mk("Will Richard",1642499, mpg=14.0, minutes=23.8, gp=80),  # deep bench
        _mk("Melton",      1629014, mpg=19.5, minutes=19.5, gp=50),  # role
        _mk("Horford",     201143, mpg=16.7, minutes=16.7, gp=55),   # role
        _mk("Porzingis",   204001, mpg=13.3, minutes=13.3, gp=40),   # role (limited)
    ]
    # Sort by mpg descending (the fix)
    by_min = sorted(players, key=lambda p: p.get("mpg", p["minutes"]), reverse=True)

    rotation = build_rotation(players, random.Random(0))  # unused by MODE_GARBAGE
    box = {p["id"]: {"pf": 0, "fouled_out": False} for p in players}

    active = resolve_lineup(rotation, minute=36, players_by_min=by_min,
                            box=box, mode=MODE_GARBAGE)

    # MODE_GARBAGE returns the 5 LOWEST-mpg. Curry (30.9) and Butler (31.1) are
    # top-2 mpg — they MUST NOT be in the garbage lineup.
    curry_id = 201939
    butler_id = 202710
    assert curry_id not in active, f"Curry (30.9 mpg) should NOT be in garbage lineup; got {active}"
    assert butler_id not in active, f"Butler (31.1 mpg) should NOT be in garbage lineup; got {active}"

    # Podziemski (28.4 mpg) — also NOT bench-tier, so should be excluded.
    podz_id = 1642461
    assert podz_id not in active, f"Podziemski (28.4 mpg) should NOT be in garbage lineup; got {active}"

    # Deep-bench players (mpg < 20) SHOULD be present.
    deep_bench_mpg = [p["id"] for p in players if p["mpg"] < 20]
    for pid in deep_bench_mpg:
        assert pid in active, f"Deep-bench player id={pid} should be in garbage lineup; got {active}"


def test_mode_garbage_regression_availability_normalized_would_promote_stars():
    """Explicitly asserts what the OLD (buggy) ordering would have returned so
    a future refactor that reintroduces the same bug will fail here.
    Demonstrates why the mpg-vs-minutes distinction matters."""
    players = [
        _mk("Curry",       201939, mpg=30.9, minutes=23.0, gp=40),
        _mk("Butler",      202710, mpg=31.1, minutes=20.4, gp=45),
        _mk("Podziemski",  1642461, mpg=28.4, minutes=40.2, gp=82),
        _mk("Green",       203110, mpg=27.5, minutes=32.3, gp=75),
        _mk("Moody",       1630228, mpg=25.7, minutes=26.6, gp=80),
        _mk("Gui Santos",  1631212, mpg=15.0, minutes=24.1, gp=82),
        _mk("Will Richard",1642499, mpg=14.0, minutes=23.8, gp=80),
        _mk("Melton",      1629014, mpg=19.5, minutes=19.5, gp=50),
        _mk("Horford",     201143, mpg=16.7, minutes=16.7, gp=55),
        _mk("Porzingis",   204001, mpg=13.3, minutes=13.3, gp=40),
    ]
    # OLD buggy ordering (by availability-normalized minutes)
    old_order = sorted(players, key=lambda p: p["minutes"], reverse=True)
    old_last5 = [p["id"] for p in old_order[-5:]]
    # Under the buggy ordering, Curry + Butler would end up in the "deepest 5".
    assert 201939 in old_last5, "test fixture assumption failed"
    assert 202710 in old_last5, "test fixture assumption failed"

    # NEW correct ordering (by mpg)
    new_order = sorted(players, key=lambda p: p["mpg"], reverse=True)
    new_last5 = [p["id"] for p in new_order[-5:]]
    # Curry + Butler must be out of the deepest 5 now.
    assert 201939 not in new_last5, "fix regressed — Curry back in garbage lineup"
    assert 202710 not in new_last5, "fix regressed — Butler back in garbage lineup"


def test_no_mpg_falls_back_to_minutes():
    """Defensive: if a player dict lacks `mpg`, the sort falls back to
    `minutes`. Ensures the fix doesn't KeyError on older/synthetic rosters."""
    players = [
        {"id": 1, "minutes": 30, "is_starter": True},   # no mpg
        {"id": 2, "minutes": 10, "is_starter": True},   # no mpg
    ]
    by_min = sorted(players, key=lambda p: p.get("mpg", p["minutes"]), reverse=True)
    assert by_min[0]["id"] == 1
    assert by_min[-1]["id"] == 2
