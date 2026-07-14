"""Game-texture decomposition — measure how a game's margin MOVES, not just where it
ends (gap 3.2 / 3.6).

Team-aggregate scoring is calibrated; game *outcome distributions* are not. Two
candidate owners are on record and this instrument is built to separate them:

  (a) STRUCTURAL — per-possession points variance is too wide (the sim over-produces
      high-variance outcomes: threes, and-1s). Signature: quarter point-differential
      variance runs hot in EVERY quarter.
  (b) NO MEAN-REVERSION — leads don't rubber-band; mid-game runs over-extend and real
      basketball's competitive-Q4 tightening never happens. Signature: Q1-Q3 variance
      MATCHES real but Q4 fails to compress, and lead changes/game run low.

Quarter-granular metrics (|margin| by quarter, per-quarter differential variance, Q4
transition deltas) are computed for BOTH real and sim from quarter line scores — real
from `Game.home_qN`, sim from `quarter_scores`. Lead changes and per-possession points
variance by sub-type are sim-only (real data is quarter-cumulative) and compared to the
documented real anchors (~9-10 lead changes/game).
"""
import argparse
import math
import os
import sys
from statistics import mean, pvariance
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.analysis.decomposition import simulate_schedule
from app.database import SessionLocal
from app.models.game import Game
from app.models.scoring_event import ScoringEvent
from app.services.roster import load_roster
from app.services.sim_config import DRAMA_M3
from sqlalchemy import select

# Entering-Q4 buckets (|margin| after Q3) — the real Q4-transition target from
# SIMULATION_GAPS.md is keyed on these.
_Q4_BUCKETS = [(0, 5), (6, 10), (11, 20), (21, 999)]
_REAL_LEAD_CHANGES = 9.5   # documented real ~9-10/game; sim flagged ~6


def _bucket(m: int) -> str:
    for lo, hi in _Q4_BUCKETS:
        if lo <= m <= hi:
            return f"{lo}-{hi}" if hi < 999 else f"{lo}+"
    return "?"


def _cumulative_margins(hq: List[int], aq: List[int]) -> List[int]:
    """Signed home margin at the end of each regulation quarter (Q1..Q4)."""
    out, ch, ca = [], 0, 0
    for k in range(4):
        ch += hq[k]
        ca += aq[k]
        out.append(ch - ca)
    return out


class TextureAccount:
    """Quarter-margin statistics accumulated over a set of games (real or sim)."""

    def __init__(self, label: str):
        self.label = label
        self.abs_margin: List[List[float]] = [[] for _ in range(4)]   # per quarter
        self.quarter_diff: List[List[float]] = [[] for _ in range(4)] # home-away that quarter
        self.q4_delta: Dict[str, List[float]] = {}
        self.final_abs: List[float] = []

    def add(self, hq: List[int], aq: List[int]) -> None:
        margins = _cumulative_margins(hq, aq)
        prev = 0
        for k in range(4):
            self.abs_margin[k].append(abs(margins[k]))
            self.quarter_diff[k].append(hq[k] - aq[k])
        entering = abs(margins[2])   # after Q3
        delta = abs(margins[3]) - abs(margins[2])
        self.q4_delta.setdefault(_bucket(entering), []).append(delta)
        self.final_abs.append(abs(margins[3]))

    @property
    def n(self) -> int:
        return len(self.final_abs)

    def blowout_rate(self) -> float:
        return mean(1.0 if m >= 20 else 0.0 for m in self.final_abs)

    def close_rate(self) -> float:
        return mean(1.0 if m <= 5 else 0.0 for m in self.final_abs)


def real_texture(db, season: str) -> TextureAccount:
    year = season.split("-")[0][-2:]
    games = db.execute(
        select(Game).where(
            Game.id.like(f"002{year}%"), Game.status == "final",
            Game.home_q1.isnot(None),
        )
    ).scalars().all()
    acc = TextureAccount(f"{season} real")
    for g in games:
        hq = [g.home_q1, g.home_q2, g.home_q3, g.home_q4]
        aq = [g.away_q1, g.away_q2, g.away_q3, g.away_q4]
        if any(v is None for v in hq + aq):
            continue
        acc.add(hq, aq)
    return acc


