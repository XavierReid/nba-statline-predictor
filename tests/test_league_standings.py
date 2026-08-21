"""Standings computation unit tests (C-2.1).

Pure-function tests for compute_standings — synthetic game tuples in,
computed standings out. GB formula and tie-breaker ordering are the
core correctness contracts.
"""
from app.services.league_simulator import compute_standings


def _game(gid, hid, aid, hs, as_, habbr, aabbr):
    return (gid, hid, aid, hs, as_, habbr, aabbr)


def test_empty_input():
    assert compute_standings([]) == []


def test_single_game_leader_and_loser():
    rows = compute_standings([_game("g1", 1, 2, 110, 100, "AAA", "BBB")])
    assert len(rows) == 2
    winner, loser = rows[0], rows[1]
    assert winner.team_id == 1 and winner.wins == 1 and winner.losses == 0
    assert winner.pct == 1.0 and winner.gb == 0.0
    assert winner.rank == 1
    assert loser.team_id == 2 and loser.wins == 0 and loser.losses == 1
    assert loser.pct == 0.0
    # GB = ((leader_w - team_w) + (team_l - leader_l)) / 2 = ((1-0)+(1-0))/2 = 1.0
    assert loser.gb == 1.0
    assert loser.rank == 2


def test_gb_formula_explicit():
    """Leader 3-0, second 2-1, third 1-2, fourth 0-3. GB should be 0, 1.0, 2.0, 3.0."""
    games = [
        # Leader (team 1) beats teams 2, 3, 4
        _game("g1", 1, 2, 110, 100, "AAA", "BBB"),
        _game("g2", 1, 3, 110, 100, "AAA", "CCC"),
        _game("g3", 1, 4, 110, 100, "AAA", "DDD"),
        # Team 2 beats 3, 4
        _game("g4", 2, 3, 110, 100, "BBB", "CCC"),
        _game("g5", 2, 4, 110, 100, "BBB", "DDD"),
        # Team 3 beats 4
        _game("g6", 3, 4, 110, 100, "CCC", "DDD"),
    ]
    rows = compute_standings(games)
    assert len(rows) == 4
    by_id = {r.team_id: r for r in rows}
    assert by_id[1].wins == 3 and by_id[1].losses == 0 and by_id[1].gb == 0.0
    assert by_id[2].wins == 2 and by_id[2].losses == 1 and by_id[2].gb == 1.0
    assert by_id[3].wins == 1 and by_id[3].losses == 2 and by_id[3].gb == 2.0
    assert by_id[4].wins == 0 and by_id[4].losses == 3 and by_id[4].gb == 3.0


def test_ordering_w_desc_l_asc_team_id_asc():
    """Two teams with same W-L must be ordered by team_id asc."""
    games = [
        # Team 2 has 1-1
        _game("g1", 2, 3, 110, 100, "BBB", "CCC"),   # team 2 wins
        _game("g2", 3, 2, 110, 100, "CCC", "BBB"),   # team 2 loses (team 3 wins)
        # Team 1 also 1-1
        _game("g3", 1, 4, 110, 100, "AAA", "DDD"),   # team 1 wins
        _game("g4", 4, 1, 110, 100, "DDD", "AAA"),   # team 1 loses
    ]
    rows = compute_standings(games)
    # All 4 teams are 1-1. Sort by team_id asc.
    assert [r.team_id for r in rows] == [1, 2, 3, 4]
    assert [r.rank for r in rows] == [1, 2, 3, 4]
    # All tied for leader; GB is 0 for the top-ranked and (some value) for others.
    # Actual formula: leader_w=1, leader_l=1. For any 1-1 team: gb = ((1-1)+(1-1))/2 = 0.
    for r in rows:
        assert r.gb == 0.0


def test_pct_and_gb_for_partial_season():
    """Provisional standings mid-season: verify pct + gb still correct."""
    games = [
        _game("g1", 1, 2, 110, 100, "AAA", "BBB"),   # team 1: 1-0
        _game("g2", 1, 3, 110, 100, "AAA", "CCC"),   # team 1: 2-0
        _game("g3", 2, 3, 110, 100, "BBB", "CCC"),   # team 2: 1-1
    ]
    rows = compute_standings(games)
    by_id = {r.team_id: r for r in rows}
    assert by_id[1].wins == 2 and by_id[1].losses == 0
    assert by_id[1].pct == 1.0 and by_id[1].gb == 0.0
    assert by_id[2].wins == 1 and by_id[2].losses == 1
    assert by_id[2].pct == 0.5
    # GB(2) = ((2-1) + (1-0)) / 2 = 1.0
    assert by_id[2].gb == 1.0
    assert by_id[3].wins == 0 and by_id[3].losses == 2
    assert by_id[3].pct == 0.0
    # GB(3) = ((2-0) + (2-0)) / 2 = 2.0
    assert by_id[3].gb == 2.0
