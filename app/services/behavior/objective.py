"""ObjectiveModifier — the Q4 team-objective behavior as a pipeline member.

Wraps late_game.derive_objective / objective_adjustments (gap 3.1) behind the
standard GameStateModifier interface so it joins the BehaviorPipeline as a normal
source instead of an inline special case in the orchestrator. The concede gate
(a conceded team's offense is NEUTRAL — bench scrubs don't run a protect/chase
strategy) reads the concession flags now carried on GameSnapshot.
"""
from app.services.late_game import derive_objective, objective_adjustments
from app.services.modifiers.base import GameSnapshot, GameStateModifier, ModifierAdjustments


class ObjectiveModifier(GameStateModifier):
    def __init__(self, cfg: object) -> None:
        self._cfg = cfg

    def get_adjustments(self, is_home: bool, game_state: GameSnapshot) -> ModifierAdjustments:
        offense_conceded = game_state.home_conceded if is_home else game_state.away_conceded
        if offense_conceded:
            return ModifierAdjustments()
        margin = game_state.home_score - game_state.away_score
        off_margin = margin if is_home else -margin
        q_idx = game_state.quarter - 1
        objective, intensity = derive_objective(
            off_margin > 0, abs(off_margin), game_state.clock_seconds, q_idx, self._cfg
        )
        return objective_adjustments(objective, intensity, self._cfg)

    def update(self, event: dict, is_home: bool, game_state: GameSnapshot) -> None:
        pass
