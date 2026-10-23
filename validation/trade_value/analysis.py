"""Analysis for the trade-value validation (see TRADE_VALUE_VALIDATION.md). Pure given the
measurement JSON plus PlayerSeasonStats; every number reported comes from here."""
import json
import math
import statistics as st
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select

from app.database import SessionLocal
from app.models.player import Player
from app.models.player_season_stats import PlayerSeasonStats
from app.services.trade_value import (
    SPEC_GAME_SCORE, SPEC_NONSHOOTING, SPEC_TS_POINTS, SPEC_TS_POINTS_NS,
    PlayerProduction, ValueModel, build_value_model, cpu_verdict, league_true_shooting,
    production_score, replacement_level,
)
from validation.trade_value import harness as H

K_GRID = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
TIE = 0.01
REGISTERED_SINGLES = ("S1", "S2", "S3", "S5", "S8")      # G6a/G6b; other ranks feed the oracle only
ELIGIBLE_SPECS = (SPEC_GAME_SCORE, SPEC_TS_POINTS, SPEC_TS_POINTS_NS)   # selection order (Amendment 2)
ALL_SPECS = ELIGIBLE_SPECS + (SPEC_NONSHOOTING,)         # the last is an attribution control


# ------------------------------------------------------------------------------ data

def load_productions(season: str) -> Tuple[List[PlayerProduction], Dict[int, str]]:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(PlayerSeasonStats, Player.full_name).join(Player, Player.id == PlayerSeasonStats.player_id)
            .where(PlayerSeasonStats.season == season, PlayerSeasonStats.team_id.isnot(None),
                   PlayerSeasonStats.team_id != 0, PlayerSeasonStats.games_played >= 1)).all()
        prods = [PlayerProduction.from_stats(r) for r, _ in rows]
        names = {r.player_id: n for r, n in rows}
        return prods, names
    finally:
        db.close()


