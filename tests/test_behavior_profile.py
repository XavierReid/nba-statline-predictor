"""Tests for BehaviorProfile — baseline behavior owned by a GamePhase."""
from app.services.behavior_profile import (
    NORMAL_PROFILE,
    BehaviorProfile,
    ShotProfile,
    profile_for_phase,
)
from app.services.game_phase import GamePhase
from app.services.sim_config import DRAMA_M3, SimConfig


class TestNormalProfile:
    def test_normal_is_identity(self):
        p = NORMAL_PROFILE
        assert p.foul_draw_mult == 1.0 and p.turnover_mult == 1.0
        assert p.pace_mult == 1.0 and p.transition_mult == 1.0
        assert p.offensive_rebound_mult == 1.0
        assert p.shot_profile.three_rate_mult == 1.0

    def test_shot_profile_not_shared_between_instances(self):
        a, b = BehaviorProfile(), BehaviorProfile()
        assert a.shot_profile is not b.shot_profile  # default_factory, not a shared default


class TestProfileForPhase:
    CFG = SimConfig()

    def test_normal_phase_returns_identity(self):
        assert profile_for_phase(GamePhase.NORMAL, self.CFG) is NORMAL_PROFILE

    def test_garbage_phase_returns_identity(self):
        assert profile_for_phase(GamePhase.GARBAGE, self.CFG) is NORMAL_PROFILE

    def test_competitive_late_uses_measured_shifts(self):
        p = profile_for_phase(GamePhase.COMPETITIVE_LATE, self.CFG)
        assert p.foul_draw_mult == self.CFG.comp_late_foul_mult
        assert p.turnover_mult == self.CFG.comp_late_tov_mult
        assert p.offensive_rebound_mult == self.CFG.comp_late_oreb_mult
        assert p.shot_profile.three_rate_mult == self.CFG.comp_late_three_mult

    def test_overtime_gets_competitive_profile(self):
        # OT is inherently competitive-late — same profile
        ot = profile_for_phase(GamePhase.OVERTIME, self.CFG)
        cl = profile_for_phase(GamePhase.COMPETITIVE_LATE, self.CFG)
        assert ot == cl

    def test_measured_directions(self):
        # sanity: measured clutch shifts point the documented way
        p = profile_for_phase(GamePhase.COMPETITIVE_LATE, self.CFG)
        assert p.foul_draw_mult > 1.0        # FTA up
        assert p.turnover_mult < 1.0         # TOV down
        assert p.offensive_rebound_mult > 1.0  # OREB up
        assert p.shot_profile.three_rate_mult == 1.0  # 3PA flat


class TestConfig:
    def test_drama_m3_enables_behavior_profile(self):
        assert DRAMA_M3.use_behavior_profile is True

    def test_default_off(self):
        assert SimConfig().use_behavior_profile is False
