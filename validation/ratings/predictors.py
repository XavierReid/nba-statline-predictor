"""Reference predictors. They receive an AsOfReader and nothing else (enforced by test: this module
must not import sqlalchemy, app.database or the data sources).

V1 here is the protocol's closed-set baseline "last season's observed raw metric, unshrunk"; it exists
to exercise the harness. It is not a fitted method and makes no accuracy claim.
"""
from typing import Dict

from validation.ratings.asof import AsOfReader, prev_season


def v1_carry_forward(reader: AsOfReader) -> Dict[int, dict]:
    prior = reader.season_stats(prev_season(reader.target))
    out = {}
    for r in prior:
        gp = r["games_played"] or 0
        fta, fg3a = (r["fta"] or 0) * gp, (r["fg3a"] or 0) * gp
        out[r["player_id"]] = {
            "mpg": r["minutes_per_game"], "gp_share": min(1.0, gp / 82.0),
            "ft_pct": r["ft_pct"], "ft_attempts": round(fta, 3),
            "fg3_pct": r["fg3_pct"], "fg3_attempts": round(fg3a, 3),
            "usg_pct": r["usg_pct"], "team_id": r["team_id"],
        }
    return out
