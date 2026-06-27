"""Possession resolution — simulate one possession and return an event dict."""
import random
from typing import Dict, Optional

from app.services.modifiers.base import ModifierAdjustments

OREB_RATE = 0.22            # offensive rebound rate on missed shots (NBA avg ~22%)
LEAGUE_AVG_TOV_PER36 = 2.5  # used to normalize per-player turnover rates


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

    shot_labels = {"three": "3-pointer", "mid": "mid-range jumper", "close": "layup/close shot"}
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

    # 4. Shot type selection
    three_rate = ball_handler["three_point_rate"]
    if adjustments and adjustments.three_rate_override:
        three_rate = min(0.60, max(0.0, three_rate + adjustments.three_rate_override))
    if is_fastbreak:
        shot_type = rng.choices(
            ["three", "mid", "close"], weights=[0.05, 0.10, 0.85]
        )[0]
    else:
        shot_type = rng.choices(
            ["three", "mid", "close"],
            weights=[three_rate, (1 - three_rate) * 0.4, (1 - three_rate) * 0.6],
        )[0]
    result["shot_type"] = shot_type
    result["scorer"] = ball_handler["id"]

    # 5. Block check — skipped on fast breaks (defender scrambling back)
    if shot_type != "three" and not is_fastbreak:
        best_blocker = max(defense, key=lambda p: p["block"])
        if rng.random() < (best_blocker["block"] / 100.0) * 0.04:
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

    # 6. Defender
    defender = rng.choice(defense)

    # 7. Make/miss
    if shot_type == "three":
        base_prob = attr_to_prob(ball_handler["three_point"], lo=0.38, hi=0.44)
        defense_penalty = defender["perimeter_defense"] / 100.0 * 0.06
    elif shot_type == "mid":
        base_prob = attr_to_prob(ball_handler["mid_range"], lo=0.51, hi=0.58)
        defense_penalty = defender["perimeter_defense"] / 100.0 * 0.05
    else:
        base_prob = attr_to_prob(ball_handler["close_shot"], lo=0.65, hi=0.72)
        defense_penalty = defender["interior_defense"] / 100.0 * 0.08

    if is_fastbreak:
        if shot_type == "close":
            base_prob = min(base_prob + 0.08, 0.85)
        defense_penalty *= 0.80

    shot_prob = (base_prob - defense_penalty + home_bonus / 100.0) * team_defense_factor
    _shot_delta = (adjustments.shot_prob_delta + adjustments.defense_penalty_delta) if adjustments else 0.0
    # Form factor: per-game variance drawn at game start; applied as a probability offset.
    # (form_factor - 1.0) converts e.g. 1.10 → +0.10 multiplied by base_prob to stay proportional.
    _form_delta = (form_factors[ball_handler["id"]] - 1.0) * base_prob if (form_factors and ball_handler["id"] in form_factors) else 0.0
    result["made"] = rng.random() < max(0.05, min(0.95, shot_prob + _shot_delta + _form_delta))

    ft_prob = attr_to_prob(ball_handler["free_throw"], lo=0.60, hi=0.95)

    # 8a. 3PT shooting foul (~2% of 3PT attempts)
    if shot_type == "three" and rng.random() < 0.02:
        result["fouled_by"] = defender["id"]
        if result["made"]:
            result["fta"] = 1
            result["ftm"] = 1 if rng.random() < ft_prob else 0
        else:
            result["fta"] = 3
            result["ftm"] = sum(1 for _ in range(3) if rng.random() < ft_prob)

    # 8b. 2PT shooting foul (~15% of non-3PT attempts)
    elif shot_type != "three" and rng.random() < 0.15:
        result["fouled_by"] = defender["id"]
        if result["made"]:
            result["fta"] = 1
            result["ftm"] = 1 if rng.random() < ft_prob else 0
        else:
            result["fta"] = 2
            result["ftm"] = sum(1 for _ in range(2) if rng.random() < ft_prob)

    # 9. Assist
    if result["made"]:
        ast_rate = 0.65 if shot_type in ("three", "mid") else 0.50
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
