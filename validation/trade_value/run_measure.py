"""Measure with/without impact for a set of teams: python -m validation.trade_value.run_measure
       --split dev|holdoutA|holdoutB [--games N] [--teams K] [--out PATH]

dev/holdoutA use 2024-25 (dev teams / holdout teams); holdoutB = holdout teams in 2016-17.
Both seed sets A and B are run so repeat variance and the noise ceiling can be reported.
"""
import argparse
import json
import time

from sqlalchemy import select

from app.database import SessionLocal
from app.models.player_season_stats import PlayerSeasonStats
from validation.trade_value import harness as H

SPLITS = {"dev": ("2024-25", 0), "holdoutA": ("2024-25", 1), "holdoutB": ("2016-17", 1),
          "confirm": ("2013-14", 2)}      # Amendment 3: every team, unseen season


def teams_with_stats(season: str) -> list:
    db = SessionLocal()
    try:
        return sorted(set(db.execute(
            select(PlayerSeasonStats.team_id).where(
                PlayerSeasonStats.season == season, PlayerSeasonStats.team_id.isnot(None),
                PlayerSeasonStats.team_id != 0)).scalars().all()))
    finally:
        db.close()


def split_ids(split: str) -> tuple:
    season, which = SPLITS[split]
    ref = teams_with_stats("2024-25")            # the split is defined once, on the 2024-25 teams
    dev, hold = H.split_teams(ref)
    members = sorted(ref) if which == 2 else (hold if which else dev)
    if season != "2024-25":
        members = [t for t in members if t in set(teams_with_stats(season))]
    return season, members, teams_with_stats(season)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=sorted(SPLITS))
    ap.add_argument("--games", type=int, default=H.G_MEASURE)
    ap.add_argument("--teams", type=int, default=0, help="limit team count (smoke tests)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    season, members, league = split_ids(args.split)
    if args.teams:
        members = members[: args.teams]
    # G6a/G6b use the registered ranks; holdout splits also measure ranks 1-10 so the measured
    # individual-impact table can serve as the G6c oracle benchmark (Amendment 2, A2.5).
    ranks = range(1, 11) if args.split.startswith(("holdout", "confirm")) else H.RANKS_SINGLE
    conditions = {f"S{r}": (r,) for r in ranks}
    conditions.update({"P" + "_".join(map(str, p)): p for p in H.PACKAGES})
    tasks = [
        {"season": season, "team_id": t, "opp_ids": [o for o in league if o != t], "games": args.games,
         "seed_set": s, "conditions": conditions}
        for t in members for s in ("A", "B")
    ]
    t0 = time.time()
    results = H.run_pool(H.measure_team, tasks)
    teams: dict = {}
    for r in results:
        d = teams.setdefault(str(r["team_id"]), {"ranked_ids": r["ranked_ids"]})
        d[r["seed_set"]] = r["conditions"]
    payload = {"meta": {"split": args.split, "season": season, "games": args.games, "teams": members,
                        "conditions": {k: list(v) for k, v in conditions.items()},
                        "config": "DRAMA_M3", "roster_depth": H.ROSTER_DEPTH,
                        "seconds": round(time.time() - t0, 1)}, "teams": teams}
    out = args.out or f"validation/trade_value/results/measure_{args.split}.json"
    with open(out, "w") as f:
        json.dump(payload, f)
    print(f"wrote {out}: {len(members)} teams x 2 seed sets x {len(conditions) + 1} conditions "
          f"x {args.games} games in {payload['meta']['seconds']}s")


if __name__ == "__main__":
    main()
