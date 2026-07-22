"""Gap 3.3 — final-possession shot-value instrument.

The behavioral decision the engine may be missing: on the trailing team's late
offensive possession, does it choose the shot VALUE that ties (a 3 when down 3,
a 2 when down 2), or does it keep taking EV-max shots?

Calibration target is the CONDITIONAL decision, not the outcome:
    P(attempt value == tying value | deficit, time remaining)

SIM: replay the schedule with capture_descriptions; walk Q4 possessions with a
running score; for each trailing-team possession in the final window record
(deficit, clock, chosen shot value, made?, resulting margin). The "final
possession" per game is the trailing team's last such possession in regulation.

REAL proxy (made-only PBP): among the trailing team's made FGs in the final
window while down k, fraction that are 3s. Biased toward 2s (higher make rate),
so it is a LOWER bound on real 3-point preference — labeled as such.

Usage: python scratch/gap33_final_possession.py --season 2016-17 --sims-per-game 3 [--window 35]
"""
import argparse
import os
import sys
import zlib
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.game import Game
from app.models.scoring_event import ScoringEvent
from app.models.team import Team
from app.services.game_simulator import simulate_game
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select

THREES = {"corner_three", "above_break_three", "three"}


def shot_value(shot_type):
    if shot_type is None:
        return None
    return 3 if shot_type in THREES else 2


def sim_final_possessions(games, rosters, season, sims_per_game, window):
    """Return (final_possessions, all_window_possessions).

    final: the trailing team's LAST qualifying regulation possession per game.
    all: every trailing-team possession in the window (for the made-only lens
    that matches the real PBP proxy population).
    """
    out = []
    pooled = []
    db = SessionLocal()
    for g in games:
        if g.home_team_id not in rosters or g.away_team_id not in rosters:
            continue
        base_seed = zlib.crc32(str(g.id).encode())
        for k in range(sims_per_game):
            r = simulate_game(
                rosters[g.home_team_id], rosters[g.away_team_id],
                seed=base_seed + k, season=season, config=DRAMA_M3,
                home_team_id=g.home_team_id, away_team_id=g.away_team_id, db=db,
                capture_descriptions=True,
            )
            hs = as_ = 0
            final = None  # last qualifying trailing possession this game
            for ev in r["events"]:
                if ev["quarter"] != 4:
                    # keep running score across all quarters
                    hs += ev["pts"] if ev["is_home"] else 0
                    as_ += 0 if ev["is_home"] else ev["pts"]
                    continue
                off_is_home = ev["is_home"]
                off_score = hs if off_is_home else as_
                def_score = as_ if off_is_home else hs
                deficit = def_score - off_score  # positive = offense trailing
                clock = ev["game_clock_seconds"]
                if 1 <= deficit <= 3 and clock <= window:
                    val = shot_value(ev.get("shot_type"))
                    pts = ev["pts"]
                    new_margin_off = (off_score + pts) - def_score
                    rec = {
                        "deficit": deficit, "clock": clock, "value": val,
                        "made": bool(ev.get("made")), "pts": pts,
                        "result_margin_off": new_margin_off,
                    }
                    final = rec
                    pooled.append(rec)
                # advance running score
                if off_is_home:
                    hs += ev["pts"]
                else:
                    as_ += ev["pts"]
            if final is not None:
                qs = r["quarter_scores"]
                final["end_reg_tie"] = sum(qs["home"][:4]) == sum(qs["away"][:4])
                final["went_ot"] = r["went_to_ot"]
                out.append(final)
    db.close()
    return out, pooled


def real_made_proxy(games, window):
    """Made-only PBP: trailing team down k, final window, share of made FGs that are 3s."""
    db = SessionLocal()
    gids = [g.id for g in games]
    by_deficit = defaultdict(lambda: [0, 0])  # deficit -> [threes, total_made_fg]
    # pull all scoring events for these games in Q4
    for gid in gids:
        evs = db.execute(
            select(ScoringEvent).where(
                ScoringEvent.game_id == gid, ScoringEvent.period == 4
            ).order_by(ScoringEvent.event_num)
        ).scalars().all()
        for e in evs:
            if e.seconds_remaining > window:
                continue
            if e.points not in (2, 3):  # ignore FTs
                continue
            # deficit of the SCORING side BEFORE this make
            if e.scoring_side == "home":
                pre_off, pre_def = e.home_score - e.points, e.away_score
            else:
                pre_off, pre_def = e.away_score - e.points, e.home_score
            deficit = pre_def - pre_off
            if 1 <= deficit <= 3:
                by_deficit[deficit][0] += int(e.points == 3)
                by_deficit[deficit][1] += 1
    db.close()
    return by_deficit


