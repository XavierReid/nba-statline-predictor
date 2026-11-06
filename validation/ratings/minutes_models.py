"""Minutes-per-game projection models (rating protocol V5 ingredient). Reader-only: no database imports.

M0  last season's MPG (the V1 carry-forward placeholder)
M1  a + b * last season's MPG                          (pull toward the middle)
M2  a + b * blend + c * changed_team                    (two-year blend when games were missed; team-change shift)

`blend` = games-weighted MPG over the last two seasons with the older season down-weighted by a FIXED 0.5
(a generic constant stated up front, not tuned). Constants a, b, c are fitted by least squares on development
targets earlier than the target season only.
"""
from typing import Dict, List

import numpy as np

from validation.ratings.asof import AsOfReader, prev_season, season_start_year

EARLIEST_SEASON = 2013      # first season in the backfill

OLD_YEAR_WEIGHT = 0.5
MODELS = ("M0", "M1", "M2")


def feature_rows(reader: AsOfReader) -> Dict[int, dict]:
    t = reader.target
    prior = {r["player_id"]: r for r in reader.season_stats(prev_season(t))}
    older_season = prev_season(t, 2)
    older = ({r["player_id"]: r for r in reader.season_stats(older_season)}
             if season_start_year(older_season) >= EARLIEST_SEASON else {})
    proxy = reader.preseason_roster_proxy("early", 10)
    out = {}
    for pid, team in proxy.items():
        r = prior.get(pid)
        if not r or not r["minutes_per_game"]:
            continue
        g1, m1 = r["games_played"] or 0, r["minutes_per_game"]
        o = older.get(pid)
        g2, m2 = ((o["games_played"] or 0), (o["minutes_per_game"] or 0.0)) if o else (0, 0.0)
        w2 = OLD_YEAR_WEIGHT * g2
        blend = (g1 * m1 + w2 * m2) / (g1 + w2) if g1 + w2 > 0 else m1
        out[pid] = {"m0": m1, "blend": blend, "changed": float(r["team_id"] != team), "team_id": team, "gp": g1}
    return out


def design(model: str, f: dict) -> List[float]:
    if model == "M1":
        return [1.0, f["m0"]]
    if model == "M2":
        return [1.0, f["blend"], f["changed"]]
    raise ValueError(model)


def fit(model: str, rows: List[dict], y: List[float]) -> List[float]:
    X = np.array([design(model, f) for f in rows])
    return list(np.linalg.lstsq(X, np.array(y), rcond=None)[0])


def predict(model: str, coef, f: dict) -> float:
    if model == "M0":
        return f["m0"]
    return float(np.dot(coef, design(model, f)))
