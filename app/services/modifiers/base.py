from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ModifierAdjustments:
    shot_prob_delta: float = 0.0
    tov_prob_delta: float = 0.0

    def __add__(self, other: "ModifierAdjustments") -> "ModifierAdjustments":
        return ModifierAdjustments(
            shot_prob_delta=self.shot_prob_delta + other.shot_prob_delta,
            tov_prob_delta=self.tov_prob_delta + other.tov_prob_delta,
        )


@dataclass
class GameState:
    home_score: int
    away_score: int
    quarter: int
    clock_seconds: float
    possession_number: int


class GameStateModifier(ABC):
    @abstractmethod
    def get_adjustments(self, is_home: bool, game_state: GameState) -> ModifierAdjustments: ...

    @abstractmethod
    def update(self, event: dict, is_home: bool, game_state: GameState) -> None: ...