def main(season, sims_per_game, window):
    db = SessionLocal()
    year = season.split("-")[0][-2:]
    games = db.execute(
        select(Game).where(
            Game.id.like(f"002{year}%"), Game.status == "final",
            Game.home_q4.isnot(None),
        )
    ).scalars().all()
    teams = db.execute(select(Team)).scalars().all()
    rosters = {}
    for t in teams:
        r = load_roster(db, t.id, season)
        if r:
            rosters[t.id] = r
    db.close()

    sim, pooled = sim_final_possessions(games, rosters, season, sims_per_game, window)
    real = real_made_proxy(games, window)

    print(f"\n{'='*64}\n  Gap 3.3 final-possession shot value: {season}  (window<= {window}s)\n{'='*64}")

    # SIM: conditional shot-value choice by deficit (attempts only)
    print(f"\n  SIM trailing-team FINAL regulation possession (n={len(sim)}):")
    print(f"  {'deficit':>7} {'n':>5} {'FGA':>5} {'P(3)':>7} {'P(2)':>7} {'tie%':>7} {'lead%':>7} {'made%':>7}")
    for d in (1, 2, 3):
        rows = [p for p in sim if p["deficit"] == d]
        n = len(rows)
        fga = [p for p in rows if p["value"] is not None]
        if not n:
            continue
        p3 = sum(1 for p in fga if p["value"] == 3) / len(fga) * 100 if fga else 0
        p2 = 100 - p3 if fga else 0
        tie = sum(1 for p in rows if p["result_margin_off"] == 0) / n * 100
        lead = sum(1 for p in rows if p["result_margin_off"] > 0) / n * 100
        made = sum(1 for p in rows if p["made"]) / n * 100
        tying_val = d if d in (2, 3) else 2
        # of the final possessions at this deficit, how many actually ended regulation tied
        endtie = sum(1 for p in rows if p.get("end_reg_tie")) / n * 100
        immtie = sum(1 for p in rows if p["result_margin_off"] == 0)
        surv = (sum(1 for p in rows if p["result_margin_off"] == 0 and p.get("end_reg_tie"))
                / immtie * 100) if immtie else 0
        print(f"  {d:>7} {n:>5} {len(fga):>5} {p3:>6.1f}% {p2:>6.1f}% {tie:>6.1f}% {lead:>6.1f}% {made:>6.1f}%  endreg0={endtie:4.1f}% tie_survive={surv:4.0f}%  (tie val={tying_val})")

    # Apples-to-apples MADE-only lens: sim vs real, same window/deficit population.
    print(f"\n  MADE-FG 3-share (down k, all window possessions) — sim vs real, identical estimator:")
    print(f"  {'deficit':>7} {'sim made':>9} {'sim 3s%':>8}   {'real made':>10} {'real 3s%':>9}")
    for d in (1, 2, 3):
        sm = [p for p in pooled if p["deficit"] == d and p["made"] and p["value"] is not None]
        s3 = sum(1 for p in sm if p["value"] == 3) / len(sm) * 100 if sm else 0
        rthrees, rtot = real[d]
        rshare = rthrees / rtot * 100 if rtot else 0
        print(f"  {d:>7} {len(sm):>9} {s3:>7.1f}%   {rtot:>10} {rshare:>8.1f}%")

    # SIM attempt-share (mechanism view; real can't give this from made-only PBP)
    print(f"\n  SIM attempt 3-share (down k, all window possessions, FGA only):")
    for d in (1, 2, 3):
        fga = [p for p in pooled if p["deficit"] == d and p["value"] is not None]
        a3 = sum(1 for p in fga if p["value"] == 3) / len(fga) * 100 if fga else 0
        print(f"    down {d}: {a3:.1f}% threes  (n_fga={len(fga)})")
    print()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2016-17")
    p.add_argument("--sims-per-game", type=int, default=3)
    p.add_argument("--window", type=int, default=35, help="final-window clock seconds")
    a = p.parse_args()
    main(a.season, a.sims_per_game, a.window)
