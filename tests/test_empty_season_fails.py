"""A season with no loaded players must fail the run, not finish 'complete' with skipped games."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import league_simulator as ls
from app.services import season_simulator as ss

GAMES = [SimpleNamespace(id=1, home_team_id=10, away_team_id=20),
         SimpleNamespace(id=2, home_team_id=20, away_team_id=30)]


def _sim(scope):
    return SimpleNamespace(id=7, scope=scope, team_id=None if scope == "league" else 10,
                           season="2026-27", seed=1, status="running", parameters={})


def _run(module, runner, sim, patches):
    db = MagicMock()
    db.get.return_value = sim
    with patch.object(module, "SessionLocal", return_value=db), \
         patch.object(module, "load_roster", return_value=[]), \
         patch.object(module, "simulate_game") as game, \
         patch.object(module, "_mark_failed") as failed:
        for name, val in patches.items():
            patch.object(module, name, return_value=val).start()
        try:
            runner(7)
        finally:
            patch.stopall()
    return game, failed


def test_league_run_fails_when_no_rosters():
    game, failed = _run(ls, ls.run_league_simulation, _sim("league"),
                        {"validate_season_schedule": SimpleNamespace(ok=True, failures=[]),
                         "_fetch_league_schedule": GAMES})
    game.assert_not_called()
    assert "no players loaded for 2026-27" in failed.call_args[0][2]
    assert "[10, 20, 30]" in failed.call_args[0][2]


def test_season_run_fails_when_no_rosters():
    game, failed = _run(ss, ss.run_season_simulation, _sim("season"), {"_fetch_schedule": GAMES})
    game.assert_not_called()
    assert "no players loaded for 2026-27" in failed.call_args[0][2]
