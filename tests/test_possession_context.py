"""Tests for PossessionContext + make_context (ARCHITECTURE_ROADMAP.md stage A)."""
import random

import pytest

from app.services.possession_context import PossessionContext, make_context
from app.services.modifiers.base import ModifierAdjustments
from app.services.sim_config import SimConfig


def _units():
    return [{"id": 1}], [{"id": 2}]


class TestMakeContext:
    def test_defaults(self):
        off, dfn = _units()
        ctx = make_context(off, dfn, random.Random(0))
        assert isinstance(ctx, PossessionContext)
        assert isinstance(ctx.cfg, SimConfig)
        assert isinstance(ctx.adjustments, ModifierAdjustments)
        assert ctx.quarter == 1 and ctx.clock_seconds == 720.0

    def test_state_override_routed_to_context(self):
        off, dfn = _units()
        ctx = make_context(off, dfn, random.Random(0), quarter=4, clock_seconds=45.0, is_fastbreak=True)
        assert ctx.quarter == 4 and ctx.clock_seconds == 45.0 and ctx.is_fastbreak is True

    def test_config_override_routed_to_cfg(self):
        off, dfn = _units()
        ctx = make_context(off, dfn, random.Random(0), use_shot_subtypes=True, foul_draw_scale=0.5)
        assert ctx.cfg.use_shot_subtypes is True
        assert ctx.cfg.foul_draw_scale == 0.5
        # base SimConfig untouched (replace produced a new instance)
        assert SimConfig().use_shot_subtypes is False

    def test_explicit_cfg_is_patched_not_replaced(self):
        off, dfn = _units()
        base = SimConfig(use_contest_model=True)
        ctx = make_context(off, dfn, random.Random(0), cfg=base, use_shot_subtypes=True)
        assert ctx.cfg.use_contest_model is True   # preserved
        assert ctx.cfg.use_shot_subtypes is True    # patched

    def test_mixed_overrides_split_correctly(self):
        off, dfn = _units()
        ctx = make_context(off, dfn, random.Random(0),
                           use_foul_drawing=True, score_margin=8)
        assert ctx.cfg.use_foul_drawing is True     # config
        assert ctx.score_margin == 8                # state

    def test_unknown_override_raises(self):
        off, dfn = _units()
        with pytest.raises(TypeError):
            make_context(off, dfn, random.Random(0), not_a_real_field=1)

    def test_context_is_frozen(self):
        off, dfn = _units()
        ctx = make_context(off, dfn, random.Random(0))
        with pytest.raises(Exception):
            ctx.quarter = 4
