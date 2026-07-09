"""Tests for the LineupQuality abstraction."""
from app.services.lineup_quality import (
    compute_lineup_quality,
    lineup_defensive_rating,
    rotation_baseline,
)
from app.services.sim_config import DRAMA_M3, SimConfig


def _p(pid, perim, interior, minutes=24.0):
    return {"id": pid, "perimeter_defense": perim, "interior_defense": interior, "minutes": minutes}


class TestBaseline:
    def test_baseline_is_minutes_weighted(self):
        # elite defender plays heavy minutes; scrub barely plays
        players = [_p(1, 90, 90, minutes=40.0), _p(2, 40, 40, minutes=4.0)]
        baseline = rotation_baseline(players)
        assert baseline > 80  # scrub's 4 minutes barely move it

    def test_flat_roster_average_would_distort(self):
        players = [_p(i, 80, 80, minutes=30.0) for i in range(5)] + [
            _p(9, 30, 30, minutes=1.0)
        ]
        assert rotation_baseline(players) > 75  # not dragged to (80*5+30)/6 ≈ 71.7


class TestLineupQuality:
    def test_normal_lineup_centers_on_one(self):
        players = [_p(i, 70, 70) for i in range(5)]
        baseline = rotation_baseline(players)
        q = compute_lineup_quality(players, baseline)
        assert abs(q["defense"] - 1.0) < 1e-9

    def test_bench_unit_defends_worse(self):
        starters = [_p(i, 80, 80) for i in range(5)]
        bench = [_p(i + 5, 60, 60) for i in range(5)]
        baseline = rotation_baseline(starters + bench)  # weighted toward starters
        q = compute_lineup_quality(bench, baseline)
        assert q["defense"] > 1.0  # opponent shots get easier

    def test_defensive_closing_group_defends_better(self):
        rotation = [_p(i, 70, 70) for i in range(10)]
        elite = [_p(i, 90, 90) for i in range(5)]
        baseline = rotation_baseline(rotation)
        q = compute_lineup_quality(elite, baseline)
        assert q["defense"] < 1.0

    def test_ten_point_gap_moves_five_percent(self):
        players = [_p(i, 60, 60) for i in range(5)]
        q = compute_lineup_quality(players, 70.0)
        assert abs(q["defense"] - 1.05) < 1e-9


class TestConfig:
    def test_drama_m3_enables_lineup_quality(self):
        assert DRAMA_M3.use_lineup_quality is True

    def test_default_off(self):
        assert SimConfig().use_lineup_quality is False
