"""Pure tests for the as-of reader, manifest audit and metamorphic canary (no database).

Includes NEGATIVE tests: deliberately leaky predictors must be caught, otherwise the canary proves
nothing (Rating protocol gates A1-A3).
"""
import ast
import os
from datetime import date

import pytest

from validation.ratings.asof import (
    AsOfReader, EXACT, GarbledSource, LeakageError, MemorySource, PROXY, REVISION_RISK, run_canary,
)
from validation.ratings.predictors import v1_carry_forward

T, P1, P2 = "2021-22", "2020-21", "2019-20"


def stats_row(pid, team, gp, mpg, ft, fg3):
    return dict(player_id=pid, team_id=team, season=None, games_played=gp, minutes_per_game=mpg, ft_pct=ft,
                fta=3.0, fg3_pct=fg3, fg3a=4.0, usg_pct=0.2, points=15.0, d_fga=5.0, ra_fga=3.0)


def make_source():
    def season_rows(s, bump):
        return [dict(stats_row(1, 10, 70, 30.0 + bump, 0.80, 0.36), season=s),
                dict(stats_row(2, 11, 60, 22.0 + bump, 0.70, 0.31), season=s),
                dict(stats_row(3, 10, 40, 14.0 + bump, 0.65, 0.28), season=s)]
    return MemorySource(
        stats={P2: season_rows(P2, 0), P1: season_rows(P1, 1), T: season_rows(T, 5)},   # T rows differ => garbling visible
        team={P1: [dict(team_id=10, season=P1, pace=99.0)], T: [dict(team_id=10, season=T, pace=101.0)]},
        logs={T: [dict(player_id=1, team_id=10, game_no=1, minutes=30.0),
                  dict(player_id=2, team_id=11, game_no=1, minutes=20.0),
                  dict(player_id=3, team_id=10, game_no=40, minutes=10.0)],
              P1: [dict(player_id=1, team_id=10, game_no=1, minutes=31.0)]},
        bio={T: [dict(PLAYER_ID=1, AGE=27.0, PLAYER_HEIGHT_INCHES=78, PLAYER_WEIGHT=210, COLLEGE="X", COUNTRY="USA",
                      DRAFT_YEAR="2016", DRAFT_ROUND="1", DRAFT_NUMBER="10", GP=70, PTS=15.0)]},
        draft={2021: [dict(PERSON_ID=9, ROUND_NUMBER=1, OVERALL_PICK=4, ORGANIZATION="Duke")],
               2022: [dict(PERSON_ID=99, ROUND_NUMBER=1, OVERALL_PICK=1, ORGANIZATION="Future U")]},
        calendar={P2: (date(2019, 10, 22), date(2020, 3, 11)), P1: (date(2020, 12, 22), date(2021, 5, 16)),
                  T: (date(2021, 10, 19), date(2022, 4, 10))},
    )


# ------------------------------------------------------------------ the guard (gate A2)

def test_reader_serves_prior_seasons_and_cutoff_is_day_before_first_game():
    r = AsOfReader(make_source(), T)
    assert r.cutoff == date(2021, 10, 18)
    assert len(r.season_stats(P1)) == 3 and len(r.prior_game_logs(P1)) == 1


def test_reader_refuses_the_target_season_and_later():
    r = AsOfReader(make_source(), T)
    for call in (lambda: r.season_stats(T), lambda: r.team_stats(T), lambda: r.prior_game_logs(T),
                 lambda: r.season_stats("2022-23")):
        with pytest.raises(LeakageError):
            call()


def test_covid_season_end_override_makes_2019_20_knowable_only_after_october_2020():
    src = MemorySource(stats={"2019-20": []},
                       calendar={"2019-20": (date(2019, 10, 22), date(2020, 3, 11)),
                                 "2020-21": (date(2020, 12, 22), date(2021, 5, 16))})
    assert AsOfReader(src, "2020-21").season_stats("2019-20") == []          # knowable 2020-10-12 <= cutoff
    early = MemorySource(stats={"2019-20": []},
                         calendar={"2019-20": (date(2019, 10, 22), date(2020, 3, 11)),
                                   "2020-21": (date(2020, 10, 1), date(2021, 5, 16))})
    with pytest.raises(LeakageError):                                            # cutoff earlier than the override date
        AsOfReader(early, "2020-21").season_stats("2019-20")


