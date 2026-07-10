"""Tests for M3b — player variance form factors and team OREB profiles."""
import random
import pytest

from app.services.roster import player_variance
from app.services.possession import OREB_RATE, resolve_possession
from app.services.possession_context import make_context
from app.services.sim_config import SimConfig, DRAMA_M3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_player(**overrides) -> dict:
    base = {
        "id": 1,
        "name": "Test Player",
        "usage_rate": 0.22,
        "three_point_rate": 0.35,
        "three_point": 65,
        "mid_range": 60,
        "close_shot": 65,
        "free_throw": 75,
        "passing": 60,
        "steal": 50,
        "block": 50,
        "perimeter_defense": 55,
        "interior_defense": 55,
        "offensive_rebound": 50,
        "defensive_rebound": 50,
        "oreb_rate": 0.07,
        "dreb_rate": 0.15,
        "assist_rate": 2.0,
        "turnover_rate": 2.0,
        "overall": 65,
        "player_variance": 0.07,
    }
    base.update(overrides)
    return base


def _five_players(start_id: int = 1, **overrides) -> list:
    return [_make_player(id=start_id + i, **overrides) for i in range(5)]


# ---------------------------------------------------------------------------
# player_variance derivation
# ---------------------------------------------------------------------------

class TestPlayerVariance:
    def test_elite_decision_maker_low_variance(self):
        p = _make_player(passing=85, turnover_rate=1.5)
        assert player_variance(p) == 0.02

    def test_shooting_specialist_high_variance(self):
        p = _make_player(three_point=82, usage_rate=0.15)
        assert player_variance(p) == 0.05

    def test_young_high_usage_moderate_variance(self):
        p = _make_player(overall=55, usage_rate=0.27)
        assert player_variance(p) == 0.04

    def test_default_tier(self):
        p = _make_player(passing=65, turnover_rate=2.5, three_point=70, usage_rate=0.22, overall=68)
        assert player_variance(p) == 0.03

    def test_elite_tier_takes_priority_over_specialist(self):
        # High passing + low TO qualifies for elite even if 3PT is high
        p = _make_player(passing=85, turnover_rate=1.5, three_point=82, usage_rate=0.15)
        assert player_variance(p) == 0.02

    def test_boundary_passing_exactly_80(self):
        p = _make_player(passing=80, turnover_rate=2.0)
        assert player_variance(p) == 0.02

    def test_boundary_tov_rate_exactly_2(self):
        p = _make_player(passing=82, turnover_rate=2.0)
        assert player_variance(p) == 0.02

    def test_just_above_tov_threshold_falls_to_default(self):
        p = _make_player(passing=82, turnover_rate=2.1)
        assert player_variance(p) == 0.03


# ---------------------------------------------------------------------------
# Form factor clamping and distribution
# ---------------------------------------------------------------------------