def sim_texture(label: str, sims: List[dict]) -> Tuple[TextureAccount, List[float], Dict[str, List[float]]]:
    """Returns the quarter account plus sim-only views: lead changes/game and
    per-possession points by shot sub-type."""
    acc = TextureAccount(label)
    lead_changes: List[float] = []
    pts_by_subtype: Dict[str, List[float]] = {}
    for gsm in sims:
        qs = gsm["quarter_scores"]
        hq, aq = qs["home"][:4], qs["away"][:4]
        acc.add(hq, aq)
        # lead changes: replay events in order, count leader flips (regulation+OT)
        h = a = 0
        leader = 0   # sign of current lead
        flips = 0
        for ev in gsm.get("events", []):
            if ev["is_home"]:
                h += ev["pts"]
            else:
                a += ev["pts"]
            s = (h > a) - (h < a)
            if s != 0 and leader != 0 and s != leader:
                flips += 1
            if s != 0:
                leader = s
            st = ev.get("shot_type")
            if st is not None:
                pts_by_subtype.setdefault(st, []).append(ev["pts"])
        lead_changes.append(flips)
    return acc, lead_changes, pts_by_subtype


def _fmt(r: Optional[float], s: float, f="{:.2f}") -> str:
    if r is None:
        return f"{'':>10}{f.format(s):>10}"
    d = s - r
    ds = ("+" if d >= 0 else "") + f.format(d)
    return f"{f.format(r):>10}{f.format(s):>10}{ds:>10}"


def compare(real: TextureAccount, sim: TextureAccount,
            lead_changes: List[float], pts_by_subtype: Dict[str, List[float]]) -> None:
    w = 78
    print("\n" + "=" * w)
    print(f"  Game-texture decomposition — {sim.label}  vs  {real.label}"
          f"   (real n={real.n}, sim n={sim.n})")
    print("=" * w)

    print(f"\n  {'metric':30}{'real':>10}{'sim':>10}{'diff':>10}")
    print(f"  {'-'*30}{'-'*30}")
    labels = ["Q1", "Q2", "Q3", "Q4(final)"]
    print("  |margin| at end of quarter (dispersion):")
    for k in range(4):
        print(f"    {labels[k]:28}{_fmt(mean(real.abs_margin[k]), mean(sim.abs_margin[k]))}")

    print("\n  quarter point-differential VARIANCE (the 3.2 metric — (a) if hot every Q):")
    for k in range(4):
        rv, sv = pvariance(real.quarter_diff[k]), pvariance(sim.quarter_diff[k])
        print(f"    {labels[k]:28}{_fmt(rv, sv, '{:.1f}')}")

    print("\n  Q4 transition Δ|margin| by entering-Q4 bucket (real compresses at 11+):")
    for lo, hi in _Q4_BUCKETS:
        b = f"{lo}-{hi}" if hi < 999 else f"{lo}+"
        rv = mean(real.q4_delta[b]) if real.q4_delta.get(b) else None
        sv = mean(sim.q4_delta[b]) if sim.q4_delta.get(b) else 0.0
        rn = len(real.q4_delta.get(b, []))
        sn = len(sim.q4_delta.get(b, []))
        print(f"    enter {b:22}{_fmt(rv, sv, '{:+.2f}')}   (n r={rn} s={sn})")

    print("\n  final-margin distribution:")
    print(f"    {'blowout% (20+)':28}{_fmt(real.blowout_rate()*100, sim.blowout_rate()*100, '{:.1f}')}")
    print(f"    {'close% (<=5)':28}{_fmt(real.close_rate()*100, sim.close_rate()*100, '{:.1f}')}")

    print("\n  lead changes / game (sim-only vs documented real ~9-10):")
    print(f"    {'lead changes':28}{_fmt(_REAL_LEAD_CHANGES, mean(lead_changes), '{:.1f}')}")

    print("\n  per-possession points by shot sub-type (sim — high-variance outcome check):")
    print(f"    {'sub-type':16}{'share':>8}{'mean pts':>10}{'var':>8}")
    total = sum(len(v) for v in pts_by_subtype.values())
    for st in sorted(pts_by_subtype, key=lambda x: -len(pts_by_subtype[x])):
        v = pts_by_subtype[st]
        print(f"    {str(st):16}{len(v)/total:>8.3f}{mean(v):>10.3f}{pvariance(v):>8.3f}")
    print("=" * w + "\n")


