"""Tests for Attribute Derivation v2 — shot-location and defensive-matchup ratings."""
from app.services.rating_engine import (
    SKILL_CONFIGS,
    _raw_close_shot,
    _raw_dunk_rim,
    _raw_interior_defense,
    _raw_layup,
    _raw_mid_range,
    _raw_perimeter_defense,
    compute_ratings_for_attribute,
    compute_tendencies,
    derive_box_score_defense,
)


class _Stats:
    """Stub with every field the raw fns touch; override per test."""
    def __init__(self, **kwargs):
        defaults = dict(
            player_id=1, team_id=None, games_played=60, minutes_per_game=30.0,
            fga=15.0, fgm=7.0, fg3a=5.0, fg3m=2.0, fg3_pct=0.38,
            fta=5.0, ft_pct=0.80, steals=1.0, blocks=0.5,
            rebounds=5.0, assists=4.0, turnovers=2.0, usg_pct=None,
            oreb_pct=None, dreb_pct=None,
            ra_fgm=3.0, ra_fga=5.0, ra_fg_pct=0.65,
            paint_fgm=1.0, paint_fga=2.5, paint_fg_pct=0.45,
            mid_fga=3.0, mid_fg_pct=0.44, corner3_fga=1.5,
            d_lt6_fga=6.0, d_lt6_plusminus=-0.05,
            d_fg3a=4.0, d_fg3_plusminus=0.01,
            d_fga=12.0, d_plusminus=-0.03,
        )
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(self, k, v)


class TestRawScores:
    def test_layup_uses_ra_efficiency(self):
        good = _raw_layup(_Stats(ra_fg_pct=0.70, ra_fga=6.0))
        bad = _raw_layup(_Stats(ra_fg_pct=0.50, ra_fga=6.0))
        assert good > bad

    def test_layup_gated_on_volume(self):
        assert _raw_layup(_Stats(ra_fga=0.5)) is None
        assert _raw_layup(_Stats(ra_fga=None)) is None

    def test_close_shot_blends_paint_and_ra(self):
        paint_only = _raw_close_shot(_Stats(paint_fg_pct=0.60, ra_fg_pct=0.0, ra_fga=0.0, paint_fga=3.0))
        with_ra = _raw_close_shot(_Stats(paint_fg_pct=0.60, ra_fg_pct=0.70, ra_fga=5.0, paint_fga=3.0))
        assert with_ra > paint_only

    def test_dunk_rim_heavier_volume_normalizer(self):
        # same efficiency, low vs high rim volume — dunk raw rewards volume more
        lo = _raw_dunk_rim(_Stats(ra_fga=3.0))
        hi = _raw_dunk_rim(_Stats(ra_fga=7.0))
        assert hi > lo

    def test_interior_defense_negative_plusminus_is_good(self):
        rim_protector = _raw_interior_defense(_Stats(d_lt6_plusminus=-0.08, d_lt6_fga=8.0))
        sieve = _raw_interior_defense(_Stats(d_lt6_plusminus=0.04, d_lt6_fga=8.0))
        assert rim_protector > 0 > sieve

    def test_perimeter_defense_uses_nonrim_component(self):
        # overall pm is dragged negative purely by rim defense; non-rim part is bad
        s = _Stats(d_fga=10.0, d_plusminus=-0.02, d_lt6_fga=6.0, d_lt6_plusminus=-0.08)
        # non-rim pm = (-0.02*10 - (-0.08*6)) / 4 = (-0.2 + 0.48)/4 = +0.07 → bad defender
        assert _raw_perimeter_defense(s) < 0

    def test_perimeter_defense_gated_on_nonrim_volume(self):
        assert _raw_perimeter_defense(_Stats(d_fga=6.0, d_lt6_fga=5.0)) is None  # 1.0 non-rim < gate
        assert _raw_perimeter_defense(_Stats(d_fga=None)) is None

    def test_mid_range_prefers_zone_data(self):
        zone = _Stats(mid_fga=4.0, mid_fg_pct=0.50)
        proxy = _Stats(mid_fga=None, mid_fg_pct=None)  # falls back to 2PT% proxy
        assert _raw_mid_range(zone) is not None
        assert _raw_mid_range(proxy) is not None


