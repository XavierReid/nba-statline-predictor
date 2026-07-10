"""Momentum modifier.

Tracks a per-team confidence float in [-momentum_max, +momentum_max].
Positive momentum boosts shot efficiency and reduces unforced turnovers;
negative momentum has the opposite effect. The decay rate (20%/possession
by default) means a momentum spike fades within ~10 possessions.

Steal probability is intentionally NOT modified here — steals are a defender
skill outcome. Momentum affects offensive decision-making (raw turnovers,
shot quality) not defensive ability.
"""
from typing import TYPE_CHECKING

from app.services.modifiers.base import GameSnapshot, GameStateModifier, ModifierAdjustments

if TYPE_CHECKING:
    from app.services.sim_config import SimConfig


class MomentumModifier(GameStateModifier):
    def __init__(self, cfg: "SimConfig", home_composure: float, away_composure: float):
        """
        Args:
            home_composure: avg overall rating / 100 for home team, used to
                dampen negative momentum from opponent runs.
            away_composure: same for away team.
        """
        self._cfg = cfg
        self._home_composure = home_composure
        self._away_composure = away_composure
        self._home: float = 0.0
        self._away: float = 0.0
        self._home_unanswered: int = 0
        self._away_unanswered: int = 0

    def get_adjustments(self, is_home: bool, game_state: GameSnapshot) -> ModifierAdjustments:
        m = self._home if is_home else self._away
        # ±2.5% shot prob at max momentum, ±1.5% TOV at max momentum
        return ModifierAdjustments(shot_prob_delta=m * 0.5, tov_prob_delta=-m * 0.3)

    def update(self, event: dict, is_home: bool, game_state: GameSnapshot) -> None:
        cfg = self._cfg
        pts = event.get("pts", 0)

        # Unanswered run tracking — opponent's run resets on any scoring
        if pts > 0:
            if is_home:
                self._home_unanswered += pts
                self._away_unanswered = 0
            else:
                self._away_unanswered += pts
                self._home_unanswered = 0

        # Decay first so boosts this possession land on top of decayed value
        self._home *= (1.0 - cfg.momentum_decay_rate)
        self._away *= (1.0 - cfg.momentum_decay_rate)

        h_delta, a_delta = 0.0, 0.0

        # Scoring runs — only when this possession scores (not on misses/turnovers)
        if pts > 0:
            if is_home:
                if self._home_unanswered >= 12:
                    h_delta += 0.020
                    a_delta -= 0.010
                elif self._home_unanswered >= 8:
                    h_delta += 0.010
            else:
                if self._away_unanswered >= 12:
                    a_delta += 0.020
                    h_delta -= 0.010
                elif self._away_unanswered >= 8:
                    a_delta += 0.010

        # Made three — energizes offense, rattles opponent
        if event.get("shot_type") == "three" and event.get("made"):
            if is_home:
                h_delta += 0.005
                a_delta -= 0.002
            else:
                a_delta += 0.005
                h_delta -= 0.002

        # Steal — ball security failure demoralizes offense; defense gets a lift
        # Note: is_home here refers to the team that had possession (offense).
        # The defense that forced the steal is the opponent.
        if event.get("steal_by") is not None:
            if is_home:
                a_delta += 0.005
                h_delta -= 0.002
            else:
                h_delta += 0.005
                a_delta -= 0.002

        # Defensive stop — dreb after missed shot (no FTs) with no oreb
        if (
            pts == 0
            and event.get("shot_type") is not None
            and event.get("rebounded_by") is not None
            and not event.get("is_oreb")
            and event.get("fta", 0) == 0
        ):
            if is_home:  # home missed, away got dreb
                a_delta += 0.003
                h_delta -= 0.001
            else:
                h_delta += 0.003
                a_delta -= 0.001

        # Composure resistance: high-rated teams absorb negative momentum better.
        # Only dampens the negative component — does not reduce positive boosts.
        if h_delta < 0:
            h_delta *= (1.0 - self._home_composure * 0.4)
        if a_delta < 0:
            a_delta *= (1.0 - self._away_composure * 0.4)

        self._home = max(-cfg.momentum_max, min(cfg.momentum_max, self._home + h_delta))
        self._away = max(-cfg.momentum_max, min(cfg.momentum_max, self._away + a_delta))
