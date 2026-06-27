from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ModifierAdjustments:
    shot_prob_delta: float = 0.0
    tov_prob_delta: float = 0.0
    defense_penalty_delta: float = 0.0  # added by defensive team's modifiers

    def __add__(self, other: "ModifierAdjustments") -> "ModifierAdjustments":
        return ModifierAdjustments(
            shot_prob_delta=self.shot_prob_delta + other.shot_prob_delta,
            tov_prob_delta=self.tov_prob_delta + other.tov_prob_delta,
            defense_penalty_delta=self.defense_penalty_delta + other.defense_penalty_delta,
        )


@dataclass
class PlayerGameState:
    """Per-player tracking needed by fatigue and foul-trouble modifiers."""
    player_id: int
    minutes_played: float = 0.0
    fouls: int = 0
    clutch_rating: int = 50


@dataclass
class GameState:
    home_score: int
    away_score: int
    quarter: int
    clock_seconds: float
    possession_number: int
    # Per-player state — keyed by player_id; populated when M2c modifiers are active
    home_players: Dict[int, PlayerGameState] = field(default_factory=dict)
    away_players: Dict[int, PlayerGameState] = field(default_factory=dict)


class GameStateModifier(ABC):
    @abstractmethod
    def get_adjustments(self, is_home: bool, game_state: GameState) -> ModifierAdjustments: ...

    @abstractmethod
    def update(self, event: dict, is_home: bool, game_state: GameState) -> None: ...
