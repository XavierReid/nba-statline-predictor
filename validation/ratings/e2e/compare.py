"""Combine the per-arm evaluation files into one comparison (A2.2). Reads results only; no database.

    python3 -m validation.ratings.e2e.compare 2021-22
"""
import json
import sys

ARMS = ["D", "P", "O_r", "O"]
HIGHER_BETTER = {"rho": True, "spread_ratio": None, "h2h": None, "ppg_mae": False, "rpg_mae": False, "apg_mae": False, "mpg_mae": False}


def stats(v):
    m = sum(v) / len(v)
    s = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5 if len(v) > 1 else 0.0
    return m, s / len(v) ** 0.5            # mean, standard error over seeds


def metrics(e):
    r = e["runs"]
    return {"rho": [x["rho"] for x in r], "spread_ratio": [x["spread_ratio"] for x in r],
            "ppg_mae": [x["mae"]["ppg"] for x in r], "rpg_mae": [x["mae"]["rpg"] for x in r],
            "apg_mae": [x["mae"]["apg"] for x in r], "mpg_mae": [x["mae"]["mpg"] for x in r],
            "top80_present": [x["top80_present"] for x in r], "h2h": [e["h2h_best_wins"] / e["h2h_games"]]}


def main(target, suffix=""):
    global ARMS
    ARMS = ["D" + suffix, "P" + suffix, "O_r" + suffix, "O"]
    ev = {a: json.load(open("validation/ratings/results/e2e/eval_%s_%s.json" % (a, target))) for a in ARMS}
    m = {a: metrics(ev[a]) for a in ARMS}
    base = lambda a: a[: -len(suffix)] if suffix and a.endswith(suffix) else a
    print("%-14s" % "metric", *("%16s" % a for a in ARMS))
    out = {}
    for k in ("rho", "spread_ratio", "h2h", "ppg_mae", "rpg_mae", "apg_mae", "mpg_mae", "top80_present"):
        row = {a: stats(m[a][k]) for a in ARMS}
        out[k] = row
        print("%-14s" % k, *("%9.3f+/-%.3f" % row[a] for a in ARMS))
    print("\nGap closed by P between D and the oracle runs: (P-D)/(ref-D); 1 = as good as the reference, <0 = worse than D")
    for k, sign in (("rho", 1), ("ppg_mae", -1), ("rpg_mae", -1), ("apg_mae", -1), ("mpg_mae", -1)):
        d, p = out[k]["D" + suffix][0], out[k]["P" + suffix][0]
        gaps = []
        for ref in ("O_r" + suffix, "O"):
            den = sign * (out[k][ref][0] - d)
            gaps.append("%s: %s" % (ref, "%.2f" % (sign * (p - d) / den) if abs(den) > 1e-9 else "n/a"))
        print("  %-8s" % k, "   ".join(gaps))
    json.dump(out, open("validation/ratings/results/e2e/compare_%s.json" % target, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
