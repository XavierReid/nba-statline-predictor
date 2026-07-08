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
