"""Tests for M3e — foul drawing tendency."""
import random

from app.services.possession import (
    _FOUL_DRAW_MULT,
    _FOUL_DRAW_RATE_CAP,
    _LEAGUE_AVG_FOUL_DRAW_RATE,
    resolve_possession,
)
from app.services.possession_context import make_context
from app.services.rating_engine import compute_tendencies
from app.services.sim_config import DRAMA_M3, DRAMA_M3_NO_SUBTYPES, SimConfig


def _player(pid: int = 1, pos: str = "G", **kwargs) -> dict:
    p = dict(
        id=pid, name=f"Player{pid}", position=pos,
        overall=75, three_point=70, mid_range=70, close_shot=70,
        layup=75, dunk=60, free_throw=75,
        perimeter_defense=70, interior_defense=70, block=50, steal=50,
        passing=70, offensive_rebound=30, defensive_rebound=50,
        usage_rate=0.20, three_point_rate=0.35, assist_rate=2.0,
        oreb_rate=0.05, dreb_rate=0.10, turnover_rate=2.0,
    )
    p.update(kwargs)
    return p


def _bonus_foul_rate(offense, n=8000, seed=42, **kwargs):
    """Fraction of possessions ending in a bonus foul (fta > 0 with no shot)."""
    rng = random.Random(seed)
    defense = [_player(pid=99)]
    count = 0
    for _ in range(n):
        e = resolve_possession(make_context(offense, defense, rng, **kwargs))
        if e["fta"] > 0 and e["shot_type"] is None and e["turnover_by"] is None:
            count += 1
    return count / n


def _shooting_foul_rate(offense, n=8000, seed=42, **kwargs):
    """Shooting fouls per shot attempt."""
    rng = random.Random(seed)
    defense = [_player(pid=99)]
    shots, fouls = 0, 0
    for _ in range(n):
        e = resolve_possession(make_context(offense, defense, rng, **kwargs))
        if e["shot_type"] is not None:
            shots += 1
            if e["fta"] > 0:
                fouls += 1
    return fouls / max(shots, 1)


class _Stats:
    def __init__(self, fga=15.0, fg3a=5.0, fta=6.0, tov=2.0, mins=32.0):
        self.fga = fga
        self.fg3a = fg3a
        self.fta = fta
        self.turnovers = tov
        self.minutes_per_game = mins
        self.assists = 4.0
        self.rebounds = 5.0
        self.team_id = None
        self.games_played = 70
        self.usg_pct = None
        self.oreb_pct = None
        self.dreb_pct = None


# ---------------------------------------------------------------------------
# compute_tendencies
# ---------------------------------------------------------------------------

class TestComputeTendencies:
    def test_foul_drawing_rate_is_fta_over_fga(self):
        t = compute_tendencies(_Stats(fga=20.0, fta=8.0))
        assert t["foul_drawing_rate"] == 0.4

    def test_foul_drawing_rate_none_when_no_fga(self):
        t = compute_tendencies(_Stats(fga=0.0, fta=0.0))
        assert t["foul_drawing_rate"] is None


# ---------------------------------------------------------------------------
# Bonus foul — player-specific rate
# ---------------------------------------------------------------------------

class TestBonusFoulRate:
    def test_disabled_matches_flat_rate(self):
        rate = _bonus_foul_rate([_player(foul_drawing_rate=0.50)], use_foul_drawing=False)
        assert 0.04 < rate < 0.07  # flat 5.5% ± sampling noise

    def test_high_drawer_exceeds_low_drawer(self):
        high = _bonus_foul_rate([_player(foul_drawing_rate=0.50)], use_foul_drawing=True)
        low = _bonus_foul_rate([_player(foul_drawing_rate=0.10)], use_foul_drawing=True)
        assert high > low

    def test_league_avg_player_near_pre_m3e_rate(self):
        # Slightly below the old 5.5% by design: foul_draw_scale compensates for
        # usage-weighting (stars draw more), so an average player lands under the flat rate.
        cfg = SimConfig()
        rate = _bonus_foul_rate(
            [_player(foul_drawing_rate=_LEAGUE_AVG_FOUL_DRAW_RATE)],
            use_foul_drawing=True, foul_draw_scale=cfg.foul_draw_scale,
        )
        assert 0.03 < rate < 0.06

    def test_missing_rate_falls_back_to_league_floor(self):
        no_history = _bonus_foul_rate([_player(foul_drawing_rate=None)], use_foul_drawing=True)
        floor = _bonus_foul_rate(
            [_player(foul_drawing_rate=_LEAGUE_AVG_FOUL_DRAW_RATE)], use_foul_drawing=True
        )
        assert abs(no_history - floor) < 0.01

    def test_below_floor_rate_clamped_up(self):
        tiny = _bonus_foul_rate([_player(foul_drawing_rate=0.01)], use_foul_drawing=True)
        floor = _bonus_foul_rate(
            [_player(foul_drawing_rate=_LEAGUE_AVG_FOUL_DRAW_RATE)], use_foul_drawing=True
        )
        assert abs(tiny - floor) < 0.01

    def test_outlier_rate_capped(self):
        outlier = _bonus_foul_rate([_player(foul_drawing_rate=1.9)], use_foul_drawing=True)
        capped = _bonus_foul_rate(
            [_player(foul_drawing_rate=_FOUL_DRAW_RATE_CAP)], use_foul_drawing=True
        )
        assert abs(outlier - capped) < 0.01


