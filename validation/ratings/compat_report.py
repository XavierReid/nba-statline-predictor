"""observed-bf1 (uniform re-derivation in the isolated database) vs the attributes production stored.

Read-only on both databases. Writes validation/ratings/results/observed_bf1_vs_production.json.
Documents Amendment 2, A2.6: nothing is overwritten; this only measures how they differ.

    docker compose run --rm -e DATABASE_URL=postgresql+psycopg2://nba:nba@postgres:5432/nba_backfill \\
        api python -m validation.ratings.compat_report
"""
import json

from sqlalchemy import create_engine, text

from app.config import settings

assert settings.database_url.endswith("/nba_backfill")
from app.database import engine  # noqa: E402

PROD = create_engine("postgresql+psycopg2://nba:nba@postgres:5432/nba_predictor", future=True)
OVERLAP = ["2013-14", "2016-17", "2019-20", "2020-21", "2024-25", "2025-26"]
GROUPS = {
    "identical (box-score derived)": ["three_point", "free_throw", "mid_range", "passing", "steal", "block",
                                      "offensive_rebound", "defensive_rebound"],
    "position/tracking dependent": ["overall_rating", "close_shot", "layup", "dunk", "perimeter_defense",
                                    "interior_defense"],
    "clutch (unreconciled)": ["clutch_rating"],
}
COLS = [c for g in GROUPS.values() for c in g]


def main():
    sel = "select player_id, " + ", ".join(COLS) + " from player_attributes where season=:s"
    report = {}
    print(f"{'season':8s} {'group':32s} {'attrs':>5s} {'rows':>5s} {'rows differing':>14s} {'mean|d|':>8s} {'max|d|':>7s}")
    for s in OVERLAP:
        with engine.connect() as c, PROD.connect() as p:
            new = {r[0]: r[1:] for r in c.execute(text(sel), {"s": s}).all()}
            old = {r[0]: r[1:] for r in p.execute(text(sel), {"s": s}).all()}
        both = sorted(new.keys() & old.keys())
        report[s] = {"players_compared": len(both), "only_in_backfill": len(new.keys() - old.keys()),
                     "only_in_production": len(old.keys() - new.keys()), "attributes": {}}
        for gname, cols in GROUPS.items():
            idx = [COLS.index(c) for c in cols]
            diffs = [abs((new[k][i] or 0) - (old[k][i] or 0)) for k in both for i in idx]
            row_diff = sum(any((new[k][i] or 0) != (old[k][i] or 0) for i in idx) for k in both)
            for c in cols:
                i = COLS.index(c)
                d = [abs((new[k][i] or 0) - (old[k][i] or 0)) for k in both]
                report[s]["attributes"][c] = {"differs": sum(x > 0 for x in d), "mean_abs": sum(d) / len(d), "max_abs": max(d)}
            print(f"{s:8s} {gname:32s} {len(cols):5d} {len(both):5d} {row_diff:14d} {sum(diffs) / len(diffs):8.2f} {max(diffs):7d}")
    json.dump(report, open("validation/ratings/results/observed_bf1_vs_production.json", "w"), indent=1)


if __name__ == "__main__":
    main()
