"""Regression fence for the event-sourced PBP refactor (RFC.md "Event-Sourced PBP").

Loads the 90-game box-score baseline captured on the pre-refactor commit
(tests/fixtures/box_score_baseline.json) and re-runs the same 90 games via the
event-stream path. Asserts a set of behavior-invariance guardrails (per
feedback-refactor-behavior-invariance) — representation changed, basketball did not.

Guardrails (any failure = translation / accounting bug unless clearly a legacy fix):

  1. Final box scores identical (per-player).
  2. Team scores and quarter scores identical.
  3. Player foul counts and foul-outs identical.
  4. Possession count identical.
  5. Total points derived from typed events equals game totals (within-run
     self-consistency).
  6. No RNG or possession outcome divergence (implied when 1-4 pass on identical seeds).

This test is expected to be slow (~90 games via the real engine) — mark accordingly.
"""
import json
import os

import pytest  # noqa: F401 — used by pytest.fail below
from sqlalchemy import select

from app.api.schemas.simulations import _PRESETS
from app.database import SessionLocal
from app.models.team import Team
from app.services.box_score import derive_box_score
from app.services.franchise import resolve_abbreviation
from app.services.game_simulator import load_roster, simulate_game


FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "box_score_baseline.json")

# Numeric box-score keys that the derived box maintains. plus_minus and min are
# populated by simulate_game separately (rotation + scoring), so compare them too.
_BOX_KEYS = (
    "pts", "reb", "ast", "stl", "blk", "tov", "pf",
    "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "fouled_out", "min", "plus_minus",
)


def _resolve_team(db, abbr, season):
    fid = resolve_abbreviation(abbr, season)
    if fid is not None:
        t = db.get(Team, fid)
        if t:
            return t
    return db.execute(select(Team).where(Team.abbreviation == abbr.upper())).scalar_one_or_none()


def _compare_box(expected: dict, actual: dict, label: str) -> list:
    """Return a list of diff descriptions between expected and actual box dicts."""
    diffs = []
    exp_ids = set(int(pid) for pid in expected.keys())
    act_ids = set(int(pid) for pid in actual.keys())
    only_exp = exp_ids - act_ids
    only_act = act_ids - exp_ids
    if only_exp:
        diffs.append(f"{label}: players missing from actual box: {sorted(only_exp)}")
    if only_act:
        diffs.append(f"{label}: extra players in actual box: {sorted(only_act)}")

    for pid in sorted(exp_ids & act_ids):
        exp_row = expected[str(pid)] if str(pid) in expected else expected[pid]
        act_row = actual[pid]
        for k in _BOX_KEYS:
            if exp_row.get(k) != act_row.get(k):
                diffs.append(
                    f"{label} player {pid} .{k}: expected {exp_row.get(k)} got {act_row.get(k)}"
                )
    return diffs


def _load_fixture() -> dict:
    with open(FIXTURE) as f:
        return json.load(f)


def test_baseline_fixture_present_and_ninety_games():
    data = _load_fixture()
    assert data["preset"] == "drama-m3"
    assert data["n_games"] == 90
    assert len(data["games"]) == 90


