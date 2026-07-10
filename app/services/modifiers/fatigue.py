"""FatigueModifier — reduces offensive efficiency for players who have logged heavy minutes.

Philosophy: temporary game-state modifier, adjusts probabilities, does NOT change player
ratings. A player who has played 38 min by halftime should already be affected — Q4 is
just when it becomes most visible.

Effect: shot_prob_delta < 0 when the active lineup's average minutes exceed the threshold.
"""
from typing import Dict

from app.services.modifiers.base import GameSnapshot, GameStateModifier, ModifierAdjustments


# Minutes threshold at which fatigue starts accumulating.
# Set below typical star usage (36 min = full game minus a short bench stint).
FATIGUE_THRESHOLD_MINUTES = 28.0

# Maximum shot-probability penalty at full fatigue (~40+ min on a 48-min clock).
# Keeps individual-game effect subtle; cumulative across many possessions is material.
MAX_SHOT_PENALTY = -0.04

# Minutes that would produce the maximum penalty (calibration lever).
FULL_FATIGUE_MINUTES = 40.0


class FatigueModifier(GameStateModifier):
    """Penalises shot probability when the offensive lineup has logged heavy minutes.

    The penalty is proportional to how far each active player exceeds the fatigue
    threshold, averaged across the lineup and scaled to MAX_SHOT_PENALTY.

    Only applies on offense — defender fatigue shows up indirectly through the
    defensive team's own offensive efficiency drop, not a separate pathway.
    """

    def __init__(self, cfg: object) -> None:
        self._cfg = cfg

    def get_adjustments(self, is_home: bool, game_state: GameSnapshot) -> ModifierAdjustments:
        players = game_state.home_players if is_home else game_state.away_players
        if not players:
            return ModifierAdjustments()

        avg_excess = _avg_excess_minutes(players)
        if avg_excess <= 0:
            return ModifierAdjustments()

        # Linear scale from threshold to full-fatigue minutes → 0 to MAX_SHOT_PENALTY
        fatigue_range = max(FULL_FATIGUE_MINUTES - FATIGUE_THRESHOLD_MINUTES, 1.0)
        penalty = MAX_SHOT_PENALTY * min(avg_excess / fatigue_range, 1.0)
        return ModifierAdjustments(shot_prob_delta=penalty)

    def update(self, event: dict, is_home: bool, game_state: GameSnapshot) -> None:
        # Minute accumulation happens in the game loop via clock tracking;
        # the modifier reads the pre-computed values, so no update logic needed here.
        pass


def _avg_excess_minutes(players: Dict) -> float:
    total_excess = sum(
        max(ps.minutes_played - FATIGUE_THRESHOLD_MINUTES, 0.0)
        for ps in players.values()
    )
    return total_excess / len(players) if players else 0.0
