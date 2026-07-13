"""Converts raw NBA season stats into 0-100 player attribute ratings.

Pipeline:
    PlayerSeasonStats -> raw_score() -> percentile -> rating (0-100)

This module is the single translation layer from observations ("what
happened", PlayerSeasonStats) into simulation abilities ("how good is this
player", PlayerAttributes). Since Attribute Derivation v2 most attributes are
data-derived, including interior finishing (shot-location zones) and
individual defense (defensive-matchup plus-minus). Only physical estimates
(speed, strength, ball_handle, ...) remain position-adjusted defaults.
"""
from dataclasses import dataclass
from typing import Optional
import math


# ---------------------------------------------------------------------------
# Percentile -> rating curve
# Anchor points: (percentile, rating). Interpolated linearly between anchors.
# Design intent: 99 is rare, most players cluster 45-75.
# ---------------------------------------------------------------------------
_CURVE_ANCHORS = [
    (0.0,  40),
    (25.0, 60),
    (50.0, 72),
    (75.0, 82),
    (90.0, 92),
    (99.0, 99),
]


def percentile_to_rating(percentile: float) -> int:
    anchors = _CURVE_ANCHORS
    if percentile <= anchors[0][0]:
        return anchors[0][1]
    if percentile >= anchors[-1][0]:
        return anchors[-1][1]
    for i in range(len(anchors) - 1):
        lo_pct, lo_rat = anchors[i]
        hi_pct, hi_rat = anchors[i + 1]
        if lo_pct <= percentile <= hi_pct:
            t = (percentile - lo_pct) / (hi_pct - lo_pct)
            return round(lo_rat + t * (hi_rat - lo_rat))
    return 50


# ---------------------------------------------------------------------------
# Skill metric configuration
# ---------------------------------------------------------------------------
@dataclass
class SkillMetricConfig:
    """Defines how a raw score is computed for one skill.

    raw_score = efficiency * min(1.0, volume / volume_normalizer)

    Players below minimum_games or minimum_minutes are excluded from the
    percentile pool and receive the default rating instead.
    """
    volume_normalizer: float       # volume at which weight reaches 1.0
    minimum_games: int = 20
    minimum_minutes: float = 15.0
    minimum_attempts: float = 0.0  # per-game attempts threshold for shooting skills


# Configs per derived attribute
SKILL_CONFIGS: dict[str, SkillMetricConfig] = {
    "three_point":        SkillMetricConfig(volume_normalizer=6.0, minimum_attempts=1.5),
    "free_throw":         SkillMetricConfig(volume_normalizer=4.0, minimum_attempts=1.0),
    "mid_range":          SkillMetricConfig(volume_normalizer=4.0, minimum_attempts=1.0),
    "steal":              SkillMetricConfig(volume_normalizer=2.0),
    "block":              SkillMetricConfig(volume_normalizer=2.0),
    "offensive_rebound":  SkillMetricConfig(volume_normalizer=3.0),
    "defensive_rebound":  SkillMetricConfig(volume_normalizer=7.0),
    "passing":            SkillMetricConfig(volume_normalizer=8.0),
    # v2 — shot-location / defensive-matchup attributes
    "layup":              SkillMetricConfig(volume_normalizer=5.0, minimum_attempts=1.5),
    "close_shot":         SkillMetricConfig(volume_normalizer=2.5, minimum_attempts=1.5),
    "dunk":               SkillMetricConfig(volume_normalizer=7.0, minimum_attempts=1.5),
    "interior_defense":   SkillMetricConfig(volume_normalizer=6.0, minimum_attempts=2.0),
    "perimeter_defense":  SkillMetricConfig(volume_normalizer=5.0, minimum_attempts=2.0),
}

# Default ratings for players excluded from the percentile pool
SHOOTING_DEFAULT = 40
ESTIMATED_DEFAULT = 50

