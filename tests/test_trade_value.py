"""Pure tests for the provisional trade-value model and the locked CPU rule (no DB)."""
import pytest

from app.services.trade_value import (
    ACCEPT, VALUE_SHORTFALL, ZERO_VALUE_TRADE,
    PlayerProduction, build_value_model, cpu_verdict, game_score, replacement_level,
)


def pp(pid, team, gp, mpg, g):
    return PlayerProduction(pid, team, gp, mpg, g)


# ---------------------------------------------------------------- game score

def test_game_score_matches_hollinger_by_hand():
    stats = dict(points=20.0, fgm=8.0, fga=16.0, fta=6.0, ftm=4.0, rebounds=8.0,
                 steals=1.0, assists=5.0, blocks=1.0, pf_per_game=2.0, turnovers=3.0)
    # 20 + 3.2 - 11.2 - 0.8 + 4.0 + 1.0 + 3.5 + 0.7 - 0.8 - 3.0
    assert game_score(stats) == pytest.approx(16.6)


def test_game_score_treats_missing_fields_as_zero():
    assert game_score({"points": 10.0}) == pytest.approx(10.0)


# ------------------------------------------------------- replacement level audit

def _league(per_team=14, teams=3):
    """Each team: rank r (1..per_team) has mpg descending and game score = 30 - 2r."""
    out, pid = [], 0
    for t in range(1, teams + 1):
        for r in range(1, per_team + 1):
            pid += 1
            out.append(pp(pid, t, 60, 40.0 - 2 * r, 30.0 - 2 * r))
    return out


def test_replacement_level_is_mean_of_ranks_11_to_13_on_every_team():
    rl = replacement_level(_league())
    assert rl.value == pytest.approx(((30 - 22) + (30 - 24) + (30 - 26)) / 3)   # 8, 6, 4 -> 6
    assert rl.teams_used == 3 and rl.n_players == 9
    assert all(v == pytest.approx(6.0) for v in rl.team_means.values())         # same on every team


def test_cameo_players_do_not_define_replacement():
    players = _league()
    # a 12-game-played hot cameo ranked by MPG among the 11-13 band would raise R if counted
    players.append(pp(999, 1, 5, 22.0, 40.0))
    assert replacement_level(players).value == pytest.approx(6.0)


def test_short_rosters_are_skipped_not_averaged_in():
    players = _league(per_team=14, teams=2) + [pp(900 + i, 9, 60, 30.0 - i, 25.0) for i in range(8)]
    rl = replacement_level(players)
    assert rl.teams_used == 2 and 9 not in rl.team_means


def test_replacement_level_requires_data():
    with pytest.raises(ValueError):
        replacement_level([pp(1, 1, 60, 30.0, 10.0)])


# --------------------------------------------------------------------- values

def _model(**kw):
    return build_value_model(_league(), **kw)


def test_value_is_zero_at_or_below_replacement_and_positive_above():
    m = _model()
    assert m.value(pp(1, 1, 60, 5.0, 6.0)) == 0.0
    assert m.value(pp(2, 1, 60, 5.0, 2.0)) == 0.0
    assert m.value(pp(3, 1, 60, 30.0, 26.0)) == pytest.approx(20.0)


def test_convexity_makes_a_star_worth_more_than_the_sum_of_equal_parts():
    star, role = pp(1, 1, 60, 36.0, 26.0), pp(2, 1, 60, 20.0, 16.0)       # +20 vs +10 above R=6
    linear, convex = _model(k=1.0), _model(k=2.0)
    assert linear.value(star) == pytest.approx(2 * linear.value(role))
    assert convex.value(star) == pytest.approx(4 * convex.value(role))


def test_shrink_pulls_thin_samples_toward_replacement():
    thin, full = pp(1, 1, 5, 30.0, 26.0), pp(2, 1, 80, 30.0, 26.0)
    off, on = _model(shrink=False), _model(shrink=True)
    assert off.value(thin) == off.value(full)
    assert on.value(thin) < on.value(full) < off.value(full)
    assert on.explain(thin)["gp_weight"] == pytest.approx(5 / 25)


def test_availability_scales_by_share_of_team_games():
    m = _model(availability=True)
    full = pp(1, 1, 60, 30.0, 26.0)            # the team's max GP is 60
    half = pp(2, 1, 30, 30.0, 26.0)
    assert m.value(half) == pytest.approx(m.value(full) / 2)
    assert _model(availability=False).value(half) == _model(availability=False).value(full)


def test_explain_components_multiply_out_to_the_value():
    m = _model(k=1.5, shrink=True, availability=True)
    e = m.explain(pp(1, 1, 45, 28.0, 22.0))
    assert e["value"] == pytest.approx((e["shrunk_game_score"] - e["replacement"]) ** 1.5 * e["availability"])
    assert m.value(pp(1, 1, 45, 28.0, 22.0)) == e["value"]


# ------------------------------------------------------------ the locked CPU rule

def test_accepts_exactly_at_the_margin_and_rejects_just_below():
    assert cpu_verdict([(1, 11.0)], [(2, 10.0)]).accepted                      # float edge: 1.1 * 10
    assert cpu_verdict([(1, 11.0)], [(2, 10.0)]).code == ACCEPT
    v = cpu_verdict([(1, 10.9)], [(2, 10.0)])
    assert not v.accepted and v.code == VALUE_SHORTFALL


def test_values_are_summed_on_each_side():
    assert cpu_verdict([(1, 6.0), (2, 6.0)], [(3, 10.0)]).accepted             # 12 >= 11
    assert not cpu_verdict([(1, 5.0), (2, 5.0)], [(3, 10.0)]).accepted         # 10 < 11


def test_outgoing_zero_accepts_only_if_something_positive_comes_back():
    free = cpu_verdict([(1, 3.0)], [(2, 0.0)])
    assert free.accepted and free.code == ACCEPT and free.ratio is None
    nothing = cpu_verdict([(1, 0.0)], [(2, 0.0)])
    assert not nothing.accepted and nothing.code == ZERO_VALUE_TRADE


def test_negative_values_are_clamped_to_zero():
    v = cpu_verdict([(1, -5.0), (2, 4.0)], [(3, 3.0)])
    assert v.incoming == 4.0 and v.accepted                                    # 4 >= 3.3
    assert cpu_verdict([(1, 5.0)], [(2, -9.0)]).code == ACCEPT                 # outgoing clamps to 0


def test_missing_values_count_as_zero_and_are_reported():
    v = cpu_verdict([(1, None), (2, 5.0)], [(3, 4.0), (4, None)])
    assert v.missing_value_players == (1, 4)
    assert v.incoming == 5.0 and v.outgoing == 4.0
    assert "1" in v.message and "4" in v.message
    assert cpu_verdict([(1, None)], [(2, None)]).code == ZERO_VALUE_TRADE


def test_every_verdict_has_a_stable_code_and_a_readable_message():
    cases = [([(1, 5.0)], [(2, 5.0)]), ([(1, 9.0)], [(2, 5.0)]), ([], []), ([(1, 1.0)], [])]
    for inc, out in cases:
        v = cpu_verdict(inc, out)
        assert v.code in {ACCEPT, VALUE_SHORTFALL, ZERO_VALUE_TRADE}
        assert isinstance(v.message, str) and len(v.message) > 10


def test_verdict_is_deterministic():
    args = ([(1, 7.0), (2, 2.0)], [(3, 8.0)])
    assert cpu_verdict(*args) == cpu_verdict(*args)
