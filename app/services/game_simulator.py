"""Possession-based game simulation engine.

Extracted from scratch/03_game_simulator.py. The scratch script is now a thin
CLI wrapper around this module.

Public surface:
    load_roster(db, team_id, season) -> list[dict]
    simulate_game(home_players, away_players, seed) -> dict
"""
import random
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.player import Player
from app.models.player_attributes import PlayerAttributes
from app.models.player_tendencies import PlayerTendencies
from app.models.player_season_stats import PlayerSeasonStats

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
POSSESSIONS_PER_GAME = 200      # ~100 per team, matches NBA average pace
GAME_MINUTES = 48
SECONDS_PER_POSSESSION = (GAME_MINUTES * 60) / POSSESSIONS_PER_GAME
HOME_ADVANTAGE = 3.0            # extra points spread across home possessions
SUB_VARIANCE = 2.0              # σ in minutes for substitution timing (Normal dist)
LEAGUE_AVG_TOV_PER36 = 2.5     # used to normalize per-player turnover rates


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


def resolve_possession(
    offense: list[dict],
    defense: list[dict],
    rng: random.Random,
    home_bonus: float = 0.0,
) -> dict:
    """Simulate one possession and return an event dict."""
    result: dict = {
        "scorer": None, "shot_type": None, "made": False,
        "assisted_by": None, "rebounded_by": None,
        "turnover_by": None, "steal_by": None, "block_by": None,
        "fouled_by": None, "fta": 0, "ftm": 0,
    }

    # 1. Ball handler — weighted by usage rate
    usage_weights = [p["usage_rate"] for p in offense]
    total_usage = sum(usage_weights)
    ball_handler = rng.choices(offense, weights=[w / total_usage for w in usage_weights])[0]

    # 2. Steal check (~1.7% of possessions)
    best_defender = max(defense, key=lambda p: p["steal"])
    if rng.random() < (best_defender["steal"] / 100.0) * 0.034:
        result["turnover_by"] = ball_handler["id"]
        result["steal_by"] = best_defender["id"]
        return result

    # 3. Turnover — bad pass, travel, etc. (~13% at league average)
    if rng.random() < (ball_handler["turnover_rate"] / LEAGUE_AVG_TOV_PER36) * 0.13:
        result["turnover_by"] = ball_handler["id"]
        return result

    # 3b. Offensive foul — charge or illegal screen (~1.5% of possessions)
    if rng.random() < 0.015:
        result["turnover_by"] = ball_handler["id"]
        result["fouled_by"] = ball_handler["id"]
        return result

    # 4. Shot type selection
    three_rate = ball_handler["three_point_rate"]
    shot_type = rng.choices(
        ["three", "mid", "close"],
        weights=[three_rate, (1 - three_rate) * 0.4, (1 - three_rate) * 0.6],
    )[0]
    result["shot_type"] = shot_type
    result["scorer"] = ball_handler["id"]

    # 5. Block check on non-three shots
    if shot_type != "three":
        best_blocker = max(defense, key=lambda p: p["block"])
        if rng.random() < (best_blocker["block"] / 100.0) * 0.04:
            result["block_by"] = best_blocker["id"]
            if rng.random() < 0.27:
                result["rebounded_by"] = rng.choices(
                    offense, weights=[p["oreb_rate"] for p in offense]
                )[0]["id"]
            else:
                result["rebounded_by"] = rng.choices(
                    defense, weights=[p["dreb_rate"] for p in defense]
                )[0]["id"]
            return result

    # 6. Defender
    defender = rng.choice(defense)

    # 7. Make/miss
    if shot_type == "three":
        base_prob = _attr_to_prob(ball_handler["three_point"], lo=0.28, hi=0.42)
        defense_penalty = defender["perimeter_defense"] / 100.0 * 0.08
    elif shot_type == "mid":
        base_prob = _attr_to_prob(ball_handler["mid_range"], lo=0.35, hi=0.52)
        defense_penalty = defender["perimeter_defense"] / 100.0 * 0.06
    else:
        base_prob = _attr_to_prob(ball_handler["close_shot"], lo=0.45, hi=0.65)
        defense_penalty = defender["interior_defense"] / 100.0 * 0.10

    result["made"] = rng.random() < max(0.15, base_prob - defense_penalty + home_bonus / 100.0)

    # 8. Foul / free throws — non-three attempts only (~20%)
    if shot_type != "three" and rng.random() < 0.20:
        result["fouled_by"] = defender["id"]
        ft_prob = _attr_to_prob(ball_handler["free_throw"], lo=0.60, hi=0.95)
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

    # 10. Rebound on miss — NBA average 27% OREB, individual weighted by OREB%/DREB%
    if not result["made"] and result["fta"] == 0:
        if rng.random() < 0.27:
            result["rebounded_by"] = rng.choices(
                offense, weights=[p["oreb_rate"] for p in offense]
            )[0]["id"]
        else:
            result["rebounded_by"] = rng.choices(
                defense, weights=[p["dreb_rate"] for p in defense]
            )[0]["id"]

    return result


