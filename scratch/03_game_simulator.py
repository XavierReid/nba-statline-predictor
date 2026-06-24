"""
Phase 1 GameSimulator scratch script.

No DB, no classes — just prove the possession-based simulation logic works.
Run from the project root:
    python scratch/03_game_simulator.py

What this proves:
  1. Rotation schedule generation with substitution variance
  2. Possession-by-possession simulation using tendencies + attributes
  3. Defender selection from opposing active lineup
  4. Box score accumulation
  5. Quarter score tracking + game clock
"""
import random
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.player import Player
from app.models.player_attributes import PlayerAttributes
from app.models.player_tendencies import PlayerTendencies
from app.models.player_season_stats import PlayerSeasonStats
from app.models.team import Team
from sqlalchemy import select

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SEASON = "2024-25"
POSSESSIONS_PER_GAME = 200          # ~100 per team, NBA average
GAME_MINUTES = 48
SECONDS_PER_POSSESSION = (GAME_MINUTES * 60) / POSSESSIONS_PER_GAME
HOME_ADVANTAGE = 3.0                # points added to home team's scoring baseline
SUB_VARIANCE = 2.0                  # σ in minutes for substitution timing
MIN_REST_MINUTES = 2.0              # min time before a player can re-enter


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_roster(db, team_id: int, season: str) -> list[dict]:
    """Load top 10 players by minutes for a team, normalized to 240 total minutes."""
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
            "is_starter": False,      # assigned below
            # attributes
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
            "assist_rate": t.assist_rate or 4.0,
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
    """
    Returns a list of 48 slots (one per game minute), each a list of 5 player IDs
    on the court during that minute.

    Approach:
      - Assign each player a set of minutes to play based on their minutes budget
      - Substitution points sampled from Normal(expected_minute, SUB_VARIANCE)
        so timing varies rather than being robotically fixed
      - Starters bias toward closing Q4 (last 4 minutes always starters)
      - Exactly 5 players active per minute
    """
    starters = [p for p in players if p["is_starter"]]
    bench = [p for p in players if not p["is_starter"]]

    # Build per-player minute sets from their budget
    # Strategy: give starters Q1 + Q3 + closing Q4, bench gets Q2 + mid-Q4
    # Then add variance by jittering the handoff points

    # Each slot: set of player IDs
    slots = [set() for _ in range(48)]

    def assign_minutes(player: dict, target_min: float, preferred_start: int, prefer_close: bool):
        """Greedily assign `target_min` minutes to a player starting around preferred_start."""
        remaining = int(target_min)
        minute = max(0, min(47, int(rng.gauss(preferred_start, SUB_VARIANCE))))
        direction = 1
        visited = set()
        while remaining > 0 and len(visited) < 48:
            idx = minute % 48
            if len(slots[idx]) < 5 and idx not in visited:
                slots[idx].add(player["id"])
                remaining -= 1
            visited.add(idx)  # always mark scanned, not just assigned
            minute = (minute + 1) % 48

    # Assign starters first — preferred start at minute 0, 24 (Q1, Q3)
    for p in starters:
        assign_minutes(p, p["minutes"], preferred_start=0, prefer_close=True)

    # Assign bench — preferred start at minute 12, 36 (Q2, mid-Q4)
    for p in bench:
        assign_minutes(p, p["minutes"], preferred_start=12, prefer_close=False)

    # Fill any remaining slots (should be rare) with highest-minute players
    sorted_by_min = sorted(players, key=lambda x: x["minutes"], reverse=True)
    for i, slot in enumerate(slots):
        while len(slot) < 5:
            for p in sorted_by_min:
                if p["id"] not in slot:
                    slot.add(p["id"])
                    break

    # Return as lists
    return [list(s) for s in slots]


# ---------------------------------------------------------------------------
# Possession resolution
# ---------------------------------------------------------------------------
def attr_to_prob(rating: int, lo: float = 0.25, hi: float = 0.75) -> float:
    """Map a 0-100 attribute rating to a probability in [lo, hi]."""
    return lo + (rating / 100.0) * (hi - lo)