ATTRIBUTE_DEFAULTS: dict[str, int] = {
    "three_point":       SHOOTING_DEFAULT,
    "free_throw":        SHOOTING_DEFAULT,
    "mid_range":         SHOOTING_DEFAULT,
    "steal":             ESTIMATED_DEFAULT,
    "block":             ESTIMATED_DEFAULT,
    "offensive_rebound": ESTIMATED_DEFAULT,
    "defensive_rebound": ESTIMATED_DEFAULT,
    "passing":           ESTIMATED_DEFAULT,
    "layup":             SHOOTING_DEFAULT,
    "close_shot":        SHOOTING_DEFAULT,
    "dunk":              SHOOTING_DEFAULT,
    "interior_defense":  ESTIMATED_DEFAULT,
    "perimeter_defense": ESTIMATED_DEFAULT,
}

# ---------------------------------------------------------------------------
# Position-adjusted defaults for estimated attributes
# Base is 50; modifiers shift up/down by position.
# ---------------------------------------------------------------------------
_POSITION_MODIFIERS: dict[str, dict[str, int]] = {
    "C": {
        "strength": +10, "interior_defense": +10, "block": +5,
        "close_shot": +5, "layup": +5,
        "speed": -10, "acceleration": -5, "ball_handle": -10, "perimeter_defense": -5,
    },
    "F": {
        "strength": +5, "perimeter_defense": +5,
        "speed": -5, "ball_handle": -5,
    },
    "G": {
        "speed": +10, "acceleration": +5, "ball_handle": +10, "perimeter_defense": +5,
        "strength": -5, "interior_defense": -10, "block": -5,
        "close_shot": -5, "layup": -5,
    },
}

_ESTIMATED_ATTRIBUTES = {
    "close_shot", "layup", "dunk", "ball_handle",
    "speed", "acceleration", "strength", "stamina", "vertical",
    "perimeter_defense", "interior_defense",
}


def position_defaults(position: Optional[str]) -> dict[str, int]:
    """Return estimated attribute defaults adjusted for player position."""
    key = None
    if position:
        pos = position.upper().split("-")[0]  # handle 'C-F', 'G-F' etc.
        if pos in _POSITION_MODIFIERS:
            key = pos
    modifiers = _POSITION_MODIFIERS.get(key, {}) if key else {}
    return {
        attr: max(30, min(99, ESTIMATED_DEFAULT + modifiers.get(attr, 0)))
        for attr in _ESTIMATED_ATTRIBUTES
    }


# ---------------------------------------------------------------------------
# Overall rating calculation
# ---------------------------------------------------------------------------
# Derived and estimated groups are kept separate so position-defaults can't
# drag down real measurements. Weights per position sum to 1.0.
#
# Each entry: (attribute_list, weight_C, weight_F, weight_G)
_OVERALL_GROUPS = [
    # --- derived (from box scores) ---
    (["mid_range", "three_point", "free_throw"], 0.12, 0.20, 0.28),
    (["passing"],                                0.12, 0.12, 0.15),
    (["steal", "block"],                         0.18, 0.15, 0.12),
    (["offensive_rebound", "defensive_rebound"], 0.35, 0.20, 0.08),
    # --- derived since Attribute v2 (shot-location + defensive-matchup data);
    # ball_handle remains position-estimated ---
    (["close_shot", "layup", "dunk"],            0.15, 0.12, 0.05),
    (["ball_handle"],                            0.03, 0.08, 0.17),
    (["perimeter_defense"],                      0.00, 0.08, 0.10),
    (["interior_defense"],                       0.05, 0.05, 0.05),
]

_POS_WEIGHT_INDEX = {"C": 1, "F": 2, "G": 3}

# Non-linear curve applied to the weighted attribute average.
# Same anchor-point design as _CURVE_ANCHORS: compresses the middle,
# expands separation at the top so elite players reach 2K-style ratings.
_OVERALL_CURVE = [
    (50.0, 60),
    (60.0, 70),
    (70.0, 80),
    (75.0, 86),
    (80.0, 90),
    (85.0, 94),
    (90.0, 97),
    (95.0, 99),
]


