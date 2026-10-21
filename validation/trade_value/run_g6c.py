"""G6c: do the locked CPU rule's verdicts agree with the simulated outcome for the CPU team?
   python -m validation.trade_value.run_g6c --split holdoutA|holdoutB

Proposals are drawn on the split's teams (needs results/measure_<split>.json for the MPG rank
lists), each trade is applied with the shipped RosterOverlay, and the CPU team's change in mean
margin is measured on a paired schedule. Reads results/frozen_model.json and never refits.
"""
import argparse
import hashlib
import json
import random

from app.services.trade_value import build_value_model
from validation.trade_value import analysis as A
from validation.trade_value import harness as H

R = "validation/trade_value/results/"
PROPOSAL_SEED = 77
PER_TYPE = 30


def make_proposals(ranked: dict, seed: int = PROPOSAL_SEED, per_type: int = PER_TYPE) -> list:
    """ranked: team_id -> player ids in MPG order (rank r = index r-1). Deterministic."""
    rng = random.Random(seed)
    teams = sorted(ranked)

    def pick(team, ranks):
        return [ranked[team][r - 1] for r in ranks]

    def near(r):
        return rng.choice([x for x in (r - 1, r, r + 1) if 1 <= x <= 8])

    out, seen = [], set()
    for ty in ("cpu_gives_star", "cpu_gets_star", "balanced_1v1", "balanced_2v2"):
        n = 0
        while n < per_type:
            a, b = rng.sample(teams, 2)                    # a = proposer, b = CPU team
            if ty == "cpu_gives_star":
                out_ids = pick(b, [rng.choice([1, 2])])
                in_ids = pick(a, rng.sample(range(3, 9), rng.choice([2, 3])))
            elif ty == "cpu_gets_star":
                in_ids = pick(a, [rng.choice([1, 2])])
                out_ids = pick(b, rng.sample(range(4, 10), 2))
            elif ty == "balanced_1v1":
                r = rng.randint(1, 8)
                in_ids, out_ids = pick(a, [r]), pick(b, [near(r)])
            else:
                r1, r2 = rng.sample(range(1, 9), 2)
                o1 = near(r1)
                o2 = near(r2)
                if o1 == o2:
                    continue
                in_ids, out_ids = pick(a, [r1, r2]), pick(b, [o1, o2])
            key = (a, b, tuple(sorted(in_ids)), tuple(sorted(out_ids)))
            if key in seen:
                continue
            seen.add(key)
            out.append({"type": ty, "proposer": a, "cpu_team": b, "in_ids": in_ids, "out_ids": out_ids})
            n += 1
    for i, p in enumerate(out):
        p["index"] = i
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["holdoutA", "holdoutB"])
    ap.add_argument("--games", type=int, default=H.G_PROPOSAL)
    ap.add_argument("--per-type", type=int, default=PER_TYPE)
    args = ap.parse_args()
    raw = open(R + "frozen_model.json", "rb").read()
    frozen = json.loads(raw)
    meas = A.load_measure(R + f"measure_{args.split}.json")
    season = meas["meta"]["season"]
    ranked = {int(t): v["ranked_ids"] for t, v in meas["teams"].items()}
    from validation.trade_value.run_measure import split_ids
    _, _, league_all = split_ids(args.split)

    proposals = make_proposals(ranked, per_type=args.per_type)
    tasks = [{**p, "season": season, "opp_ids": league_all, "games": args.games} for p in proposals]
    raw_path = R + f"g6c_{args.split}_raw.json"
    results = sorted(H.run_pool(H.measure_proposal, tasks), key=lambda r: r["index"])
    json.dump(results, open(raw_path, "w"))

    prods, _ = A.load_productions(season)
    idx = A.prod_index(prods)
    report = {"season": season, "split": args.split, "games": args.games, "n_proposals": len(results),
              "frozen_sha256": hashlib.sha256(raw).hexdigest()[:16], "models": {}}
    for c in frozen["candidates"]:
        m = build_value_model(prods, k=c["k"], shrink=frozen["shrink"], availability=frozen["availability"])
        rows = A.g6c_rows(results, m, idx)
        report["models"][c["label"]] = {"k": c["k"], "tau_1.0": A.g6c_metrics(rows, 1.0),
                                        "tau_0.5": A.g6c_metrics(rows, 0.5, boot=False),
                                        "tau_1.5": A.g6c_metrics(rows, 1.5, boot=False)}
        o = report["models"][c["label"]]["tau_1.0"]["overall"]
        o["pass"] = {
            "preserve_given_accepted_ge_0.85": o["p_preserve_given_accepted"] >= 0.85,
            "lift_ge_0.10": o["lift_vs_base"] >= 0.10,
            "accept_rate_in_10_70": 0.10 <= o["accept_rate"] <= 0.70,
            "recall_beneficial_ge_0.40": o["p_accepted_given_beneficial"] >= 0.40}
        o["pass_all"] = all(o["pass"].values())
    with open(R + f"g6c_{args.split}_report.json", "w") as f:
        json.dump(report, f, indent=1)

    print(f"G6c {args.split} season {season}: {len(results)} proposals x {args.games} games/arm; frozen sha {report['frozen_sha256']}")
    for label, mm in report["models"].items():
        o = mm["tau_1.0"]["overall"]
        print(f"\n[{label} k={mm['k']}] accept {o['accept_rate']:.0%} | base P(preserve) {o['base_rate_preserve']:.0%} | "
              f"P(preserve|acc) {o['p_preserve_given_accepted']:.0%} lift {o['lift_vs_base']:+.0%} | "
              f"P(benef|rej) {o['p_beneficial_given_rejected']:.0%} | P(acc|benef) {o['p_accepted_given_beneficial']:.0%} | "
              f"rho(dV,delta) {o['spearman_dV_vs_delta']:.2f} | unresolvable {o['share_unresolvable_2se']:.0%} | PASS_ALL={o['pass_all']}")
        for ty, v in mm["tau_1.0"]["by_type"].items():
            print(f"    {ty:15s} n={v['n']:3d} accept {v['accept_rate']:.0%} P(pres|acc) {v['p_preserve_given_accepted']:.0%} "
                  f"mean delta acc {v['mean_delta_accepted']:+.2f} rej {v['mean_delta_rejected']:+.2f}")


if __name__ == "__main__":
    main()
