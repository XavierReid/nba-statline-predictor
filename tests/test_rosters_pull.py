from app.ingestion import rosters as r


def _row(pid, team, two_way=0, bd="JAN 02, 2000", h="6-8", w="210"):
    return {"PLAYER_ID": pid, "TeamID": team, "SUPPLEMENTAL_STATUS": two_way,
            "BIRTH_DATE": bd, "HEIGHT": h, "WEIGHT": w, "POSITION": "F"}


def test_status_and_height():
    assert r._status(_row(1, 1, two_way=1)) == "two_way"
    assert r._status(_row(1, 1)) == "standard"
    assert r._height_in("6-8") == 80.0 and r._height_in(None) is None


def test_validate_flags_duplicate_missing_bio_and_feed_disagreement():
    rosters = [_row(1, 10), _row(1, 11), _row(2, 10, w=None)]
    index = [{"PERSON_ID": 2, "TEAM_ID": 99}]
    problems = " | ".join(r.validate(rosters, index))
    assert "expected 30 teams" in problems
    assert "player 1 on 2 teams" in problems
    assert "player 2 missing bio fields" in problems
    assert "feeds disagree on player 2" in problems


def test_first_season_from_experience():
    assert r._first_season("2026-27", "R") == "2026-27"
    assert r._first_season("2026-27", "9") == "2017-18"
