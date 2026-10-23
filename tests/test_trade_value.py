"""Pure tests for the provisional trade-value model and the locked CPU rule (no DB)."""
import pytest

from app.services.trade_value import (
    ACCEPT, SPEC_GAME_SCORE, SPEC_NONSHOOTING, SPEC_TS_POINTS, SPEC_TS_POINTS_NS, SPECS,
    VALUE_SHORTFALL, ZERO_VALUE_TRADE, PlayerProduction, build_value_model, cpu_verdict,
    game_score, league_true_shooting, nonshooting_score, production_score, replacement_level,
    true_shooting_attempts,
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


# ----------------------------------------------- registered production specs (Amendment 2)

STATS = dict(player_id=1, team_id=1, games_played=70, minutes_per_game=30.0, points=20.0, fgm=8.0,
             fga=16.0, fta=6.0, ftm=4.0, rebounds=8.0, steals=1.0, assists=5.0, blocks=1.0,
             pf_per_game=2.0, turnovers=3.0)


def test_true_shooting_attempts_and_nonshooting_by_hand():
    assert true_shooting_attempts(STATS) == pytest.approx(2 * (16 + 0.44 * 6))            # 37.28
    assert nonshooting_score(STATS) == pytest.approx(4.0 + 1.0 + 3.5 + 0.7 - 0.8 - 3.0)   # 5.4
    # shooting terms + non-shooting terms recompose the full Game Score
    shooting = 20.0 + 0.4 * 8 - 0.7 * 16 - 0.4 * (6 - 4)
    assert game_score(STATS) == pytest.approx(shooting + nonshooting_score(STATS))


def test_from_stats_carries_the_spec_inputs():
    p = PlayerProduction.from_stats(STATS)
    assert p.points == 20.0 and p.tsa == pytest.approx(37.28) and p.nonshooting == pytest.approx(5.4)
    assert p.game_score == pytest.approx(game_score(STATS))


def test_league_true_shooting_is_games_weighted_points_over_attempts():
    a = PlayerProduction(1, 1, 80, 30.0, 0.0, points=20.0, tsa=20.0)      # TS 1.00
    b = PlayerProduction(2, 1, 20, 30.0, 0.0, points=10.0, tsa=20.0)      # TS 0.50
    assert league_true_shooting([a, b]) == pytest.approx((20 * 80 + 10 * 20) / (20 * 80 + 20 * 20))


def test_ts_points_is_efficiency_times_attempts_and_equals_points_minus_league_rate_times_tsa():
    p = PlayerProduction(1, 1, 60, 30.0, 0.0, points=20.0, tsa=30.0)      # TS% 0.667
    assert production_score(p, SPEC_TS_POINTS, 0.55) == pytest.approx((20 / 30 - 0.55) * 30)
    assert production_score(p, SPEC_TS_POINTS, 0.55) == pytest.approx(20 - 0.55 * 30)
    # a player who never shoots has no shooting value (not a divide-by-zero)
    assert production_score(PlayerProduction(2, 1, 60, 5.0, 0.0, tsa=0.0), SPEC_TS_POINTS, 0.55) == 0.0


def test_spec_composition_and_unknown_spec():
    p = PlayerProduction(1, 1, 60, 30.0, 12.0, points=20.0, tsa=30.0, nonshooting=4.0)
    ts = production_score(p, SPEC_TS_POINTS, 0.55)
    assert production_score(p, SPEC_TS_POINTS_NS, 0.55) == pytest.approx(ts + 4.0)
    assert production_score(p, SPEC_NONSHOOTING, 0.55) == 4.0
    assert production_score(p, SPEC_GAME_SCORE, 0.55) == 12.0
    with pytest.raises(ValueError):
        production_score(p, "nope", 0.55)


def _spec_league():
    out, pid = [], 0
    for t in (1, 2, 3):
        for r in range(1, 15):
            pid += 1
            out.append(PlayerProduction(pid, t, 60, 40.0 - 2 * r, 20.0 - r, points=(24 - r) * 1.0,
                                        tsa=22.0 - r, nonshooting=3.0 - 0.1 * r))
    return out


@pytest.mark.parametrize("spec", SPECS)
def test_replacement_level_is_computed_the_same_way_for_every_spec(spec):
    players = _spec_league()
    m = build_value_model(players, spec=spec)
    # ranks 11-13 on every team, scored under this spec, pooled
    ranked = sorted([p for p in players if p.team_id == 1], key=lambda p: -p.mpg)[10:13]
    want = sum(production_score(p, spec, m.league_ts) for p in ranked) / 3
    assert m.replacement == pytest.approx(want)               # all three teams are identical here
    assert m.spec == spec


def test_value_under_each_spec_is_zero_at_replacement_and_positive_above_it():
    players = _spec_league()
    for spec in SPECS:
        m = build_value_model(players, spec=spec)
        scores = sorted(m.score(p) for p in players)
        assert m.value(next(p for p in players if m.score(p) == scores[0])) == 0.0
        assert m.value(next(p for p in players if m.score(p) == scores[-1])) > 0.0
