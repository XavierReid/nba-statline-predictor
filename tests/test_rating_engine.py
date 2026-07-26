"""Unit tests for the RatingEngine.

Tests four archetypes:
- Elite high-volume shooter (Steph-like)
- Efficient low-volume shooter (specialist)
- High-assist guard (Jokic/CP3-like)
- Low-minute bench player
"""
from app.services.rating_engine import (
    SKILL_CONFIGS,
    compute_ball_handle_ratings,
    compute_ratings_for_attribute,
    percentile_to_rating,
)


class FakeStats:
    def __init__(self, player_id, **kwargs):
        self.player_id = player_id
        self.games_played = kwargs.get('games_played', 60)
        self.minutes_per_game = kwargs.get('minutes_per_game', 30.0)
        self.fg3a = kwargs.get('fg3a', 0.0)
        self.fg3m = kwargs.get('fg3m', 0.0)
        self.fg3_pct = kwargs.get('fg3_pct', 0.0)
        self.fga = kwargs.get('fga', 0.0)
        self.fgm = kwargs.get('fgm', 0.0)
        self.fta = kwargs.get('fta', 0.0)
        self.ftm = kwargs.get('ftm', 0.0)
        self.ft_pct = kwargs.get('ft_pct', 0.0)
        self.steals = kwargs.get('steals', 0.0)
        self.blocks = kwargs.get('blocks', 0.0)
        self.rebounds = kwargs.get('rebounds', 0.0)
        self.assists = kwargs.get('assists', 0.0)
        self.turnovers = kwargs.get('turnovers', 0.0)
        self.points = kwargs.get('points', 0.0)
        self.plus_minus = kwargs.get('plus_minus', 0.0)
        self.team_id = kwargs.get('team_id', 1)
        self.season = kwargs.get('season', '2024-25')


def test_percentile_curve_anchors():
    assert percentile_to_rating(0) == 40
    assert percentile_to_rating(99) == 99
    assert percentile_to_rating(50) == 72


def test_elite_high_volume_shooter_rated_above_role_player():
    # Both eligible — only test ordering within the pool
    elite = FakeStats(1, fg3a=10.0, fg3m=4.2, fg3_pct=0.42)
    role = FakeStats(2, fg3a=3.0, fg3m=1.1, fg3_pct=0.37)
    all_stats = [elite, role]
    ratings = compute_ratings_for_attribute("three_point", all_stats, SKILL_CONFIGS["three_point"])
    assert ratings[1] > ratings[2]


def test_low_volume_efficient_shooter_rated_below_high_volume():
    # Volume weighting means 8att/42.5% > 1att/43%
    high_vol = FakeStats(1, fg3a=8.0, fg3m=3.4, fg3_pct=0.425)
    low_vol = FakeStats(2, fg3a=2.0, fg3m=0.85, fg3_pct=0.425)
    all_stats = [high_vol, low_vol]
    ratings = compute_ratings_for_attribute("three_point", all_stats, SKILL_CONFIGS["three_point"])
    assert ratings[1] > ratings[2]


def test_high_assist_guard_gets_higher_passing_than_scorer():
    # Test relative ordering — absolute threshold meaningless in small pool
    playmaker = FakeStats(1, assists=10.0, games_played=70)
    scorer = FakeStats(2, assists=3.0, games_played=70)
    role = FakeStats(3, assists=1.5, games_played=70)
    all_stats = [playmaker, scorer, role]
    ratings = compute_ratings_for_attribute("passing", all_stats, SKILL_CONFIGS["passing"])
    assert ratings[1] > ratings[2] > ratings[3]


def test_low_minute_bench_player_gets_default():
    starter = FakeStats(1, fg3a=6.0, fg3m=2.4, fg3_pct=0.40, games_played=70, minutes_per_game=32)
    bench = FakeStats(2, fg3a=2.0, fg3m=0.8, fg3_pct=0.40, games_played=10, minutes_per_game=8)
    all_stats = [starter, bench]
    ratings = compute_ratings_for_attribute("three_point", all_stats, SKILL_CONFIGS["three_point"])
    assert ratings[2] == 40  # below minimums -> default


