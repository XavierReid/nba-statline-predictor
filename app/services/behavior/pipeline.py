"""BehaviorPipeline — one owner for all per-possession behavior sources.

Builds the active source list from cfg (the registry logic previously inlined in
the orchestrator), combines their adjustments for a possession, and fans out the
post-possession update. Sources currently return ModifierAdjustments (deltas);
converting them to richer intentions combined into a BehaviorState is a later,
per-source, measured step (ARCHITECTURE_ROADMAP.md stage C notes) — this pass is
the structural extraction only.
"""
from typing import List

from app.services.modifiers.base import GameSnapshot, GameStateModifier, ModifierAdjustments


class BehaviorPipeline:
    def __init__(self, cfg: object, home_players: list, away_players: list) -> None:
        self._sources: List[GameStateModifier] = _build_sources(cfg, home_players, away_players)

    @property
    def is_empty(self) -> bool:
        return not self._sources

    def adjustments(self, is_home: bool, snapshot: GameSnapshot) -> ModifierAdjustments:
        """Combined adjustments for one possession. Additive fields sum; pace
        compounds by product (see ModifierAdjustments.__add__)."""
        combined = ModifierAdjustments()
        for src in self._sources:
            combined = combined + src.get_adjustments(is_home, snapshot)
        return combined

    def update(self, event: dict, is_home: bool, snapshot: GameSnapshot) -> None:
        for src in self._sources:
            src.update(event, is_home, snapshot)


def _build_sources(cfg, home_players, away_players) -> List[GameStateModifier]:
    """Assemble the active behavior sources from config toggles. Clock-based
    sources require use_clock; the Q4 objective is one source among the rest."""
    sources: List[GameStateModifier] = []
    if not cfg.use_clock:
        return sources

    if cfg.use_momentum:
        from app.services.modifiers.momentum import MomentumModifier
        home_composure = (
            sum(p["overall"] for p in home_players) / len(home_players) / 100.0
            if home_players else 0.75
        )
        away_composure = (
            sum(p["overall"] for p in away_players) / len(away_players) / 100.0
            if away_players else 0.75
        )
        sources.append(MomentumModifier(cfg, home_composure, away_composure))

    if cfg.use_fatigue:
        from app.services.modifiers.fatigue import FatigueModifier
        sources.append(FatigueModifier(cfg))

    if cfg.use_foul_trouble:
        from app.services.modifiers.foul_trouble import FoulTroubleModifier
        sources.append(FoulTroubleModifier(cfg))

    if cfg.use_clutch:
        from app.services.modifiers.clutch import ClutchModifier
        sources.append(ClutchModifier(cfg))

    # use_catch_up kept only for isolation replays; use_team_objectives supersedes it.
    if cfg.use_catch_up:
        from app.services.modifiers.catch_up import CatchUpModifier
        sources.append(CatchUpModifier(cfg))

    if cfg.use_garbage_time:
        from app.services.modifiers.garbage_time import GarbageTimeModifier
        sources.append(GarbageTimeModifier(cfg))

    if cfg.use_team_objectives:
        from app.services.behavior.objective import ObjectiveModifier
        sources.append(ObjectiveModifier(cfg))

    return sources
