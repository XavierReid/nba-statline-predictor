"""Possession resolution — simulate one possession and return an event dict."""
import random
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from app.services.behavior_profile import NORMAL_PROFILE


def _profile(ctx):
    """The active BehaviorProfile for this possession (identity when none set)."""
    return ctx.behavior_profile or NORMAL_PROFILE

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

# Sub-type -> observed zone make-prob key on the player (roster.py). When present,
# the 2pt base make probability IS the shooter's real (era-embedded) zone FG%
# instead of an attribute→modern-band round-trip. Threes are unchanged (already
# accurate). Absent for synthetic rosters / seasons without shot data -> attr band.
_OBSERVED_ZONE_KEY: Dict[str, str] = {
    "dunk": "rim_fg_prob", "layup": "rim_fg_prob", "close": "rim_fg_prob",
    "floater": "nonrim_fg_prob",
    "mid_range": "nonrim_fg_prob", "mid": "nonrim_fg_prob",
    "corner_three": "three_fg_prob", "above_break_three": "three_fg_prob", "three": "three_fg_prob",
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

# Stage B signal gain — league-average make probability per sub-type, measured from
# the engine itself (300 games DRAMA_M3, 2026-07-08, post attribute-v2). signal_gain
# stretches each shot's deviation from these anchors, amplifying player/team
# differentiation while holding league scoring fixed by construction. Re-measure
# whenever base probability ranges or attribute derivation change.
_LEAGUE_AVG_MAKE: Dict[str, float] = {
    "corner_three":      0.410,
    "above_break_three": 0.370,
    "mid_range":         0.530,
    "floater":           0.603,
    "layup":             0.658,
    "dunk":              0.688,
    # coarse fallbacks (use_shot_subtypes=False) — blended from sub-type anchors
    "three": 0.380, "mid": 0.530, "close": 0.660,
}

# Position group sets — used for matchup filtering.
# Intentionally module-level so future matchup systems can import them.
# M3e — foul drawing tendency constants
# Shot-type multipliers on the player's bonus foul draw probability.
# Interior attacks create more pre-shot contact than perimeter catches-and-shoots.
# Values estimated from positional foul-drawing patterns; not derived from shot-zone FTA charts.
_FOUL_DRAW_MULT: Dict[str, float] = {
    "dunk":              1.5,
    "layup":             1.3,
    "floater":           1.1,
    "mid_range":         0.9,
    "above_break_three": 0.75,
    "corner_three":      0.65,
    # coarse fallbacks (use_shot_subtypes=False)
    "close": 1.3,
    "mid":   0.9,
    "three": 0.75,
}

# League-average foul drawing rate (FTA/FGA) — used as the floor for players without history.
# NBA 2024-25: ~0.22 FTA per FGA league-wide.
_LEAGUE_AVG_FOUL_DRAW_RATE: float = 0.22

# Cap on foul_drawing_rate — low-FGA players produce absurd FTA/FGA ratios (seeded max 1.92).
# Elite real foul drawers (Giannis, Embiid) sit near 0.55; 0.60 leaves headroom without outliers.
_FOUL_DRAW_RATE_CAP: float = 0.60

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
        # Non-rim 2pt: mostly mid-range jumpers, a few floaters (both paint/mid zone).
        # Keeping floaters here makes "close" pure rim, so the mid/rim split is exactly
        # the player's observed non-rim share (mid_shot_rate) with no bucket leakage.
        floater_rate = ball_handler.get("floater_rate", defaults["floater_rate"])
        return rng.choices(["mid_range", "floater"],
                           weights=[1.0 - floater_rate, floater_rate])[0]

    # close — pure rim
    dunk_rate  = ball_handler.get("dunk_rate", defaults["dunk_rate"])
    layup_rate = max(0.0, 1.0 - dunk_rate)
    return rng.choices(["dunk", "layup"], weights=[dunk_rate, layup_rate])[0]


def attr_to_prob(rating: int, lo: float = 0.25, hi: float = 0.75) -> float:
    return lo + (rating / 100.0) * (hi - lo)


def _free_throw_prob(player: dict) -> float:
    """Observed FT% (shrunk, from roster load) with a rating fallback for
    synthetic rosters that carry no ft_prob."""
    p = player.get("ft_prob")
    return p if p is not None else attr_to_prob(player["free_throw"], lo=0.60, hi=0.95)


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


# ---------------------------------------------------------------------------
# Decision pipeline (roadmap stage D) — a possession resolves as a sequence of
# named basketball stages, each visible on its own:
#     select_action -> resolve_matchup -> evaluate_shot -> resolve_outcome
# The stages preserve the exact RNG draw order of the original monolith, so this
# is a pure readability extraction (behavior-neutral).
# ---------------------------------------------------------------------------


@dataclass
class Action:
    """What the offense attempts this possession."""
    ball_handler: dict
    terminal: bool                      # possession ended with no live shot (foul / turnover)
    coarse_type: Optional[str] = None   # three / mid / close
    sub_type: Optional[str] = None      # corner_three / layup / ... (or coarse when sub-types off)
    initiator: Optional[dict] = None    # who ran the possession; credited the assist if a
                                        # teammate scores (not automatically the shooter)


@dataclass
class Matchup:
    """The defensive response to the shot attempt."""
    defender: Optional[dict]
    blocked: bool = False               # rim protection ended the possession


@dataclass
class ShotQuality:
    """How likely the attempt is to go in, after everything."""
    make_prob: float


def _empty_result() -> dict:
    return {
        "scorer": None, "shot_type": None, "made": False,
        "assisted_by": None, "rebounded_by": None, "is_oreb": False,
        "turnover_by": None, "steal_by": None, "block_by": None,
        "fouled_by": None, "fta": 0, "ftm": 0,
    }


def _finish(ctx, result: dict) -> dict:
    if ctx.name_map is not None:
        result["description"] = describe_event(result, ctx.name_map)
    return result


def resolve_possession(ctx: "PossessionContext") -> dict:
    """Simulate one possession and return an event dict.

    Reads the possession's starting state from a PossessionContext and resolves it
    through the decision pipeline below. See each stage for its basketball role.
    """
    result = _empty_result()

    action = _select_action(ctx, result)
    if action.terminal:
        return _finish(ctx, result)

    matchup = _resolve_matchup(ctx, action, result)
    if matchup.blocked:
        return _finish(ctx, result)

    quality = _evaluate_shot(ctx, action, matchup)
    _resolve_outcome(ctx, action, matchup, quality, result)
    return _finish(ctx, result)


# --- stage 1: select_action -------------------------------------------------

def _bonus_foul_prob(ctx, ball_handler: dict) -> float:
    """Non-shooting (over-the-limit) foul probability for this ball handler.

    use_foul_drawing: player-specific rate (FTA/FGA x scale), floored at the league
    average and escalated late in close games. Otherwise the flat pre-M3e 5.5%.
    """
    cfg = ctx.cfg
    if not cfg.use_foul_drawing:
        prob = 0.055
    else:
        raw_rate = ball_handler.get("foul_drawing_rate") or _LEAGUE_AVG_FOUL_DRAW_RATE
        raw_rate = min(raw_rate, _FOUL_DRAW_RATE_CAP)
        prob = max(raw_rate * cfg.foul_draw_scale, _LEAGUE_AVG_FOUL_DRAW_RATE * cfg.foul_draw_scale)

    if cfg.use_behavior_profile:
        # Phase profile owns competitive-late fouling (replaces the M3e clock zones).
        if ctx.behavior_profile:
            prob *= ctx.behavior_profile.foul_draw_mult
    elif cfg.use_foul_drawing and ctx.quarter >= 4 and ctx.clock_seconds <= cfg.foul_draw_late_zone1_clock:
        # Legacy M3e clock-zone escalation (presets without a behavior profile).
        margin = abs(ctx.score_margin)
        if margin <= cfg.foul_draw_late_zone2_margin and ctx.clock_seconds <= cfg.foul_draw_late_zone2_clock:
            prob *= cfg.foul_draw_late_zone2_mult
        elif margin <= cfg.foul_draw_late_zone1_margin:
            prob *= cfg.foul_draw_late_zone1_mult
    return prob


def _select_action(ctx, result: dict) -> Action:
    """Who acts and what they attempt. Pre-shot events (bonus foul, steal, turnover,
    offensive foul) end the possession terminally; otherwise a shot type is chosen."""
    rng, offense, defense, cfg = ctx.rng, ctx.offense, ctx.defense, ctx.cfg

    # ball handler — weighted by usage rate, concentrated by usage_concentration.
    # Linear (gamma=1) allocated offensive load too democratically: stars lost FGA/
    # FT/assists to the bench (player_accounting.py, gap 3.4). gamma>1 routes
    # disproportionately more possessions to high-usage players — no player-specific
    # bonuses; a star scores more because the engine runs more through him.
    g = cfg.usage_concentration
    usage_weights = [p["usage_rate"] ** g for p in offense]
    total_usage = sum(usage_weights)
    ball_handler = rng.choices(offense, weights=[w / total_usage for w in usage_weights])[0]

    # initiator — who ran the possession (usage + playmaking), drawn independently of
    # the shooter. Credited the assist when a TEAMMATE scores; if the initiator also
    # takes the shot it is self-created (no assist). Decoupling this from the shooter
    # is what lets a lead creator accumulate real assist share (gap 3.4c), and keeps
    # it stable under usage_concentration (which only concentrates the shooter).
    init_weights = [p["assist_rate"] for p in offense]
    initiator = rng.choices(offense, weights=init_weights)[0]

    # bonus foul (non-shooting)
    if rng.random() < _bonus_foul_prob(ctx, ball_handler):
        result["scorer"] = ball_handler["id"]
        result["fouled_by"] = rng.choice(defense)["id"]
        ft_prob = _free_throw_prob(ball_handler)
        result["fta"] = 2
        result["ftm"], last_missed = _shoot_free_throws(2, ft_prob, rng)
        _credit_ft_rebound(ctx, result, last_missed)
        return Action(ball_handler, terminal=True)

    # steal: best on-ball defender. Rate from cfg (gap 3.5 raised it to hit real steal
    # volume; total TOV held via tov_scale). Still charges the ball handler a turnover.
    best_defender = max(defense, key=lambda p: p["steal"])
    if rng.random() < (best_defender["steal"] / 100.0) * cfg.steal_rate:
        result["turnover_by"] = ball_handler["id"]
        result["steal_by"] = best_defender["id"]
        return Action(ball_handler, terminal=True)

    # unforced turnover — driven by the player's observed per-possession turnover
    # economy (tov_per_poss), so it does NOT inflate under usage concentration the way
    # TOV/36 did (gap 3.4b). Phase profile scales it; modifier delta adds on top.
    # Fallback to the legacy TOV/36 formula for synthetic rosters without the economy.
    profile = _profile(ctx)
    tov_delta = ctx.adjustments.tov_prob_delta if ctx.adjustments else 0.0
    tpp = ball_handler.get("tov_per_poss")
    if tpp is not None:
        base_tov = tpp * cfg.tov_scale * profile.turnover_mult
    else:
        base_tov = (ball_handler["turnover_rate"] / LEAGUE_AVG_TOV_PER36) * 0.13 * profile.turnover_mult
    tov_prob = max(0.02, base_tov + tov_delta)
    if rng.random() < tov_prob:
        result["turnover_by"] = ball_handler["id"]
        return Action(ball_handler, terminal=True)

    # offensive foul — charge / illegal screen (~1.5%)
    if rng.random() < 0.015:
        result["turnover_by"] = ball_handler["id"]
        result["fouled_by"] = ball_handler["id"]
        return Action(ball_handler, terminal=True)

    # shot type — objective three_rate_override then phase shot-profile multiplier
    three_rate = ball_handler["three_point_rate"]
    if ctx.adjustments and ctx.adjustments.three_rate_override:
        three_rate = min(0.60, max(0.0, three_rate + ctx.adjustments.three_rate_override))
    if profile.shot_profile.three_rate_mult != 1.0:
        three_rate = min(0.60, max(0.0, three_rate * profile.shot_profile.three_rate_mult))
    if ctx.is_fastbreak:
        coarse_type = rng.choices(["three", "mid", "close"], weights=[0.05, 0.10, 0.85])[0]
    else:
        # Mid share of 2pt attempts is the player's observed tendency (era-embedded);
        # 0.4 is only the fallback for synthetic rosters / seasons without shot data.
        mid_frac = ball_handler.get("mid_shot_rate", 0.4)
        coarse_type = rng.choices(
            ["three", "mid", "close"],
            weights=[three_rate, (1 - three_rate) * mid_frac, (1 - three_rate) * (1 - mid_frac)],
        )[0]
    sub_type = _select_sub_type(ball_handler, coarse_type, rng) if cfg.use_shot_subtypes else coarse_type

    result["shot_type"] = sub_type
    result["scorer"] = ball_handler["id"]
    return Action(ball_handler, terminal=False, coarse_type=coarse_type,
                  sub_type=sub_type, initiator=initiator)


# --- stage 2: resolve_matchup (rim protection + on-ball assignment) ----------

def _assign_rebound(ctx, result: dict) -> None:
    """Offensive or defensive rebound, weighted by the players' rebound rates."""
    rng = ctx.rng
    oreb_rate = min(0.60, ctx.offense_oreb_rate * _profile(ctx).offensive_rebound_mult)
    if rng.random() < oreb_rate:
        result["rebounded_by"] = rng.choices(
            ctx.offense, weights=[p["oreb_rate"] for p in ctx.offense]
        )[0]["id"]
        result["is_oreb"] = True
    else:
        result["rebounded_by"] = rng.choices(
            ctx.defense, weights=[p["dreb_rate"] for p in ctx.defense]
        )[0]["id"]


def _shoot_free_throws(n: int, ft_prob: float, rng) -> Tuple[int, bool]:
    """Sample n free throws; return (makes, last_missed). Draws n rng values in the
    same order as the old inline generator, so the FT outcomes are RNG-identical; the
    last-FT flag is what lets us credit the live rebound on a missed final FT."""
    makes = [rng.random() < ft_prob for _ in range(n)]
    return sum(makes), not makes[-1]


def _credit_ft_rebound(ctx, result: dict, last_missed: bool) -> None:
    """A missed LAST free throw is a live rebound (gap 3.5). The possession already
    flips to the defense in the game loop (no OREB-off-FT is modeled), so credit a
    DEFENSIVE rebounder — is_oreb stays False, so this adds only the box-score credit
    that was missing and cannot trigger a second chance (scoring/possession-neutral)."""
    if last_missed:
        result["rebounded_by"] = ctx.rng.choices(
            ctx.defense, weights=[p["dreb_rate"] for p in ctx.defense]
        )[0]["id"]


def _resolve_matchup(ctx, action: Action, result: dict) -> Matchup:
    """Defensive response: rim protection first (a block ends the possession, with a
    rebound), then the on-ball defender assignment."""
    rng, defense, cfg = ctx.rng, ctx.defense, ctx.cfg
    sub_type, coarse_type = action.sub_type, action.coarse_type

    block_eligible = (sub_type in _BLOCK_ELIGIBLE) if cfg.use_shot_subtypes else (coarse_type != "three")
    if block_eligible and not ctx.is_fastbreak:
        block_mult = _BLOCK_MULT.get(sub_type, 1.0) if cfg.use_shot_subtypes else 1.0
        best_blocker = max(defense, key=lambda p: p["block"])
        if rng.random() < (best_blocker["block"] / 100.0) * 0.04 * block_mult:
            result["block_by"] = best_blocker["id"]
            _assign_rebound(ctx, result)
            return Matchup(defender=None, blocked=True)

    # on-ball assignment — positional matchups filter to the ball handler's group;
    # chosen uniformly within the pool (attributes affect the outcome, not selection).
    if cfg.use_positional_matchups:
        pos_group = _position_group(action.ball_handler.get("position", "F"))
        candidates = [p for p in defense if _position_group(p.get("position", "F")) == pos_group] or defense
        defender = rng.choice(candidates)
    else:
        defender = rng.choice(defense)
    return Matchup(defender=defender)


# --- stage 3: evaluate_shot (no make/miss draw — that is the outcome) --------

def _evaluate_shot(ctx, action: Action, matchup: Matchup) -> ShotQuality:
    """Compute how likely the attempt is to go in: base ability by sub-type, minus the
    defender's penalty, adjusted by the contest model, signal gain, home court, and
    per-possession modifier/form deltas. No RNG for make/miss here (see resolve_outcome)."""
    cfg, rng = ctx.cfg, ctx.rng
    defender = matchup.defender
    sub_type, coarse_type, ball_handler = action.sub_type, action.coarse_type, action.ball_handler

    attr_key, lo, hi = _SUB_TYPE_SPECS[sub_type]
    zone_key = _OBSERVED_ZONE_KEY.get(sub_type)
    observed = ball_handler.get(zone_key) if zone_key else None
    base_prob = (observed if observed is not None
                 else attr_to_prob(ball_handler.get(attr_key, ball_handler["close_shot"]), lo=lo, hi=hi))

    if sub_type in ("corner_three", "above_break_three", "three"):
        scale, basis = 0.06, lambda p: p["perimeter_defense"]
    elif sub_type in ("mid_range", "mid"):
        scale, basis = 0.05, lambda p: p["perimeter_defense"]
    elif sub_type in _INTERIOR_TYPES:
        scale, basis = 0.08, lambda p: p["interior_defense"]
    else:  # floater — blend interior/perimeter
        scale, basis = 0.07, lambda p: p["interior_defense"] * 0.6 + p["perimeter_defense"] * 0.4
    def_attr = basis(defender)
    defense_penalty = def_attr / 100.0 * scale
    # When the base is an OBSERVED zone FG% (already realized vs an average defender),
    # the defender effect must be a DEVIATION from the average defender on the floor —
    # otherwise league-average defense is subtracted twice. Centering on the defending
    # lineup (not a fixed 50) self-calibrates across eras, whose rating scales differ
    # (percentile-curve median ~72, tracking ~63, position-default ~50). Attribute-derived
    # bases keep the full penalty (their bands were calibrated to include it).
    if observed is not None:
        center = sum(basis(d) for d in ctx.defense) / len(ctx.defense)
        defense_penalty = (def_attr - center) / 100.0 * scale

    if ctx.is_fastbreak:
        if coarse_type == "close":
            base_prob = min(base_prob + 0.08, 0.85)
        defense_penalty *= 0.80

    # contest model — separates whether the defender reaches the shot from its impact
    if cfg.use_contest_model:
        def_attr = (
            defender["perimeter_defense"] if sub_type in _PERIMETER_TYPES
            else defender["interior_defense"]
        )
        is_contested = rng.random() < (def_attr / 100.0) * _CONTEST_REACH[sub_type]
        defense_penalty *= _CONTEST_IMPACT[sub_type] if is_contested else 1.0

    shot_prob = (base_prob - defense_penalty) * ctx.team_defense_factor
    if cfg.signal_gain != 1.0:
        anchor = _LEAGUE_AVG_MAKE[sub_type]
        shot_prob = anchor + (shot_prob - anchor) * cfg.signal_gain
    shot_prob = shot_prob + ctx.home_bonus

    shot_delta = (ctx.adjustments.shot_prob_delta + ctx.adjustments.defense_penalty_delta) if ctx.adjustments else 0.0
    form_delta = (
        (ctx.form_factors[ball_handler["id"]] - 1.0) * base_prob
        if (ctx.form_factors and ball_handler["id"] in ctx.form_factors)
        else 0.0
    )
    make_prob = max(0.05, min(0.95, shot_prob + shot_delta + form_delta))
    return ShotQuality(make_prob=make_prob)


# --- stage 4: resolve_outcome -----------------------------------------------

def _resolve_outcome(ctx, action: Action, matchup: Matchup, quality: ShotQuality, result: dict) -> None:
    """Draw the shot result, then shooting fouls, assist, and rebound-on-miss."""
    rng, cfg = ctx.rng, ctx.cfg
    ball_handler, defender = action.ball_handler, matchup.defender
    sub_type, coarse_type = action.sub_type, action.coarse_type

    result["made"] = rng.random() < quality.make_prob
    ft_prob = _free_throw_prob(ball_handler)
    shoot_foul_mult = _FOUL_DRAW_MULT.get(sub_type, 1.0) if cfg.use_foul_drawing else 1.0
    # NB: the phase foul boost lives on the non-shooting bonus foul (which REPLACES a shot
    # with a low-variance FT trip). Boosting shooting fouls instead adds and-1s — higher
    # variance — so it is deliberately NOT applied here.

    # 3PT shooting foul (~2% x sub-type multiplier)
    if coarse_type == "three" and rng.random() < 0.02 * shoot_foul_mult:
        result["fouled_by"] = defender["id"]
        if result["made"]:
            result["fta"] = 1
            result["ftm"], last_missed = _shoot_free_throws(1, ft_prob, rng)
        else:
            result["fta"] = 3
            result["ftm"], last_missed = _shoot_free_throws(3, ft_prob, rng)
        _credit_ft_rebound(ctx, result, last_missed)
    # 2PT shooting foul — base 0.13 under foul drawing (multiplier averages ~1.16), else 0.15
    elif coarse_type != "three" and rng.random() < (0.13 if cfg.use_foul_drawing else 0.15) * shoot_foul_mult:
        result["fouled_by"] = defender["id"]
        if result["made"]:
            result["fta"] = 1
            result["ftm"], last_missed = _shoot_free_throws(1, ft_prob, rng)
        else:
            result["fta"] = 2
            result["ftm"], last_missed = _shoot_free_throws(2, ft_prob, rng)
        _credit_ft_rebound(ctx, result, last_missed)

    # assist on a make — credited to the possession INITIATOR when a teammate scored.
    # A self-created basket (initiator == shooter) is unassisted. The assist rate is
    # unchanged (gap 3.4c fix is allocation, not attribution); who receives it now
    # follows who ran the possession rather than a random draw among leftovers.
    if result["made"]:
        initiator = action.initiator
        if initiator is not None and initiator["id"] != ball_handler["id"]:
            # rate re-derived for the initiator model: a made shot is assisted only
            # when a teammate created it (self-creates are unassisted), so the base
            # rate is scaled up to keep team AST/FGM at real ~0.60.
            ast_rate = 0.85 if coarse_type in ("three", "mid") else 0.66
            if rng.random() < ast_rate:
                result["assisted_by"] = initiator["id"]

    # block attribution on a missed rim shot (gap 3.5): a block is a KIND of missed FG.
    # The forced-miss rim-protection path (_resolve_matchup) ends possessions; this
    # relabels the REST of the missed block-eligible shots so total blocks reach real
    # ~4.9. Pure box-score relabel — the shot already missed, so scoring/possession and
    # the rebound below are untouched.
    block_eligible = (sub_type in _BLOCK_ELIGIBLE) if cfg.use_shot_subtypes else (coarse_type != "three")
    if (not result["made"] and result["fta"] == 0 and not ctx.is_fastbreak
            and block_eligible and cfg.block_attribution_scale > 0):
        blocker = max(ctx.defense, key=lambda p: p["block"])
        bmult = _BLOCK_MULT.get(sub_type, 1.0) if cfg.use_shot_subtypes else 1.0
        if rng.random() < (blocker["block"] / 100.0) * cfg.block_attribution_scale * bmult:
            result["block_by"] = blocker["id"]

    # rebound on a live miss (no free throws pending)
    if not result["made"] and result["fta"] == 0:
        _assign_rebound(ctx, result)
