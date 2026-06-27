"""Possession resolution — simulate one possession and return an event dict."""
import random
from typing import Dict, Optional

from app.services.modifiers.base import ModifierAdjustments

OREB_RATE = 0.22            # offensive rebound rate on missed shots (NBA avg ~22%)
LEAGUE_AVG_TOV_PER36 = 2.5  # used to normalize per-player turnover rates

# ---------------------------------------------------------------------------
# M3d — Shot sub-type constants
# ---------------------------------------------------------------------------

# Positional defaults for within-bucket sub-type rates.
# Keys are replaceable by player tendency fields (archetypes, shot-location data) in future
# without changing the selection interface — callers use ball_handler.get(key, default).
_POSITIONAL_DEFAULTS: Dict[str, Dict[str, float]] = {
    # floater rates kept low — floaters are specialist shots; inflating rate deflates avg close FG%
    "guard": {"corner_three_rate": 0.25, "dunk_rate": 0.05, "floater_rate": 0.05},
    "wing":  {"corner_three_rate": 0.35, "dunk_rate": 0.20, "floater_rate": 0.07},
    "big":   {"corner_three_rate": 0.10, "dunk_rate": 0.50, "floater_rate": 0.03},
}

# (attr_key, lo, hi) per sub-type.
# Ranges: layup/mid anchored to pre-M3d close/mid ranges so sub-types add differentiation
# without a net scoring shift. Floater sits between mid and layup (NBA floater ~38-48% FG).
# Fallback keys ("three", "mid", "close") keep backward compat when use_shot_subtypes=False.
_SUB_TYPE_SPECS: Dict[str, tuple] = {
    "corner_three":      ("three_point", 0.40, 0.46),
    "above_break_three": ("three_point", 0.36, 0.42),
    "mid_range":         ("mid_range",   0.51, 0.58),  # identical to pre-M3d mid range
    "floater":           ("close_shot",  0.59, 0.67),  # above mid, below layup
    "layup":             ("layup",       0.65, 0.72),  # identical to pre-M3d close range
    "dunk":              ("dunk",        0.68, 0.76),
    # fallback — used when use_shot_subtypes=False
    "three":             ("three_point", 0.38, 0.44),
    "mid":               ("mid_range",   0.51, 0.58),
    "close":             ("close_shot",  0.65, 0.72),
}

# Sub-types where a block is physically possible
_BLOCK_ELIGIBLE = frozenset({"layup", "dunk", "floater", "close"})

# Multiplier on base block rate per sub-type.
# Dunks and floaters are harder to cleanly block (arc/power), hence 0.5×.
_BLOCK_MULT: Dict[str, float] = {"layup": 1.0, "dunk": 0.5, "floater": 0.5, "close": 1.0}

# Defense attribute used for contest probability by sub-type
_PERIMETER_TYPES = frozenset({"corner_three", "above_break_three", "mid_range", "three", "mid"})
_INTERIOR_TYPES  = frozenset({"layup", "dunk", "close"})

# Contest probability scaling by sub-type — how reliably a defender can physically reach the shot.
# Corner threes and floaters are harder to contest (rotation distance, arc).
_CONTEST_REACH: Dict[str, float] = {
    "corner_three":      0.65,
    "above_break_three": 0.80,
    "mid_range":         0.85,
    "floater":           0.60,
    "layup":             0.90,
    "dunk":              0.75,
    # fallback
    "three": 0.75, "mid": 0.85, "close": 0.90,
}

# When contested: multiplier on the raw defense_penalty (separates reach from impact).
# Dunk contests carry foul risk, inflating impact; corner threes are hard to impact cleanly.
_CONTEST_IMPACT: Dict[str, float] = {
    "corner_three":      0.80,
    "above_break_three": 1.00,
    "mid_range":         1.00,
    "floater":           0.85,
    "layup":             1.10,
    "dunk":              1.20,
    # fallback
    "three": 1.00, "mid": 1.00, "close": 1.10,
}

# Position group sets — used for matchup filtering.
# Intentionally module-level so future matchup systems can import them.
_GUARD_POSITIONS = frozenset({"G", "G-F"})
_WING_POSITIONS  = frozenset({"F", "F-G", "F-C"})


def _position_group(pos: str) -> str:
    if pos in _GUARD_POSITIONS:
        return "guard"
    if pos in _WING_POSITIONS:
        return "wing"
    return "big"