class TestPercentilePipeline:
    def test_ineligible_omitted_when_flag_off(self):
        stats = [
            _Stats(player_id=1, ra_fga=6.0),
            _Stats(player_id=2, ra_fga=0.2),  # below layup gate
            _Stats(player_id=3, ra_fga=5.0, ra_fg_pct=0.55),
        ]
        r = compute_ratings_for_attribute("layup", stats, SKILL_CONFIGS["layup"],
                                          default_for_ineligible=False)
        assert 1 in r and 3 in r and 2 not in r

    def test_ineligible_gets_default_when_flag_on(self):
        stats = [_Stats(player_id=1, ra_fga=6.0), _Stats(player_id=2, ra_fga=0.2)]
        r = compute_ratings_for_attribute("layup", stats, SKILL_CONFIGS["layup"])
        assert r[2] == 40  # SHOOTING_DEFAULT


class TestBoxScoreDefenseFallback:
    """Pre-2013-14 fallback: team def_rating + individual box proxy -> defense."""

    def _players(self):
        # two teams: 100 (elite defense) and 200 (poor defense); within each a
        # rim protector (high blocks/steals) and a turnstile (none).
        return [
            _Stats(player_id=1, team_id=100, blocks=2.5, steals=1.8),
            _Stats(player_id=2, team_id=100, blocks=0.1, steals=0.2),
            _Stats(player_id=3, team_id=200, blocks=2.5, steals=1.8),
            _Stats(player_id=4, team_id=200, blocks=0.1, steals=0.2),
        ]

    def test_team_defense_quality_drives_rating(self):
        # same individual profile, different team quality -> better team rates higher
        d = derive_box_score_defense(
            self._players(), {100: 105.0, 200: 118.0},  # lower def_rating = better
            positions={1: "C", 2: "C", 3: "C", 4: "C"},
        )
        assert d[1]["interior_defense"] > d[3]["interior_defense"]
        assert d[2]["perimeter_defense"] > d[4]["perimeter_defense"]

    def test_individual_proxy_separates_within_team(self):
        d = derive_box_score_defense(
            self._players(), {100: 105.0, 200: 118.0},
            positions={1: "F", 2: "F", 3: "F", 4: "F"},
        )
        assert d[1]["interior_defense"] > d[2]["interior_defense"]  # blocks
        assert d[1]["perimeter_defense"] > d[2]["perimeter_defense"]  # steals

    def test_ratings_bounded(self):
        d = derive_box_score_defense(
            self._players(), {100: 105.0, 200: 118.0},
            positions={1: "C", 2: "G", 3: "F", 4: "G"},
        )
        for v in d.values():
            assert 30 <= v["interior_defense"] <= 99
            assert 30 <= v["perimeter_defense"] <= 99


class TestTendencies:
    def test_corner_three_rate_from_zone_data(self):
        t = compute_tendencies(_Stats(corner3_fga=2.0, fg3a=8.0))
        assert t["corner_three_rate"] == 0.25

    def test_corner_three_rate_none_without_data(self):
        t = compute_tendencies(_Stats(corner3_fga=None))
        assert t["corner_three_rate"] is None

    def test_corner_three_rate_none_on_tiny_volume(self):
        t = compute_tendencies(_Stats(corner3_fga=0.4, fg3a=0.4))
        assert t["corner_three_rate"] is None

    def test_corner_three_rate_capped_at_one(self):
        t = compute_tendencies(_Stats(corner3_fga=9.0, fg3a=8.0))
        assert t["corner_three_rate"] == 1.0
