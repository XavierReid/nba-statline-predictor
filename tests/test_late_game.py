"""Tests for the late-game context and endgame incentive pacing (gap 1.2)."""
import random

from app.services.late_game import build_context, possession_time_override
from app.services.sim_config import DRAMA_M3, SimConfig

CFG = SimConfig()  # default window: <=120s, |margin| <= 8


class TestContext:
    def test_inactive_outside_final_period(self):
        ctx = build_context(2, 60.0, 100, 95, True, CFG)  # Q3
        assert not ctx.in_window

    def test_inactive_early_in_final_period(self):
        ctx = build_context(3, 300.0, 100, 95, True, CFG)
        assert not ctx.in_window

    def test_inactive_when_margin_too_wide(self):
        ctx = build_context(3, 60.0, 100, 88, True, CFG)  # +12
        assert not ctx.in_window

    def test_active_in_q4_window(self):
        ctx = build_context(3, 90.0, 100, 96, True, CFG)
        assert ctx.in_window and ctx.offense_leading

    def test_active_in_ot(self):
        ctx = build_context(4, 60.0, 100, 102, True, CFG)  # OT, home down 2
        assert ctx.in_window and ctx.offense_trailing

    def test_margin_is_offense_perspective(self):
        # away offense, away trailing by 4
        ctx = build_context(3, 60.0, 100, 96, False, CFG)
        assert ctx.offense_margin == -4 and ctx.offense_trailing

    def test_tied_neither_leading_nor_trailing(self):
        ctx = build_context(3, 30.0, 100, 100, True, CFG)
        assert ctx.in_window
        assert not ctx.offense_trailing and not ctx.offense_leading


class TestTimeOverride:
    def test_trailing_plays_fast(self):
        rng = random.Random(1)
        ctx = build_context(3, 60.0, 96, 100, True, CFG)
        times = [possession_time_override(ctx, CFG, rng) for _ in range(200)]
        assert all(4.0 <= t <= 14.0 for t in times)
        assert sum(times) / len(times) < 11.0

    def test_leading_milks_clock(self):
        rng = random.Random(1)
        ctx = build_context(3, 60.0, 100, 96, True, CFG)
        times = [possession_time_override(ctx, CFG, rng) for _ in range(200)]
        assert all(14.0 <= t <= 24.0 for t in times)
        assert sum(times) / len(times) > 18.0

    def test_no_override_outside_window(self):
        rng = random.Random(1)
        ctx = build_context(1, 60.0, 96, 100, True, CFG)
        assert possession_time_override(ctx, CFG, rng) is None

    def test_no_override_when_tied(self):
        rng = random.Random(1)
        ctx = build_context(3, 30.0, 100, 100, True, CFG)
        assert possession_time_override(ctx, CFG, rng) is None


class TestConfig:
    def test_drama_m3_enables_endgame_pacing(self):
        assert DRAMA_M3.use_endgame_pacing is True

    def test_default_off(self):
        assert SimConfig().use_endgame_pacing is False
