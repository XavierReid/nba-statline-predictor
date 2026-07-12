"""BehaviorProfile — how basketball is *normally* played during a game phase.

Separate from Objectives (what each team is trying to accomplish): a BehaviorProfile
is the baseline style of play for a GamePhase, independent of either team's intent.
Behavior sources compose — baseline + phase profile + objectives + (future) coaching,
identity, fatigue — none overwrites another.

A phase resolves to a profile through profile_for_phase() (a small lookup layer). Today
it returns NORMAL / COMPETITIVE_LATE profiles; later, without touching the possession
engine, it could return playoff, team-identity, or coach-specific profiles. The engine
stays data-driven and unaware of where a profile came from.

All multipliers are relative to normal play (1.0 = unchanged). Shot selection is its own
object so future systems (identity, coaching, fatigue, hot hand) share one abstraction
instead of each inventing another "three-point multiplier".
"""
from dataclasses import dataclass, field

from app.services.game_phase import GamePhase


@dataclass(frozen=True)
class ShotProfile:
    three_rate_mult: float = 1.0     # future: rim_rate_mult, mid_rate_mult, ...


@dataclass(frozen=True)
class BehaviorProfile:
    foul_draw_mult: float = 1.0
    turnover_mult: float = 1.0
    pace_mult: float = 1.0
    transition_mult: float = 1.0
    offensive_rebound_mult: float = 1.0
    shot_profile: ShotProfile = field(default_factory=ShotProfile)


NORMAL_PROFILE = BehaviorProfile()   # identity — the possession engine sees no change


def profile_for_phase(phase, cfg) -> BehaviorProfile:
    """Resolve a GamePhase to its BehaviorProfile. Measured competitive-late values
    (clutch splits) live on cfg; NORMAL/GARBAGE keep normal play (garbage behavior is
    already handled by rotations and objectives)."""
    if phase in (GamePhase.COMPETITIVE_LATE, GamePhase.OVERTIME):
        return BehaviorProfile(
            foul_draw_mult=cfg.comp_late_foul_mult,
            turnover_mult=cfg.comp_late_tov_mult,
            offensive_rebound_mult=cfg.comp_late_oreb_mult,
            shot_profile=ShotProfile(three_rate_mult=cfg.comp_late_three_mult),
        )
    return NORMAL_PROFILE