# --------------------------------------------------------------------------------
# Run & drought analysis (gap 3.2 triangulation — third angle after Q4 variance and
# lead changes). A "scoring sequence" is the ordered list of made scores for one game
# as (side, points, elapsed_seconds); real comes from ScoringEvent, sim from events.
# --------------------------------------------------------------------------------

def _elapsed(period: int, seconds_remaining: int) -> float:
    """Absolute game-clock seconds since tip (regulation 720s/period, OT 300s)."""
    if period <= 4:
        return (period - 1) * 720 + (720 - seconds_remaining)
    return 4 * 720 + (period - 5) * 300 + (300 - seconds_remaining)


def real_sequences(db, season: str) -> List[List[Tuple[str, int, float]]]:
    year = season.split("-")[0][-2:]
    rows = db.execute(
        select(ScoringEvent).join(Game, ScoringEvent.game_id == Game.id)
        .where(Game.id.like(f"002{year}%"))
        .order_by(ScoringEvent.game_id, ScoringEvent.event_num)
    ).scalars().all()
    by_game: Dict[str, List[Tuple[str, int, float]]] = {}
    for e in rows:
        by_game.setdefault(e.game_id, []).append(
            (e.scoring_side, e.points, _elapsed(e.period, e.seconds_remaining))
        )
    return list(by_game.values())


def sim_sequences(sims: List[dict]) -> List[List[Tuple[str, int, float]]]:
    out = []
    for gsm in sims:
        seq = []
        for ev in gsm.get("events", []):
            if ev["pts"] > 0:
                side = "home" if ev["is_home"] else "away"
                seq.append((side, ev["pts"], _elapsed(ev["quarter"], ev["game_clock_seconds"])))
        out.append(seq)
    return out


def _run_drought(sequences: List[List[Tuple[str, int, float]]]) -> dict:
    """Unanswered-run lengths, big-run frequency, and interior scoring droughts."""
    run_lengths: List[int] = []
    runs_6 = runs_8 = runs_10 = 0
    droughts: List[float] = []
    n_games = len(sequences)
    for seq in sequences:
        cur_side, cur_pts = None, 0
        last_score_time = {"home": None, "away": None}
        for side, pts, t in seq:
            # unanswered run: accumulate until the other side scores
            if side == cur_side:
                cur_pts += pts
            else:
                if cur_pts:
                    run_lengths.append(cur_pts)
                cur_side, cur_pts = side, pts
            # interior drought: gap since this team last scored
            if last_score_time[side] is not None:
                droughts.append(t - last_score_time[side])
            last_score_time[side] = t
        if cur_pts:
            run_lengths.append(cur_pts)
    # big-run counts per game (per team-run event, normalized to per game)
    for r in run_lengths:
        runs_6 += r >= 6
        runs_8 += r >= 8
        runs_10 += r >= 10
    return {
        "mean_run": mean(run_lengths) if run_lengths else 0.0,
        "runs_6_pg": runs_6 / n_games,
        "runs_8_pg": runs_8 / n_games,
        "runs_10_pg": runs_10 / n_games,
        "mean_drought": mean(droughts) if droughts else 0.0,
        "p90_drought": sorted(droughts)[int(len(droughts) * 0.9)] if droughts else 0.0,
    }


def _lag1_autocorr(series: List[float]) -> Optional[float]:
    xs, ys = series[:-1], series[1:]
    if len(xs) < 2:
        return None
    mx, my = mean(xs), mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = sum((a - mx) ** 2 for a in xs)
    dy = sum((b - my) ** 2 for b in ys)
    if dx == 0 or dy == 0:
        return None
    return num / math.sqrt(dx * dy)


