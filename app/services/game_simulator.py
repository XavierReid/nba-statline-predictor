"""Possession-based game simulation engine.

Extracted from scratch/03_game_simulator.py. The scratch script is now a thin
CLI wrapper around this module.

Public surface:
    load_roster(db, team_id, season) -> list[dict]
    simulate_game(home_players, away_players, seed, season, steps) -> dict
"""
import math
import random
from typing import Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.player import Player
from app.models.player_attributes import PlayerAttributes
from app.models.player_tendencies import PlayerTendencies
from app.models.player_season_stats import PlayerSeasonStats
from app.models.team_season_stats import TeamSeasonStats
from app.services.modifiers.base import GameState, GameStateModifier, ModifierAdjustments

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
POSSESSIONS_PER_GAME = 200      # ~100 per team, matches NBA average pace
GAME_MINUTES = 48
SECONDS_PER_POSSESSION = (GAME_MINUTES * 60) / POSSESSIONS_PER_GAME
POSSESSIONS_PER_OT = 20         # ~10 per team per OT period
OT_MINUTES = 5
SECONDS_PER_OT_POSSESSION = (OT_MINUTES * 60) / POSSESSIONS_PER_OT   # 15.0
QUARTER_SECONDS = 720           # 12 minutes per quarter
OT_SECONDS = 300                # 5 minutes per OT period
HOME_ADVANTAGE = 3.0            # extra points spread across home possessions
SUB_VARIANCE = 2.0              # σ in minutes for substitution timing (Normal dist)
LEAGUE_AVG_TOV_PER36 = 2.5     # used to normalize per-player turnover rates
OREB_RATE = 0.22                # offensive rebound rate on missed shots (NBA avg ~22%)
ELIGIBLE_MISS_RATE = 0.32       # fraction of possessions ending in a reboundable miss


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_roster(db: Session, team_id: int, season: str) -> list[dict]:
    """Load top 10 players by minutes for a team in a given season.

    Minutes are normalized so the 10 players sum to 240 (5 players × 48 min).
    Returns an empty list if no stats exist for that team/season combination.
    """
    rows = db.execute(
        select(Player, PlayerAttributes, PlayerTendencies, PlayerSeasonStats)
        .join(PlayerAttributes, PlayerAttributes.player_id == Player.id)
        .join(PlayerTendencies, PlayerTendencies.player_id == Player.id)
        .join(PlayerSeasonStats, PlayerSeasonStats.player_id == Player.id)
        .where(Player.team_id == team_id)
        .where(PlayerAttributes.season == season)
        .where(PlayerTendencies.season == season)
        .where(PlayerSeasonStats.season == season)
        .order_by(PlayerSeasonStats.minutes_per_game.desc())
        .limit(10)
    ).all()

    if not rows:
        return []

    players = []
    for p, a, t, s in rows:
        players.append({
            "id": p.id,
            "name": p.full_name,
            "position": p.position or "F",
            "minutes": s.minutes_per_game,
            "is_starter": False,
            # attributes (0-100 scale)
            "three_point": a.three_point,
            "mid_range": a.mid_range,
            "free_throw": a.free_throw,
            "close_shot": a.close_shot,
            "passing": a.passing,
            "steal": a.steal,
            "block": a.block,
            "perimeter_defense": a.perimeter_defense,
            "interior_defense": a.interior_defense,
            "offensive_rebound": a.offensive_rebound,
            "defensive_rebound": a.defensive_rebound,
            "overall": a.overall_rating,
            # tendencies
            "usage_rate": t.usage_rate or 0.20,
            "three_point_rate": t.three_point_rate or 0.30,
            "shot_tendency": t.shot_tendency or 15.0,
            "assist_rate": s.assists or 1.0,       # AST/game — per-game weight for attribution
            "oreb_rate": t.oreb_rate or 0.05,      # OREB% from NBA Advanced stats
            "dreb_rate": t.dreb_rate or 0.10,      # DREB% from NBA Advanced stats
            "rebound_rate": t.rebound_rate or 5.0,
            "turnover_rate": t.turnover_rate or 2.0,
        })

    # Top 5 by minutes are starters
    for i, p in enumerate(players):
        p["is_starter"] = i < 5

    # Normalize minutes to sum to 240
    total = sum(p["minutes"] for p in players)
    if total > 0:
        for p in players:
            p["minutes"] = round(p["minutes"] / total * 240, 1)

    return players


