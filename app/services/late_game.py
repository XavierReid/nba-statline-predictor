"""Late-game context — one place that answers "is this an endgame possession?"

Every late-game mechanic (urgency pace, clock milking, strategic fouls, future
timeout/inbound logic) consumes this context instead of scattering
`clock <= X and margin <= Y` checks through the possession loop.

Philosophy (SIMULATION_GAPS.md gap 1.2): model incentives, not outcomes. The
trailing team values possessions over efficiency; the leading team values time
over expected points. Compression emerges from both teams optimizing.
"""
import random
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LateGameContext:
    """Situation snapshot for one possession, from the offense's perspective."""
    is_final_period: bool     # Q4 or any OT
    clock_seconds: float      # remaining in period
    offense_margin: int       # positive = offense leading
    in_window: bool           # final period + inside clock/margin window

    @property
    def offense_trailing(self) -> bool:
        return self.in_window and self.offense_margin < 0

    @property
    def offense_leading(self) -> bool:
        return self.in_window and self.offense_margin > 0


def build_context(
    q_idx: int,
    clock_seconds: float,
    home_total: int,
    away_total: int,
    offense_is_home: bool,
    cfg,
) -> LateGameContext:
    is_final = q_idx >= 3
    margin = (home_total - away_total) if offense_is_home else (away_total - home_total)
    in_window = (
        is_final
        and clock_seconds <= cfg.endgame_clock_window
        and abs(margin) <= cfg.endgame_margin_max
    )
    return LateGameContext(
        is_final_period=is_final,
        clock_seconds=clock_seconds,
        offense_margin=margin,
        in_window=in_window,
    )


def should_concede(
    is_leading: bool,
    margin_abs: int,
    clock_seconds: float,
    q_idx: int,
    cfg,
    currently_conceded: bool,
) -> bool:
    """Per-team rotation concession decision — incentives are asymmetric.

    The leading team's game is decided; resting stars is pure upside, so it
    concedes at garbage_time_margin. The trailing team still values win
    probability — it holds its starters until the deficit is hopeless (bigger
    margin, or big margin with little clock). The window where the leader's
    bench faces the trailer's starters is where margins compress organically.

    Threshold-based today; the (team, game-state) signature is the decision
    layer — win probability, playoff context, back-to-backs, coaching
    tendencies can move in here without touching the rotation engine.
    """
    if q_idx < 2:
        return False
    if currently_conceded:
        return margin_abs >= cfg.garbage_exit_margin  # hysteresis, both sides

    if q_idx == 2:
        # Q3: only truly decided games — thresholds scaled up, no clock gate
        # (a 25-pt Q3 lead is when real coaches start emptying the bench)
        lead_margin = cfg.garbage_time_margin + cfg.q3_concede_margin_bonus
        trail_margin = cfg.concede_trailing_margin + cfg.q3_concede_margin_bonus
        return margin_abs >= (lead_margin if is_leading else trail_margin)

    # Q4/OT
    if clock_seconds > cfg.garbage_time_clock_threshold:
        return False
    if is_leading:
        return margin_abs >= cfg.garbage_time_margin
    return margin_abs >= cfg.concede_trailing_margin or (
        margin_abs >= cfg.garbage_time_margin
        and clock_seconds <= cfg.concede_trailing_clock
    )


def garbage_time_state(
    q_idx: int,
    clock_seconds: float,
    home_total: int,
    away_total: int,
    cfg,
    currently_active: bool,
) -> bool:
    """Is the game in a garbage-time state? Single definition shared by the
    GarbageTimeModifier (behavior) and the rotation resolver (personnel).

    Hysteresis: enter at margin >= garbage_time_margin (a 20-pt Q4 lead is
    decided), exit only if the margin collapses below garbage_exit_margin
    (coaches don't panic when 21 becomes 18; they do at 11 with time left).
    Threshold-based today; the signature takes full game state so this can
    become a win-probability evaluation without changing callers.
    """
    if q_idx < 3:
        return False
    margin = abs(home_total - away_total)
    if currently_active:
        return margin >= cfg.garbage_exit_margin
    return clock_seconds <= cfg.garbage_time_clock_threshold and margin >= cfg.garbage_time_margin


def possession_time_override(
    ctx: LateGameContext, cfg, rng: random.Random
) -> Optional[float]:
    """Incentive-driven possession time inside the endgame window, else None.

    Trailing offense: possessions are worth more than efficiency — play fast.
    Leading offense: time is worth more than expected points — milk the shot
    clock. (Leading-team possessions are usually intercepted by the strategic
    foul mechanic first; this covers the ones that survive un-fouled.)
    Tied games get no override — normal basketball.
    """
    if ctx.offense_trailing:
        return max(4.0, min(14.0, rng.gauss(cfg.endgame_urgency_time_mean, cfg.endgame_urgency_time_std)))
    if ctx.offense_leading:
        return max(14.0, min(24.0, rng.gauss(cfg.endgame_milk_time_mean, cfg.endgame_milk_time_std)))
    return None
