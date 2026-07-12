"""GamePhase — "what kind of basketball is being played right now?"

The engine has many scattered checks on quarter/clock/margin/concession that are
all really asking this one question. GamePhase names it as its own layer, sitting
between GameState (what is objectively true) and Objectives (what each team is
optimizing for): GameState → GamePhase → Objectives → Behavior Sources.

It is orthogonal to period: a team can concede in Q3, so GARBAGE is NOT a
sub-case of "final period" — period stays on GameState.is_final_period; GamePhase
owns the "kind of basketball" axis.

Introduced behavior-neutral. COMPETITIVE_LATE is defined as the seam for later
late-game behavior work (gap 3.2 — competitive Q4 variance), but no behavior keys
off its margin boundary yet, so it changes nothing today.
"""
from enum import Enum


class GamePhase(str, Enum):
    NORMAL = "normal"                    # default — no special phase
    COMPETITIVE_LATE = "competitive_late"  # final period, close, no concession
    GARBAGE = "garbage"                  # a team has conceded (decided game)
    OVERTIME = "overtime"                # overtime, not garbage


def derive_phase(period_index, abs_margin, home_conceded, away_conceded, cfg) -> GamePhase:
    """Classify the current possession's phase from primitives (works from either
    GameState or GameSnapshot). Priority: GARBAGE > OVERTIME > COMPETITIVE_LATE > NORMAL."""
    if home_conceded or away_conceded:
        return GamePhase.GARBAGE
    if period_index >= 4:
        return GamePhase.OVERTIME
    if period_index == 3 and abs_margin <= cfg.competitive_late_margin:
        return GamePhase.COMPETITIVE_LATE
    return GamePhase.NORMAL
