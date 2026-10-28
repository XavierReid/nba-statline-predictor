"""Coverage and roster-reconstruction diagnostics for the rating backtest (Amendment 2, A2.3 / Section 4).

EVALUATION-side code (it reads season-T outcomes on purpose: it measures the targets and the proxies, it
does not predict). Read-only on the isolated backfill database. Writes results/coverage_checks.json.

    docker compose run --rm -e DATABASE_URL=postgresql+psycopg2://nba:nba@postgres:5432/nba_backfill \\
        api python -m validation.ratings.coverage_checks
"""
import json
from collections import defaultdict

from validation.ratings.asof import prev_season, season_start_year
from validation.ratings.asof_db import DBSource

SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2026)]
TARGETS = SEASONS[1:]
DEV = TARGETS[:8]            # 2014-15 .. 2021-22
LOCKED = TARGETS[8:]         # 2022-23 .. 2025-26
CATS = ["V1", "V2", "V3", "V4", "Y1", "Y2", "R-A", "R-B", "R-C", "R-D", "L"]
CRITICAL = ["V1", "Y1", "R-A", "R-B", "L"]
EARLY_GAMES = 10


def to_int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def main():
    src = DBSource()
    stats = {s: {r["player_id"]: r for r in src.season_stats(s)} for s in SEASONS}
    season_games = {s: max((r["games_played"] or 0) for r in stats[s].values()) for s in SEASONS}
    logs = {}
    first_team, last_team, first_game, minutes = {}, {}, {}, {}
    for s in SEASONS:
        rows = src.game_logs(s)
        logs[s] = rows
        ft, lt, fg, mn = {}, {}, {}, defaultdict(float)
        for r in sorted(rows, key=lambda r: (r["game_no"], r["player_id"])):
            ft.setdefault(r["player_id"], r["team_id"])
            fg.setdefault(r["player_id"], r["game_no"])
            lt[r["player_id"]] = r["team_id"]
            mn[r["player_id"]] += r["minutes"] or 0.0
        first_team[s], last_team[s], first_game[s], minutes[s] = ft, lt, fg, mn

    report = {"categories": {}, "reconstruction": {}, "critical_totals": {}}
    gate = lambda r: (r["games_played"] or 0) >= 20 and (r["minutes_per_game"] or 0) >= 15
    for t in TARGETS:
        ty, p, pp = season_start_year(t), prev_season(t), (prev_season(t, 2) if t != TARGETS[0] else None)
        bio = {r["PLAYER_ID"]: r for r in src.bio(t)}
        counts = {c: {"all": 0, "gated": 0} for c in CATS}
        for pid, r in stats[t].items():
            b = bio.get(pid, {})
            age, dy, pick = b.get("AGE"), to_int(b.get("DRAFT_YEAR")), to_int(b.get("DRAFT_NUMBER"))
            prior_n = sum(pid in stats[s] for s in SEASONS if season_start_year(s) < ty)
            has_prior = pid in stats[p]
            exp = (ty - dy) if dy else (prior_n if prior_n else None)
            rp = stats[p].get(pid)
            gp_p = (rp["games_played"] or 0) if rp else 0
            prior_min = gp_p * ((rp["minutes_per_game"] or 0) if rp else 0)
            cats = set()
            if dy == ty:
                cats.add("R-A" if pick and pick <= 14 else "R-B" if pick and pick <= 30 else "R-C")
            elif dy is None and prior_n == 0:
                cats.add("R-D")
            if dy == ty - 1 or (dy is None and prior_n == 1 and has_prior):
                cats.add("Y1")
            if dy == ty - 2 or (age is not None and age <= 24 and exp is not None and exp >= 2 and dy != ty - 1):
                cats.add("Y2")
            if has_prior and exp is not None and exp >= 4 and age is not None and age < 30:
                cats.add("V1")
            if has_prior and age is not None and age >= 30:
                cats.add("V2")
            if has_prior and pid in last_team[p] and pid in first_team[t] and last_team[p][pid] != first_team[t][pid]:
                cats.add("V3")
            if pp and has_prior and gp_p < 0.4 * season_games[p] and (stats[pp].get(pid, {}).get("games_played") or 0) >= 0.6 * season_games[pp]:
                cats.add("V4")
            if prior_min < 500:
                cats.add("L")
            for c in cats:
                counts[c]["all"] += 1
                counts[c]["gated"] += gate(r)
        report["categories"][t] = counts

        # --- roster reconstruction (A2.3)
        fg, mn, ft = first_game[t], minutes[t], first_team[t]
        total_min = sum(mn.values()) or 1.0
        late = [pid for pid, g in fg.items() if g > EARLY_GAMES]
        late_min = sum(mn[pid] for pid in late)
        by_team = defaultdict(list)
        for pid in fg:
            by_team[ft[pid]].append(pid)
        missing_top = 0
        sizes_early = []
        for team, pids in by_team.items():
            early = [x for x in pids if fg[x] <= EARLY_GAMES]
            sizes_early.append(len(early))
            top10 = sorted(pids, key=lambda x: -mn[x])[:10]
            missing_top += sum(x not in early for x in top10)
        report["reconstruction"][t] = {
            "players_with_logs": len(fg), "late_arrivals": len(late), "late_arrival_share": len(late) / len(fg),
            "late_arrival_minute_share": late_min / total_min, "unprojectable_minute_share_R_early": late_min / total_min,
            "top10_minute_players_missing_from_R_early": missing_top,
            "R_early_roster_size_min_mean_max": [min(sizes_early), sum(sizes_early) / len(sizes_early), max(sizes_early)],
        }

    for name, ts in (("dev", DEV), ("locked", LOCKED)):
        report["critical_totals"][name] = {c: {k: sum(report["categories"][t][c][k] for t in ts) for k in ("all", "gated")} for c in CATS}
    json.dump(report, open("validation/ratings/results/coverage_checks.json", "w"), indent=1)

    print("CATEGORY SIZES (player-seasons; 'gated' = meets the >=20 GP, >=15 MPG rating gate in the target season)")
    print(f"{'cat':5s} {'dev all':>8s} {'dev gated':>10s} {'locked all':>11s} {'locked gated':>13s}   critical-category check (>=50 gated)")
    for c in CATS:
        d, l = report["critical_totals"]["dev"][c], report["critical_totals"]["locked"][c]
        flag = ""
        if c in CRITICAL:
            flag = "OK" if d["gated"] >= 50 and l["gated"] >= 50 else f"BELOW 50 (dev {d['gated']}, locked {l['gated']})"
        print(f"{c:5s} {d['all']:8d} {d['gated']:10d} {l['all']:11d} {l['gated']:13d}   {flag}")
    print("\nper-season rookie-class sizes (R-A+R-B+R-C drafted this year): all / gated")
    print("  " + "  ".join(f"{t}:{sum(report['categories'][t][c]['all'] for c in ('R-A','R-B','R-C'))}/{sum(report['categories'][t][c]['gated'] for c in ('R-A','R-B','R-C'))}" for t in TARGETS))
    print("\nROSTER RECONSTRUCTION (first appearance; R-early = first appearance within game 10)")
    print(f"{'target':8s} {'players':>7s} {'late':>5s} {'late %':>7s} {'late min %':>10s} {'top10 missing':>13s} {'R-early size min/mean/max':>26s}")
    for t in TARGETS:
        r = report["reconstruction"][t]
        mm = r["R_early_roster_size_min_mean_max"]
        print(f"{t:8s} {r['players_with_logs']:7d} {r['late_arrivals']:5d} {r['late_arrival_share']:7.1%} {r['late_arrival_minute_share']:10.1%} "
              f"{r['top10_minute_players_missing_from_R_early']:13d} {mm[0]:8d}/{mm[1]:.1f}/{mm[2]:d}")


if __name__ == "__main__":
    main()