def _apply_overall_curve(raw: float) -> int:
    anchors = _OVERALL_CURVE
    if raw <= anchors[0][0]:
        return anchors[0][1]
    if raw >= anchors[-1][0]:
        return anchors[-1][1]
    for i in range(len(anchors) - 1):
        lo_raw, lo_rat = anchors[i]
        hi_raw, hi_rat = anchors[i + 1]
        if lo_raw <= raw <= hi_raw:
            t = (raw - lo_raw) / (hi_raw - lo_raw)
            return round(lo_rat + t * (hi_rat - lo_rat))
    return 75


def compute_overall(attrs: dict[str, int], position: Optional[str] = None) -> int:
    pos_key = None
    if position:
        pos_key = position.upper().split("-")[0]
    weight_idx = _POS_WEIGHT_INDEX.get(pos_key, 2)  # default to F if unknown
    raw = 0.0
    for group in _OVERALL_GROUPS:
        attr_list, *weights = group
        weight = weights[weight_idx - 1]
        group_avg = sum(attrs.get(a, ESTIMATED_DEFAULT) for a in attr_list) / len(attr_list)
        raw += group_avg * weight
    return _apply_overall_curve(raw)


# ---------------------------------------------------------------------------
# Raw score functions per attribute
# ---------------------------------------------------------------------------
def _raw_three_point(stats) -> Optional[float]:
    if not stats.fg3a or stats.fg3a < SKILL_CONFIGS["three_point"].minimum_attempts:
        return None
    return stats.fg3_pct * min(1.0, stats.fg3a / SKILL_CONFIGS["three_point"].volume_normalizer)


def _raw_free_throw(stats) -> Optional[float]:
    if not stats.fta or stats.fta < SKILL_CONFIGS["free_throw"].minimum_attempts:
        return None
    return stats.ft_pct * min(1.0, stats.fta / SKILL_CONFIGS["free_throw"].volume_normalizer)


def _raw_mid_range(stats) -> Optional[float]:
    # Prefer real mid-range zone data (shot locations); fall back to the
    # 2PT% proxy for seasons ingested before shot-location data existed.
    if getattr(stats, "mid_fga", None) is not None and stats.mid_fga >= 0.5:
        return (stats.mid_fg_pct or 0) * min(1.0, stats.mid_fga / SKILL_CONFIGS["mid_range"].volume_normalizer)
    non3_attempts = (stats.fga or 0) - (stats.fg3a or 0)
    if non3_attempts < SKILL_CONFIGS["mid_range"].minimum_attempts:
        return None
    fg2_pct = ((stats.fgm or 0) - (stats.fg3m or 0)) / non3_attempts if non3_attempts > 0 else 0
    return fg2_pct * min(1.0, non3_attempts / SKILL_CONFIGS["mid_range"].volume_normalizer)


def _raw_layup(stats) -> Optional[float]:
    # Restricted-area finishing: efficiency x volume weight
    if getattr(stats, "ra_fga", None) is None or stats.ra_fga < SKILL_CONFIGS["layup"].minimum_attempts:
        return None
    return (stats.ra_fg_pct or 0) * min(1.0, stats.ra_fga / SKILL_CONFIGS["layup"].volume_normalizer)


def _raw_close_shot(stats) -> Optional[float]:
    # Paint (non-RA) touch/floater game, blended 70/30 with RA finishing —
    # pure paint-non-RA volume is thin for many players.
    if getattr(stats, "paint_fga", None) is None:
        return None
    total = (stats.paint_fga or 0) + (stats.ra_fga or 0)
    if total < SKILL_CONFIGS["close_shot"].minimum_attempts:
        return None
    paint_part = (stats.paint_fg_pct or 0) * min(1.0, (stats.paint_fga or 0) / SKILL_CONFIGS["close_shot"].volume_normalizer)
    ra_part = (stats.ra_fg_pct or 0) * min(1.0, (stats.ra_fga or 0) / 5.0)
    return 0.7 * paint_part + 0.3 * ra_part


