"""Unit tests for derive_box_score / apply_typed_event.

Covers per-type accumulation and the composition property: apply_typed_event ==
derive_box_score(one event at a time). The full behavior-invariance fence lives
in test_box_score_derivation_fixture.py (loads the 90-game baseline)."""
from app.services.box_score import (
    apply_typed_event,
    derive_box_score,
    empty_stats,
    foul_outs_from_events,
)
from app.services.possession_events import possession_to_events


ROSTER = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
HDR = dict(possession=1, quarter=1, game_clock_seconds=720, is_home=True)


def _ev(**kw):
    """Build a typed event with the standard header + any overrides."""
    return {**HDR, **kw}


# ---------------------------------------------------------------------------
# Per-type accumulation
# ---------------------------------------------------------------------------

def test_made_two_shot_adds_fga_fgm_pts_two():
    box = derive_box_score([
        _ev(type="SHOT", player_id=1, shot_type="mid", made=True, pts=2),
    ], ROSTER)
    assert box[1]["fga"] == 1
    assert box[1]["fgm"] == 1
    assert box[1]["fg3a"] == 0
    assert box[1]["pts"] == 2


def test_made_three_adds_fg3a_fg3m_pts_three():
    box = derive_box_score([
        _ev(type="SHOT", player_id=1, shot_type="three", made=True, pts=3),
    ], ROSTER)
    assert box[1]["fga"] == 1
    assert box[1]["fgm"] == 1
    assert box[1]["fg3a"] == 1
    assert box[1]["fg3m"] == 1
    assert box[1]["pts"] == 3


def test_missed_shot_adds_fga_only():
    box = derive_box_score([
        _ev(type="SHOT", player_id=1, shot_type="close", made=False, pts=0),
    ], ROSTER)
    assert box[1]["fga"] == 1
    assert box[1]["fgm"] == 0
    assert box[1]["pts"] == 0


def test_ft_made_adds_fta_ftm_pts_one():
    box = derive_box_score([
        _ev(type="FT", player_id=1, attempt=1, of=2, made=True, pts=1),
    ], ROSTER)
    assert box[1]["fta"] == 1
    assert box[1]["ftm"] == 1
    assert box[1]["pts"] == 1


def test_ft_missed_adds_fta_only():
    box = derive_box_score([
        _ev(type="FT", player_id=1, attempt=1, of=1, made=False, pts=0),
    ], ROSTER)
    assert box[1]["fta"] == 1
    assert box[1]["ftm"] == 0


def test_foul_adds_pf():
    box = derive_box_score([
        _ev(type="FOUL", player_id=2, foul_kind="shooting", fouled_on=1),
    ], ROSTER)
    assert box[2]["pf"] == 1
    assert box[2]["fouled_out"] is False


def test_reb_tov_stl_blk_ast():
    events = [
        _ev(type="REB", player_id=1, is_oreb=False),
        _ev(type="TOV", player_id=2),
        _ev(type="STL", player_id=3),
        _ev(type="BLK", player_id=4),
        _ev(type="AST", player_id=5),
    ]
    box = derive_box_score(events, ROSTER)
    assert box[1]["reb"] == 1
    assert box[2]["tov"] == 1
    assert box[3]["stl"] == 1
    assert box[4]["blk"] == 1
    assert box[5]["ast"] == 1


# ---------------------------------------------------------------------------
# Foul-out mechanics
# ---------------------------------------------------------------------------

def test_sixth_foul_triggers_fouled_out():
    events = [_ev(type="FOUL", player_id=2, foul_kind="shooting", fouled_on=1)
              for _ in range(6)]
    box = derive_box_score(events, ROSTER)
    assert box[2]["pf"] == 6
    assert box[2]["fouled_out"] is True


def test_further_fouls_after_out_do_not_increment_pf():
    events = [_ev(type="FOUL", player_id=2, foul_kind="shooting", fouled_on=1)
              for _ in range(8)]
    box = derive_box_score(events, ROSTER)
    assert box[2]["pf"] == 6
    assert box[2]["fouled_out"] is True


