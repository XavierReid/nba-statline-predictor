"""Late-game context + team objectives — the first real domain concept.

Every late-game mechanic (urgency pace, clock milking, strategic fouls, future
timeout/inbound logic) consumes this context instead of scattering
`clock <= X and margin <= Y` checks through the possession loop.

Philosophy (SIMULATION_GAPS.md gap 1.2 / 3.1): teams have OBJECTIVES that change
with game state, and basketball behavior is a consequence of the objective — not
a pile of ad-hoc late-game buffs/nerfs. A leading team shifts toward win
probability (values clock over points → conservative selection, milk tempo); a
trailing team shifts toward comeback probability (values possessions/variance →
more threes, faster tempo, efficiency-neutral). Margin compression emerges from
the objective-driven behavior, not from directly editing make probabilities.
This is the first Behavior-Engine citizen (ARCHITECTURE_ROADMAP.md stage C):
derive intention from state, then translate intention → adjustments.
"""
import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.services.modifiers.base import ModifierAdjustments


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


class TeamObjective(str, Enum):
    NEUTRAL = "neutral"   # maximize expected points — normal basketball
    PROTECT = "protect"   # leading: value clock/win-probability over points
    CHASE = "chase"       # trailing: value possessions/variance over efficiency


def derive_objective(is_leading, margin_abs, clock_seconds, q_idx, cfg):
    """Pure game-state → (TeamObjective, intensity in [0,1]). Q4/OT only.

    Intensity rises with margin (a bigger lead is more decided) and as the
    period elapses (coaches commit harder late). This is the INTENTION layer;
    translation to concrete adjustments is a separate step (objective_adjustments),
    so future systems (fatigue, coaching) can contribute to the intention before
    it becomes probabilities.
    """
    if q_idx < 3 or margin_abs < cfg.objective_min_margin:
        return TeamObjective.NEUTRAL, 0.0
    span = max(1, cfg.objective_full_margin - cfg.objective_min_margin)
    margin_factor = min(1.0, (margin_abs - cfg.objective_min_margin) / span)
    period_len = 300.0 if q_idx >= 4 else 720.0
    elapsed = max(0.0, min(1.0, (period_len - clock_seconds) / period_len))
    intensity = margin_factor * (0.5 + 0.5 * elapsed)  # 0.5..1.0 of margin_factor
    return (TeamObjective.PROTECT if is_leading else TeamObjective.CHASE), intensity


def objective_adjustments(objective, intensity, cfg, margin_abs: int = 0) -> "ModifierAdjustments":
    """Translate an objective+intensity into behavior.

    We first tried behavior-only (shot-selection + tempo, efficiency emerging). The
    Q4 diagnostic disproved it for this engine: cutting a protecting team's three
    rate pushes shots into the mid/close split, and close = layups/dunks = the most
    efficient shot — so "conservative" RAISED efficiency, the opposite of real
    basketball (conservative = worse late-clock shots). The compression mechanism is
    therefore modeled explicitly: a PROTECT team milking clock takes lower-quality
    shots (shot_prob_delta < 0). This is a consequence of the win-probability
    objective, not an arbitrary nerf. CHASE stays efficiency-neutral urgency (tempo
    only) — chasing teams offset rushed shots with transition and offensive glass.

    PROTECT has two regimes (gap 3.1 clutch vs gap 3.2 comfortable lead), chosen by
    `margin_abs`: a comfortable but not-decided lead (> competitive_late_margin, i.e.
    9-20) is managed HARDER than a one-possession clutch lead — real leading teams
    trade aggression for clock management across the whole comfortable band. Default
    margin_abs=0 keeps the clutch constants (backward-compatible).

    Tempo (pace_multiplier) is auto-suppressed for endgame-window possessions by
    the game loop (possession_time_override owns tempo there) — no gating needed here.
    """
    if objective == TeamObjective.NEUTRAL or intensity <= 0:
        return ModifierAdjustments()
    if objective == TeamObjective.PROTECT:
        comfortable = margin_abs > cfg.competitive_late_margin
        cost = cfg.comfortable_lead_efficiency_cost if comfortable else cfg.protect_efficiency_cost
        three = cfg.comfortable_lead_three_shift if comfortable else cfg.protect_three_shift
        pace = cfg.comfortable_lead_pace_bonus if comfortable else cfg.protect_pace_bonus
        return ModifierAdjustments(
            shot_prob_delta=-cost * intensity,          # clock-priority = worse shots
            three_rate_override=-three * intensity,     # fewer threes (variance ↓)
            pace_multiplier=1.0 + pace * intensity,     # milk clock
        )
    return ModifierAdjustments(  # CHASE — efficiency-neutral urgency, tempo only
        three_rate_override=cfg.chase_three_shift * intensity,         # variance ↑ (default 0)
        pace_multiplier=1.0 - cfg.chase_pace_bonus * intensity,        # faster
    )


def tie_seek_three_shift(off_margin: int, clock_seconds: float, q_idx: int, cfg) -> float:
    """Additive three-rate shift the trailing team applies on a late final-period
    possession — the decision the base engine lacks (gap 3.3): match shot VALUE to
    the deficit. Down 3 you need a three to TIE (shift up); down 1 a two takes the
    lead (shift down); down 2 mild. Sharpens toward the buzzer (urgency) — the
    measured deficit-sensitivity steepens as the clock shrinks. Zero outside the
    final period, when leading/tied, or before the window.

    Distinct from CHASE: the tie-seek deficits (1-3) sit below objective_min_margin,
    so derive_objective returns NEUTRAL here — this is added independently.
    """
    if q_idx < 3 or off_margin >= 0 or clock_seconds > cfg.tie_seek_clock_window:
        return 0.0
    base = {
        1: cfg.tie_seek_down1_shift,
        2: cfg.tie_seek_down2_shift,
        3: cfg.tie_seek_down3_shift,
    }.get(-off_margin, 0.0)
    if base == 0.0:
        return 0.0
    urgency = 1.0 - clock_seconds / cfg.tie_seek_clock_window  # 0 at window edge → 1 at buzzer
    return base * (0.5 + 0.5 * urgency)


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