def _raw_dunk_rim(stats) -> Optional[float]:
    # Rim-finishing component of the dunk hybrid: RA efficiency with a heavier
    # volume normalizer than layup — high-volume rim finishers rank higher.
    if getattr(stats, "ra_fga", None) is None or stats.ra_fga < SKILL_CONFIGS["dunk"].minimum_attempts:
        return None
    return (stats.ra_fg_pct or 0) * min(1.0, stats.ra_fga / SKILL_CONFIGS["dunk"].volume_normalizer)


def _raw_interior_defense(stats) -> Optional[float]:
    # PLUSMINUS is defended FG% minus shooters' normal — negative is good, so
    # invert. Volume weight rewards defenders who are actually tested at the rim.
    if getattr(stats, "d_lt6_fga", None) is None or stats.d_lt6_fga < SKILL_CONFIGS["interior_defense"].minimum_attempts:
        return None
    return -(stats.d_lt6_plusminus or 0) * min(1.0, stats.d_lt6_fga / SKILL_CONFIGS["interior_defense"].volume_normalizer)


def _raw_perimeter_defense(stats) -> Optional[float]:
    # Non-rim defended plus-minus: overall defended shots minus the rim component.
    # Defended 3P% alone is luck-dominated at season samples and punishes on-ball
    # stoppers who take the hardest assignments (e.g. Dort rated 65 under 3PT-only).
    # Mid-range + three defense together is a larger, steadier sample.
    if getattr(stats, "d_fga", None) is None or stats.d_fga is None:
        return None
    lt6_fga = stats.d_lt6_fga or 0.0
    lt6_pm = stats.d_lt6_plusminus or 0.0
    nonrim_fga = stats.d_fga - lt6_fga
    if nonrim_fga < SKILL_CONFIGS["perimeter_defense"].minimum_attempts:
        return None
    nonrim_pm = ((stats.d_plusminus or 0) * stats.d_fga - lt6_pm * lt6_fga) / nonrim_fga
    return -nonrim_pm * min(1.0, nonrim_fga / SKILL_CONFIGS["perimeter_defense"].volume_normalizer)


def _raw_steal(stats) -> Optional[float]:
    if not stats.steals:
        return None
    return stats.steals * min(1.0, (stats.games_played or 0) / 50)


def _raw_block(stats) -> Optional[float]:
    if not stats.blocks:
        return None
    return stats.blocks * min(1.0, (stats.games_played or 0) / 50)


def _raw_offensive_rebound(stats) -> Optional[float]:
    if not stats.rebounds or not stats.minutes_per_game:
        return None
    oreb_estimate = stats.rebounds * 0.3  # rough split; replace with real OREB when available
    return oreb_estimate * min(1.0, (stats.games_played or 0) / 50)


def _raw_defensive_rebound(stats) -> Optional[float]:
    if not stats.rebounds or not stats.minutes_per_game:
        return None
    dreb_estimate = stats.rebounds * 0.7
    return dreb_estimate * min(1.0, (stats.games_played or 0) / 50)


def _raw_passing(stats) -> Optional[float]:
    if not stats.assists:
        return None
    return stats.assists * min(1.0, (stats.games_played or 0) / 50)


# ---------------------------------------------------------------------------
# Box-score defense fallback (pre-2013-14 eras — no player-tracking defense)
# ---------------------------------------------------------------------------
# LeagueDashPtDefend (defended FG% matchups) only exists from 2013-14 on, so
# _raw_interior_defense / _raw_perimeter_defense return None for older seasons
# and every player collapses to a flat positional default — erasing team
# defensive identity (the dominant driver of cross-era strength). This fallback
# reconstructs *relative* defense from data every era has: a team component
# (team def_rating, the reliable signal for telling elite defenses apart) blended
# with an individual box proxy (blocks->interior, steals->perimeter) that keeps
# within-team identity. Measured against the 2005-06 schedule replay (strength
# slope); current-season rating never uses this path so its baseline is untouched.
_TEAM_DEF_WEIGHT = 0.6   # team defensive quality dominates for strength differentiation
_INDIV_DEF_WEIGHT = 0.4  # individual box proxy preserves rim-protector vs turnstile


