"""ClutchModifier — adjusts shot efficiency and turnover rate in late close-game situations.

Philosophy: temporary game-state modifier. In clutch moments (≤2 min in Q4/OT, within 5 pts)
teams with higher average clutch ratings execute better. Effects are:
  - shot_prob_delta: clutch advantage over neutral 50 translates to small boost
  - tov_prob_delta: clutch advantage reduces turnover tendency

Clutch ratings come from LeagueDashPlayerClutch (FG%, FT%, TOV rate in last 5 min ±5 pts).
"""
from app.services.modifiers.base import GameSnapshot, GameStateModifier, ModifierAdjustments

# Seconds remaining in Q4/OT that activate clutch mode
CLUTCH_CLOCK_THRESHOLD = 120.0  # 2 minutes

# Point differential within which clutch activates
CLUTCH_POINT_DIFF = 5

# Maximum shot-probability boost for a lineup with avg clutch_rating = 99
MAX_SHOT_BOOST = 0.03

# Maximum TOV reduction for a lineup with avg clutch_rating = 99
MAX_TOV_REDUCTION = -0.015


class ClutchModifier(GameStateModifier):
    """Applies clutch rating advantage as shot/TOV adjustments in close late-game moments.

    The adjustment is the offensive team's average clutch rating delta vs the neutral
    midpoint (50), scaled to the max deltas above. A lineup with clutch_rating = 75
    gets a moderate boost; one with 50 (unknown/average) gets nothing.
    """

    def __init__(self, cfg: object) -> None:
        self._cfg = cfg

    def get_adjustments(self, is_home: bool, game_state: GameSnapshot) -> ModifierAdjustments:
        if not _is_clutch_situation(game_state):
            return ModifierAdjustments()

        players = game_state.home_players if is_home else game_state.away_players
        if not players:
            return ModifierAdjustments()

        avg_clutch = sum(ps.clutch_rating for ps in players.values()) / len(players)
        # Delta from neutral (50); positive = above average clutch
        delta = (avg_clutch - 50.0) / 50.0  # normalised to [-1, 1]

        return ModifierAdjustments(
            shot_prob_delta=MAX_SHOT_BOOST * delta,
            tov_prob_delta=MAX_TOV_REDUCTION * delta,
        )

    def update(self, event: dict, is_home: bool, game_state: GameSnapshot) -> None:
        # Clutch state is fully derived from GameSnapshot on each call; no accumulated state.
        pass


def _is_clutch_situation(gs: GameSnapshot) -> bool:
    """True when the game is in a late, close-game situation."""
    if gs.quarter < 4:
        return False
    if gs.clock_seconds > CLUTCH_CLOCK_THRESHOLD:
        return False
    score_diff = abs(gs.home_score - gs.away_score)
    return score_diff <= CLUTCH_POINT_DIFF