def _select_sub_type(ball_handler: dict, shot_type: str, rng: random.Random) -> str:
    """Derive the shot sub-type from the coarse bucket and player/positional rates.

    Designed so player tendency fields (corner_three_rate, dunk_rate, floater_rate)
    can override positional defaults once ingested, without changing this interface.
    """
    pos_group = _position_group(ball_handler.get("position", "F"))
    defaults = _POSITIONAL_DEFAULTS[pos_group]

    if shot_type == "three":
        corner_rate = ball_handler.get("corner_three_rate", defaults["corner_three_rate"])
        return rng.choices(
            ["corner_three", "above_break_three"],
            weights=[corner_rate, 1.0 - corner_rate],
        )[0]

    if shot_type == "mid":
        return "mid_range"

    # close
    dunk_rate    = ball_handler.get("dunk_rate",    defaults["dunk_rate"])
    floater_rate = ball_handler.get("floater_rate", defaults["floater_rate"])
    layup_rate   = max(0.0, 1.0 - dunk_rate - floater_rate)
    return rng.choices(
        ["dunk", "layup", "floater"],
        weights=[dunk_rate, layup_rate, floater_rate],
    )[0]


def attr_to_prob(rating: int, lo: float = 0.25, hi: float = 0.75) -> float:
    return lo + (rating / 100.0) * (hi - lo)


def describe_event(event: dict, name_map: dict) -> str:
    """Return a human-readable description of a possession event."""
    def name(pid: Optional[int]) -> str:
        return name_map.get(pid, f"Player {pid}") if pid else "Unknown"

    scorer = event.get("scorer")

    if event.get("turnover_by"):
        if event.get("steal_by"):
            return f"{name(event['turnover_by'])} turns it over — stolen by {name(event['steal_by'])}"
        if event.get("fouled_by") == event.get("turnover_by"):
            return f"{name(event['turnover_by'])} commits an offensive foul"
        return f"{name(event['turnover_by'])} turns it over"

    if not event.get("shot_type"):
        ftm, fta = event.get("ftm", 0), event.get("fta", 0)
        return f"{name(scorer)} shoots {ftm}/{fta} FTs (bonus foul)"

    shot_labels = {
        "three": "3-pointer", "mid": "mid-range jumper", "close": "layup/close shot",
        "corner_three": "corner 3-pointer", "above_break_three": "3-pointer",
        "mid_range": "mid-range jumper", "floater": "floater",
        "layup": "layup", "dunk": "dunk",
    }
    shot = shot_labels.get(event["shot_type"], "shot")

    if event.get("block_by"):
        return f"{name(scorer)} blocked by {name(event['block_by'])}"

    if not event.get("made"):
        desc = f"{name(scorer)} misses a {shot}"
        if event.get("rebounded_by"):
            reb_type = "offensive rebound" if event.get("is_oreb") else "defensive rebound"
            desc += f" — {name(event['rebounded_by'])} ({reb_type})"
        if event.get("fta"):
            desc += f" — shooting foul, {event['ftm']}/{event['fta']} FTs"
        return desc

    desc = f"{name(scorer)} hits a {shot}"
    if event.get("assisted_by"):
        desc += f" (assisted by {name(event['assisted_by'])})"
    if event.get("fta"):
        desc += f" — and-1, {event['ftm']}/1 FT"
    return desc


