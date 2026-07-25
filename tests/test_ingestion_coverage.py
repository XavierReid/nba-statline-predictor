"""Coverage-verification guard (jobs.verify_season_coverage).

2024-25 shipped incomplete twice — zero shot-location data AND only 431/569 attributes
seeded — because ingestion soft-fails silently and nothing checked. These lock in the
loud check that catches both.
"""
from app.ingestion.jobs import SEASONS, verify_season_coverage


class _Row:
    def __init__(self, ra_fga):
        self.ra_fga = ra_fga


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        rows = self._rows

        class _S:
            def all(self_inner):
                return rows
        return _S()

    def scalar(self):
        return self._scalar


class _StubDB:
    """execute() returns queued results in call order: rows, then attrs count, tends count."""
    def __init__(self, results):
        self._results = list(results)

    def execute(self, _stmt):
        return self._results.pop(0)


def _db(rows, attrs, tends):
    return _StubDB([_Result(rows=rows), _Result(scalar=attrs), _Result(scalar=tends)])


def test_seasons_is_canonical():
    assert "2024-25" in SEASONS and "2025-26" in SEASONS
    assert SEASONS[0] == "1996-97"  # the data cliff
    assert len(SEASONS) == len(set(SEASONS))


def test_flags_missing_shot_locations():
    rows = [_Row(None) for _ in range(100)]  # nobody has shot-location data (the 2024-25 bug)
    gaps = verify_season_coverage(_db(rows, 100, 100), "2024-25")
    assert any("shot-location" in g for g in gaps)


def test_flags_attribute_gap():
    rows = [_Row(5.0) for _ in range(100)]  # shots fine, but attrs seeded for only 60
    gaps = verify_season_coverage(_db(rows, 60, 60), "2024-25")
    assert any("attributes seeded for 60/100" in g for g in gaps)
    assert any("tendencies seeded for 60/100" in g for g in gaps)


def test_passes_when_fully_covered():
    rows = [_Row(5.0) for _ in range(90)] + [_Row(None) for _ in range(10)]  # 90% shots
    assert verify_season_coverage(_db(rows, 100, 100), "2016-17") == []


def test_flags_empty_season():
    gaps = verify_season_coverage(_StubDB([_Result(rows=[])]), "2030-31")
    assert gaps and "no PlayerSeasonStats" in gaps[0]