def _percentile_ratings(scored: dict) -> dict:
    """{key: raw_score} -> {key: 0-100 rating} by within-pool percentile rank."""
    if not scored:
        return {}
    scores = list(scored.values())
    return {
        k: percentile_to_rating(sum(1 for x in scores if x < s) / len(scores) * 100)
        for k, s in scored.items()
    }


def derive_box_score_defense(
    all_stats: list,
    team_def_ratings: dict,
    positions: dict,
) -> dict:
    """Fallback {player_id: {"interior_defense", "perimeter_defense"}} for eras
    without tracking defense. See section header for rationale.

    team_def_ratings: {team_id: def_rating} (lower = better). positions: {pid: pos}.
    """
    # Team quality -> rating: invert def_rating (lower is better) then percentile
    # across the league's teams. One rating per team, shared by its players.
    team_score = {tid: -dr for tid, dr in team_def_ratings.items() if dr is not None}
    team_rating = _percentile_ratings(team_score)

    # Individual box proxies, volume-weighted by games so small samples regress.
    blk_score, stl_score = {}, {}
    for s in all_stats:
        gw = min(1.0, (s.games_played or 0) / 50)
        if s.blocks:
            blk_score[s.player_id] = s.blocks * gw
        if s.steals:
            stl_score[s.player_id] = s.steals * gw
    blk_rating = _percentile_ratings(blk_score)
    stl_rating = _percentile_ratings(stl_score)

    out: dict = {}
    for s in all_stats:
        pid = s.player_id
        tr = team_rating.get(s.team_id, ESTIMATED_DEFAULT)
        pos = (positions.get(pid) or "F").upper().split("-")[0]
        pmods = _POSITION_MODIFIERS.get(pos, {})

        interior = (_TEAM_DEF_WEIGHT * tr
                    + _INDIV_DEF_WEIGHT * blk_rating.get(pid, ESTIMATED_DEFAULT))
        perimeter = (_TEAM_DEF_WEIGHT * tr
                     + _INDIV_DEF_WEIGHT * stl_rating.get(pid, ESTIMATED_DEFAULT))
        out[pid] = {
            "interior_defense": max(30, min(99, round(interior + pmods.get("interior_defense", 0)))),
            "perimeter_defense": max(30, min(99, round(perimeter + pmods.get("perimeter_defense", 0)))),
        }
    return out


_RAW_SCORE_FNS = {
    "three_point":       _raw_three_point,
    "free_throw":        _raw_free_throw,
    "mid_range":         _raw_mid_range,
    "steal":             _raw_steal,
    "block":             _raw_block,
    "offensive_rebound": _raw_offensive_rebound,
    "defensive_rebound": _raw_defensive_rebound,
    "passing":           _raw_passing,
    # v2 — shot-location and defensive-matchup data (Attribute Derivation v2)
    "layup":              _raw_layup,
    "close_shot":         _raw_close_shot,
    "dunk":               _raw_dunk_rim,   # rim component; blended with layup in seed job
    "interior_defense":   _raw_interior_defense,
    "perimeter_defense":  _raw_perimeter_defense,
}


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------
def compute_ratings_for_attribute(
    attribute: str,
    all_stats: list,
    config: SkillMetricConfig,
    default_for_ineligible: bool = True,
) -> dict[int, int]:
    """Return {player_id: rating} for one attribute across all players.

    default_for_ineligible=False omits players below the volume gates from the
    result instead of assigning the flat default — callers can then fall back
    to position-adjusted defaults (used by the v2 shot-location attributes).
    """
    raw_fn = _RAW_SCORE_FNS[attribute]
    default = ATTRIBUTE_DEFAULTS[attribute]

    eligible = []
    for stats in all_stats:
        if (stats.games_played or 0) < config.minimum_games:
            continue
        if (stats.minutes_per_game or 0) < config.minimum_minutes:
            continue
        score = raw_fn(stats)
        if score is not None:
            eligible.append((stats.player_id, score))

    if not eligible:
        return {s.player_id: default for s in all_stats}

    scores = [score for _, score in eligible]
    eligible_ids = {pid for pid, _ in eligible}

    ratings: dict[int, int] = {}
    for player_id, score in eligible:
        rank = sum(1 for s in scores if s < score)
        percentile = (rank / len(scores)) * 100
        ratings[player_id] = percentile_to_rating(percentile)

    if default_for_ineligible:
        for stats in all_stats:
            if stats.player_id not in eligible_ids:
                ratings[stats.player_id] = default

    return ratings