def load_measure(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _impact(base: Sequence[float], cond: Sequence[float]) -> Tuple[float, float]:
    d = [a - b for a, b in zip(base, cond)]
    return st.mean(d), st.stdev(d) / math.sqrt(len(d))


def condition_rows(meas: dict) -> List[dict]:
    """One row per (team, removal condition): impact in sets A and B and their average."""
    rows = []
    for tid, t in meas["teams"].items():
        for name in t["A"]:
            if name == "base":
                continue
            ia, sa = _impact(t["A"]["base"]["margins"], t["A"][name]["margins"])
            ib, sb = _impact(t["B"]["base"]["margins"], t["B"][name]["margins"])
            rows.append({
                "team": int(tid), "cond": name,
                "kind": ("package" if name.startswith("P")
                         else "single" if name in REGISTERED_SINGLES else "extra"),
                "removed": t["A"][name]["removed"], "impA": ia, "impB": ib,
                "imp": (ia + ib) / 2, "se": math.sqrt(sa ** 2 + sb ** 2) / 2,
            })
    return rows


def reliability(rows: List[dict]) -> dict:
    out = {}
    for kind in ("single", "package", "all"):
        sel = [r for r in rows if (r["kind"] in ("single", "package") if kind == "all" else r["kind"] == kind)]
        r_ab = H.spearman([r["impA"] for r in sel], [r["impB"] for r in sel])
        out[kind] = {"n": len(sel), "r_AB": r_ab, "ceiling": H.noise_ceiling(r_ab),
                     "mean_se": st.mean(r["se"] for r in sel)}
    return out


def interaction(rows: List[dict]) -> dict:
    singles = {(r["team"], r["cond"]): r for r in rows if r["kind"] == "single"}
    vals, zs = [], []
    for r in rows:
        if r["kind"] != "package":
            continue
        ranks = [int(x) for x in r["cond"][1:].split("_")]
        parts = [singles[(r["team"], f"S{k}")] for k in ranks]
        add = sum(p["imp"] for p in parts)
        se = math.sqrt(r["se"] ** 2 + sum(p["se"] ** 2 for p in parts))
        vals.append(r["imp"] - add)
        zs.append((r["imp"] - add) / se)
    return {"n": len(vals), "mean": st.mean(vals), "sd": st.stdev(vals),
            "mean_abs_z": st.mean(abs(z) for z in zs),
            "share_beyond_2se": sum(abs(z) > 2 for z in zs) / len(zs)}


def additive_reference(rows: List[dict]) -> float:
    """Spearman of the package impact against the SUM of its members' separately measured
    single impacts: how far an additive per-player model could possibly go."""
    singles = {(r["team"], r["cond"]): r["imp"] for r in rows if r["kind"] == "single"}
    pk = [r for r in rows if r["kind"] == "package"]
    pred = [sum(singles[(r["team"], f"S{k}")] for k in map(int, r["cond"][1:].split("_"))) for r in pk]
    return H.spearman(pred, [r["imp"] for r in pk])


# ---------------------------------------------------------------------------- models

def prod_index(prods: List[PlayerProduction]) -> Dict[Tuple[int, int], PlayerProduction]:
    return {(p.player_id, p.team_id): p for p in prods}


def model_values(rows: List[dict], model: ValueModel, idx) -> List[float]:
    """Summed value of each row's removed players under `model`."""
    return [sum(model.value(idx[(pid, r["team"])]) for pid in r["removed"]) for r in rows]


def variant_scores(rows, prods, k, shrink, avail, spec=SPEC_GAME_SCORE) -> dict:
    model = build_value_model(prods, k=k, shrink=shrink, availability=avail, spec=spec)
    idx = prod_index(prods)
    s = [r for r in rows if r["kind"] == "single"]
    p = [r for r in rows if r["kind"] == "package"]
    reg = s + p
    return {
        "rho_single": H.spearman(model_values(s, model, idx), [r["imp"] for r in s]),
        "rho_package": H.spearman(model_values(p, model, idx), [r["imp"] for r in p]),
        "rho_all": H.spearman(model_values(reg, model, idx), [r["imp"] for r in reg]),
        "beta": H.ols_through_origin(model_values(reg, model, idx), [r["imp"] for r in reg]),
    }


def select_stage1(rows, prods) -> dict:
    """(shrink, availability) at k=1 by dev single-player Spearman; ties -> shrink on, avail off."""
    table = {}
    for shrink in (False, True):
        for avail in (False, True):
            table[f"shrink={shrink},avail={avail}"] = variant_scores(rows, prods, 1.0, shrink, avail)
    best = max(v["rho_single"] for v in table.values())
    tied = [(sh, av) for sh in (True, False) for av in (False, True)
            if table[f"shrink={sh},avail={av}"]["rho_single"] >= best - TIE]
    choice = sorted(tied, key=lambda t: (not t[0], t[1]))[0]      # prefer shrink on, then avail off
    return {"table": table, "best_rho": best, "shrink": choice[0], "availability": choice[1]}


def select_spec(rows, prods, shrink=True, avail=False) -> dict:
    """Amendment 2, step 1: among the eligible specs at k=1, the highest mean of dev single-player
    and dev package rho. A candidate must beat the current best by MORE than TIE to displace it;
    ties keep the earlier spec in ELIGIBLE_SPECS. The attribution control is scored, never chosen."""
    table = {}
    for spec in ALL_SPECS:
        v = variant_scores(rows, prods, 1.0, shrink, avail, spec)
        v["mean_single_package"] = (v["rho_single"] + v["rho_package"]) / 2
        v["eligible"] = spec in ELIGIBLE_SPECS
        table[spec] = v
    best = ELIGIBLE_SPECS[0]
    for spec in ELIGIBLE_SPECS[1:]:
        if table[spec]["mean_single_package"] > table[best]["mean_single_package"] + TIE:
            best = spec
    return {"table": table, "selected": best}


def select_stage2(rows, prods, shrink, avail, spec=SPEC_GAME_SCORE) -> dict:
    """k by dev PACKAGE Spearman; ties within 0.01 -> smaller k."""
    table = {str(k): variant_scores(rows, prods, k, shrink, avail, spec) for k in K_GRID}
    best = max(v["rho_package"] for v in table.values())
    k_star = min(k for k in K_GRID if table[str(k)]["rho_package"] >= best - TIE)
    return {"table": table, "best_rho_package": best, "k_star": k_star}


# --------------------------------------------------------------------------- audits

def replacement_audit(prods: List[PlayerProduction], names: Dict[int, str], spec: str = SPEC_GAME_SCORE) -> dict:
    lg_ts = league_true_shooting(prods)
    score = lambda p: production_score(p, spec, lg_ts)
    rl = replacement_level(prods, score=score)
    means = list(rl.team_means.values())
    sens = {}
    for label, win, gp in (("ranks 11-13, GP>=10 (used)", (11, 13), 10), ("ranks 10-14", (10, 14), 10),
                           ("ranks 12-15", (12, 15), 10), ("ranks 11-13, GP>=20", (11, 13), 20),
                           ("ranks 11-13, GP>=1", (11, 13), 1)):
        try:
            sens[label] = replacement_level(prods, win, gp, score=score).value
        except ValueError:
            sens[label] = None
    model = build_value_model(prods, spec=spec)
    buckets = defaultdict(list)
    for p in prods:
        if p.games_played < 10:
            continue
        b = ("<5" if p.mpg < 5 else "5-10" if p.mpg < 10 else "10-15" if p.mpg < 15 else
             "15-20" if p.mpg < 20 else "20-25" if p.mpg < 25 else "25+")
        buckets[b].append(model.value(p))
    table = {b: {"n": len(v), "share_zero": sum(x == 0 for x in v) / len(v),
                 "median": st.median(v), "max": max(v)} for b, v in buckets.items()}
    low = sorted(((model.value(p), p) for p in prods if p.mpg < 10 and p.games_played >= 10),
                 key=lambda vp: (-vp[0], vp[1].player_id))[:5]
    return {
        "spec": spec, "league_ts": lg_ts,
        "replacement": rl.value, "teams_used": rl.teams_used, "n_players": rl.n_players,
        "team_mean_sd": st.stdev(means), "team_mean_min": min(means), "team_mean_max": max(means),
        "sensitivity": sens, "by_mpg_bucket": table,
        "highest_valued_under_10_mpg": [{"name": names.get(p.player_id, "?"), "mpg": round(p.mpg, 1),
                                         "gp": p.games_played, "score": round(model.score(p), 1),
                                         "value": round(v, 2)} for v, p in low],
        "corr_value_mpg": H.spearman([model.value(p) for p in prods if p.games_played >= 10],
                                     [p.mpg for p in prods if p.games_played >= 10]),
    }


def redistribution(meas: dict) -> dict:
    """Where a removed player's minutes go (mean over teams, seed set A), by group."""
    out = {}
    for name in ("S1", "S2", "S3", "S5", "S8"):
        lost, g_top5, g_next, g_promoted = [], [], [], []
        poss_delta = []
        for t in meas["teams"].values():
            base, cond = t["A"]["base"], t["A"][name]
            ranked = t["ranked_ids"]
            removed = cond["removed"][0]
            lost.append(base["mean_minutes"][str(removed)])
            top5 = [p for p in ranked[:5] if p != removed]
            nxt = [p for p in ranked[5:] if p != removed]
            gain = lambda p: cond["mean_minutes"].get(str(p), 0.0) - base["mean_minutes"].get(str(p), 0.0)
            g_top5.append(sum(gain(p) for p in top5))
            g_next.append(sum(gain(p) for p in nxt))
            g_promoted.append(sum(v for k, v in cond["mean_minutes"].items() if int(k) not in set(ranked)))
            poss_delta.append(cond["mean_possessions"] - base["mean_possessions"])
        out[name] = {"minutes_lost": st.mean(lost), "to_other_top5": st.mean(g_top5),
                     "to_ranks_6_10": st.mean(g_next), "to_promoted_11th_man": st.mean(g_promoted),
                     "possessions_per_game_change": st.mean(poss_delta)}
    return out


# ------------------------------------------------------------------------------ G6c

def classify(delta: float, tau: float) -> str:
    return "beneficial" if delta >= tau else ("harmful" if delta < -tau else "neutral")


def g6c_rows(results: List[dict], model: ValueModel, idx) -> List[dict]:
    rows = []
    for r in results:
        inc = [(p, model.value(idx[(p, r["proposer"])]) if (p, r["proposer"]) in idx else None) for p in r["in_ids"]]
        out = [(p, model.value(idx[(p, r["cpu_team"])]) if (p, r["cpu_team"]) in idx else None) for p in r["out_ids"]]
        v = cpu_verdict(inc, out)
        rows.append({**r, "accepted": v.accepted, "code": v.code, "incoming": v.incoming,
                     "outgoing": v.outgoing, "dV": v.incoming - v.outgoing, "ratio": v.ratio})
    return rows


def oracle_table(meas: dict) -> Dict[int, Dict[int, float]]:
    """Measured single-player impact (A/B average) per team and player, for every rank measured."""
    table: Dict[int, Dict[int, float]] = {}
    for tid, t in meas["teams"].items():
        d = {}
        for name, c in t["A"].items():
            if name.startswith("S") and name != "base":
                ia, _ = _impact(t["A"]["base"]["margins"], c["margins"])
                ib, _ = _impact(t["B"]["base"]["margins"], t["B"][name]["margins"])
                d[c["removed"][0]] = (ia + ib) / 2
        table[int(tid)] = d
    return table


def g6c_rows_oracle(results: List[dict], table: Dict[int, Dict[int, float]], mode: str) -> Optional[List[dict]]:
    """The same proposals judged with MEASURED impacts instead of a formula. `ratio` applies the
    locked 1.1x rule to impacts clamped at 0; `diff` accepts iff incoming impact >= outgoing.
    Returns None when some player in a proposal was not measured (e.g. a dry run on dev data)."""
    rows = []
    for r in results:
        try:
            inc = [(p, table[r["proposer"]][p]) for p in r["in_ids"]]
            out = [(p, table[r["cpu_team"]][p]) for p in r["out_ids"]]
        except KeyError:
            return None
        if mode == "ratio":
            v = cpu_verdict([(p, max(0.0, x)) for p, x in inc], [(p, max(0.0, x)) for p, x in out])
            rows.append({**r, "accepted": v.accepted, "code": v.code, "incoming": v.incoming,
                         "outgoing": v.outgoing, "dV": v.incoming - v.outgoing, "ratio": v.ratio})
        else:
            tin, tout = sum(x for _, x in inc), sum(x for _, x in out)
            rows.append({**r, "accepted": tin >= tout, "code": "ACCEPT" if tin >= tout else "VALUE_SHORTFALL",
                         "incoming": tin, "outgoing": tout, "dV": tin - tout, "ratio": None})
    return rows


def _rate(num, den):
    return num / den if den else float("nan")


def g6c_metrics(rows: List[dict], tau: float = 1.0, boot: bool = True) -> dict:
    def core(rs):
        acc = [r for r in rs if r["accepted"]]
        rej = [r for r in rs if not r["accepted"]]
        pres = lambda r: r["delta"] >= -tau
        ben = lambda r: r["delta"] >= tau
        base = _rate(sum(pres(r) for r in rs), len(rs))
        p_acc = _rate(sum(pres(r) for r in acc), len(acc))
        n_ben = sum(ben(r) for r in rs)
        return {
            "n": len(rs), "accept_rate": _rate(len(acc), len(rs)),
            "base_rate_preserve": base, "p_preserve_given_accepted": p_acc,
            "lift_vs_base": p_acc - base if p_acc == p_acc else float("nan"),
            "p_harmful_given_accepted": _rate(sum(r["delta"] < -tau for r in acc), len(acc)),
            "p_beneficial_given_rejected": _rate(sum(ben(r) for r in rej), len(rej)),
            "p_accepted_given_beneficial": _rate(sum(ben(r) and r["accepted"] for r in rs), n_ben),
            "mean_delta_accepted": st.mean(r["delta"] for r in acc) if acc else float("nan"),
            "mean_delta_rejected": st.mean(r["delta"] for r in rej) if rej else float("nan"),
            "spearman_dV_vs_delta": H.spearman([r["dV"] for r in rs], [r["delta"] for r in rs]),
            "share_unresolvable_2se": _rate(sum(abs(r["delta"]) < 2 * r["se"] for r in rs), len(rs)),
        }
    out = {"tau": tau, "overall": core(rows), "by_type": {}}
    for ty in sorted({r["type"] for r in rows}):
        out["by_type"][ty] = core([r for r in rows if r["type"] == ty])
    if boot:
        by_team = defaultdict(list)
        for r in rows:
            by_team[r.get("cluster", r["cpu_team"])].append(r)
        for key in ("p_preserve_given_accepted", "lift_vs_base", "accept_rate", "p_accepted_given_beneficial"):
            out["overall"][key + "_ci"] = H.cluster_bootstrap(by_team, lambda rs, k=key: core(rs)[k], n_boot=1000)[1:]
    srt = sorted(rows, key=lambda r: r["dV"])
    cuts = [round(i * len(srt) / 5) for i in range(6)]
    out["calibration_quintiles"] = [
        {"quintile": i + 1, "n": len(chunk), "mean_dV": st.mean(r["dV"] for r in chunk),
         "mean_delta": st.mean(r["delta"] for r in chunk)}
        for i in range(5) if (chunk := srt[cuts[i]:cuts[i + 1]])
    ]
    return out