# ---------------------------------------------------------------------------
# Box score helpers
# ---------------------------------------------------------------------------
def _empty_stats() -> dict:
    return {
        "pts": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0,
        "tov": 0, "pf": 0, "fgm": 0, "fga": 0,
        "fg3m": 0, "fg3a": 0, "ftm": 0, "fta": 0,
        "min": 0.0, "fouled_out": False, "plus_minus": 0,
    }


def _snapshot_box(box: dict) -> dict:
    """Shallow-copy a box score dict. Safe because all values are primitives."""
    return {pid: dict(stats) for pid, stats in box.items()}


def _apply_event(box: dict, event: dict) -> Tuple[int, Optional[int]]:
    """Apply one possession event to box in place.

    Returns (pts_scored, fouled_out_player_id or None). Rotation patching for
    foul-outs is left to the caller since it requires simulation state.
    """
    pts = 0

    if event["turnover_by"] and event["turnover_by"] in box:
        box[event["turnover_by"]]["tov"] += 1
        if event.get("steal_by") and event["steal_by"] in box:
            box[event["steal_by"]]["stl"] += 1

    elif event["scorer"]:
        if event.get("block_by") and event["block_by"] in box:
            box[event["block_by"]]["blk"] += 1

        pid = event["scorer"]
        if pid in box:
            shot_type = event.get("shot_type")
            if shot_type:  # bonus fouls have no shot attempt — skip FGA
                if shot_type == "three":
                    box[pid]["fg3a"] += 1
                    box[pid]["fga"] += 1
                    if event["made"]:
                        box[pid]["fg3m"] += 1
                        box[pid]["fgm"] += 1
                        box[pid]["pts"] += 3
                        pts = 3
                else:
                    box[pid]["fga"] += 1
                    if event["made"]:
                        box[pid]["fgm"] += 1
                        box[pid]["pts"] += 2
                        pts = 2

            if event["fta"] > 0:
                box[pid]["fta"] += event["fta"]
                box[pid]["ftm"] += event["ftm"]
                box[pid]["pts"] += event["ftm"]
                pts += event["ftm"]

        if event.get("assisted_by") and event["assisted_by"] in box:
            box[event["assisted_by"]]["ast"] += 1
        if event.get("rebounded_by") and event["rebounded_by"] in box:
            box[event["rebounded_by"]]["reb"] += 1

    fouled_out_pid = None
    fouled_pid = event.get("fouled_by")
    if fouled_pid and fouled_pid in box and not box[fouled_pid]["fouled_out"]:
        box[fouled_pid]["pf"] += 1
        if box[fouled_pid]["pf"] >= 6:
            box[fouled_pid]["fouled_out"] = True
            fouled_out_pid = fouled_pid

    event["pts"] = pts
    return pts, fouled_out_pid


# ---------------------------------------------------------------------------
# Rotation schedule
# ---------------------------------------------------------------------------
def build_rotation(players: list[dict], rng: random.Random) -> list[list[int]]:
    """Build a 48-slot minute schedule with 5 player IDs per slot.

    Substitution timing is sampled from a Normal distribution so rotations
    vary between games even for the same team.
    """
    slots: list[set] = [set() for _ in range(GAME_MINUTES)]

    def assign_minutes(player: dict, target_min: float, preferred_start: int) -> None:
        remaining = int(target_min)
        minute = max(0, min(47, int(rng.gauss(preferred_start, SUB_VARIANCE))))
        visited: set = set()
        while remaining > 0 and len(visited) < GAME_MINUTES:
            idx = minute % GAME_MINUTES
            if len(slots[idx]) < 5 and idx not in visited:
                slots[idx].add(player["id"])
                remaining -= 1
            visited.add(idx)
            minute = (minute + 1) % GAME_MINUTES

    starters = [p for p in players if p["is_starter"]]
    bench = [p for p in players if not p["is_starter"]]

    for p in starters:
        assign_minutes(p, p["minutes"], preferred_start=0)
    for p in bench:
        assign_minutes(p, p["minutes"], preferred_start=12)

    # Fill any under-staffed slots with highest-minute eligible players
    sorted_by_min = sorted(players, key=lambda x: x["minutes"], reverse=True)
    for slot in slots:
        while len(slot) < 5:
            for p in sorted_by_min:
                if p["id"] not in slot:
                    slot.add(p["id"])
                    break

    return [list(s) for s in slots]