def test_players_below_attempt_minimum_get_default():
    shooter = FakeStats(1, fg3a=0.5, fg3m=0.2, fg3_pct=0.40)  # below 1.5 minimum
    all_stats = [shooter]
    ratings = compute_ratings_for_attribute("three_point", all_stats, SKILL_CONFIGS["three_point"])
    assert ratings[1] == 40


# ---------------------------------------------------------------------------
# ball_handle derivation (Attribute v3)
# ---------------------------------------------------------------------------


def _handle_stats(pid, usg=0.20, assists=5.0, tov=2.0, fga=12.0, fta=4.0,
                  games=70, mpg=32.0):
    """FakeStats configured for handle-rating tests. `usg` sets usg_pct directly
    so the derivation short-circuits the team_totals fallback."""
    s = FakeStats(pid, games_played=games, minutes_per_game=mpg,
                  fga=fga, fta=fta, assists=assists, turnovers=tov)
    s.usg_pct = usg
    return s


def test_ball_handle_monotonic_in_usage():
    low = _handle_stats(1, usg=0.14, assists=5.0, tov=2.0)
    mid = _handle_stats(2, usg=0.22, assists=5.0, tov=2.0)
    hi  = _handle_stats(3, usg=0.30, assists=5.0, tov=2.0)
    ratings = compute_ball_handle_ratings([low, mid, hi], team_totals={})
    assert ratings[3] > ratings[2] > ratings[1]


def test_ball_handle_monotonic_in_assists():
    low = _handle_stats(1, usg=0.22, assists=2.0, tov=2.0)
    mid = _handle_stats(2, usg=0.22, assists=5.0, tov=2.0)
    hi  = _handle_stats(3, usg=0.22, assists=9.0, tov=2.0)
    ratings = compute_ball_handle_ratings([low, mid, hi], team_totals={})
    assert ratings[3] > ratings[2] > ratings[1]


def test_ball_handle_monotonic_in_turnover_economy():
    # Lower TOV rate should score HIGHER (more economy). fga/fta held constant
    # so tov_per_poss = tov / (fga + 0.44*fta + tov) moves monotonically with tov.
    hi_tov  = _handle_stats(1, usg=0.22, assists=5.0, tov=4.0)
    mid_tov = _handle_stats(2, usg=0.22, assists=5.0, tov=2.5)
    lo_tov  = _handle_stats(3, usg=0.22, assists=5.0, tov=1.5)
    ratings = compute_ball_handle_ratings([hi_tov, mid_tov, lo_tov], team_totals={})
    assert ratings[3] > ratings[2] > ratings[1]


def test_ball_handle_star_guard_scores_higher_than_bench_role():
    # Realistic guard vs role player: usage + assists both dominate.
    star = _handle_stats(1, usg=0.30, assists=8.0, tov=2.8)
    role = _handle_stats(2, usg=0.13, assists=1.5, tov=0.6)
    filler = _handle_stats(3, usg=0.20, assists=3.0, tov=1.5)  # widen the pool
    ratings = compute_ball_handle_ratings([star, role, filler], team_totals={})
    assert ratings[1] > ratings[2]


def test_ball_handle_below_games_gate_is_excluded():
    # Below 20-game minimum → falls through to position_defaults (caller's job).
    partial = _handle_stats(1, games=15)
    starter = _handle_stats(2, games=70)
    ratings = compute_ball_handle_ratings([partial, starter], team_totals={})
    assert 1 not in ratings
    assert 2 in ratings


def test_ball_handle_below_minutes_gate_is_excluded():
    bench = _handle_stats(1, mpg=8.0)
    starter = _handle_stats(2, mpg=32.0)
    ratings = compute_ball_handle_ratings([bench, starter], team_totals={})
    assert 1 not in ratings
    assert 2 in ratings


def test_ball_handle_below_usage_gate_is_excluded():
    # Pure spot-up shooter with 9% usage — not a testable handle signal.
    spot_up = _handle_stats(1, usg=0.09, assists=1.0)
    on_ball = _handle_stats(2, usg=0.22, assists=5.0)
    ratings = compute_ball_handle_ratings([spot_up, on_ball], team_totals={})
    assert 1 not in ratings
    assert 2 in ratings


def test_ball_handle_empty_pool_returns_empty_dict():
    partial = _handle_stats(1, games=10)  # ineligible
    ratings = compute_ball_handle_ratings([partial], team_totals={})
    assert ratings == {}
