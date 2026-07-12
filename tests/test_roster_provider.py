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