def resolve_possession(
    offense: list[dict],         # 5 active offensive players
    defense: list[dict],         # 5 active defensive players
    players_by_id: dict,
    rng: random.Random,
    home_bonus: float = 0.0,
) -> dict:
    """
    Simulate one possession. Returns a dict of events:
      scorer, shot_type, made, assisted_by, rebounded_by, turnover_by, fouled
    """
    result = {
        "scorer": None, "shot_type": None, "made": False,
        "assisted_by": None, "rebounded_by": None,
        "turnover_by": None, "steal_by": None, "block_by": None,
        "fta": 0, "ftm": 0,
    }

    # 1. Select ball handler weighted by usage_rate
    usage_weights = [p["usage_rate"] for p in offense]
    total_usage = sum(usage_weights)
    ball_handler = rng.choices(offense, weights=[w / total_usage for w in usage_weights])[0]

    # 2. Steal check — defender intercepts before a shot
    # Steals happen on ~13% of turnovers; ~1.7% of all possessions end in a steal
    best_defender = max(defense, key=lambda p: p["steal"])
    steal_prob = (best_defender["steal"] / 100.0) * 0.034
    if rng.random() < steal_prob:
        result["turnover_by"] = ball_handler["id"]
        result["steal_by"] = best_defender["id"]
        return result

    # 3. Turnover check — ball handler miscues (bad pass, travel, etc.)
    # Normalized to league average (~2.5 TOV/36) so rate lands near 13% per possession
    LEAGUE_AVG_TOV_PER36 = 2.5
    tov_rate = (ball_handler["turnover_rate"] / LEAGUE_AVG_TOV_PER36) * 0.13
    if rng.random() < tov_rate:
        result["turnover_by"] = ball_handler["id"]
        return result

    # 4. Shot type selection weighted by tendencies
    three_rate = ball_handler["three_point_rate"]
    mid_rate = (1 - three_rate) * 0.4
    close_rate = (1 - three_rate) * 0.6

    shot_type = rng.choices(
        ["three", "mid", "close"],
        weights=[three_rate, mid_rate, close_rate]
    )[0]
    result["shot_type"] = shot_type
    result["scorer"] = ball_handler["id"]

    # 5. Block check on non-three shots
    # Best interior defender has a small chance to block; blocked shots miss
    if shot_type != "three":
        best_blocker = max(defense, key=lambda p: p["block"])
        block_prob = (best_blocker["block"] / 100.0) * 0.04
        if rng.random() < block_prob:
            result["made"] = False
            result["block_by"] = best_blocker["id"]
            # blocked shot goes to rebound — fall through to rebound logic below
            # skip make/miss resolution and foul check
            ow = [p["offensive_rebound"] for p in offense]
            dw = [p["defensive_rebound"] for p in defense]
            if rng.random() < sum(ow) / (sum(ow) + sum(dw) * 2.5):
                result["rebounded_by"] = rng.choices(offense, weights=ow)[0]["id"]
            else:
                result["rebounded_by"] = rng.choices(defense, weights=dw)[0]["id"]
            return result

    # 6. Defender — random from active defense
    defender = rng.choice(defense)

    # 7. Resolve make/miss
    if shot_type == "three":
        defense_penalty = defender["perimeter_defense"] / 100.0 * 0.08
        base_prob = attr_to_prob(ball_handler["three_point"], lo=0.28, hi=0.42)
    elif shot_type == "mid":
        defense_penalty = defender["perimeter_defense"] / 100.0 * 0.06
        base_prob = attr_to_prob(ball_handler["mid_range"], lo=0.35, hi=0.52)
    else:  # close
        defense_penalty = defender["interior_defense"] / 100.0 * 0.10
        base_prob = attr_to_prob(ball_handler["close_shot"], lo=0.45, hi=0.65)

    make_prob = max(0.15, base_prob - defense_penalty + home_bonus / 100.0)
    result["made"] = rng.random() < make_prob

    # 8. Foul / free throws — applies to all non-three attempts
    # ~20% of non-three attempts draw a foul (makes = and-one, misses = 2 FTs)
    if shot_type != "three" and rng.random() < 0.20:
        ft_prob = attr_to_prob(ball_handler["free_throw"], lo=0.60, hi=0.95)
        if result["made"]:
            # and-one: shot counts + 1 FT
            result["fta"] = 1
            result["ftm"] = 1 if rng.random() < ft_prob else 0
        else:
            # shooting foul: 2 FTs, shot wiped
            result["fta"] = 2
            result["ftm"] = sum(1 for _ in range(2) if rng.random() < ft_prob)

    # 9. Assist (if made, ~60% assisted)
    if result["made"] and shot_type in ("three", "mid"):
        if rng.random() < 0.60:
            passers = [p for p in offense if p["id"] != ball_handler["id"]]
            if passers:
                ast_weights = [p["passing"] for p in passers]
                result["assisted_by"] = rng.choices(
                    passers, weights=ast_weights
                )[0]["id"]

    # 8. Rebound on miss
    if not result["made"] and result["fta"] == 0:
        oreb_weights = [p["offensive_rebound"] for p in offense]
        dreb_weights = [p["defensive_rebound"] for p in defense]
        total_oreb = sum(oreb_weights)
        total_dreb = sum(dreb_weights)
        oreb_prob = total_oreb / (total_oreb + total_dreb * 2.5)   # defense favored
        if rng.random() < oreb_prob:
            result["rebounded_by"] = rng.choices(offense, weights=oreb_weights)[0]["id"]
        else:
            result["rebounded_by"] = rng.choices(defense, weights=dreb_weights)[0]["id"]

    return result


