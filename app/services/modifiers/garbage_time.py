"""GarbageTimeModifier — large leads in late Q3/Q4 produce the garbage-time effect."""
from app.services.modifiers.base import GameState, GameStateModifier, ModifierAdjustments


class GarbageTimeModifier(GameStateModifier):
    """Activates when a team leads by ≥ 20 in the final ~10 min of Q3 or Q4.

    Models the effect of reduced intensity without simulating actual substitutions:
      - Leading team:  lower shot efficiency, softer defense (lets trailing team score).
      - Trailing team: bombs threes, plays faster, turns it over more.

    The asymmetric leading-team defense softening produces the "closer than the score
    suggests" effect common in real NBA garbage time.
    """

    def __init__(self, cfg: object) -> None:
        self._cfg = cfg

    def get_adjustments(self, is_home: bool, game_state: GameState) -> ModifierAdjustments:
        cfg = self._cfg
        if game_state.quarter < 3:
            return ModifierAdjustments()
        if game_state.clock_seconds > cfg.garbage_time_clock_threshold:
            return ModifierAdjustments()

        lead = game_state.home_score - game_state.away_score
        home_leads = lead >= cfg.garbage_time_margin
        away_leads = lead <= -cfg.garbage_time_margin

        if not home_leads and not away_leads:
            return ModifierAdjustments()

        offense_is_trailing = (is_home and away_leads) or (not is_home and home_leads)

        if offense_is_trailing:
            # Trailing team bombs threes; leading team's defense is soft (benefits offense here)
            return ModifierAdjustments(
                three_rate_override=0.08,
                defense_penalty_delta=0.02,  # leading team's defense is soft → easier shots
                tov_prob_delta=0.01,          # some risk-taking, but small to avoid self-defeating TOs
            )
        else:
            # Leading team coasts — reduced offensive intensity only
            return ModifierAdjustments(
                shot_prob_delta=-0.02,
            )

    def update(self, event: dict, is_home: bool, game_state: GameState) -> None:
        pass