def _response_stats(sequences: List[List[Tuple[str, int, float]]],
                    bin_s: int = 120, n_bins: int = 24,
                    run_min: int = 8, window_s: int = 180, counter_min: int = 6) -> dict:
    """Run RESPONSE (the refined 3.2 owner): does a scoring surge get answered?

    (1) lag-1 autocorrelation of per-window signed margins — negative means a hot
        window is followed by a cold one (runs answered / rubber-band); ~0 means
        memoryless. (2) answered-run rate — after a team's >=run_min unanswered run,
        the opponent posts a >=counter_min counter within window_s.
    """
    autocorrs: List[float] = []
    triggers = answered = 0
    for seq in sequences:
        m = [0.0] * n_bins
        for side, pts, t in seq:
            b = int(t // bin_s)
            if 0 <= b < n_bins:
                m[b] += pts if side == "home" else -pts
        r = _lag1_autocorr(m)
        if r is not None:
            autocorrs.append(r)
        # answered-run: find unanswered runs >= run_min, scan the next window_s
        cur_side, cur_pts, break_t = None, 0, None
        for i, (side, pts, t) in enumerate(seq):
            if side == cur_side:
                cur_pts += pts
                continue
            if cur_pts >= run_min:   # run by cur_side just ended at time t
                opp = cur_side
                run_side = cur_side
                net = 0
                for s2, p2, t2 in seq[i:]:
                    if t2 > t + window_s:
                        break
                    net += p2 if s2 != run_side else -p2
                triggers += 1
                answered += net >= counter_min
            cur_side, cur_pts = side, pts
    return {
        "autocorr": mean(autocorrs) if autocorrs else 0.0,
        "answered_rate": answered / triggers if triggers else 0.0,
        "triggers_pg": triggers / len(sequences),
    }


def compare_run_drought(real_seq, sim_seq) -> None:
    w = 78
    r, s = _run_drought(real_seq), _run_drought(sim_seq)
    print("\n" + "=" * w)
    print(f"  Run & drought analysis   (real games={len(real_seq)}, sim games={len(sim_seq)})")
    print("=" * w)
    print(f"  {'metric':30}{'real':>10}{'sim':>10}{'diff':>10}")
    print(f"    {'mean unanswered run (pts)':28}{_fmt(r['mean_run'], s['mean_run'])}")
    print(f"    {'runs >=6 / game':28}{_fmt(r['runs_6_pg'], s['runs_6_pg'])}")
    print(f"    {'runs >=8 / game':28}{_fmt(r['runs_8_pg'], s['runs_8_pg'])}")
    print(f"    {'runs >=10 / game':28}{_fmt(r['runs_10_pg'], s['runs_10_pg'])}")
    print(f"    {'mean scoring drought (s)':28}{_fmt(r['mean_drought'], s['mean_drought'], '{:.1f}')}")
    print(f"    {'p90 scoring drought (s)':28}{_fmt(r['p90_drought'], s['p90_drought'], '{:.1f}')}")

    rr, sr = _response_stats(real_seq), _response_stats(sim_seq)
    print("\n  run RESPONSE (the refined owner — is a surge answered?):")
    print(f"    {'lag-1 margin autocorr':28}{_fmt(rr['autocorr'], sr['autocorr'], '{:+.3f}')}")
    print(f"    {'answered-run rate (>=8->>=6)':28}{_fmt(rr['answered_rate'], sr['answered_rate'], '{:.3f}')}")
    print(f"    {'>=8 runs / game (triggers)':28}{_fmt(rr['triggers_pg'], sr['triggers_pg'], '{:.2f}')}")
    print("=" * w + "\n")


def run(season: str, sims_per_game: int, config=DRAMA_M3, runs: bool = False) -> None:
    db = SessionLocal()
    real = real_texture(db, season)
    sims = simulate_schedule(db, season, config, sims_per_game)
    sim, lead_changes, pts_by_subtype = sim_texture(f"{season} sim", sims)
    compare(real, sim, lead_changes, pts_by_subtype)
    if runs:
        real_seq = real_sequences(db, season)
        if not real_seq:
            print("  [run/drought] no real scoring events ingested — run the "
                  "/seasons/{season}/play-by-play ingest first.\n")
        else:
            compare_run_drought(real_seq, sim_sequences(sims))
    db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2024-25")
    p.add_argument("--sims", type=int, default=1)
    p.add_argument("--runs", action="store_true", help="include run & drought analysis")
    args = p.parse_args()
    run(args.season, args.sims, runs=args.runs)