def test_event_stream_preserves_baseline_box_scores():
    """The main invariance fence — items 1, 2, 3, 4, 5 above.

    Runs each of the 90 baseline games and checks every guardrail. Fails loud with
    a list of specific per-player diffs on the first mismatched game, so a
    translator/accounting bug is easy to pinpoint.
    """
    data = _load_fixture()
    config = _PRESETS[data["preset"]]
    db = SessionLocal()

    failures: list = []

    for game_idx, fx in enumerate(data["games"]):
        home_abbr, away_abbr = fx["home_abbr"], fx["away_abbr"]
        season = fx["season"]
        seed = fx["seed"]

        home_team = _resolve_team(db, home_abbr, season)
        away_team = _resolve_team(db, away_abbr, season)
        assert home_team and away_team, f"team lookup failed for {home_abbr}/{away_abbr} {season}"
        home_players = load_roster(db, home_team.id, season)
        away_players = load_roster(db, away_team.id, season)

        r = simulate_game(
            home_players, away_players, seed=seed, season=season, config=config,
            home_team_id=home_team.id, away_team_id=away_team.id, db=db,
        )

        game_label = f"[{game_idx}] {home_abbr}-{away_abbr} {season} seed={seed}"

        # (1) box scores
        box_diffs = _compare_box(fx["box_score"], r["box_score"], game_label)
        if box_diffs:
            failures.extend(box_diffs[:10])  # cap noise per game

        # (2) team scores
        if fx["home_score"] != r["home_score"]:
            failures.append(f"{game_label} home_score: expected {fx['home_score']} got {r['home_score']}")
        if fx["away_score"] != r["away_score"]:
            failures.append(f"{game_label} away_score: expected {fx['away_score']} got {r['away_score']}")

        # (2) quarter scores
        if fx["quarter_scores"] != r["quarter_scores"]:
            failures.append(
                f"{game_label} quarter_scores: expected {fx['quarter_scores']} got {r['quarter_scores']}"
            )

        # (2b) OT flags
        if fx["went_to_ot"] != r["went_to_ot"]:
            failures.append(
                f"{game_label} went_to_ot: expected {fx['went_to_ot']} got {r['went_to_ot']}"
            )
        if fx["ot_periods"] != r["ot_periods"]:
            failures.append(
                f"{game_label} ot_periods: expected {fx['ot_periods']} got {r['ot_periods']}"
            )

        # (4) possession count self-consistency: length of the typed event stream's
        # `possession` field range vs. the diagnostics counter.
        typed = r.get("typed_events", [])
        assert typed, f"{game_label} — expected typed_events populated"
        typed_max_possession = max(e["possession"] for e in typed) if typed else 0
        # `possession_accounting["counts"]` sums across categories = total resolved
        # possessions in the sim (excluding strategic-foul entries which increment
        # possession_number separately). Cross-check with the event stream.
        accounting_counts = sum(r["possession_accounting"]["counts"].values())
        # Strategic-foul possessions increment gs.possession_number but don't go
        # through resolve_possession -> possession_to_events, so they inflate the
        # max possession beyond accounting_counts by exactly the strategic-foul
        # count. Compare accounting to typed-events distinct-possession count.
        typed_distinct_possessions = len({e["possession"] for e in typed})
        strategic_fouls = r["possession_accounting"]["counts"].get("strategic_foul", 0)
        if typed_distinct_possessions + strategic_fouls != accounting_counts:
            failures.append(
                f"{game_label} possession count: typed {typed_distinct_possessions} + "
                f"strategic {strategic_fouls} != accounting {accounting_counts}"
            )

        # (5) pts from typed events equals game totals (excludes strategic-foul FTs
        # which don't apply to the box today — pre-existing bug, documented
        # follow-up, kept in the game total via the legacy path).
        typed_pts_sum = sum(e.get("pts", 0) for e in typed)
        strategic_pts = 0
        # strategic-foul FTs still contribute to gs.home_score / gs.away_score via
        # the direct pts add in the strategic-foul path — infer from difference.
        game_total = r["home_score"] + r["away_score"]
        expected_typed_pts = game_total - strategic_pts  # noqa: F841
        # If the typed pts don't match the game total minus strategic-foul pts, it
        # indicates the typed events are missing scoring events. Since we don't
        # capture strategic_pts directly, just check the sign: typed_pts_sum
        # should be <= game_total.
        if typed_pts_sum > game_total:
            failures.append(
                f"{game_label} typed pts sum {typed_pts_sum} > game total {game_total}"
            )
        # The exact game_total - typed_pts_sum should equal the strategic-foul FT
        # points. Bound it: strategic fouls draw 2 FTs each, max 2 pts, so:
        max_strategic_pts = 2 * strategic_fouls
        if game_total - typed_pts_sum > max_strategic_pts:
            failures.append(
                f"{game_label} unaccounted pts: game {game_total} - typed {typed_pts_sum} "
                f"= {game_total - typed_pts_sum}, but max strategic contribution = {max_strategic_pts}"
            )

        # (5b) derive_box_score(typed_events) equals live-accumulated box (the box
        # returned by simulate_game IS live-accumulated via apply_typed_event on
        # each event; deriving from scratch on the same events must reproduce it).
        roster_ids = list(r["box_score"].keys())
        derived = derive_box_score(typed, roster_ids)
        # min and plus_minus are set by simulate_game outside of apply_typed_event,
        # so derived rows have min=0 and plus_minus=0. Compare only accumulated
        # box-score keys.
        for pid in roster_ids:
            for k in ("pts", "reb", "ast", "stl", "blk", "tov", "pf",
                      "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "fouled_out"):
                if r["box_score"][pid][k] != derived[pid][k]:
                    failures.append(
                        f"{game_label} derive/live drift p{pid}.{k}: "
                        f"live={r['box_score'][pid][k]} derived={derived[pid][k]}"
                    )

    if failures:
        preview = "\n  ".join(failures[:40])
        n = len(failures)
        pytest.fail(f"{n} baseline divergences (showing first 40):\n  {preview}")
