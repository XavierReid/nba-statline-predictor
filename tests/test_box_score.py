"""FGA accounting in apply_event — a foul-negated shot attempt is NOT a FGA."""
from app.services.box_score import apply_event, empty_stats


PID = 1
DEF_PID = 2


def _box_with(pid=PID):
    return {pid: empty_stats()}


def _event(**overrides):
    base = {
        "scorer": PID, "shot_type": "mid", "made": False,
        "turnover_by": None, "assisted_by": None, "rebounded_by": None,
        "steal_by": None, "block_by": None, "fouled_by": None,
        "nonshooting_foul_by": None, "fta": 0, "ftm": 0,
    }
    base.update(overrides)
    return base


def test_missed_shot_with_shooting_foul_does_not_count_as_fga():
    box = _box_with()
    apply_event(box, _event(shot_type="mid", made=False, fouled_by=DEF_PID, fta=2, ftm=1))
    s = box[PID]
    assert s["fga"] == 0
    assert s["fgm"] == 0
    assert s["fta"] == 2
    assert s["ftm"] == 1
    assert s["pts"] == 1


def test_missed_three_with_shooting_foul_does_not_count_as_fga_or_fg3a():
    box = _box_with()
    apply_event(box, _event(shot_type="three", made=False, fouled_by=DEF_PID, fta=3, ftm=2))
    s = box[PID]
    assert s["fga"] == 0
    assert s["fg3a"] == 0
    assert s["fta"] == 3
    assert s["ftm"] == 2
    assert s["pts"] == 2


def test_and_one_still_counts_as_fga():
    box = _box_with()
    apply_event(box, _event(shot_type="mid", made=True, fouled_by=DEF_PID, fta=1, ftm=1))
    s = box[PID]
    assert s["fga"] == 1
    assert s["fgm"] == 1
    assert s["fta"] == 1
    assert s["ftm"] == 1
    assert s["pts"] == 3


def test_and_one_three_still_counts_as_fg3a():
    box = _box_with()
    apply_event(box, _event(shot_type="three", made=True, fouled_by=DEF_PID, fta=1, ftm=1))
    s = box[PID]
    assert s["fga"] == 1
    assert s["fg3a"] == 1
    assert s["fgm"] == 1
    assert s["fg3m"] == 1
    assert s["pts"] == 4


def test_clean_miss_still_counts_as_fga():
    box = _box_with()
    apply_event(box, _event(shot_type="mid", made=False, fta=0, ftm=0))
    s = box[PID]
    assert s["fga"] == 1
    assert s["fgm"] == 0
    assert s["pts"] == 0


def test_clean_make_counts_as_fga():
    box = _box_with()
    apply_event(box, _event(shot_type="mid", made=True, fta=0, ftm=0))
    s = box[PID]
    assert s["fga"] == 1
    assert s["fgm"] == 1
    assert s["pts"] == 2


def test_bonus_foul_shot_type_none_no_fga():
    box = _box_with()
    apply_event(box, _event(shot_type=None, made=False, fouled_by=DEF_PID, fta=2, ftm=2))
    s = box[PID]
    assert s["fga"] == 0
    assert s["fta"] == 2
    assert s["ftm"] == 2
    assert s["pts"] == 2