def test_listing_exposes_static_columns_only_never_season_outcomes():
    listing = AsOfReader(make_source(), T).listing()
    assert set(listing[1]) <= {"PLAYER_ID", "AGE", "PLAYER_HEIGHT_INCHES", "PLAYER_WEIGHT", "COLLEGE",
                               "COUNTRY", "DRAFT_YEAR", "DRAFT_ROUND", "DRAFT_NUMBER"}
    assert "GP" not in listing[1] and "PTS" not in listing[1]


def test_draft_facts_include_the_target_years_draft_but_not_later_ones():
    facts = AsOfReader(make_source(), T).draft_facts()
    assert 9 in facts and 99 not in facts                                        # 2021 draft yes, 2022 draft no


def test_roster_proxy_reveals_identity_only_and_early_variant_drops_late_arrivals():
    r = AsOfReader(make_source(), T)
    assert r.preseason_roster_proxy("early", 10) == {1: 10, 2: 11}                # player 3 first appears in game 40
    assert r.preseason_roster_proxy("all") == {1: 10, 2: 11, 3: 10}
    with pytest.raises(ValueError):
        r.preseason_roster_proxy("bogus")


def test_forbidden_inputs_raise():
    r = AsOfReader(make_source(), T)
    for name in ("players.position", "players.team_id", "directory.roster_status"):
        with pytest.raises(LeakageError):
            r.read_forbidden(name)


def test_manifest_records_fidelity_classes_and_passes_the_audit():
    r = AsOfReader(make_source(), T)
    r.season_stats(P1)
    r.team_stats(P1)
    r.listing()
    r.preseason_roster_proxy()
    m = r.manifest()
    assert r.audit() == []
    assert set(m["non_exact_classes"]) == {REVISION_RISK, PROXY}
    kinds = {i["kind"]: i["fidelity"] for i in m["inputs"]}
    assert kinds["team_stats"] == EXACT and kinds["season_stats"] == REVISION_RISK
    r2 = AsOfReader(make_source(), T)
    r2.season_stats(P1, include_tracking_defense=False)
    assert r2.manifest()["non_exact_classes"] == []                                         # box-score-only run: every input is exact


def test_tracking_columns_can_be_dropped_for_the_sensitivity_run():
    rows = AsOfReader(make_source(), T).season_stats(P1, include_tracking_defense=False)
    assert "d_fga" not in rows[0] and "ra_fga" not in rows[0] and "ft_pct" in rows[0]


# ------------------------------------------------- the metamorphic canary (gate A1) incl. negatives

def test_clean_predictor_passes_the_canary():
    res = run_canary(v1_carry_forward, make_source(), T)
    assert res.identical, res


def test_garbled_source_really_changes_season_t_outcomes_but_not_identity():
    g = GarbledSource(make_source(), T)
    real, bad = make_source().season_stats(T), g.season_stats(T)
    assert [r["player_id"] for r in real] == [r["player_id"] for r in bad]
    assert real != bad and g.season_stats(P1) == make_source().season_stats(P1)  # earlier seasons untouched


def test_predictor_reading_the_target_season_is_caught():
    def leaky(reader):
        return {r["player_id"]: {"mpg": r["minutes_per_game"]} for r in reader.season_stats(reader.target)}
    res = run_canary(leaky, make_source(), T)
    assert not res.identical and "guard" in res.error


def test_leak_that_evades_the_guard_is_still_caught_by_garbling():
    """A predictor that switches the guard off and reads season-T outcomes: the real run gives it real
    numbers, the garbled run gives it garbage, so the two outputs differ and the canary flags it."""
    def sneaky(reader):
        reader._guard = False
        return {r["player_id"]: {"mpg": r["minutes_per_game"]} for r in reader.season_stats(reader.target)}
    res = run_canary(sneaky, make_source(), T)
    assert not res.identical and res.error == "outputs differ under garbling"


def test_predictor_using_forbidden_input_is_caught():
    def uses_position(reader):
        reader.read_forbidden("players.position")
        return {}
    assert not run_canary(uses_position, make_source(), T).identical


# --------------------------------------------- predictors never get a database (architecture rule)

def test_predictor_modules_do_not_import_database_or_sources():
    path = os.path.join(os.path.dirname(__file__), "..", "validation", "ratings", "predictors.py")
    imported = set()
    for node in ast.walk(ast.parse(open(path).read())):
        if isinstance(node, ast.Import):
            imported |= {n.name for n in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    banned = {"sqlalchemy", "app.database", "validation.ratings.asof_db", "psycopg2", "requests"}
    assert not any(i == b or i.startswith(b + ".") for i in imported for b in banned), imported
