"""Phase 1 instrumentation for the post-M3 calibration diagnostic.

Measures the four Tier 1 hypotheses from SIMULATION_GAPS.md:
  1.1/1.2  Margin distribution entering the final 2 min vs final margin
           (does anything compress margins late?), ties at end of regulation
  1.3      Margin trajectory by quarter (does the leader keep stretching?)
  1.4      Actual possessions per team per game vs pace input (~99 real)

Usage:
    python scratch/diagnose_calibration.py [--games 300]
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select

MATCHUPS = [
    ("BOS", "LAL"), ("OKC", "GSW"), ("DEN", "MIL"), ("PHX", "MIA"),
    ("HOU", "MIN"), ("LAL", "CHA"), ("BOS", "DET"), ("GSW", "WAS"),
    ("OKC", "UTA"), ("DEN", "SAS"),
]


def main(n_games: int) -> None:
    db = SessionLocal()
    rosters, team_ids = {}, {}
    for h, a in MATCHUPS:
        for abbr in (h, a):
            if abbr not in rosters:
                team = db.execute(select(Team).where(Team.abbreviation == abbr)).scalar_one()
                rosters[abbr] = load_roster(db, team.id, "2025-26")
                team_ids[abbr] = team.id

    poss_per_team = []          # 1.4 — possessions per team per game
    acct_counts = Counter()     # possession accounting: counts by category
    acct_time = Counter()       # possession accounting: clock seconds by category
    catch_up_delta = 0.0
    pace_budgets = []
    sf_games = 0                # games with >=1 strategic foul
    sf_counts = []              # strategic fouls per game (when >0)
    margin_enter_final2 = []    # 1.2 — |margin| when clock first <= 120s in Q4
    final_margins = []          # 1.2 — |final margin| (regulation end, pre-OT)
    close_late_outcomes = Counter()  # 1.2 — for games within 5 entering final 2 min
    quarter_margin_walk = [[], [], [], []]  # 1.3 — |margin| at end of each quarter
    lead_changes = []           # 1.3 — lead changes per game
    reg_ties = 0                # ties at end of regulation (went to OT)

    per_matchup = max(1, n_games // len(MATCHUPS))
    total = 0
    for h, a in MATCHUPS:
        for i in range(per_matchup):
            seed = total * 7919 + i
            r = simulate_game(
                rosters[h], rosters[a], seed=seed, season="2025-26",
                config=DRAMA_M3, capture_descriptions=True,
                home_team_id=team_ids[h], away_team_id=team_ids[a], db=db,
            )
            total += 1
            events = r["events"]

            acct = r["possession_accounting"]
            for k, v in acct["counts"].items():
                acct_counts[k] += v
            for k, v in acct["time"].items():
                acct_time[k] += v
            catch_up_delta += acct["catch_up_time_delta"]
            pace_budgets.append(acct["pace_budget"])
            n_sf = acct["counts"]["strategic_foul"]
            if n_sf:
                sf_games += 1
                sf_counts.append(n_sf)

            # 1.4 possessions: count events per side (regulation only, quarter <= 4)
            reg_events = [e for e in events if e["quarter"] <= 4]
            poss_home = sum(1 for e in reg_events if e["is_home"])
            poss_away = len(reg_events) - poss_home
            poss_per_team.append(poss_home)
            poss_per_team.append(poss_away)

            # margin walk — reconstruct running margin by accumulating pts
            eq = {q: None for q in (1, 2, 3, 4)}
            m_final2 = None
            changes = 0
            prev_sign = 0
            run_h = run_a = 0
            for e in reg_events:
                if e["is_home"]:
                    run_h += e.get("pts", 0)
                else:
                    run_a += e.get("pts", 0)
                margin = run_h - run_a
                sign = (margin > 0) - (margin < 0)
                if sign != 0 and prev_sign != 0 and sign != prev_sign:
                    changes += 1
                if sign != 0:
                    prev_sign = sign
                eq[e["quarter"]] = abs(margin)
                if e["quarter"] == 4 and e["game_clock_seconds"] <= 120 and m_final2 is None:
                    m_final2 = abs(margin)
            lead_changes.append(changes)
            for qi in range(4):
                if eq[qi + 1] is not None:
                    quarter_margin_walk[qi].append(eq[qi + 1])

            # regulation-end margin: quarter_scores sums (excludes OT columns)
            reg_h = sum(r["quarter_scores"]["home"][:4])
            reg_a = sum(r["quarter_scores"]["away"][:4])
            reg_margin = abs(reg_h - reg_a)
            final_margins.append(reg_margin)
            if m_final2 is not None:
                margin_enter_final2.append(m_final2)
                if m_final2 <= 5:
                    if reg_margin == 0:
                        close_late_outcomes["tie (OT)"] += 1
                    elif reg_margin <= 3:
                        close_late_outcomes["1-3"] += 1
                    elif reg_margin <= 5:
                        close_late_outcomes["4-5"] += 1
                    else:
                        close_late_outcomes["6+ (pulled away)"] += 1
    db.close()

    reg_ties = sum(1 for m in final_margins if m == 0)

    def stats(xs):
        xs = sorted(xs)
        return f"mean {sum(xs)/len(xs):.1f}  p50 {xs[len(xs)//2]}  min {xs[0]}  max {xs[-1]}"

    print(f"\n{'='*64}\n  Diagnostic: {total} games (DRAMA_M3)\n{'='*64}")

    print(f"\n[1.4] Possessions per team per game (real ~99):")
    print(f"      {stats(poss_per_team)}")

    n_acct = sum(acct_counts.values())
    budget = sum(pace_budgets) / len(pace_budgets)
    print(f"\n[acct] Possession accounting (per game, both teams):")
    print(f"      {'pace budget':<22} {budget:>7.1f}")
    for k in ("halfcourt", "second_chance", "fastbreak", "strategic_foul"):
        c = acct_counts[k] / total
        t = acct_time[k] / max(acct_counts[k], 1)
        frac = acct_counts[k] / max(n_acct, 1)
        print(f"      {k:<22} {c:>7.1f}  (avg {t:.1f}s, {frac*100:.1f}% of poss)")
    print(f"      {'total':<22} {n_acct/total:>7.1f}  (excess vs budget: {n_acct/total - budget:+.1f})")
    print(f"      catch-up net clock saved: {catch_up_delta/total:+.1f} s/game"
          f" ({catch_up_delta/total/2880*100:+.2f}% of regulation clock)")

    print(f"\n[sf]  Strategic foul sequences:")
    print(f"      games with >=1: {sf_games}/{total} ({sf_games/total*100:.1f}%)")
    if sf_counts:
        print(f"      fouls per game when present: {stats(sf_counts)}")
        print(f"      avg foul possession length: {acct_time['strategic_foul']/max(acct_counts['strategic_foul'],1):.1f}s")

    print(f"\n[1.3] Avg |margin| at end of each quarter (does it keep widening?):")
    for qi in range(4):
        xs = quarter_margin_walk[qi]
        print(f"      Q{qi+1}: {sum(xs)/len(xs):.1f}")
    print(f"      Lead changes per game: {sum(lead_changes)/len(lead_changes):.1f} (real ~9-10)")

    print(f"\n[1.2] Margin entering final 2 min of Q4: {stats(margin_enter_final2)}")
    within5 = sum(1 for m in margin_enter_final2 if m <= 5)
    print(f"      Games within 5 entering final 2 min: {within5}/{len(margin_enter_final2)}"
          f" ({within5/len(margin_enter_final2)*100:.1f}%)")
    print(f"      Outcomes of those close-late games:")
    for k in ("tie (OT)", "1-3", "4-5", "6+ (pulled away)"):
        c = close_late_outcomes.get(k, 0)
        print(f"        {k:<18} {c:>4}  ({c/max(within5,1)*100:.1f}%)")
    print(f"\n      Regulation ties (OT games): {reg_ties}/{total} ({reg_ties/total*100:.1f}%)")
    print(f"{'='*64}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=300)
    args = parser.parse_args()
    main(args.games)
