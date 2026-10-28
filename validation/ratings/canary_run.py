"""Gate A1-A3 on REAL data: run the metamorphic canary for every target season with the V1 reference
predictor, check the manifest audit, and confirm the guard refuses the target season.

    docker compose run --rm -e DATABASE_URL=postgresql+psycopg2://nba:nba@postgres:5432/nba_backfill \\
        api python -m validation.ratings.canary_run
"""
import json

from validation.ratings.asof import AsOfReader, LeakageError, run_canary
from validation.ratings.asof_db import DBSource
from validation.ratings.predictors import v1_carry_forward

TARGETS = [f"{y}-{str(y + 1)[2:]}" for y in range(2014, 2026)]
OUT = "validation/ratings/results/canary_run.json"


def main():
    src = DBSource()
    results = {}
    print(f"{'target':8s} {'canary':>9s} {'players':>7s} {'audit':>6s} {'guard blocks T':>14s}  non-exact input classes")
    for t in TARGETS:
        res = run_canary(v1_carry_forward, src, t)
        reader = AsOfReader(src, t)
        preds = v1_carry_forward(reader)
        try:
            reader.season_stats(t)
            blocked = False
        except LeakageError:
            blocked = True
        results[t] = {"canary_identical": res.identical, "error": res.error, "players_predicted": len(preds),
                      "manifest_audit_violations": reader.audit(), "guard_blocks_target": blocked,
                      "manifest_sha256": reader.manifest()["sha256"],
                      "non_exact_classes": reader.manifest()["non_exact_classes"]}
        print(f"{t:8s} {'IDENTICAL' if res.identical else 'LEAK':>9s} {len(preds):7d} "
              f"{'ok' if not reader.audit() else 'FAIL':>6s} {str(blocked):>14s}  {reader.manifest()['non_exact_classes']}")
    ok = all(r["canary_identical"] and not r["manifest_audit_violations"] and r["guard_blocks_target"]
             for r in results.values())
    json.dump({"all_pass": ok, "results": results}, open(OUT, "w"), indent=1)
    print("\nGATES A1 (canary) + A2 (guard) + A3 (manifest audit) on real data:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