def compute_tendencies(stats, team_totals: Optional[dict] = None) -> dict:
    """Derive tendency values from a single player's season stats.

    team_totals: optional dict {team_id: (fga, fta, tov)} for accurate usage_rate.
    If not provided, usage_rate is approximated from the player's raw numbers.
    """
    fga = stats.fga or 0
    fg3a = stats.fg3a or 0
    fta = stats.fta or 0
    tov = stats.turnovers or 0
    minutes = stats.minutes_per_game or 1

    player_possessions = fga + 0.44 * fta + tov

    if getattr(stats, "usg_pct", None) is not None:
        # NBA-provided usage % — most accurate
        usage_rate = stats.usg_pct
    elif team_totals and stats.team_id in team_totals:
        # Standard NBA usage formula (season totals):
        # USG% = (player_poss * team_min / 5) / (player_min * team_poss)
        # Stats are per-game averages in the DB, so convert to season totals first.
        gp = stats.games_played or 1
        player_poss_season = player_possessions * gp  # player_possessions is already per-game
        player_min_season = minutes * gp
        team_poss_season, team_min_season = team_totals[stats.team_id]
        usage_rate = (player_poss_season * (team_min_season / 5)) / max(player_min_season * team_poss_season, 1)
    else:
        # Fallback: approximate via minutes share — intentionally rough
        minutes_pct = minutes / 240.0
        estimated_team_poss = player_possessions / max(minutes_pct, 0.01)
        usage_rate = player_possessions / estimated_team_poss if estimated_team_poss > 0 else None

    # Rebound rates: prefer NBA-provided percentages (available after Advanced ingest),
    # fall back to per-36 derived from box score totals.
    if getattr(stats, "oreb_pct", None) is not None:
        oreb_rate = stats.oreb_pct
    else:
        oreb_rate = (stats.rebounds or 0) / max(minutes, 1) * 36  # rough fallback

    if getattr(stats, "dreb_pct", None) is not None:
        dreb_rate = stats.dreb_pct
    else:
        dreb_rate = (stats.rebounds or 0) / max(minutes, 1) * 36

    return {
        "usage_rate": round(usage_rate, 4) if usage_rate else None,
        "shot_tendency": fga / max(minutes, 1) * 36,
        "three_point_rate": fg3a / fga if fga > 0 else 0,
        "assist_rate": (stats.assists or 0) / max(minutes, 1) * 36,
        "oreb_rate": round(oreb_rate, 4),
        "dreb_rate": round(dreb_rate, 4),
        "rebound_rate": (stats.rebounds or 0) / max(minutes, 1) * 36,
        "turnover_rate": tov / max(minutes, 1) * 36,
        "foul_drawing_rate": round(fta / fga, 4) if fga > 0 else None,
        "corner_three_rate": (
            round(min(stats.corner3_fga / fg3a, 1.0), 4)
            if getattr(stats, "corner3_fga", None) is not None and fg3a > 0.5
            else None
        ),
    }


def apply_overrides(attributes: dict, overrides: list) -> dict:
    """Apply manual override values on top of computed ratings."""
    result = dict(attributes)
    for override in overrides:
        if override.attribute in result:
            result[override.attribute] = override.value
    return result
