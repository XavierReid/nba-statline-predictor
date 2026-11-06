"""Fit the M3 bucket means on EARLIER development seasons (evaluation side; may read outcomes of seasons < target).

    priors(src, target) -> {"R-A": {"mpg", "gp", "n"}, ..., "ALL": {...}}
"""
from validation.ratings.asof import AsOfReader
from validation.ratings.predictors import v1_carry_forward
from validation.ratings.rookie_minutes import BUCKETS, unseen_players

DEV = ["2014-15", "2015-16", "2016-17", "2017-18", "2018-19", "2019-20", "2020-21", "2021-22"]


def priors(src, target: str):
    acc = {b: [] for b in BUCKETS + ("ALL",)}
    for t in [s for s in DEV if s < target]:
        reader = AsOfReader(src, t)
        have = {pid for pid, p in v1_carry_forward(reader).items() if p["mpg"]}
        actual = {r["player_id"]: r for r in src.season_stats(t)}
        for pid, u in unseen_players(reader, have).items():
            r = actual.get(pid)
            if r and r["minutes_per_game"] is not None:
                acc[u["bucket"]].append((r["minutes_per_game"], r["games_played"] or 0))
                acc["ALL"].append((r["minutes_per_game"], r["games_played"] or 0))
    return {b: {"mpg": sum(m for m, _ in v) / len(v), "gp": sum(g for _, g in v) / len(v), "n": len(v)}
            for b, v in acc.items() if v}
