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


def run(season: str, sims_per_game: int, config=DRAMA_M3) -> None:
    db = SessionLocal()
    real = real_texture(db, season)
    sims = simulate_schedule(db, season, config, sims_per_game)
    sim, lead_changes, pts_by_subtype = sim_texture(f"{season} sim", sims)
    db.close()
    compare(real, sim, lead_changes, pts_by_subtype)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--season", default="2025-26")
    p.add_argument("--sims", type=int, default=1)
    args = p.parse_args()
    run(args.season, args.sims)