# ---------------------------------------------------------------------------
# Shot-type multipliers on shooting fouls
# ---------------------------------------------------------------------------

class TestShotTypeMultipliers:
    def test_interior_multipliers_above_perimeter(self):
        assert _FOUL_DRAW_MULT["dunk"] > _FOUL_DRAW_MULT["layup"] > 1.0
        assert _FOUL_DRAW_MULT["corner_three"] < _FOUL_DRAW_MULT["above_break_three"] < 1.0

    def test_rim_attacker_draws_more_shooting_fouls_than_shooter(self):
        rim = [_player(pos="C", three_point_rate=0.0, dunk_rate=1.0, foul_drawing_rate=0.22)]
        shooter = [_player(pos="G", three_point_rate=1.0, foul_drawing_rate=0.22)]
        kw = dict(use_foul_drawing=True, use_shot_subtypes=True)
        assert _shooting_foul_rate(rim, **kw) > _shooting_foul_rate(shooter, **kw)

    def test_multiplier_noop_when_disabled(self):
        rim = [_player(pos="C", three_point_rate=0.0, dunk_rate=1.0, foul_drawing_rate=0.22)]
        with_mult = _shooting_foul_rate(rim, use_foul_drawing=True, use_shot_subtypes=True)
        without = _shooting_foul_rate(rim, use_foul_drawing=False, use_shot_subtypes=True)
        assert with_mult > without


# ---------------------------------------------------------------------------
# Late-game escalation
# ---------------------------------------------------------------------------

class TestLateGameEscalation:
    BASE_KW = dict(use_foul_drawing=True)

    def test_zone2_exceeds_zone1_exceeds_base(self):
        off = [_player(foul_drawing_rate=0.22)]
        base = _bonus_foul_rate(off, quarter=4, clock_seconds=300.0, score_margin=3, **self.BASE_KW)
        zone1 = _bonus_foul_rate(off, quarter=4, clock_seconds=100.0, score_margin=7, **self.BASE_KW)
        zone2 = _bonus_foul_rate(off, quarter=4, clock_seconds=45.0, score_margin=3, **self.BASE_KW)
        assert zone2 > zone1 > base

    def test_no_escalation_outside_q4(self):
        off = [_player(foul_drawing_rate=0.22)]
        q2 = _bonus_foul_rate(off, quarter=2, clock_seconds=45.0, score_margin=3, **self.BASE_KW)
        base = _bonus_foul_rate(off, quarter=1, clock_seconds=700.0, score_margin=0, **self.BASE_KW)
        assert abs(q2 - base) < 0.01

    def test_no_escalation_when_margin_too_wide(self):
        off = [_player(foul_drawing_rate=0.22)]
        blowout = _bonus_foul_rate(off, quarter=4, clock_seconds=45.0, score_margin=20, **self.BASE_KW)
        base = _bonus_foul_rate(off, quarter=1, clock_seconds=700.0, score_margin=0, **self.BASE_KW)
        assert abs(blowout - base) < 0.01

    def test_escalation_fires_in_ot(self):
        off = [_player(foul_drawing_rate=0.22)]
        ot = _bonus_foul_rate(off, quarter=5, clock_seconds=45.0, score_margin=2, **self.BASE_KW)
        base = _bonus_foul_rate(off, quarter=1, clock_seconds=700.0, score_margin=0, **self.BASE_KW)
        assert ot > base


# ---------------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------------

class TestConfig:
    def test_drama_m3_enables_foul_drawing(self):
        assert DRAMA_M3.use_foul_drawing is True

    def test_no_subtypes_preset_keeps_it_off(self):
        assert DRAMA_M3_NO_SUBTYPES.use_foul_drawing is False

    def test_default_off(self):
        assert SimConfig().use_foul_drawing is False

    def test_scale_below_naive_equivalence(self):
        # Naive equivalence would be 0.055 / 0.22 = 0.25; the default sits below it
        # to compensate for usage-weighted ball-handler selection favoring high drawers.
        assert SimConfig().foul_draw_scale < 0.055 / _LEAGUE_AVG_FOUL_DRAW_RATE
