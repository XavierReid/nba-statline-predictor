"""CatchUpModifier — trailing teams push pace and shoot more threes; leaders protect."""
from app.services.modifiers.base import GameSnapshot, GameStateModifier, ModifierAdjustments


class CatchUpModifier(GameStateModifier):
    """Activates in Q4/OT when a team trails by ≤ 15 with ≤ 150s on the clock.

    Trailing team:  shifts shot selection toward threes, hurries possessions, accepts
                    more turnovers from increased risk-taking.
    Leading team:   slows down, takes conservative shots to burn clock.
    """

    def __init__(self, cfg: object) -> None:
        self._cfg = cfg

    def get_adjustments(self, is_home: bool, game_state: GameSnapshot) -> ModifierAdjustments:
        cfg = self._cfg
        if game_state.quarter < 4:
            return ModifierAdjustments()

        clock = game_state.clock_seconds
        if clock > cfg.catch_up_clock_threshold:
            return ModifierAdjustments()

        lead = game_state.home_score - game_state.away_score
        # From the perspective of the team currently on offense (is_home)
        deficit = -lead if is_home else lead  # positive = trailing

        if deficit > 0 and deficit <= cfg.catch_up_max_deficit:
            # Trailing team — urgency scales with deficit and clock
            if deficit <= 5:
                three_shift = 0.08 if clock <= 60 else 0.04
            elif deficit <= 10:
                three_shift = 0.14 if clock <= 60 else 0.08
            else:
                three_shift = 0.20 if clock <= 60 else 0.12

            pace_mult = 0.75 if clock <= 60 else 0.85
            return ModifierAdjustments(
                three_rate_override=three_shift,
                pace_multiplier=pace_mult,
                tov_prob_delta=0.02,
            )

        elif deficit < 0 and abs(deficit) <= cfg.catch_up_max_deficit:
            # Leading team — conserve clock
            if clock <= 90:
                return ModifierAdjustments(
                    pace_multiplier=1.15,
                    shot_prob_delta=-0.015,
                )

        return ModifierAdjustments()

    def update(self, event: dict, is_home: bool, game_state: GameSnapshot) -> None:
        pass