# ---------------------------------------------------------------------------
# Possession resolution
# ---------------------------------------------------------------------------
def _attr_to_prob(rating: int, lo: float = 0.25, hi: float = 0.75) -> float:
    return lo + (rating / 100.0) * (hi - lo)


def describe_event(event: dict, name_map: dict) -> str:
    """Return a human-readable description of a possession event.

    name_map: {player_id: player_name} — built from home + away player lists.
    Kept as a module-level function so both resolve_possession (when
    capture_descriptions=True) and the step-through flatten pass can call it.
    """
    def name(pid: Optional[int]) -> str:
        return name_map.get(pid, f"Player {pid}") if pid else "Unknown"

    scorer = event.get("scorer")

    if event.get("turnover_by"):
        if event.get("steal_by"):
            return f"{name(event['turnover_by'])} turns it over — stolen by {name(event['steal_by'])}"
        if event.get("fouled_by") == event.get("turnover_by"):
            return f"{name(event['turnover_by'])} commits an offensive foul"
        return f"{name(event['turnover_by'])} turns it over"

    # Bonus foul (no shot_type) or shooting foul on a missed shot
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
    adjustments: Optional["ModifierAdjustments"] = None,
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

    # 1b. Bonus foul — approximates non-shooting fouls when team is over the foul
    # limit (~5.5% of possessions). Real tracking requires per-quarter foul counts
    # (v1.5). The 2 FTs end the possession with no field goal attempt.
    if rng.random() < 0.055:
        result["scorer"] = ball_handler["id"]
        result["fouled_by"] = rng.choice(defense)["id"]
        ft_prob = _attr_to_prob(ball_handler["free_throw"], lo=0.60, hi=0.95)
        result["fta"] = 2
        result["ftm"] = sum(1 for _ in range(2) if rng.random() < ft_prob)
        return _done(result)

    # 2. Steal check (~1.7% of possessions)
    best_defender = max(defense, key=lambda p: p["steal"])
    if rng.random() < (best_defender["steal"] / 100.0) * 0.034:
        result["turnover_by"] = ball_handler["id"]
        result["steal_by"] = best_defender["id"]
        return _done(result)

    # 3. Turnover — bad pass, travel, etc. (~13% at league average)
    # tov_prob_delta from momentum raises/lowers unforced turnover rate;
    # the steal check above is unchanged (defender skill, not offensive pressure).
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

    # 4. Shot type selection — fast break skews heavily toward close shots
    three_rate = ball_handler["three_point_rate"]
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
            if rng.random() < OREB_RATE:
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
        base_prob = _attr_to_prob(ball_handler["three_point"], lo=0.38, hi=0.44)
        defense_penalty = defender["perimeter_defense"] / 100.0 * 0.06
    elif shot_type == "mid":
        base_prob = _attr_to_prob(ball_handler["mid_range"], lo=0.51, hi=0.58)
        defense_penalty = defender["perimeter_defense"] / 100.0 * 0.05
    else:
        base_prob = _attr_to_prob(ball_handler["close_shot"], lo=0.65, hi=0.72)
        defense_penalty = defender["interior_defense"] / 100.0 * 0.08

    # Fast break: boost close-shot probability, reduce defender effectiveness
    if is_fastbreak:
        if shot_type == "close":
            base_prob = min(base_prob + 0.08, 0.85)
        defense_penalty *= 0.80

    shot_prob = (base_prob - defense_penalty + home_bonus / 100.0) * team_defense_factor
    _shot_delta = adjustments.shot_prob_delta if adjustments else 0.0
    result["made"] = rng.random() < max(0.05, min(0.95, shot_prob + _shot_delta))

    ft_prob = _attr_to_prob(ball_handler["free_throw"], lo=0.60, hi=0.95)

    # 8a. 3PT shooting foul (~2% of 3PT attempts): missed = 3 FTs, made = and-1
    if shot_type == "three" and rng.random() < 0.02:
        result["fouled_by"] = defender["id"]
        if result["made"]:
            result["fta"] = 1
            result["ftm"] = 1 if rng.random() < ft_prob else 0
        else:
            result["fta"] = 3
            result["ftm"] = sum(1 for _ in range(3) if rng.random() < ft_prob)

    # 8b. 2PT shooting foul (~15% of non-3PT attempts): made = and-1, missed = 2 FTs
    elif shot_type != "three" and rng.random() < 0.15:
        result["fouled_by"] = defender["id"]
        if result["made"]:
            result["fta"] = 1
            result["ftm"] = 1 if rng.random() < ft_prob else 0
        else:
            result["fta"] = 2
            result["ftm"] = sum(1 for _ in range(2) if rng.random() < ft_prob)

    # 9. Assist — rate varies by shot type, weighted by AST/game
    if result["made"]:
        ast_rate = 0.65 if shot_type in ("three", "mid") else 0.50
        if rng.random() < ast_rate:
            passers = [p for p in offense if p["id"] != ball_handler["id"]]
            if passers:
                result["assisted_by"] = rng.choices(
                    passers, weights=[p["assist_rate"] for p in passers]
                )[0]["id"]

    # 10. Rebound on miss — individual weighted by OREB%/DREB%
    if not result["made"] and result["fta"] == 0:
        if rng.random() < OREB_RATE:
            result["rebounded_by"] = rng.choices(
                offense, weights=[p["oreb_rate"] for p in offense]
            )[0]["id"]
            result["is_oreb"] = True
        else:
            result["rebounded_by"] = rng.choices(
                defense, weights=[p["dreb_rate"] for p in defense]
            )[0]["id"]

    return _done(result)