def test_foul_outs_reported_in_order():
    events = (
        [_ev(type="FOUL", player_id=2, foul_kind="shooting", fouled_on=1)] * 6 +
        [_ev(type="FOUL", player_id=3, foul_kind="shooting", fouled_on=1)] * 6
    )
    outs = foul_outs_from_events(events, ROSTER)
    assert outs == [2, 3]


def test_apply_typed_event_returns_fouled_out_pid_on_sixth():
    box = {pid: empty_stats() for pid in ROSTER}
    for _ in range(5):
        pts, out = apply_typed_event(box, _ev(type="FOUL", player_id=2, fouled_on=1))
        assert out is None
    pts, out = apply_typed_event(box, _ev(type="FOUL", player_id=2, fouled_on=1))
    assert out == 2


# ---------------------------------------------------------------------------
# Composition: apply_typed_event step-by-step == derive_box_score all-at-once
# ---------------------------------------------------------------------------

def test_incremental_matches_all_at_once():
    events = [
        _ev(type="SHOT", player_id=1, shot_type="three", made=True, pts=3),
        _ev(type="AST", player_id=2),
        _ev(type="SHOT", player_id=3, shot_type="close", made=False, pts=0),
        _ev(type="BLK", player_id=4),
        _ev(type="REB", player_id=5, is_oreb=False),
        _ev(type="TOV", player_id=1),
        _ev(type="STL", player_id=2),
        _ev(type="FOUL", player_id=3, foul_kind="offensive", fouled_on=None),
        _ev(type="FOUL", player_id=4, foul_kind="shooting", fouled_on=1),
        _ev(type="FT", player_id=1, attempt=1, of=2, made=True, pts=1),
        _ev(type="FT", player_id=1, attempt=2, of=2, made=False, pts=0),
    ]
    incremental = {pid: empty_stats() for pid in ROSTER}
    for e in events:
        apply_typed_event(incremental, e)
    all_at_once = derive_box_score(events, ROSTER)
    assert incremental == all_at_once


# ---------------------------------------------------------------------------
# Round-trip: possession result -> translator -> derive -> matches legacy shape
# ---------------------------------------------------------------------------

def test_and_one_translator_plus_derive_matches_expected_box():
    """And-1 through the event stream: shooter gets FGA/FGM/PTS + FTA/FTM/PTS,
    fouler gets +1 PF."""
    result = {
        "scorer": 1, "shot_type": "close", "sub_type": "layup", "made": True,
        "assisted_by": 2,
        "rebounded_by": None, "is_oreb": False,
        "turnover_by": None, "steal_by": None, "block_by": None,
        "fouled_by": 6, "fta": 1, "ftm": 1, "ft_makes": [True],
        "nonshooting_foul_by": None, "nonshooting_foul_on": None,
    }
    events = possession_to_events(result, **HDR)
    box = derive_box_score(events, ROSTER)
    assert box[1]["fga"] == 1
    assert box[1]["fgm"] == 1
    assert box[1]["fta"] == 1
    assert box[1]["ftm"] == 1
    assert box[1]["pts"] == 3
    assert box[2]["ast"] == 1
    assert box[6]["pf"] == 1


def test_foul_drawn_miss_translator_plus_derive_zero_fga():
    """PR #8 rule at the event layer: foul-drawn miss produces no SHOT event,
    so shooter's FGA stays 0 while FTs still credit."""
    result = {
        "scorer": 1, "shot_type": "mid", "sub_type": "mid_range", "made": False,
        "assisted_by": None, "rebounded_by": None, "is_oreb": False,
        "turnover_by": None, "steal_by": None, "block_by": None,
        "fouled_by": 6, "fta": 2, "ftm": 1, "ft_makes": [True, False],
        "nonshooting_foul_by": None, "nonshooting_foul_on": None,
    }
    events = possession_to_events(result, **HDR)
    box = derive_box_score(events, ROSTER)
    assert box[1]["fga"] == 0
    assert box[1]["fgm"] == 0
    assert box[1]["fta"] == 2
    assert box[1]["ftm"] == 1
    assert box[1]["pts"] == 1
    assert box[6]["pf"] == 1
