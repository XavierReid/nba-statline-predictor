"""Lineup-creation shot-quality term (gap 3.4g) — EXPERIMENTAL, representation not yet chosen.

The decomposition + discriminator proved that an elite creator lifts TEAMMATES' own shot
quality (same teammates shoot ~+1.8 eFG worse when he sits), and that this is a lineup
INTERACTION, not player valuation or assist generation. This module is the hook for it:
the shooter's make probability shifts with the CREATION quality of the OTHER four on the
floor, derived from EXISTING calibrated attributes — NOT a new persistent "gravity" number.

Deliberately PLUGGABLE: `CREATION_FORMS` holds candidate definitions of "creation" and the
paired counterfactual harness (scratch/gap34g_creation_sweep.py) chooses one by fit rather
than baking `usage x (passing + spacing)` as an architectural assumption. The engine hook is
fixed (mean-zero lineup term at _evaluate_shot); only `f` is experimental.

Mean-zero: each form is normalized to league-mean 1.0, so a league-AVERAGE supporting cast
contributes 0 shift (aggregate shooting calibration preserved); only above/below-average
creation moves make prob. Uniform across shot types for now — `shift()` takes the shot
sub_type so shot-type weighting can be added later without changing callers.
"""
from typing import Callable, Dict, List, Optional

# Candidate creation forms. Each maps a dict of the player's MEAN-1 normalized offensive
# attributes -> a raw creation scalar. The composite is re-normalized to mean 1 in
# set_league_baseline, so mixing scales inside a form is fine.
CREATION_FORMS: Dict[str, Callable[[dict], float]] = {
    "passing": lambda n: n["passing"],
    "usage": lambda n: n["usage"],
    "assist": lambda n: n["assist"],
    "usage_passing_add": lambda n: 0.5 * (n["usage"] + n["passing"]),
    "usage_passing_mul": lambda n: n["usage"] * n["passing"],
    "usage_pass_space": lambda n: n["usage"] * 0.5 * (n["passing"] + n["three"]),
    "pass_space_add": lambda n: 0.5 * (n["passing"] + n["three"]),
    "equal_linear": lambda n: (n["usage"] + n["passing"] + n["assist"] + n["three"]) / 4.0,
}

_ATTR_KEYS = {"usage": "usage_rate", "passing": "passing", "assist": "assist_rate",
              "three": "three_point"}

# league baselines, keyed by form: {"attr_means": {...}, "composite_mean": float}
_BASELINE: Dict[str, dict] = {}
# (season, form) pairs whose league baseline has been established, so a season sim pays the
# league-wide roster scan once, not per game.
_LEAGUE_DONE = set()


def ensure_league_baseline(db, season: str, form: str) -> None:
    """Establish the league mean-1 baseline for (season, form) once, from EVERY team's roster
    (same attribute derivation the sim uses, so creation values are self-consistent). Cached."""
    key = (season, form)
    if key in _LEAGUE_DONE:
        return
    from sqlalchemy import select
    from app.models.team import Team
    from app.services.roster import load_roster
    players = []
    for t in db.execute(select(Team)).scalars().all():
        players.extend(load_roster(db, t.id, season))
    if players:
        set_league_baseline(players, form)
    _LEAGUE_DONE.add(key)


def _norm_attrs(player: dict, attr_means: Dict[str, float]) -> dict:
    return {k: (player.get(src, 0.0) or 0.0) / attr_means[k] if attr_means[k] else 1.0
            for k, src in _ATTR_KEYS.items()}


def set_league_baseline(players: List[dict], form: str) -> None:
    """Compute league means so the form is mean-1. Call once per (season, form) over the
    whole player pool before simulating."""
    n = len(players) or 1
    attr_means = {k: (sum((p.get(src) or 0.0) for p in players) / n) or 1.0
                  for k, src in _ATTR_KEYS.items()}
    fn = CREATION_FORMS[form]
    comps = [fn(_norm_attrs(p, attr_means)) for p in players]
    comp_mean = (sum(comps) / len(comps)) if comps else 1.0
    cm = comp_mean or 1.0
    normed = [c / cm for c in comps]
    var = sum((c - 1.0) ** 2 for c in normed) / len(normed) if normed else 0.0
    _BASELINE[form] = {"attr_means": attr_means, "composite_mean": cm, "std": var ** 0.5}


def creation_std(form: str) -> float:
    """League std of the mean-1 creation value — lets a harness set k per form so different
    forms are compared at matched shift variance rather than a raw common coefficient."""
    b = _BASELINE.get(form)
    return b["std"] if b else 0.0


def creation_value(player: dict, form: str) -> float:
    """Player's mean-1 creation under `form`. 1.0 for an average creator, >1 elite."""
    b = _BASELINE.get(form)
    if b is None:
        return 1.0
    raw = CREATION_FORMS[form](_norm_attrs(player, b["attr_means"]))
    return raw / b["composite_mean"]


def annotate_team_baseline(full_roster: List[dict], form: str) -> None:
    """Write each player's TEAM-RELATIVE creation reference (minutes-weighted mean creation
    over the team's FULL roster) onto p['_creation_ref']. Computed at full strength and
    persisted on the dicts, so it does NOT move when a player is later sat/removed — that
    invariance is what lets the shift go negative under absence instead of cancelling.
    League-centering (ref=1.0) failed the sweep by inflating above-average casts at full
    strength; team-centering is 0 at full strength PER TEAM, preserving each team's eFG.

    The reference is the MINUTES-WEIGHTED mean creation over the full roster — the balanced
    choice: starter lineups run slightly above it, bench lineups slightly below, and the two
    net ~0 across a game (a starter-UNIT reference instead over-penalized all bench minutes,
    −5.5 pts/game). A creator's absence drops the on-floor others below this reference (the
    drop we want) while aggregate league scoring stays ~neutral."""
    wsum = sum((p.get("mpg") or p.get("minutes") or 0.0) for p in full_roster) or 1.0
    ref = sum((p.get("mpg") or p.get("minutes") or 0.0) * creation_value(p, form)
              for p in full_roster) / wsum
    for p in full_roster:
        p["_creation_ref"] = ref


def shift(shooter: dict, offense: List[dict], form: str, k: float,
          sub_type: Optional[str] = None) -> float:
    """Additive make-prob shift for `shooter` from the OTHER four's creation quality,
    centered on the shooter's TEAM baseline (p['_creation_ref'], set at full strength).
    ~0 when the on-floor cast matches the team's full-strength cast; negative when a
    creator is missing. `sub_type` is accepted (unused for now) so shot-type weighting can
    be added later without touching the call site."""
    if k == 0.0 or form not in _BASELINE:
        return 0.0
    others = [p for p in offense if p["id"] != shooter["id"]]
    if not others:
        return 0.0
    mean_c = sum(creation_value(p, form) for p in others) / len(others)
    ref = shooter.get("_creation_ref", 1.0)
    return k * (mean_c - ref)