# ---------------------------------------------------------------------------
# Game simulation
# ---------------------------------------------------------------------------
def simulate_game(
    home_players: list[dict],
    away_players: list[dict],
    seed: int,
    season: Optional[str] = None,
) -> dict:
    """Simulate one full game.

    Returns a dict with:
        home_score, away_score, quarter_scores, box_score, season
    """
    rng = random.Random(seed)

    home_by_id = {p["id"]: p for p in home_players}
    away_by_id = {p["id"]: p for p in away_players}

    home_rotation = build_rotation(home_players, rng)
    away_rotation = build_rotation(away_players, rng)

    box: dict = {
        pid: {
            "pts": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0,
            "tov": 0, "pf": 0, "fgm": 0, "fga": 0,
            "fg3m": 0, "fg3a": 0, "ftm": 0, "fta": 0,
            "min": 0.0, "fouled_out": False,
        }
        for pid in list(home_by_id) + list(away_by_id)
    }

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

    quarter_scores: dict = {"home": [0, 0, 0, 0], "away": [0, 0, 0, 0]}
    game_clock = 0.0
    min_per_poss = GAME_MINUTES / POSSESSIONS_PER_GAME

    for poss_idx in range(POSSESSIONS_PER_GAME):
        game_clock += SECONDS_PER_POSSESSION
        current_minute = min(GAME_MINUTES - 1, int(game_clock / 60))
        q_idx = min(3, current_minute // 12)

        home_active_ids = home_rotation[current_minute]
        away_active_ids = away_rotation[current_minute]
        home_active = [home_by_id[pid] for pid in home_active_ids if pid in home_by_id]
        away_active = [away_by_id[pid] for pid in away_active_ids if pid in away_by_id]

        for pid in home_active_ids:
            if pid in box:
                box[pid]["min"] += min_per_poss
        for pid in away_active_ids:
            if pid in box:
                box[pid]["min"] += min_per_poss

        if poss_idx % 2 == 0:
            offense, defense, is_home = home_active, away_active, True
        else:
            offense, defense, is_home = away_active, home_active, False

        if not offense or not defense:
            continue

        home_bonus = HOME_ADVANTAGE / POSSESSIONS_PER_GAME if is_home else 0.0
        event = resolve_possession(offense, defense, rng, home_bonus)

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
                if event["shot_type"] == "three":
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

        fouled_pid = event.get("fouled_by")
        if fouled_pid and fouled_pid in box and not box[fouled_pid]["fouled_out"]:
            box[fouled_pid]["pf"] += 1
            if box[fouled_pid]["pf"] >= 6:
                box[fouled_pid]["fouled_out"] = True
                if fouled_pid in home_by_id:
                    patch_rotation(home_rotation, fouled_pid, home_by_min, current_minute + 1)
                else:
                    patch_rotation(away_rotation, fouled_pid, away_by_min, current_minute + 1)

        quarter_scores["home" if is_home else "away"][q_idx] += pts

    return {
        "season": season,
        "home_score": sum(quarter_scores["home"]),
        "away_score": sum(quarter_scores["away"]),
        "quarter_scores": quarter_scores,
        "box_score": box,
    }
