"""FoulTroubleModifier — reduces defensive aggressiveness when key defenders are in foul trouble.

Philosophy: temporary game-state modifier, adjusts probabilities, does NOT change player
ratings. A player with 4 fouls is coached to play softer; this shows up as a reduced
ability to deter interior attempts, not a direct steal/block rating change.

Effect: defense_penalty_delta > 0 (positive = worsened defense) on the defensive team's
adjustment when their lineup has players sitting in foul trouble.

Foul-trouble management (sitting a player, changing rotations) is tracked in the Parking Lot
as a future enhancement; for now we model the degraded defense in the aggregate.
"""
from typing import Dict

from app.services.modifiers.base import GameSnapshot, GameStateModifier, ModifierAdjustments

# Foul count at which trouble begins (Q1-Q3) or escalates (Q4)
FOUL_TROUBLE_THRESHOLD = 4

# Max penalty when 2+ starters are in foul trouble deep in Q4
MAX_DEFENSE_PENALTY = 0.04


class FoulTroubleModifier(GameStateModifier):
    """Applies a defense penalty to the team whose players have accumulated fouls.

    This modifier is applied to the DEFENSIVE team. When the offensive team calls
    get_adjustments, is_home refers to the offensive team. We use this to look up
    the *defensive* team's foul state (opposite side).

    The penalty is returned as defense_penalty_delta and applied to the shot probability
    of the offense (i.e., a positive value benefits the offense).
    """

    def __init__(self, cfg: object) -> None:
        self._cfg = cfg

    def get_adjustments(self, is_home: bool, game_state: GameSnapshot) -> ModifierAdjustments:
        # is_home = True means the home team is on offense → look at away team's defense
        defensive_players = game_state.away_players if is_home else game_state.home_players
        if not defensive_players:
            return ModifierAdjustments()

        penalty = _compute_defense_penalty(defensive_players, game_state.quarter)
        return ModifierAdjustments(defense_penalty_delta=penalty)

    def update(self, event: dict, is_home: bool, game_state: GameSnapshot) -> None:
        # Foul accumulation is handled in the game loop via event["foul_on"] tracking;
        # the modifier reads pre-computed values.
        pass


def _compute_defense_penalty(players: Dict, quarter: int) -> float:
    """Return a positive defense penalty based on how many players are in foul trouble."""
    trouble_count = sum(
        1 for ps in players.values()
        if ps.fouls >= FOUL_TROUBLE_THRESHOLD
    )
    if trouble_count == 0:
        return 0.0

    # Scale: 1 player in trouble = 50% of max; 2+ = 100%
    base_penalty = MAX_DEFENSE_PENALTY * min(trouble_count / 2.0, 1.0)

    # Q4 escalation: defenders play even softer with foul trouble late
    if quarter >= 4:
        base_penalty *= 1.25

    return min(base_penalty, MAX_DEFENSE_PENALTY * 1.25)
