"""Minimum detectable effect for the 4 locked seasons, computed from DEVELOPMENT seasons only (A2.1).

The locked set is not read. The challenger is a deliberately simple stand-in (V1 shrunk toward the prior
season's league mean by volume with a generic constant, i.e. a V2-like method), so this measures how much
the paired relative MAE reduction moves between seasons and between resamples, not how good any real
method is. Rate targets are attempt-weighted (protocol Sec. 3.2). Predictions come through AsOfReader; only the target-season outcomes are read directly
(evaluation side).

    docker compose run --rm -e DATABASE_URL=postgresql+psycopg2://nba:nba@postgres:5432/nba_backfill \\
        api python -m validation.ratings.mde
"""
import json

import numpy as np

from validation.ratings.asof import AsOfReader, prev_season
from validation.ratings.asof_db import DBSource

DEV = [f"{y}-{str(y + 1)[2:]}" for y in range(2014, 2022)]
T_CRIT = {3: 3.182, 7: 2.365}
T_POWER80 = {3: 0.978, 7: 0.896}
# target field, volume field (per-game attempts or games), generic prior strength in units of that volume
TARGETS = {
    "ft_pct": ("fta", 100.0, True),
    "fg3_pct": ("fg3a", 150.0, True),
    "usg_pct": ("games_played", 20.0, False),
    "minutes_per_game": ("games_played", 20.0, False),
}
RNG = np.random.default_rng(20261101)
DRAWS = 2000


def season_errors(src, t, field, vol_field, k, per_game):
    prior = AsOfReader(src, t).season_stats(prev_season(t))
    truth = {r["player_id"]: r for r in src.season_stats(t)
             if (r["games_played"] or 0) >= 20 and (r["minutes_per_game"] or 0) >= 15}
    rows = [r for r in prior if r["player_id"] in truth and r[field] is not None and truth[r["player_id"]][field] is not None]
    vol = lambda r: (r[vol_field] or 0) * ((r["games_played"] or 0) if per_game else 1)
    w = np.array([vol(r) for r in rows])
    x = np.array([r[field] for r in rows], dtype=float)
    mu = float(np.average(x, weights=np.maximum(w, 1e-9)))
    shrunk = (w * x + k * mu) / (w + k)
    y = np.array([truth[r["player_id"]][field] for r in rows], dtype=float)
    pid = np.array([r["player_id"] for r in rows])
    team = np.array([truth[r["player_id"]]["team_id"] for r in rows])
    # protocol Sec. 3.2: rate targets are weighted by the season-T attempt volume (a 0% from a non-shooter
    # carries no information about shooting skill, and unweighted it makes any shrinkage look harmful)
    wt = np.array([(truth[r["player_id"]][vol_field] or 0) * truth[r["player_id"]]["games_played"]
                   if per_game else 1.0 for r in rows])
    return np.abs(x - y) * wt, np.abs(shrunk - y) * wt, pid, team


def pooled_reduction(eb, em):
    return 1.0 - em.sum() / eb.sum()


def cluster_boot(eb, em, labels):
    uniq, inv = np.unique(labels, return_inverse=True)
    sb, sm = np.bincount(inv, eb), np.bincount(inv, em)
    n = len(uniq)
    idx = RNG.integers(0, n, size=(DRAWS, n))
    red = 1.0 - sm[idx].sum(1) / sb[idx].sum(1)
    return float(np.percentile(red, 2.5)), float(np.percentile(red, 97.5))


def main():
    src = DBSource()
    out = {}
    print(f"{'target':18s} {'pooled red':>10s} {'per-season sd':>13s} {'E1 hw':>7s} {'E2 hw':>7s} {'E3 hw(8)':>9s}"
          f" | 4 locked seasons: {'E3 hw':>7s} {'E1/E2 hw*':>10s} {'MDE80':>7s}")
    for name, (vol_field, k, per_game) in TARGETS.items():
        per, eb_all, em_all, pid_all, unit_all = [], [], [], [], []
        for t in DEV:
            eb, em, pid, team = season_errors(src, t, name, vol_field, k, per_game)
            per.append(pooled_reduction(eb, em))
            eb_all.append(eb); em_all.append(em); pid_all.append(pid)
            unit_all.append(np.array([f"{t}:{x}" for x in team]))
        eb, em, pid, unit = map(np.concatenate, (eb_all, em_all, pid_all, unit_all))
        pooled = pooled_reduction(eb, em)
        s = float(np.std(per, ddof=1))
        lo1, hi1 = cluster_boot(eb, em, pid)
        lo2, hi2 = cluster_boot(eb, em, unit)
        hw1, hw2 = (hi1 - lo1) / 2, (hi2 - lo2) / 2
        hw3_dev = T_CRIT[7] * s / np.sqrt(8)
        hw3_lock = T_CRIT[3] * s / 2.0
        scale = np.sqrt(2.0)                               # half the seasons -> roughly sqrt(2) wider resampling intervals
        widest_lock = max(hw3_lock, hw1 * scale, hw2 * scale)
        mde = widest_lock + T_POWER80[3] * s / 2.0
        out[name] = {"per_season_reduction": per, "pooled_reduction": pooled, "per_season_sd": s,
                     "E1_halfwidth_dev": hw1, "E2_halfwidth_dev": hw2, "E3_halfwidth_dev": hw3_dev,
                     "E3_halfwidth_4_locked": hw3_lock, "widest_halfwidth_4_locked_est": widest_lock,
                     "mde_80pct_power_4_locked_est": mde, "players": int(len(np.unique(pid))), "rows": int(len(eb))}
        print(f"{name:18s} {pooled:10.1%} {s:13.1%} {hw1:7.1%} {hw2:7.1%} {hw3_dev:9.1%} |{'':20s}{hw3_lock:7.1%} {max(hw1, hw2) * scale:10.1%} {mde:7.1%}")
        print(f"{'':18s} per-season reductions: " + " ".join(f"{p:+.1%}" for p in per))
    print("\n* dev E1/E2 half-width scaled by sqrt(2) (4 locked seasons vs 8 dev); MDE80 = widest half-width + 0.978*sd/2 (t-power, 3 d.f.)")
    json.dump(out, open("validation/ratings/results/mde.json", "w"), indent=1)


if __name__ == "__main__":
    main()