# ---------------------------------------------------------------------------
# Game simulation
# ---------------------------------------------------------------------------
def simulate_game(
    home_players: list[dict],
    away_players: list[dict],
    seed: int,
    season: Optional[str] = None,
    steps: Optional[int] = None,
    capture_descriptions: bool = False,
    config: Optional["SimConfig"] = None,
    home_team_id: Optional[int] = None,
    away_team_id: Optional[int] = None,
    db: Optional[object] = None,
) -> dict:
    """Simulate one full game including any overtime periods.

    Chunks (when steps is provided) are time-based: chunk_duration = 48 / steps
    minutes. Snapshots fire whenever the game clock crosses a multiple of
    chunk_duration, so OT periods generate proportional extra chunks automatically.
    A final snapshot is always added at game end if the last possession didn't
    coincide with a threshold.

    Returns a dict with:
        home_score, away_score, quarter_scores, box_score, season, chunks,
        chunk_events, went_to_ot, ot_periods
    """
    from app.services.sim_config import SimConfig
    cfg: SimConfig = config if config is not None else SimConfig()

    rng = random.Random(seed)

    home_by_id = {p["id"]: p for p in home_players}
    away_by_id = {p["id"]: p for p in away_players}
    # Build name_map whenever descriptions are needed — independent of step-through.
    # Step-through also needs descriptions, so OR with steps here.
    name_map = (
        {p["id"]: p["name"] for p in home_players + away_players}
        if (capture_descriptions or steps)
        else None
    )

    # Load team season stats for pace/defense modifiers when needed
    home_stats: Optional[dict] = None
    away_stats: Optional[dict] = None
    if db is not None and (cfg.use_pace or cfg.use_team_defense) and season:
        if home_team_id:
            row = db.execute(select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == home_team_id,
                TeamSeasonStats.season == season,
            )).scalar_one_or_none()
            if row:
                home_stats = {"pace": row.pace, "def_rating": row.def_rating}
        if away_team_id:
            row = db.execute(select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == away_team_id,
                TeamSeasonStats.season == season,
            )).scalar_one_or_none()
            if row:
                away_stats = {"pace": row.pace, "def_rating": row.def_rating}

    home_pace = (home_stats or {}).get("pace", cfg.league_avg_pace) if cfg.use_pace else cfg.league_avg_pace
    away_pace = (away_stats or {}).get("pace", cfg.league_avg_pace) if cfg.use_pace else cfg.league_avg_pace
    expected_possessions = round((home_pace + away_pace) / 2) * 2 if cfg.use_pace else POSSESSIONS_PER_GAME

    home_rotation = build_rotation(home_players, rng)
    away_rotation = build_rotation(away_players, rng)

    # Tip-off: coin flip determines Q1 possession; Q3 goes to the tip loser.
    # Q2 and Q4 continue alternating naturally from their preceding quarter.
    tip_winner_is_home = rng.random() < 0.5

    box: dict = {pid: _empty_stats() for pid in list(home_by_id) + list(away_by_id)}

    home_by_min = sorted(home_players, key=lambda p: p["minutes"], reverse=True)
    away_by_min = sorted(away_players, key=lambda p: p["minutes"], reverse=True)

    def patch_rotation(rotation: list, fouled_out_id: int, players_by_min: list, from_minute: int) -> None:
        for m in range(from_minute, GAME_MINUTES):
            if fouled_out_id not in rotation[m]:
                continue
            replacement = next(
                (p["id"] for p in players_by_min
                 if p["id"] != fouled_out_id
                 and p["id"] not in rotation[m]
                 and not box[p["id"]]["fouled_out"]),
                None,
            )
            rotation[m].remove(fouled_out_id)
            if replacement:
                rotation[m].append(replacement)

    # Time-based chunk tracking — threshold stored in a list so the nested
    # helper can mutate it without nonlocal.
    chunk_duration = GAME_MINUTES / steps if steps else None
    next_threshold = [chunk_duration]
    chunks: list = []
    chunk_events: list = []
    current_chunk_events: list = []
    # Flat event list — populated only when capture_descriptions=True and not
    # step-through mode. Step-through callers get events per-chunk instead.
    all_events: list = []
    home_total = 0
    away_total = 0
    possession_counter = 0
    q_idx = 0

    quarter_scores: dict = {"home": [0, 0, 0, 0], "away": [0, 0, 0, 0]}
    game_clock = 0.0
    min_per_poss = GAME_MINUTES / POSSESSIONS_PER_GAME

    def _maybe_snapshot(elapsed_minutes: float, current_q_idx: int) -> None:
        while chunk_duration and elapsed_minutes >= next_threshold[0]:
            chunks.append({
                "home_score": home_total,
                "away_score": away_total,
                "elapsed_minutes": round(elapsed_minutes, 1),
                "quarter": current_q_idx + 1,
                "box": _snapshot_box(box),
            })
            chunk_events.append(list(current_chunk_events))
            current_chunk_events.clear()
            next_threshold[0] += chunk_duration

    def _apply_possession(
        home_active_ids: list,
        away_active_ids: list,
        is_home: bool,
        sec_per_poss: float,
        min_per_poss_val: float,
        current_q_idx: int,
        game_clock_override: Optional[int] = None,
        team_defense_factor: float = 1.0,
        is_fastbreak: bool = False,
        adjustments: Optional[ModifierAdjustments] = None,
    ) -> Tuple[Optional[int], dict]:
        nonlocal game_clock, home_total, away_total, possession_counter, q_idx

        game_clock += sec_per_poss
        elapsed_minutes = game_clock / 60
        q_idx = current_q_idx

        home_active = [home_by_id[pid] for pid in home_active_ids if pid in home_by_id]
        away_active = [away_by_id[pid] for pid in away_active_ids if pid in away_by_id]

        for pid in home_active_ids:
            if pid in box:
                box[pid]["min"] += min_per_poss_val
        for pid in away_active_ids:
            if pid in box:
                box[pid]["min"] += min_per_poss_val

        offense, defense = (home_active, away_active) if is_home else (away_active, home_active)
        if not offense or not defense:
            return None, {}

        home_bonus = HOME_ADVANTAGE / expected_possessions if is_home else 0.0
        event = resolve_possession(
            offense, defense, rng, home_bonus, name_map,
            team_defense_factor=team_defense_factor,
            is_fastbreak=is_fastbreak,
            adjustments=adjustments,
        )

        pts, fouled_out_pid = _apply_event(box, event)

        if is_home:
            home_total += pts
        else:
            away_total += pts
        quarter_scores["home" if is_home else "away"][current_q_idx] += pts

        home_delta = pts if is_home else -pts
        for pid in home_active_ids:
            if pid in box:
                box[pid]["plus_minus"] += home_delta
        for pid in away_active_ids:
            if pid in box:
                box[pid]["plus_minus"] -= home_delta

        possession_counter += 1
        clock_secs = game_clock_override if game_clock_override is not None else round(game_clock)
        poss_record = {
            "possession": possession_counter,
            "game_clock_seconds": clock_secs,
            "quarter": current_q_idx + 1,
            "is_home": is_home,
            "pts": pts,
            **event,
        }
        if steps:
            current_chunk_events.append(poss_record)
        elif capture_descriptions:
            all_events.append(poss_record)

        _maybe_snapshot(elapsed_minutes, current_q_idx)
        return fouled_out_pid, event

    # -----------------------------------------------------------------------
    # Regulation
    # -----------------------------------------------------------------------
    home_ids = set(home_by_id.keys())
    away_ids = set(away_by_id.keys())

    # Build active modifier list (only populated for clock-based loop)
    active_modifiers: list = []
    if cfg.use_clock and cfg.use_momentum:
        from app.services.modifiers.momentum import MomentumModifier
        home_composure = (
            sum(p["overall"] for p in home_players) / len(home_players) / 100.0
            if home_players else 0.75
        )
        away_composure = (
            sum(p["overall"] for p in away_players) / len(away_players) / 100.0
            if away_players else 0.75
        )
        active_modifiers.append(MomentumModifier(cfg, home_composure, away_composure))

    if cfg.use_clock:
        # Clock-based loop — each quarter runs until the clock hits 0
        mean_quarter_possessions = expected_possessions / 4
        mean_poss_time_clock = QUARTER_SECONDS / mean_quarter_possessions
        # Compensate for oreb chains consuming less clock than base possessions.
        # Derive t_base so the weighted avg possession time stays at the pace-expected
        # mean: (1 - oreb_frac) × t_base + oreb_frac × t_oreb = mean → solve for t_base.
        if cfg.use_second_chance:
            oreb_frac = OREB_RATE * ELIGIBLE_MISS_RATE
            mean_poss_time_clock = (
                (mean_poss_time_clock - oreb_frac * cfg.second_chance_time_mean)
                / (1.0 - oreb_frac)
            )
        current_is_home = tip_winner_is_home

        for reg_q_idx in range(4):
            quarter_clock = float(QUARTER_SECONDS)
            # NBA possession arrow: Q1=tip winner, Q2=tip loser, Q3=tip winner, Q4=tip loser
            current_is_home = tip_winner_is_home if reg_q_idx % 2 == 0 else not tip_winner_is_home

            oreb_depth = 0
            next_is_fastbreak = False

            while quarter_clock > 0:

                # Check for strategic foul before running offense for the leading team
                if cfg.use_strategic_foul and quarter_clock <= cfg.strategic_foul_clock_threshold:
                    lead = home_total - away_total
                    # Trailing team is on defense; offense is the leading team
                    trailing_is_home = lead < 0
                    if current_is_home != trailing_is_home:
                        # Current offense IS the leading team — trailing defense may foul
                        margin = abs(lead)
                        if cfg.strategic_foul_margin_min <= margin <= cfg.strategic_foul_margin_max:
                            if rng.random() < cfg.strategic_foul_probability:
                                # Generate intentional foul — FTs for worst FT shooter on offense
                                offense_ids = home_ids if current_is_home else away_ids
                                offense_on_court = [
                                    p for p in (home_players if current_is_home else away_players)
                                    if p["id"] in offense_ids
                                ]
                                target = min(offense_on_court, key=lambda p: p["free_throw"])
                                ft_prob = _attr_to_prob(target["free_throw"], lo=0.60, hi=0.95)
                                fta = 2
                                ftm = sum(1 for _ in range(fta) if rng.random() < ft_prob)
                                foul_time = max(2.0, min(8.0, rng.gauss(4.0, 1.0)))
                                quarter_clock = max(0.0, quarter_clock - foul_time)
                                game_clock += foul_time
                                pts = ftm
                                if current_is_home:
                                    home_total += pts
                                    quarter_scores["home"][reg_q_idx] += pts
                                else:
                                    away_total += pts
                                    quarter_scores["away"][reg_q_idx] += pts
                                possession_counter += 1
                                foul_event = {
                                    "scorer": target["id"], "shot_type": None, "made": False,
                                    "assisted_by": None, "rebounded_by": None,
                                    "turnover_by": None, "steal_by": None, "block_by": None,
                                    "fouled_by": None, "fta": fta, "ftm": ftm,
                                    "description": f"{target['name']} shoots {ftm}/{fta} FTs (intentional foul)" if name_map else None,
                                }
                                if steps:
                                    current_chunk_events.append({
                                        "possession": possession_counter,
                                        "game_clock_seconds": int(quarter_clock),
                                        "quarter": reg_q_idx + 1,
                                        "is_home": current_is_home,
                                        "pts": pts,
                                        **foul_event,
                                    })
                                _maybe_snapshot(game_clock / 60, reg_q_idx)
                                # After intentional foul, flip possession (offense inbounds)
                                current_is_home = not current_is_home
                                oreb_depth = 0
                                next_is_fastbreak = False
                                continue

                # Sample possession time based on context
                if next_is_fastbreak:
                    poss_time = max(3.0, min(12.0, rng.gauss(cfg.fastbreak_time_mean, cfg.fastbreak_time_std)))
                elif oreb_depth > 0:
                    poss_time = max(3.0, min(14.0, rng.gauss(cfg.second_chance_time_mean, cfg.second_chance_time_std)))
                else:
                    poss_time = max(5.0, min(24.0, rng.gauss(mean_poss_time_clock, cfg.halfcourt_time_std)))
                poss_time = min(poss_time, quarter_clock)
                quarter_clock -= poss_time

                current_minute = min(GAME_MINUTES - 1, reg_q_idx * 12 + int((QUARTER_SECONDS - quarter_clock) / 60))
                home_active_ids = home_rotation[current_minute]
                away_active_ids = away_rotation[current_minute]

                team_defense_factor = 1.0
                if cfg.use_team_defense:
                    defending_stats = away_stats if current_is_home else home_stats
                    if defending_stats:
                        # Dampen to 50% of raw spread — full strength over-rewards
                        # top/bottom defenses given our limited player rating granularity
                        raw = defending_stats["def_rating"] / cfg.league_avg_def_rating
                        team_defense_factor = 1.0 + (raw - 1.0) * 0.5

                game_state = GameState(
                    home_score=home_total,
                    away_score=away_total,
                    quarter=reg_q_idx + 1,
                    clock_seconds=quarter_clock,
                    possession_number=possession_counter,
                )
                poss_adjustments: Optional[ModifierAdjustments] = None
                if active_modifiers:
                    poss_adjustments = ModifierAdjustments()
                    for mod in active_modifiers:
                        poss_adjustments = poss_adjustments + mod.get_adjustments(current_is_home, game_state)

                fouled_out_pid, event = _apply_possession(
                    home_active_ids, away_active_ids, current_is_home,
                    poss_time, poss_time / 60.0, reg_q_idx,
                    game_clock_override=int(quarter_clock),
                    team_defense_factor=team_defense_factor,
                    is_fastbreak=next_is_fastbreak,
                    adjustments=poss_adjustments,
                )
                for mod in active_modifiers:
                    mod.update(event, current_is_home, game_state)

                if fouled_out_pid:
                    if fouled_out_pid in home_by_id:
                        patch_rotation(home_rotation, fouled_out_pid, home_by_min, current_minute + 1)
                    else:
                        patch_rotation(away_rotation, fouled_out_pid, away_by_min, current_minute + 1)

                # Second-chance: check if offense got an offensive rebound
                next_is_fastbreak = False
                rebounded_by = event.get("rebounded_by")
                offense_ids = home_ids if current_is_home else away_ids
                is_oreb = (
                    cfg.use_second_chance
                    and rebounded_by is not None
                    and rebounded_by in offense_ids
                    and event.get("shot_type") is not None
                    and not event.get("made")
                )
                if is_oreb and oreb_depth < cfg.oreb_chain_cap:
                    oreb_depth += 1
                    # Same team keeps possession — don't flip
                else:
                    oreb_depth = 0
                    current_is_home = not current_is_home
                    # Fast break: only on steals
                    if cfg.use_fast_break and event.get("steal_by") is not None:
                        next_is_fastbreak = True

    else:
        # Fixed-possession loop (original behavior)
        # Possession flips on every non-oreb outcome; oreb keeps the same team.
        current_is_home = tip_winner_is_home
        for poss_idx in range(expected_possessions):
            current_minute = min(GAME_MINUTES - 1, int((game_clock + SECONDS_PER_POSSESSION) / 60))
            reg_q_idx = min(3, current_minute // 12)

            home_active_ids = home_rotation[current_minute]
            away_active_ids = away_rotation[current_minute]

            fouled_out_pid, event = _apply_possession(
                home_active_ids, away_active_ids, current_is_home,
                SECONDS_PER_POSSESSION, min_per_poss, reg_q_idx,
            )
            if fouled_out_pid:
                if fouled_out_pid in home_by_id:
                    patch_rotation(home_rotation, fouled_out_pid, home_by_min, current_minute + 1)
                else:
                    patch_rotation(away_rotation, fouled_out_pid, away_by_min, current_minute + 1)

            offense_ids = home_ids if current_is_home else away_ids
            rebounded_by = event.get("rebounded_by")
            is_oreb = (
                rebounded_by is not None
                and rebounded_by in offense_ids
                and event.get("shot_type") is not None
                and not event.get("made")
            )
            if not is_oreb:
                current_is_home = not current_is_home

    # -----------------------------------------------------------------------
    # Overtime — loop until a winner emerges
    # -----------------------------------------------------------------------
    ot_period = 0
    min_per_ot_poss = OT_MINUTES / POSSESSIONS_PER_OT

    while home_total == away_total:
        ot_period += 1
        ot_tip_is_home = rng.random() < 0.5
        ot_q_idx = 3 + ot_period  # OT1=4, OT2=5, ...
        quarter_scores["home"].append(0)
        quarter_scores["away"].append(0)

        # OT lineup: start from Q4 end, track foul-outs within this OT period
        home_ot_ids = list(home_rotation[GAME_MINUTES - 1])
        away_ot_ids = list(away_rotation[GAME_MINUTES - 1])

        for ot_poss_idx in range(POSSESSIONS_PER_OT):
            is_home = (ot_poss_idx % 2 == 0) == ot_tip_is_home
            fouled_out_pid, _ = _apply_possession(
                home_ot_ids, away_ot_ids, is_home,
                SECONDS_PER_OT_POSSESSION, min_per_ot_poss, ot_q_idx,
            )
            # Handle foul-outs within OT by replacing directly in the active list
            if fouled_out_pid:
                if fouled_out_pid in home_ot_ids:
                    home_ot_ids = [p for p in home_ot_ids if p != fouled_out_pid]
                    repl = next((p["id"] for p in home_by_min
                                 if p["id"] not in home_ot_ids and not box[p["id"]]["fouled_out"]), None)
                    if repl:
                        home_ot_ids.append(repl)
                else:
                    away_ot_ids = [p for p in away_ot_ids if p != fouled_out_pid]
                    repl = next((p["id"] for p in away_by_min
                                 if p["id"] not in away_ot_ids and not box[p["id"]]["fouled_out"]), None)
                    if repl:
                        away_ot_ids.append(repl)

    # Final snapshot if game end didn't land on a time threshold
    if steps and (not chunks or chunks[-1]["home_score"] != home_total or chunks[-1]["away_score"] != away_total):
        chunks.append({
            "home_score": home_total,
            "away_score": away_total,
            "elapsed_minutes": round(game_clock / 60, 1),
            "quarter": q_idx + 1,
            "box": _snapshot_box(box),
        })
        chunk_events.append(list(current_chunk_events))

    return {
        "season": season,
        "home_score": home_total,
        "away_score": away_total,
        "quarter_scores": quarter_scores,
        "box_score": box,
        "chunks": chunks,
        "chunk_events": chunk_events,
        "events": all_events,
        "went_to_ot": ot_period > 0,
        "ot_periods": ot_period,
    }