# ---------------------------------------------------------------------------
# Game simulation
# ---------------------------------------------------------------------------
def simulate_game(home_players: list[dict], away_players: list[dict], seed: int) -> dict:
    """
    Simulate one full game. Returns:
      - box_score: {player_id: stat line}
      - score: {home: int, away: int}
      - quarter_scores: {home: [q1,q2,q3,q4], away: [q1,q2,q3,q4]}
      - minutes_log: {player_id: [{minute, possession_count}]}  (for step-through)
    """
    rng = random.Random(seed)

    home_by_id = {p["id"]: p for p in home_players}
    away_by_id = {p["id"]: p for p in away_players}
    all_by_id = {**home_by_id, **away_by_id}

    home_rotation = build_rotation(home_players, rng)
    away_rotation = build_rotation(away_players, rng)

    # Box score: player_id → stats
    box = {pid: {"pts": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0,
                 "tov": 0, "fgm": 0, "fga": 0, "fg3m": 0, "fg3a": 0,
                 "ftm": 0, "fta": 0, "min": 0.0, "possessions": 0}
           for pid in list(home_by_id) + list(away_by_id)}

    quarter_scores = {"home": [0, 0, 0, 0], "away": [0, 0, 0, 0]}
    game_clock = 0.0   # seconds elapsed
    q_idx = 0

    possessions_per_minute = POSSESSIONS_PER_GAME / GAME_MINUTES

    for poss_idx in range(POSSESSIONS_PER_GAME):
        # Advance game clock
        game_clock += SECONDS_PER_POSSESSION
        current_minute = min(47, int(game_clock / 60))
        q_idx = min(3, current_minute // 12)

        # Who's on court this minute
        home_active_ids = home_rotation[current_minute]
        away_active_ids = away_rotation[current_minute]
        home_active = [home_by_id[pid] for pid in home_active_ids if pid in home_by_id]
        away_active = [away_by_id[pid] for pid in away_active_ids if pid in away_by_id]

        # Accrue minutes for active players
        min_per_poss = GAME_MINUTES / POSSESSIONS_PER_GAME
        for pid in home_active_ids:
            if pid in box:
                box[pid]["min"] += min_per_poss
        for pid in away_active_ids:
            if pid in box:
                box[pid]["min"] += min_per_poss

        # Alternate possessions: even = home offense, odd = away offense
        if poss_idx % 2 == 0:
            offense, defense = home_active, away_active
            is_home = True
        else:
            offense, defense = away_active, home_active
            is_home = False

        if not offense or not defense:
            continue

        home_bonus = HOME_ADVANTAGE / POSSESSIONS_PER_GAME if is_home else 0.0
        result = resolve_possession(offense, defense, all_by_id, rng, home_bonus)

        # Accumulate stats
        pts = 0
        if result["turnover_by"]:
            pid = result["turnover_by"]
            if pid in box:
                box[pid]["tov"] += 1
            # credit steal to defender (on opposite team)
            steal_pid = result.get("steal_by")
            if steal_pid and steal_pid in box:
                box[steal_pid]["stl"] += 1

        elif result["scorer"]:
            # credit block to defender
            block_pid = result.get("block_by")
            if block_pid and block_pid in box:
                box[block_pid]["blk"] += 1

            pid = result["scorer"]
            if pid in box:
                box[pid]["possessions"] += 1
                if result["shot_type"] == "three":
                    box[pid]["fg3a"] += 1
                    box[pid]["fga"] += 1
                    if result["made"]:
                        box[pid]["fg3m"] += 1
                        box[pid]["fgm"] += 1
                        box[pid]["pts"] += 3
                        pts = 3
                else:
                    box[pid]["fga"] += 1
                    if result["made"]:
                        box[pid]["fgm"] += 1
                        box[pid]["pts"] += 2
                        pts = 2

                if result["fta"] > 0:
                    box[pid]["fta"] += result["fta"]
                    box[pid]["ftm"] += result["ftm"]
                    box[pid]["pts"] += result["ftm"]
                    pts += result["ftm"]

            if result["assisted_by"] and result["assisted_by"] in box:
                box[result["assisted_by"]]["ast"] += 1

            if result["rebounded_by"] and result["rebounded_by"] in box:
                box[result["rebounded_by"]]["reb"] += 1

        # Quarter scores
        side = "home" if is_home else "away"
        quarter_scores[side][q_idx] += pts

    home_score = sum(quarter_scores["home"])
    away_score = sum(quarter_scores["away"])

    return {
        "home_score": home_score,
        "away_score": away_score,
        "quarter_scores": quarter_scores,
        "box_score": box,
    }


# ---------------------------------------------------------------------------
# Pretty print
# ---------------------------------------------------------------------------
def print_box_score(players_by_id: dict, box: dict, team_name: str, team_ids: set):
    print(f"\n  {team_name}")
    print(f"  {'Name':<25} {'MIN':>5} {'PTS':>4} {'REB':>4} {'AST':>4} {'STL':>4} {'BLK':>4} {'TOV':>4} {'FG':>8} {'3PT':>8} {'FT':>8}")
    rows = sorted(
        [(pid, s) for pid, s in box.items() if pid in team_ids],
        key=lambda x: x[1]["pts"], reverse=True
    )
    for pid, s in rows:
        if s["min"] < 0.5:
            continue
        name = players_by_id.get(pid, {}).get("name", str(pid))
        fg = f"{s['fgm']}/{s['fga']}"
        three = f"{s['fg3m']}/{s['fg3a']}"
        ft = f"{s['ftm']}/{s['fta']}"
        print(f"  {name:<25} {s['min']:>5.1f} {s['pts']:>4} {s['reb']:>4} {s['ast']:>4} {s['stl']:>4} {s['blk']:>4} {s['tov']:>4} {fg:>8} {three:>8} {ft:>8}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    db = SessionLocal()

    # Pick two teams by abbreviation — change these to test any matchup
    HOME_ABBR = sys.argv[1] if len(sys.argv) > 1 else "DEN"
    AWAY_ABBR = sys.argv[2] if len(sys.argv) > 2 else "GSW"
    SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 42

    home_team = db.execute(select(Team).where(Team.abbreviation == HOME_ABBR)).scalar_one_or_none()
    away_team = db.execute(select(Team).where(Team.abbreviation == AWAY_ABBR)).scalar_one_or_none()

    if not home_team or not away_team:
        print(f"Team not found. Check abbreviations.")
        sys.exit(1)

    print(f"\nLoading rosters for {home_team.city} {home_team.nickname} vs {away_team.city} {away_team.nickname}...")
    home_players = load_roster(db, home_team.id, SEASON)
    away_players = load_roster(db, away_team.id, SEASON)
    db.close()

    if not home_players or not away_players:
        print("Could not load rosters. Make sure season stats are ingested.")
        sys.exit(1)

    print(f"Simulating with seed={SEED}...\n")
    result = simulate_game(home_players, away_players, seed=SEED)

    home_ids = {p["id"] for p in home_players}
    away_ids = {p["id"] for p in away_players}
    all_by_id = {p["id"]: p for p in home_players + away_players}

    qs = result["quarter_scores"]
    print(f"  {'':25} {'Q1':>4} {'Q2':>4} {'Q3':>4} {'Q4':>4} {'TOT':>5}")
    print(f"  {home_team.city + ' ' + home_team.nickname:<25} {qs['home'][0]:>4} {qs['home'][1]:>4} {qs['home'][2]:>4} {qs['home'][3]:>4} {result['home_score']:>5}")
    print(f"  {away_team.city + ' ' + away_team.nickname:<25} {qs['away'][0]:>4} {qs['away'][1]:>4} {qs['away'][2]:>4} {qs['away'][3]:>4} {result['away_score']:>5}")

    print_box_score(all_by_id, result["box_score"], f"{home_team.city} {home_team.nickname} (Home)", home_ids)
    print_box_score(all_by_id, result["box_score"], f"{away_team.city} {away_team.nickname} (Away)", away_ids)
