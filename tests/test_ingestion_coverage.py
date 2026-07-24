"""Coverage-verification guard (jobs.verify_season_coverage).

2024-25 shipped with zero shot-location data because ingest_shot_defense soft-fails
silently and nothing checked. These lock in the loud check that catches that.
"""
from app.ingestion.jobs import SEASONS, verify_season_coverage


class _Row:
    def __init__(self, ra_fga):
        self.ra_fga = ra_fga


class _StubDB:
    """Minimal stand-in for a Session: execute(...).scalars().all() -> rows."""
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt):
        rows = self._rows

        class _Res:
            def scalars(self):
                class _S:
                    def all(self_inner):
                        return rows
                return _S()
        return _Res()


def test_seasons_is_canonical():
    assert "2024-25" in SEASONS and "2025-26" in SEASONS
    assert SEASONS[0] == "1996-97"  # the data cliff
    assert len(SEASONS) == len(set(SEASONS))


def test_flags_missing_shot_locations():
    rows = [_Row(None) for _ in range(100)]  # nobody has shot-location data (the 2024-25 bug)
    gaps = verify_season_coverage(_StubDB(rows), "2024-25")
    assert gaps and "shot-location" in gaps[0]


def test_passes_when_covered():
    rows = [_Row(5.0) for _ in range(90)] + [_Row(None) for _ in range(10)]  # 90% covered
    assert verify_season_coverage(_StubDB(rows), "2016-17") == []


def test_flags_empty_season():
    gaps = verify_season_coverage(_StubDB([]), "2030-31")
    assert gaps and "no PlayerSeasonStats" in gaps[0]
