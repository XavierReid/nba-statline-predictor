"""ObjectiveModifier — the Q4 team-objective behavior as a pipeline member.

Wraps late_game.derive_objective / objective_adjustments (gap 3.1) behind the
standard GameStateModifier interface so it joins the BehaviorPipeline as a normal
source instead of an inline special case in the orchestrator. The concede gate
(a conceded team's offense is NEUTRAL — bench scrubs don't run a protect/chase
strategy) reads the concession flags now carried on GameSnapshot.
"""
from app.services.late_game import derive_objective, objective_adjustments, tie_seek_three_shift
from app.services.modifiers.base import GameSnapshot, GameStateModifier, ModifierAdjustments


class ObjectiveModifier(GameStateModifier):
    def __init__(self, cfg: object) -> None:
        self._cfg = cfg

    def get_adjustments(self, is_home: bool, game_state: GameSnapshot) -> ModifierAdjustments:
        # A conceded OFFENSE runs no protect/chase strategy — its bench scrubs just play.
        # (Per-team, not game-level GARBAGE: the other team may still be competing.)
        offense_conceded = game_state.home_conceded if is_home else game_state.away_conceded
        if offense_conceded:
            return ModifierAdjustments()
        margin = game_state.home_score - game_state.away_score
        off_margin = margin if is_home else -margin
        q_idx = game_state.quarter - 1
        objective, intensity = derive_objective(
            off_margin > 0, abs(off_margin), game_state.clock_seconds, q_idx, self._cfg
        )
        adj = objective_adjustments(objective, intensity, self._cfg, abs(off_margin))
        # Tie-seeking shot value (gap 3.3) — a distinct late-game decision from CHASE:
        # the trailing team's one-possession deficit (1-3) sits below objective_min_margin,
        # so it is added here independently of the (NEUTRAL) objective result.
        if getattr(self._cfg, "use_tie_seek", False):
            shift = tie_seek_three_shift(off_margin, game_state.clock_seconds, q_idx, self._cfg)
            if shift:
                adj = adj + ModifierAdjustments(three_rate_override=shift)
        return adj

    def update(self, event: dict, is_home: bool, game_state: GameSnapshot) -> None:
        pass