def resolve_possession(
    offense: list[dict],
    defense: list[dict],
    rng: random.Random,
    home_bonus: float = 0.0,
    name_map: Optional[dict] = None,
    team_defense_factor: float = 1.0,
    is_fastbreak: bool = False,
    adjustments: Optional[ModifierAdjustments] = None,
    form_factors: Optional[Dict[int, float]] = None,
    offense_oreb_rate: float = OREB_RATE,
    use_shot_subtypes: bool = False,
    use_contest_model: bool = False,
    use_positional_matchups: bool = False,
) -> dict:
    """Simulate one possession and return an event dict."""
    def _done(r: dict) -> dict:
        if name_map is not None:
            r["description"] = describe_event(r, name_map)
        return r

    result: dict = {
        "scorer": None, "shot_type": None, "made": False,
        "assisted_by": None, "rebounded_by": None, "is_oreb": False,
        "turnover_by": None, "steal_by": None, "block_by": None,
        "fouled_by": None, "fta": 0, "ftm": 0,
    }

    # 1. Ball handler — weighted by usage rate
    usage_weights = [p["usage_rate"] for p in offense]
    total_usage = sum(usage_weights)
    ball_handler = rng.choices(offense, weights=[w / total_usage for w in usage_weights])[0]

    # 1b. Bonus foul — approximates non-shooting fouls when team is over the foul limit
    if rng.random() < 0.055:
        result["scorer"] = ball_handler["id"]
        result["fouled_by"] = rng.choice(defense)["id"]
        ft_prob = attr_to_prob(ball_handler["free_throw"], lo=0.60, hi=0.95)
        result["fta"] = 2
        result["ftm"] = sum(1 for _ in range(2) if rng.random() < ft_prob)
        return _done(result)

    # 2. Steal check (~1.7% of possessions)
    best_defender = max(defense, key=lambda p: p["steal"])
    if rng.random() < (best_defender["steal"] / 100.0) * 0.034:
        result["turnover_by"] = ball_handler["id"]
        result["steal_by"] = best_defender["id"]
        return _done(result)

    # 3. Turnover — bad pass, travel, etc.
    # tov_prob_delta from modifiers raises/lowers unforced turnover rate;
    # the steal check above is intentionally unchanged (defender skill, not pressure).
    _tov_delta = adjustments.tov_prob_delta if adjustments else 0.0
    _tov_prob = max(0.02, (ball_handler["turnover_rate"] / LEAGUE_AVG_TOV_PER36) * 0.13 + _tov_delta)
    if rng.random() < _tov_prob:
        result["turnover_by"] = ball_handler["id"]
        return _done(result)

    # 3b. Offensive foul — charge or illegal screen (~1.5% of possessions)
    if rng.random() < 0.015:
        result["turnover_by"] = ball_handler["id"]
        result["fouled_by"] = ball_handler["id"]
        return _done(result)

    # 4. Shot type selection — three_rate_override applied before sub-type derivation
    three_rate = ball_handler["three_point_rate"]
    if adjustments and adjustments.three_rate_override:
        three_rate = min(0.60, max(0.0, three_rate + adjustments.three_rate_override))

    if is_fastbreak:
        coarse_type = rng.choices(
            ["three", "mid", "close"], weights=[0.05, 0.10, 0.85]
        )[0]
    else:
        coarse_type = rng.choices(
            ["three", "mid", "close"],
            weights=[three_rate, (1 - three_rate) * 0.4, (1 - three_rate) * 0.6],
        )[0]

    sub_type = _select_sub_type(ball_handler, coarse_type, rng) if use_shot_subtypes else coarse_type
    result["shot_type"] = sub_type
    result["scorer"] = ball_handler["id"]

    # 5. Block check
    # With sub-types: only block-eligible shots can be blocked; rate scaled by sub-type.
    # Without sub-types: original behavior (non-three, non-fastbreak).
    block_eligible = (sub_type in _BLOCK_ELIGIBLE) if use_shot_subtypes else (coarse_type != "three")
    if block_eligible and not is_fastbreak:
        block_mult = _BLOCK_MULT.get(sub_type, 1.0) if use_shot_subtypes else 1.0
        best_blocker = max(defense, key=lambda p: p["block"])
        if rng.random() < (best_blocker["block"] / 100.0) * 0.04 * block_mult:
            result["block_by"] = best_blocker["id"]
            if rng.random() < offense_oreb_rate:
                result["rebounded_by"] = rng.choices(
                    offense, weights=[p["oreb_rate"] for p in offense]
                )[0]["id"]
                result["is_oreb"] = True
            else:
                result["rebounded_by"] = rng.choices(
                    defense, weights=[p["dreb_rate"] for p in defense]
                )[0]["id"]
            return _done(result)

    # 6. Defender selection
    # With positional matchups: filter to same position group, fall back to full pool if none.
    # Defender is chosen uniformly within the matched pool — attributes affect outcome (step 7),
    # not selection frequency. This leaves room for explicit assignment logic in future phases.
    if use_positional_matchups:
        pos_group = _position_group(ball_handler.get("position", "F"))
        candidates = [p for p in defense if _position_group(p.get("position", "F")) == pos_group]
        if not candidates:
            candidates = defense
        defender = rng.choice(candidates)
    else:
        defender = rng.choice(defense)

    # 7. Base probability and defense penalty by sub-type
    attr_key, lo, hi = _SUB_TYPE_SPECS[sub_type]
    base_prob = attr_to_prob(ball_handler.get(attr_key, ball_handler["close_shot"]), lo=lo, hi=hi)

    if sub_type in ("corner_three", "above_break_three", "three"):
        defense_penalty = defender["perimeter_defense"] / 100.0 * 0.06
    elif sub_type in ("mid_range", "mid"):
        defense_penalty = defender["perimeter_defense"] / 100.0 * 0.05
    elif sub_type in _INTERIOR_TYPES:
        defense_penalty = defender["interior_defense"] / 100.0 * 0.08
    else:  # floater — blend
        defense_penalty = (
            defender["interior_defense"] * 0.6 + defender["perimeter_defense"] * 0.4
        ) / 100.0 * 0.07

    if is_fastbreak:
        if coarse_type == "close":
            base_prob = min(base_prob + 0.08, 0.85)
        defense_penalty *= 0.80

    # 7b. Contest model — separates whether the defender reaches the shot (contest_prob)
    # from how much that contest affects the outcome (contest_impact).
    if use_contest_model:
        def_attr = (
            defender["perimeter_defense"] if sub_type in _PERIMETER_TYPES
            else defender["interior_defense"]
        )
        contest_prob = (def_attr / 100.0) * _CONTEST_REACH[sub_type]
        is_contested = rng.random() < contest_prob
        # Non-contested = baseline penalty unchanged (1.0×); contested = CONTEST_IMPACT deviation.
        # This makes the model additive texture (harder dunks, easier corner threes when contested)
        # rather than a systematic open-shot bonus that inflates scoring globally.
        contest_mult = _CONTEST_IMPACT[sub_type] if is_contested else 1.0
        defense_penalty *= contest_mult

    shot_prob = (base_prob - defense_penalty + home_bonus / 100.0) * team_defense_factor
    _shot_delta = (adjustments.shot_prob_delta + adjustments.defense_penalty_delta) if adjustments else 0.0
    # Form factor: per-game variance drawn at game start; applied as a probability offset.
    # (form_factor - 1.0) converts e.g. 1.10 → +0.10 multiplied by base_prob to stay proportional.
    _form_delta = (
        (form_factors[ball_handler["id"]] - 1.0) * base_prob
        if (form_factors and ball_handler["id"] in form_factors)
        else 0.0
    )
    result["made"] = rng.random() < max(0.05, min(0.95, shot_prob + _shot_delta + _form_delta))

    ft_prob = attr_to_prob(ball_handler["free_throw"], lo=0.60, hi=0.95)

    # 8a. 3PT shooting foul (~2% of 3PT attempts)
    if coarse_type == "three" and rng.random() < 0.02:
        result["fouled_by"] = defender["id"]
        if result["made"]:
            result["fta"] = 1
            result["ftm"] = 1 if rng.random() < ft_prob else 0
        else:
            result["fta"] = 3
            result["ftm"] = sum(1 for _ in range(3) if rng.random() < ft_prob)

    # 8b. 2PT shooting foul (~15% of non-3PT attempts)
    elif coarse_type != "three" and rng.random() < 0.15:
        result["fouled_by"] = defender["id"]
        if result["made"]:
            result["fta"] = 1
            result["ftm"] = 1 if rng.random() < ft_prob else 0
        else:
            result["fta"] = 2
            result["ftm"] = sum(1 for _ in range(2) if rng.random() < ft_prob)

    # 9. Assist
    if result["made"]:
        ast_rate = 0.65 if coarse_type in ("three", "mid") else 0.50
        if rng.random() < ast_rate:
            passers = [p for p in offense if p["id"] != ball_handler["id"]]
            if passers:
                result["assisted_by"] = rng.choices(
                    passers, weights=[p["assist_rate"] for p in passers]
                )[0]["id"]

    # 10. Rebound on miss
    if not result["made"] and result["fta"] == 0:
        if rng.random() < offense_oreb_rate:
            result["rebounded_by"] = rng.choices(
                offense, weights=[p["oreb_rate"] for p in offense]
            )[0]["id"]
            result["is_oreb"] = True
        else:
            result["rebounded_by"] = rng.choices(
                defense, weights=[p["dreb_rate"] for p in defense]
            )[0]["id"]

    return _done(result)
