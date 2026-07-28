"""Tests for RosterProvider selection (multi-season Phase 1).

Roster construction has two modes with different notions of "correct":
CURRENT (live roster, Player.team_id) for the roster-snapshot season, HISTORICAL
(season-accurate, PlayerSeasonStats.team_id) for completed seasons. The possession
engine is unaware of which produced a roster. Full load() behavior is exercised via
load_roster in the game/replay tests; here we pin the selection logic.
"""
from app.models.player import Player
from app.models.player_season_stats import PlayerSeasonStats
from app.services.roster import (
    CURRENT_ROSTER_SEASONS,
    CurrentRosterProvider,
    HistoricalRosterProvider,
    _ZONE_FOUL_MISS_RATE,
    _pre_negation_prob,
    roster_provider_for,
)


class TestProviderSelection:
    def test_current_season_uses_current_provider(self):
        assert isinstance(roster_provider_for("2025-26"), CurrentRosterProvider)

    def test_historical_seasons_use_historical_provider(self):
        for season in ("2024-25", "2018-19", "2005-06", "1999-00"):
            assert isinstance(roster_provider_for(season), HistoricalRosterProvider)

    def test_only_snapshot_season_is_current(self):
        # keeps every existing calibration baseline byte-identical
        assert CURRENT_ROSTER_SEASONS == frozenset({"2025-26"})


class TestTeamMembership:
    def test_current_filters_on_live_team(self):
        # the current provider resolves membership from the player's present team
        clause = CurrentRosterProvider()._team_membership(1610612738)
        assert Player.team_id.key in str(clause)

    def test_historical_filters_on_season_team(self):
        # the historical provider resolves membership from the season's own stats
        clause = HistoricalRosterProvider()._team_membership(1610612738)
        assert PlayerSeasonStats.team_id.key in str(clause.left)


class TestPreNegationTransform:
    """`_pre_negation_prob` inverts sim's PR#8 foul-negation to recover the
    pre-negation make probability from a real POST-negation zone FG% — the
    Session 3 fix for the interior/mid FG% double-count."""

    def test_identity_roundtrip(self):
        # If p is the true make rate and f is P(foul | miss), then post-neg
        # observed Q = p / (p + (1-p)(1-f)). The transform must invert this
        # exactly for every combination inside the [0,1] interior.
        for p in (0.30, 0.45, 0.62, 0.75):
            for f in (0.05, 0.19, 0.24, 0.40):
                q = p / (p + (1 - p) * (1 - f))
                recovered = _pre_negation_prob(q, f)
                assert abs(recovered - p) < 5e-4, (
                    f"round-trip failed for p={p}, f={f}: q={q}, recovered={recovered}"
                )

    def test_passthrough_on_none(self):
        # Callers pipe shrunk output (which is None when no observed data exists)
        # straight through — no null-check should be needed at the call site.
        assert _pre_negation_prob(None, 0.24) is None

    def test_passthrough_on_boundary_values(self):
        # 0.0 and 1.0 are boundary attractors of the identity — passthrough
        # avoids divide-by-tiny / negative-numerator artifacts.
        assert _pre_negation_prob(0.0, 0.24) == 0.0
        assert _pre_negation_prob(1.0, 0.24) == 1.0

    def test_transform_lowers_observed_values(self):
        # Post-neg data is inflated relative to true make prob by the negation
        # lift; the transform must strictly lower it (for f > 0, 0 < obs < 1).
        for zone, f in _ZONE_FOUL_MISS_RATE.items():
            for obs in (0.35, 0.50, 0.66):
                pre = _pre_negation_prob(obs, f)
                assert pre < obs, f"zone {zone}: pre {pre} should be < obs {obs}"

    def test_constants_are_within_expected_range(self):
        # Sanity fence on the measured constants. If the sim's foul-drawing model
        # drifts and these need re-measurement, the test surfaces the assumption.
        assert 0.20 <= _ZONE_FOUL_MISS_RATE["rim"] <= 0.28
        assert 0.15 <= _ZONE_FOUL_MISS_RATE["nonrim"] <= 0.24
        assert 0.02 <= _ZONE_FOUL_MISS_RATE["three"] <= 0.08
