"""Shared machinery for the trade-value validation (see TRADE_VALUE_VALIDATION.md).

Ground truth is the simulator itself: play a team's games with and without a set of players,
on a fixed paired schedule, and compare the mean point margin. Everything here is deterministic
given (season, team, seed_base), so a measurement can be reproduced exactly.
"""
import math
import multiprocessing as mp
import random
from typing import Dict, List, Optional, Sequence, Tuple

SPLIT_SEED = 20261020
G_MEASURE = 1500           # games per condition per seed set (G6a/G6b) — Amendment 1
G_PROPOSAL = 1500          # games per proposal arm (G6c) — Amendment 1
RANKS_SINGLE = (1, 2, 3, 5, 8)
PACKAGES = ((1, 2), (3, 5), (5, 8), (1, 8), (2, 5, 8))
SEED_BASE = {"A": 1, "B": 2, "G6C": 3}
ROSTER_DEPTH = 10          # DRAMA_M3: availability off, depth 10

_STATE: dict = {}


# ------------------------------------------------------------------------- statistics

def rank_avg(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    n = len(x)
    if n < 3:
        return float("nan")
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return float("nan")
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    return pearson(rank_avg(x), rank_avg(y))


def ols_through_origin(x: Sequence[float], y: Sequence[float]) -> float:
    sxx = sum(a * a for a in x)
    return sum(a * b for a, b in zip(x, y)) / sxx if sxx else 0.0


def noise_ceiling(r_ab: float) -> float:
    """Max attainable correlation with the A/B-averaged estimate (Spearman-Brown on the
    split-half reliability r_ab)."""
    if not r_ab or r_ab != r_ab or r_ab <= 0:
        return float("nan")
    return math.sqrt(2 * r_ab / (1 + r_ab))


def cluster_bootstrap(items_by_cluster: Dict[int, list], stat, n_boot: int = 2000, seed: int = 11):
    """Resample whole teams (clusters) with replacement; returns (point, lo95, hi95)."""
    keys = sorted(items_by_cluster)
    rng = random.Random(seed)
    flat = [x for k in keys for x in items_by_cluster[k]]
    point = stat(flat)
    draws = []
    for _ in range(n_boot):
        sample = [x for k in (rng.choice(keys) for _ in keys) for x in items_by_cluster[k]]
        v = stat(sample)
        if v == v:
            draws.append(v)
    draws.sort()
    if not draws:
        return point, float("nan"), float("nan")
    return point, draws[int(0.025 * len(draws))], draws[min(len(draws) - 1, int(0.975 * len(draws)))]


def split_teams(team_ids: Sequence[int]) -> Tuple[List[int], List[int]]:
    """Deterministic 15/15 development/holdout split of the 30 teams (fixed seed)."""
    ids = sorted(team_ids)
    random.Random(SPLIT_SEED).shuffle(ids)
    half = len(ids) // 2
    return sorted(ids[:half]), sorted(ids[half:])


# ---------------------------------------------------------------------- paired schedule

def make_schedule(team_id: int, opp_ids: Sequence[int], games: int, seed_set: str,
                  extra: int = 0) -> List[Tuple[int, bool, int]]:
    """Fixed list of (opponent, team_is_home, seed): the SAME list is replayed for the baseline
    and every removal condition, which is what pairs them. Opponents cycle through `opp_ids`
    (full-strength) and venue alternates."""
    opps = sorted(opp_ids)
    base = SEED_BASE[seed_set]
    return [
        (opps[(g + team_id + extra) % len(opps)], g % 2 == 0,
         (base * 1_000_003 + team_id * 10_007 + extra * 101 + g) % (2 ** 31))
        for g in range(games)
    ]


# ---------------------------------------------------------------------------- simulation

def _init_worker():
    from app.database import engine
    engine.dispose()           # never share a connection across forked processes
    _STATE.clear()


def _db():
    if "db" not in _STATE:
        from app.database import SessionLocal
        _STATE["db"] = SessionLocal()
    return _STATE["db"]


def _cfg():
    from app.api.schemas.simulations import DRAMA_M3
    return DRAMA_M3


def roster(season: str, team_id: int, depth: int = ROSTER_DEPTH, overlay=None) -> list:
    key = ("r", season, team_id, depth, overlay.key() if overlay else None)
    if key not in _STATE:
        from app.services.roster import load_roster
        cfg = _cfg()
        _STATE[key] = load_roster(_db(), team_id, season, depth=depth,
                                  pre_negation=cfg.use_pre_negation_probs,
                                  paint_mid_split=cfg.use_paint_mid_split, overlay=overlay)
    return _STATE[key]


def roster_without(season: str, team_id: int, removed: Sequence[int]) -> list:
    """Mirror advance_to's M-4 backfill: load `depth + n_removed`, then drop the removed."""
    gone = set(removed)
    return [p for p in roster(season, team_id, ROSTER_DEPTH + len(gone)) if p["id"] not in gone]


def play(season: str, team_id: int, team_players: list, opp_id: int, team_home: bool, seed: int):
    from app.services.game_simulator import simulate_game
    opp_players = roster(season, opp_id)
    home, away = (team_players, opp_players) if team_home else (opp_players, team_players)
    hid, aid = (team_id, opp_id) if team_home else (opp_id, team_id)
    r = simulate_game(home, away, seed=seed, season=season, config=_cfg(), db=_db(),
                      home_team_id=hid, away_team_id=aid)
    margin = r["home_score"] - r["away_score"]
    return (margin if team_home else -margin), r


def run_condition(season: str, team_id: int, team_players: list, schedule) -> dict:
    margins, minutes, poss = [], {}, 0
    ids = [p["id"] for p in team_players]
    for opp, home, seed in schedule:
        m, r = play(season, team_id, team_players, opp, home, seed)
        margins.append(m)
        poss += sum(r["possession_accounting"]["counts"].values())
        for pid in ids:
            minutes[pid] = minutes.get(pid, 0.0) + r["box_score"].get(pid, {}).get("min", 0.0)
    n = len(schedule)
    return {"margins": margins, "mean_minutes": {str(k): round(v / n, 3) for k, v in minutes.items()},
            "mean_possessions": poss / n}


def measure_team(task: dict) -> dict:
    """One team x one seed set: baseline plus every removal condition on the same schedule."""
    season, team_id = task["season"], task["team_id"]
    ranked = [p["id"] for p in roster(season, team_id)]           # MPG order from load_roster
    sched = make_schedule(team_id, task["opp_ids"], task["games"], task["seed_set"])
    out = {"base": run_condition(season, team_id, roster(season, team_id), sched)}
    for name, ranks in task["conditions"].items():
        removed = [ranked[r - 1] for r in ranks]
        out[name] = run_condition(season, team_id, roster_without(season, team_id, removed), sched)
        out[name]["removed"] = removed
    return {"team_id": team_id, "seed_set": task["seed_set"], "ranked_ids": ranked, "conditions": out}


def measure_proposal(task: dict) -> dict:
    """One G6c proposal: the CPU team B before vs after the trade, paired on one schedule."""
    from app.services.roster import RosterOverlay
    season, b, a = task["season"], task["cpu_team"], task["proposer"]
    out_ids, in_ids = task["out_ids"], task["in_ids"]        # B gives out_ids, receives in_ids
    opp_ids = [t for t in task["opp_ids"] if t not in (a, b)]   # avoid duplicate-player opponents
    sched = make_schedule(b, opp_ids, task["games"], "G6C", extra=task["index"])
    overlay = RosterOverlay(
        overrides={**{p: a for p in out_ids}, **{p: b for p in in_ids}},
        base_team_of={**{p: b for p in out_ids}, **{p: a for p in in_ids}},
    )
    before = run_condition(season, b, roster(season, b), sched)
    after = run_condition(season, b, roster(season, b, overlay=overlay), sched)
    diffs = [x - y for x, y in zip(after["margins"], before["margins"])]
    n = len(diffs)
    mean = sum(diffs) / n
    sd = math.sqrt(sum((d - mean) ** 2 for d in diffs) / (n - 1))
    return {**{k: task[k] for k in ("index", "type", "cpu_team", "proposer", "out_ids", "in_ids")},
            "delta": mean, "se": sd / math.sqrt(n),
            "before_margin": sum(before["margins"]) / n, "after_margin": sum(after["margins"]) / n}


def run_pool(fn, tasks: Sequence[dict], workers: Optional[int] = None) -> list:
    from app.database import engine
    engine.dispose()
    workers = workers or max(1, min(10, (mp.cpu_count() or 2) - 2))
    with mp.get_context("fork").Pool(workers, initializer=_init_worker) as pool:
        return list(pool.imap_unordered(fn, tasks, chunksize=1))
