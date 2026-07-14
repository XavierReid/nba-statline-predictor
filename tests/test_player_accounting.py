"""Player possession accounting — decomposition identities (3.4)."""
from app.analysis.player_accounting import PlayerAccount, points_waterfall, assists_waterfall


def _acct(**kw):
    base = dict(player_id=1, name="P", team_id=1, tier="star", minutes=32.0,
                pts=25.0, reb=5.0, ast=6.0, tov=3.0, fga=18.0, fgm=9.0, fg3m=3.0,
                fta=5.0, ftm=4.0, usage=20.0, usage_share=0.30, ast_share=0.30)
    base.update(kw)
    return PlayerAccount(**base)


def test_points_waterfall_sums_to_delta():
    real = _acct(pts=25.0, fga=18.0, fgm=9.0, fg3m=3.0, ftm=4.0, minutes=33.0)
    sim = _acct(pts=19.0, fga=13.0, fgm=6.0, fg3m=2.0, ftm=3.0, minutes=32.0)
    w = points_waterfall(real, sim)
    assert abs(w["minutes"] + w["usage"] + w["efficiency"] + w["ft"] - w["total"]) < 1e-6
    # total equals the real point difference the box scores imply
    assert abs(w["total"] - (sim.fg_pts + sim.ftm - real.fg_pts - real.ftm)) < 1e-6


def test_assists_waterfall_sums_to_delta():
    real = _acct(ast_share=0.30)
    sim = _acct(ast_share=0.22)
    w = assists_waterfall(real, sim, team_fgm_real=40.0, team_fgm_sim=39.0,
                          team_ast_real=24.0, team_ast_sim=22.0)
    assert abs(w["team_makes"] + w["attribution"] + w["ball_handler"] - w["total"]) < 1e-6


def test_usage_share_moves_points():
    # holding all else equal, a lower usage (fewer FGA) drives the usage term negative
    real = _acct(fga=18.0, fgm=9.0, fg3m=3.0)
    sim = _acct(fga=12.0, fgm=6.0, fg3m=2.0)
    w = points_waterfall(real, sim)
    assert w["usage"] < 0
