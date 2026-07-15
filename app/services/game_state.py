"""GameState — the persistent, authoritative simulation state (roadmap stage B).

Owns everything that SURVIVES ACROSS POSSESSIONS: score, per-quarter scores,
elapsed clock, possession count, current period, and rotation concession flags
(which have hysteresis and therefore genuinely can't be derived per-possession).
This replaces the loose `nonlocal` scalars previously juggled across the two
nested closures in game_simulator.

Boundary (deliberate): anything that survives across possessions lives here;
anything that exists only within a single possession lives on PossessionContext.

Scope for stage B is ownership only — fields plus read-only computed properties.
Mutation is still performed inline by the game loop (it mutates gs.field directly,
so no `nonlocal` is needed). State-transition METHODS (advance_clock, apply_score,
update_concessions, next_period, ...) are a stage C+ follow-up once the boundary
is stable.
"""
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class GameState:
    home_score: int = 0
    away_score: int = 0
    possession_number: int = 0
    period_index: int = 0                       # 0-3 regulation, 4+ OT
    game_clock: float = 0.0                      # elapsed seconds
    home_conceded: bool = False                  # garbage-rotation state (hysteretic)
    away_conceded: bool = False
    home_quarter_fouls: int = 0                  # team fouls this period (bonus at >= threshold; reset each period)
    away_quarter_fouls: int = 0
    quarter_scores: Dict[str, List[int]] = field(
        default_factory=lambda: {"home": [0, 0, 0, 0], "away": [0, 0, 0, 0]}
    )

    # --- read-only computed basketball state (one authoritative source) ---
    @property
    def margin(self) -> int:
        """Home perspective: positive = home leads."""
        return self.home_score - self.away_score

    @property
    def abs_margin(self) -> int:
        return abs(self.margin)

    @property
    def leading_is_home(self) -> bool:
        return self.home_score > self.away_score

    @property
    def is_tied(self) -> bool:
        return self.home_score == self.away_score

    @property
    def is_final_period(self) -> bool:
        """Q4 or any overtime."""
        return self.period_index >= 3

    def offense_margin(self, is_home: bool) -> int:
        """Margin from the perspective of the team currently on offense."""
        return self.margin if is_home else -self.margin
