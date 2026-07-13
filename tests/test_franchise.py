"""Season-aware franchise identity (Multi-Season Phase 2)."""
from app.services.franchise import resolve_abbreviation, team_identity

SONICS = 1610612760   # Seattle SuperSonics -> Oklahoma City Thunder (2008-09)
GRIZ = 1610612763     # Vancouver -> Memphis (2001-02)
LAKERS = 1610612747   # never moved


class TestResolveAbbreviation:
    def test_era_abbreviation_resolves_to_franchise(self):
        assert resolve_abbreviation("SEA", "2005-06") == SONICS
        assert resolve_abbreviation("VAN", "2000-01") == GRIZ

    def test_current_abbreviation_falls_back(self):
        # OKC didn't exist in 2005-06 — era resolution returns None so the caller
        # uses the teams table (which does have OKC today).
        assert resolve_abbreviation("OKC", "2005-06") is None
        assert resolve_abbreviation("MEM", "2000-01") is None

    def test_current_abbreviation_in_current_era(self):
        assert resolve_abbreviation("OKC", "2025-26") == SONICS

    def test_relocation_boundary(self):
        assert resolve_abbreviation("SEA", "2007-08") == SONICS   # last Seattle year
        assert resolve_abbreviation("SEA", "2008-09") is None     # became OKC
        assert resolve_abbreviation("OKC", "2008-09") == SONICS

    def test_unmoved_franchise_has_no_override(self):
        assert resolve_abbreviation("LAL", "1996-97") is None


class TestTeamIdentity:
    def test_era_identity(self):
        cur = ("OKC", "Oklahoma City", "Thunder")
        assert team_identity(SONICS, "2005-06", cur) == ("Seattle", "SuperSonics", "SEA")

    def test_current_identity(self):
        cur = ("OKC", "Oklahoma City", "Thunder")
        assert team_identity(SONICS, "2025-26", cur) == ("Oklahoma City", "Thunder", "OKC")

    def test_nickname_only_change(self):
        cur = ("WAS", "Washington", "Wizards")
        assert team_identity(1610612764, "1996-97", cur) == ("Washington", "Bullets", "WAS")

    def test_unmoved_franchise_returns_current(self):
        cur = ("LAL", "Los Angeles", "Lakers")
        assert team_identity(LAKERS, "1996-97", cur) == cur
