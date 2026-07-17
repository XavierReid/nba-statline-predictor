"""PossessionContext — the canonical description of a possession's starting state.

The engine's first domain object (ARCHITECTURE_ROADMAP.md stage A). It holds only
STATE that already exists when a possession begins: the offense/defense units on
the floor, score margin, clock, quarter, the pre-combined modifier adjustments,
the config, and the RNG. It deliberately does NOT hold DECISIONS produced during
resolution — ball handler, primary defender, shot sub-type, contest level, shot
quality, outcome. Those belong to later pipeline stages and will become their own
domain objects (Action, Matchup, ShotQuality, Outcome) when possession resolution
is split. Keeping the state/decision boundary clean here makes those refactors easy.

`cfg` (SimConfig) stays the single source of truth for static configuration — the
context owns dynamic game state, not a copy of every config field. Every basketball
system should consume a PossessionContext instead of growing its parameter list.
Construct via make_context() — the one construction path for production and tests.
"""
import random
from dataclasses import dataclass
from typing import Optional

from app.services.modifiers.base import ModifierAdjustments
from app.services.possession import OREB_RATE


@dataclass(frozen=True)
class PossessionContext:
    offense: list
    defense: list
    rng: random.Random
    cfg: object                             # SimConfig — single source of static config
    adjustments: ModifierAdjustments
    home_bonus: float = 0.0
    team_defense_factor: float = 1.0
    is_fastbreak: bool = False
    form_factors: Optional[dict] = None
    offense_oreb_rate: float = OREB_RATE
    quarter: int = 1
    clock_seconds: float = 720.0
    score_margin: int = 0
    name_map: Optional[dict] = None
    behavior_profile: object = None   # BehaviorProfile for this possession's phase
    defense_in_bonus: bool = False    # defensive team over the team-foul limit this period
    foul_counts: Optional[dict] = None  # live per-player PF (id -> fouls) for the state-dependent foul hazard


def make_context(offense, defense, rng, cfg=None, adjustments=None, **overrides):
    """Canonical builder for PossessionContext — one construction path for
    production and tests.

    cfg defaults to a fresh SimConfig(); adjustments to an empty ModifierAdjustments().
    Extra keyword overrides are routed by name: SimConfig fields (e.g.
    use_shot_subtypes, foul_draw_scale) patch the config; PossessionContext fields
    (e.g. is_fastbreak, quarter, clock_seconds) set state. This lets callers override
    just the pieces they care about without hand-building a SimConfig, while keeping
    the state/config boundary intact. Unknown names raise TypeError.
    """
    from dataclasses import fields, replace
    from app.services.behavior_profile import NORMAL_PROFILE
    from app.services.sim_config import SimConfig

    cfg = cfg if cfg is not None else SimConfig()
    overrides.setdefault("behavior_profile", NORMAL_PROFILE)
    cfg_names = {f.name for f in fields(SimConfig)}
    ctx_names = {f.name for f in fields(PossessionContext)} - {"offense", "defense", "rng", "cfg", "adjustments"}

    cfg_overrides = {k: v for k, v in overrides.items() if k in cfg_names}
    ctx_overrides = {k: v for k, v in overrides.items() if k in ctx_names}
    unknown = set(overrides) - cfg_names - ctx_names
    if unknown:
        raise TypeError(f"make_context got unknown override(s): {sorted(unknown)}")
    if cfg_overrides:
        cfg = replace(cfg, **cfg_overrides)

    return PossessionContext(
        offense=offense,
        defense=defense,
        rng=rng,
        cfg=cfg,
        adjustments=adjustments if adjustments is not None else ModifierAdjustments(),
        **ctx_overrides,
    )
