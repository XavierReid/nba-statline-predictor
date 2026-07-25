"""Unit tests for possession_to_events — one test per canonical ordering row in
RFC.md "Event-Sourced PBP"."""
from app.services.possession_events import possession_to_events


HEADER = dict(possession=1, quarter=1, game_clock_seconds=720, is_home=True)

SHOOTER = 100
DEF = 200
ASSISTER = 101
BLOCKER = 201
REBOUNDER = 202
STEALER = 203
FOULED_ON = 102


def _base_result(**overrides):
    base = {
        "scorer": None, "shot_type": None, "sub_type": None, "made": False,
        "assisted_by": None, "rebounded_by": None, "is_oreb": False,
        "turnover_by": None, "steal_by": None, "block_by": None,
        "fouled_by": None, "fta": 0, "ftm": 0, "ft_makes": [],
        "nonshooting_foul_by": None, "nonshooting_foul_on": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Shots
# ---------------------------------------------------------------------------

def test_clean_made_two_no_assist():
    r = _base_result(scorer=SHOOTER, shot_type="mid", sub_type="mid_range", made=True)
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["SHOT"]
    e = events[0]
    assert e["player_id"] == SHOOTER
    assert e["pts"] == 2
    assert e["made"] is True
    assert e["shot_type"] == "mid"


def test_clean_made_three_with_assist():
    r = _base_result(scorer=SHOOTER, shot_type="three", sub_type="above_break_three",
                     made=True, assisted_by=ASSISTER)
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["SHOT", "AST"]
    assert events[0]["pts"] == 3
    assert events[1]["player_id"] == ASSISTER


def test_clean_missed_two_with_dreb():
    r = _base_result(scorer=SHOOTER, shot_type="mid", sub_type="mid_range", made=False,
                     rebounded_by=REBOUNDER, is_oreb=False)
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["SHOT", "REB"]
    assert events[0]["made"] is False
    assert events[0]["pts"] == 0
    assert events[1]["player_id"] == REBOUNDER
    assert events[1]["is_oreb"] is False


def test_clean_missed_with_block():
    r = _base_result(scorer=SHOOTER, shot_type="close", sub_type="layup", made=False,
                     block_by=BLOCKER, rebounded_by=REBOUNDER, is_oreb=False)
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["SHOT", "BLK", "REB"]
    assert events[1]["player_id"] == BLOCKER


def test_missed_with_oreb():
    r = _base_result(scorer=SHOOTER, shot_type="close", sub_type="layup", made=False,
                     rebounded_by=REBOUNDER, is_oreb=True)
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["SHOT", "REB"]
    assert events[1]["is_oreb"] is True


# ---------------------------------------------------------------------------
# Fouls + FTs
# ---------------------------------------------------------------------------

def test_and_one_two_pointer():
    r = _base_result(scorer=SHOOTER, shot_type="close", sub_type="layup", made=True,
                     fouled_by=DEF, fta=1, ftm=1, ft_makes=[True])
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["SHOT", "FOUL", "FT"]
    assert events[0]["pts"] == 2
    assert events[1]["foul_kind"] == "shooting"
    assert events[1]["fouled_on"] == SHOOTER
    assert events[2]["attempt"] == 1
    assert events[2]["of"] == 1
    assert events[2]["made"] is True
    assert events[2]["pts"] == 1


def test_and_one_three_pointer():
    r = _base_result(scorer=SHOOTER, shot_type="three", sub_type="corner_three",
                     made=True, fouled_by=DEF, fta=1, ftm=0, ft_makes=[False])
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["SHOT", "FOUL", "FT"]
    assert events[0]["pts"] == 3
    assert events[2]["made"] is False
    assert events[2]["pts"] == 0


def test_foul_drawn_miss_two_shot_trip_no_shot_event():
    # PR #8 at the event layer: no SHOT event, no FGA.
    r = _base_result(scorer=SHOOTER, shot_type="mid", sub_type="mid_range", made=False,
                     fouled_by=DEF, fta=2, ftm=1, ft_makes=[True, False])
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["FOUL", "FT", "FT"]
    assert events[0]["foul_kind"] == "shooting"
    assert events[0]["player_id"] == DEF
    assert events[0]["fouled_on"] == SHOOTER
    assert events[1]["attempt"] == 1 and events[1]["of"] == 2 and events[1]["made"] is True
    assert events[2]["attempt"] == 2 and events[2]["of"] == 2 and events[2]["made"] is False


def test_foul_drawn_miss_on_three_gives_three_fts():
    r = _base_result(scorer=SHOOTER, shot_type="three", sub_type="above_break_three",
                     made=False, fouled_by=DEF, fta=3, ftm=2,
                     ft_makes=[True, True, False])
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["FOUL", "FT", "FT", "FT"]
    assert events[3]["made"] is False
    total_ft_pts = sum(e["pts"] for e in events if e["type"] == "FT")
    assert total_ft_pts == 2


def test_bonus_ft_from_non_shooting_foul():
    # Non-shooting foul in the bonus: no shot_type, scorer=fouled_on player, fta>0.
    r = _base_result(scorer=FOULED_ON, fouled_by=DEF, fta=2, ftm=2, ft_makes=[True, True])
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["FOUL", "FT", "FT"]
    assert events[0]["foul_kind"] == "non_shooting"
    assert events[0]["fouled_on"] == FOULED_ON
    assert events[0]["player_id"] == DEF
    assert all(e["player_id"] == FOULED_ON for e in events[1:])


def test_non_shooting_foul_no_bonus_terminal():
    r = _base_result(nonshooting_foul_by=DEF, nonshooting_foul_on=SHOOTER)
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["FOUL"]
    assert events[0]["foul_kind"] == "non_shooting"
    assert events[0]["player_id"] == DEF
    assert events[0]["fouled_on"] == SHOOTER


# ---------------------------------------------------------------------------
# Turnovers
# ---------------------------------------------------------------------------

def test_unforced_turnover():
    r = _base_result(turnover_by=SHOOTER)
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["TOV"]
    assert events[0]["player_id"] == SHOOTER


def test_turnover_with_steal():
    r = _base_result(turnover_by=SHOOTER, steal_by=STEALER)
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["TOV", "STL"]
    assert events[1]["player_id"] == STEALER


def test_offensive_foul_is_tov_plus_foul_same_player():
    r = _base_result(turnover_by=SHOOTER, fouled_by=SHOOTER)
    events = possession_to_events(r, **HEADER)
    assert [e["type"] for e in events] == ["TOV", "FOUL"]
    assert events[0]["player_id"] == SHOOTER
    assert events[1]["player_id"] == SHOOTER
    assert events[1]["foul_kind"] == "offensive"


# ---------------------------------------------------------------------------
# Header + edge cases
# ---------------------------------------------------------------------------

def test_header_fields_propagate_to_every_event():
    r = _base_result(scorer=SHOOTER, shot_type="mid", sub_type="mid_range", made=True,
                     assisted_by=ASSISTER)
    header = dict(possession=42, quarter=3, game_clock_seconds=15, is_home=False)
    events = possession_to_events(r, **header)
    for e in events:
        assert e["possession"] == 42
        assert e["quarter"] == 3
        assert e["game_clock_seconds"] == 15
        assert e["is_home"] is False


def test_ft_makes_fallback_when_missing_uses_makes_first_order():
    # Legacy result without ft_makes list — translator should reconstruct plausibly
    # so unit tests can synthesize old-shape results without breaking.
    r = _base_result(scorer=SHOOTER, fouled_by=DEF, fta=3, ftm=2)
    r.pop("ft_makes")  # remove entirely
    events = possession_to_events(r, **HEADER)
    ft_events = [e for e in events if e["type"] == "FT"]
    assert len(ft_events) == 3
    assert sum(e["made"] for e in ft_events) == 2
