"""
Quick data explorer — run from the project root:
    python scratch/explore_ratings.py           # defaults to 2024-25
    python scratch/explore_ratings.py 2025-26   # or pass any season
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import SessionLocal
from app.models.player import Player
from app.models.player_attributes import PlayerAttributes
from app.models.player_tendencies import PlayerTendencies
from app.models.team import Team
from app.models.player_season_stats import PlayerSeasonStats
from sqlalchemy import select, func

SEASON = sys.argv[1] if len(sys.argv) > 1 else "2024-25"
print(f"Season: {SEASON}\n")
db = SessionLocal()

def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


# ------------------------------------------------------------------
# 1. Top 30 overall ratings
# ------------------------------------------------------------------
section("TOP 30 OVERALL RATINGS")
rows = db.execute(
    select(Player, PlayerAttributes)
    .join(PlayerAttributes, PlayerAttributes.player_id == Player.id)
    .where(PlayerAttributes.season == SEASON)
    .order_by(PlayerAttributes.overall_rating.desc())
    .limit(30)
).all()
print(f"{'#':<4} {'Name':<25} {'Pos':<6} {'OVR':<5} {'3PT':<5} {'MID':<5} {'FT':<5} {'PASS':<5} {'STL':<5} {'BLK':<5} {'REB'}")
for i, (p, a) in enumerate(rows, 1):
    reb = round((a.offensive_rebound + a.defensive_rebound) / 2)
    print(f"{i:<4} {p.full_name:<25} {(p.position or '?'):<6} {a.overall_rating:<5} "
          f"{a.three_point:<5} {a.mid_range:<5} {a.free_throw:<5} "
          f"{a.passing:<5} {a.steal:<5} {a.block:<5} {reb}")


# ------------------------------------------------------------------
# 2. Best by skill category
# ------------------------------------------------------------------
section("BEST BY SKILL")
skills = [
    ("3PT Shooting",    PlayerAttributes.three_point),
    ("Free Throw",      PlayerAttributes.free_throw),
    ("Mid Range",       PlayerAttributes.mid_range),
    ("Passing",         PlayerAttributes.passing),
    ("Steal",           PlayerAttributes.steal),
    ("Block",           PlayerAttributes.block),
    ("Off Rebound",     PlayerAttributes.offensive_rebound),
    ("Def Rebound",     PlayerAttributes.defensive_rebound),
]
for label, col in skills:
    top = db.execute(
        select(Player.full_name, col)
        .join(PlayerAttributes, PlayerAttributes.player_id == Player.id)
        .where(PlayerAttributes.season == SEASON)
        .order_by(col.desc())
        .limit(5)
    ).all()
    names = ", ".join(f"{name} ({val})" for name, val in top)
    print(f"  {label:<16}: {names}")


# ------------------------------------------------------------------
# 3. Top players by position
# ------------------------------------------------------------------
section("TOP 10 PER POSITION")
for pos in ["G", "F", "C"]:
    rows = db.execute(
        select(Player, PlayerAttributes)
        .join(PlayerAttributes, PlayerAttributes.player_id == Player.id)
        .where(PlayerAttributes.season == SEASON)
        .where(Player.position.ilike(f"{pos}%"))
        .order_by(PlayerAttributes.overall_rating.desc())
        .limit(10)
    ).all()
    print(f"\n  {pos}:")
    for p, a in rows:
        print(f"    {p.full_name:<25} {p.position:<6} OVR={a.overall_rating}")


# ------------------------------------------------------------------
# 4. Rating distribution
# ------------------------------------------------------------------
section("OVERALL RATING DISTRIBUTION")
all_ratings = db.execute(
    select(PlayerAttributes.overall_rating).where(PlayerAttributes.season == SEASON)
).scalars().all()
buckets = [
    ("95+",   lambda r: r >= 95),
    ("90-94", lambda r: 90 <= r < 95),
    ("85-89", lambda r: 85 <= r < 90),
    ("80-84", lambda r: 80 <= r < 85),
    ("75-79", lambda r: 75 <= r < 80),
    ("70-74", lambda r: 70 <= r < 75),
    ("65-69", lambda r: 65 <= r < 70),
    ("<65",   lambda r: r < 65),
]
for label, fn in buckets:
    count = sum(1 for r in all_ratings if fn(r))
    bar = "#" * count
    print(f"  {label:<8} {count:>4}  {bar}")


# ------------------------------------------------------------------
# 5. Lookup a specific player
# ------------------------------------------------------------------
section("PLAYER LOOKUP")
lookups = ["Nikola Joki", "Stephen Curry", "Victor Wembanyama", "Luka Don", "LeBron James"]
for name in lookups:
    row = db.execute(
        select(Player, PlayerAttributes, PlayerTendencies)
        .join(PlayerAttributes, PlayerAttributes.player_id == Player.id)
        .join(PlayerTendencies, PlayerTendencies.player_id == Player.id)
        .where(Player.full_name.ilike(f"%{name}%"))
        .where(PlayerAttributes.season == SEASON)
    ).first()
    if not row:
        print(f"  {name}: not found")
        continue
    p, a, t = row
    print(f"\n  {p.full_name} ({p.position}) — OVR {a.overall_rating}")
    print(f"    Shooting : 3PT={a.three_point}  MID={a.mid_range}  FT={a.free_throw}")
    print(f"    Playmaking: PASS={a.passing}  BALL_HANDLE={a.ball_handle}")
    print(f"    Defense  : STL={a.steal}  BLK={a.block}  PERIM_DEF={a.perimeter_defense}")
    print(f"    Rebounding: OREB={a.offensive_rebound}  DREB={a.defensive_rebound}")
    print(f"    Tendencies: usage={t.usage_rate:.2f}  shot/36={t.shot_tendency:.1f}  3pt_rate={t.three_point_rate:.2f}  ast/36={t.assist_rate:.1f}")

db.close()
