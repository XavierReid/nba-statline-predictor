"""Tests for GamePhase — "what kind of basketball" classification layer."""
from app.services.game_phase import GamePhase, derive_phase
from app.services.sim_config import SimConfig

CFG = SimConfig()  # competitive_late_margin = 8


def _phase(period_index, abs_margin, home_conceded=False, away_conceded=False):
    return derive_phase(period_index, abs_margin, home_conceded, away_conceded, CFG)


class TestDerivePhase:
    def test_normal_early(self):
        assert _phase(0, 4) is GamePhase.NORMAL      # Q1
        assert _phase(2, 20) is GamePhase.NORMAL      # Q3, not conceded

    def test_competitive_late_q4_close(self):
        assert _phase(3, 5) is GamePhase.COMPETITIVE_LATE
        assert _phase(3, 8) is GamePhase.COMPETITIVE_LATE   # boundary inclusive

    def test_q4_not_close_is_normal(self):
        # Q4 but margin above competitive threshold, no concession
        assert _phase(3, 12) is GamePhase.NORMAL

    def test_overtime(self):
        assert _phase(4, 2) is GamePhase.OVERTIME
        assert _phase(5, 6) is GamePhase.OVERTIME

    def test_garbage_takes_priority(self):
        # a concession outranks period/margin — garbage can be Q3 or Q4
        assert _phase(2, 25, home_conceded=True) is GamePhase.GARBAGE
        assert _phase(3, 4, away_conceded=True) is GamePhase.GARBAGE
        # garbage outranks overtime too (edge case)
        assert _phase(4, 3, home_conceded=True) is GamePhase.GARBAGE

    def test_competitive_late_requires_no_concession(self):
        # a conceded Q4 close game is GARBAGE, not COMPETITIVE_LATE
        assert _phase(3, 6, home_conceded=True) is GamePhase.GARBAGE

    def test_margin_boundary_respects_config(self):
        cfg = SimConfig(competitive_late_margin=5)
        assert derive_phase(3, 6, False, False, cfg) is GamePhase.NORMAL
        assert derive_phase(3, 5, False, False, cfg) is GamePhase.COMPETITIVE_LATE
