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

    def test_drama_m3_enables_garbage_rotation(self):
        assert DRAMA_M3.use_garbage_rotation is True

    def test_garbage_rotation_default_off(self):
        assert SimConfig().use_garbage_rotation is False


# ---------------------------------------------------------------------------
# Garbage-time rotation (gap 2.1)
# ---------------------------------------------------------------------------

from app.services.rotation import MODE_GARBAGE, MODE_SCHEDULED, resolve_lineup


from app.services.late_game import should_concede


class TestShouldConcede:
    def test_leader_concedes_at_garbage_margin(self):
        assert should_concede(True, 21, 500.0, 3, CFG, False) is True

    def test_trailer_holds_at_garbage_margin(self):
        # down 21 with 8+ minutes — still fighting
        assert should_concede(False, 21, 500.0, 3, CFG, False) is False

    def test_trailer_concedes_at_hopeless_margin(self):
        assert should_concede(False, 29, 500.0, 3, CFG, False) is True

    def test_trailer_concedes_late_with_big_deficit(self):
        assert should_concede(False, 22, 200.0, 3, CFG, False) is True

    def test_no_concession_before_q3(self):
        assert should_concede(True, 30, 500.0, 1, CFG, False) is False

    def test_q3_concession_needs_bigger_margin(self):
        # leader: Q4 threshold 20, Q3 threshold 25
        assert should_concede(True, 22, 500.0, 2, CFG, False) is False
        assert should_concede(True, 26, 500.0, 2, CFG, False) is True
        # trailer: Q3 threshold 33
        assert should_concede(False, 30, 500.0, 2, CFG, False) is False
        assert should_concede(False, 34, 500.0, 2, CFG, False) is True

    def test_hysteresis_applies_to_conceded_team(self):
        assert should_concede(True, 15, 300.0, 3, CFG, True) is True
        assert should_concede(True, 11, 300.0, 3, CFG, True) is False


class TestResolveLineup:
    def _team(self):
        # rotation hierarchy: p1 (most minutes) .. p10 (fewest)
        players_by_min = [{"id": i} for i in range(1, 11)]
        box = {i: {"fouled_out": False} for i in range(1, 11)}
        rotation = [[1, 2, 3, 4, 5]] * 48
        return rotation, players_by_min, box

    def test_scheduled_mode_uses_rotation_slot(self):
        rotation, by_min, box = self._team()
        assert resolve_lineup(rotation, 10, by_min, box, MODE_SCHEDULED) == [1, 2, 3, 4, 5]

    def test_garbage_mode_empties_bench_by_hierarchy(self):
        rotation, by_min, box = self._team()
        assert resolve_lineup(rotation, 45, by_min, box, MODE_GARBAGE) == [6, 7, 8, 9, 10]

    def test_garbage_mode_backfills_on_foulouts(self):
        rotation, by_min, box = self._team()
        box[9]["fouled_out"] = True
        box[10]["fouled_out"] = True
        # bench short two — backfill up the hierarchy with p4, p5
        assert resolve_lineup(rotation, 45, by_min, box, MODE_GARBAGE) == [4, 5, 6, 7, 8]