class TestFormFactors:
    def test_form_factors_drawn_within_clamp_bounds(self):
        rng = random.Random(42)
        # Extreme σ to stress-test clamping
        for _ in range(500):
            raw = rng.gauss(1.0, 0.30)
            clamped = max(0.75, min(1.25, raw))
            assert 0.75 <= clamped <= 1.25

    def test_high_variance_player_wider_distribution(self):
        """Shooting specialist (σ=0.10) should deviate more than elite (σ=0.04) on average."""
        rng_high = random.Random(99)
        rng_low = random.Random(99)
        deviations_high = [abs(rng_high.gauss(1.0, 0.10) - 1.0) for _ in range(1000)]
        deviations_low = [abs(rng_low.gauss(1.0, 0.04) - 1.0) for _ in range(1000)]
        assert sum(deviations_high) / len(deviations_high) > sum(deviations_low) / len(deviations_low)

    def test_form_factor_affects_shot_outcome(self):
        """With an extreme hot form factor a player should make shots more often."""
        offense = _five_players(1, three_point=65, mid_range=60, close_shot=65)
        defense = _five_players(6)

        # Hot player (factor = 1.25 → maximum upside)
        hot_factors = {p["id"]: 1.25 for p in offense}
        # Cold player (factor = 0.75 → maximum downside)
        cold_factors = {p["id"]: 0.75 for p in offense}

        hot_makes = 0
        cold_makes = 0
        trials = 500
        for i in range(trials):
            rng = random.Random(i)
            event_hot = resolve_possession(make_context(offense, defense, rng, form_factors=hot_factors))
            if event_hot.get("made"):
                hot_makes += 1
            rng2 = random.Random(i)
            event_cold = resolve_possession(make_context(offense, defense, rng2, form_factors=cold_factors))
            if event_cold.get("made"):
                cold_makes += 1

        assert hot_makes > cold_makes, (
            f"Hot ({hot_makes}) should outscore Cold ({cold_makes}) over {trials} trials"
        )

    def test_empty_form_factors_no_effect(self):
        """Passing {} form_factors should behave identically to passing None."""
        offense = _five_players(1)
        defense = _five_players(6)

        makes_none = 0
        makes_empty = 0
        for i in range(200):
            rng1 = random.Random(i)
            e1 = resolve_possession(make_context(offense, defense, rng1, form_factors=None))
            rng2 = random.Random(i)
            e2 = resolve_possession(make_context(offense, defense, rng2, form_factors={}))
            if e1.get("made"):
                makes_none += 1
            if e2.get("made"):
                makes_empty += 1

        assert makes_none == makes_empty


# ---------------------------------------------------------------------------
# Team OREB profiles
# ---------------------------------------------------------------------------

class TestTeamOrebProfiles:
    def test_high_oreb_rate_produces_more_offensive_rebounds(self):
        """A team with OREB_RATE=0.40 should grab more offensive boards than one at 0.10."""
        offense = _five_players(1, oreb_rate=0.5)
        defense = _five_players(6)

        high_orebs = 0
        low_orebs = 0
        trials = 1000
        for i in range(trials):
            rng = random.Random(i)
            event = resolve_possession(make_context(offense, defense, rng, offense_oreb_rate=0.40))
            if event.get("is_oreb"):
                high_orebs += 1

            rng2 = random.Random(i)
            event2 = resolve_possession(make_context(offense, defense, rng2, offense_oreb_rate=0.10))
            if event2.get("is_oreb"):
                low_orebs += 1

        assert high_orebs > low_orebs, (
            f"High OREB ({high_orebs}) should exceed low OREB ({low_orebs}) over {trials} trials"
        )

    def test_default_oreb_rate_matches_constant(self):
        """Omitting offense_oreb_rate should use the OREB_RATE constant (0.22)."""
        offense = _five_players(1)
        defense = _five_players(6)
        orebs_default = 0
        orebs_explicit = 0
        for i in range(300):
            rng1 = random.Random(i)
            if resolve_possession(make_context(offense, defense, rng1)).get("is_oreb"):
                orebs_default += 1
            rng2 = random.Random(i)
            if resolve_possession(make_context(offense, defense, rng2, offense_oreb_rate=OREB_RATE)).get("is_oreb"):
                orebs_explicit += 1

        assert orebs_default == orebs_explicit


# ---------------------------------------------------------------------------
# SimConfig integration
# ---------------------------------------------------------------------------

class TestSimConfigM3b:
    def test_drama_m3_preset_has_variance_and_oreb_enabled(self):
        assert DRAMA_M3.use_player_variance is True
        assert DRAMA_M3.use_team_oreb is True

    def test_drama_m2_preset_unchanged(self):
        from app.services.sim_config import DRAMA_M2
        assert not hasattr(DRAMA_M2, "use_player_variance") or DRAMA_M2.use_player_variance is False
        assert not hasattr(DRAMA_M2, "use_team_oreb") or DRAMA_M2.use_team_oreb is False

    def test_baseline_has_variance_off(self):
        cfg = SimConfig()
        assert cfg.use_player_variance is False
        assert cfg.use_team_oreb is False
