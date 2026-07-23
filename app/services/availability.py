"""Per-game availability — who is active tonight (gap 3.4).

Real teams play ~10.6 of a ~13-14 man roster each game (measured from PlayerGameLog:
active count distributed 9-13; appearance rate ~= games_played/82, and the WHOLE
rotation turns over, not just the deep bench). On a regular's off-night his minutes
flow to the deeper roster — which is why the fixed-10 model can't reproduce real MPG.

This is an ELIGIBILITY layer, deliberately separate from the rotation engine (which
decides how the active players are USED): the reverted 15-player experiment showed
representation and rotation policy are different systems. It returns NEW player dicts
so the caller's shared roster is never mutated across games.
"""
import random
from typing import List, Optional

SEASON_GAMES = 82.0
_ACTIVE_TARGET_CACHE: dict = {}


def season_active_target(db, season: str) -> Optional[float]:
    """Real mean active players/team-game from PlayerGameLog (min>0). None if the
    season's game logs aren't ingested. Cached per season."""
    if season in _ACTIVE_TARGET_CACHE:
        return _ACTIVE_TARGET_CACHE[season]
    from sqlalchemy import select, func
    from app.models.player_game_log import PlayerGameLog
    rows = db.execute(
        select(PlayerGameLog.game_id, PlayerGameLog.team_id)
        .where(PlayerGameLog.season == season, PlayerGameLog.minutes > 0)
    ).all()
    if not rows:
        _ACTIVE_TARGET_CACHE[season] = None
        return None
    per_tg: dict = {}
    for gid, tid in rows:
        per_tg[(gid, tid)] = per_tg.get((gid, tid), 0) + 1
    target = sum(per_tg.values()) / len(per_tg)
    _ACTIVE_TARGET_CACHE[season] = target
    return target


def calibrate_avail_prob(players: List[dict], target: Optional[float]) -> None:
    """Set each player's per-game availability prob. Base = games_played/82 (measured
    appearance rate). If a real active-count TARGET is known (game logs ingested), solve
    a per-season scalar k so Σ min(1, k·gp/82) over the LOADED roster = target — this
    absorbs the deep-bench minutes the top-N loading can't hold (modern GP runs low, so
    the loaded rotation is active slightly more to reach the real active count). Mutates
    players in place with p['avail_prob']."""
    def base(p):
        return min(1.0, (p.get("games_played") or 0) / SEASON_GAMES)
    if target is None or not players:
        for p in players:
            p["avail_prob"] = base(p)
        return
    lo, hi = 0.1, 6.0
    for _ in range(40):
        k = (lo + hi) / 2
        e = sum(min(1.0, k * (p.get("games_played") or 0) / SEASON_GAMES) for p in players)
        if e < target:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    for p in players:
        p["avail_prob"] = min(1.0, k * (p.get("games_played") or 0) / SEASON_GAMES)


def select_active_roster(players: List[dict], rng: random.Random, cfg) -> List[dict]:
    if not players:
        return players
    min_active = getattr(cfg, "availability_min_active", 8)
    # availability draw: p['avail_prob'] (calibrated to the season's real active count if game
    # logs exist, else games_played/82). Set at roster load.
    active_ids = {p["id"] for p in players
                  if rng.random() < p.get("avail_prob",
                                          min(1.0, (p.get("games_played") or 0) / SEASON_GAMES))}
    active = [p for p in players if p["id"] in active_ids]
    # floor: a team never dresses fewer than ~min_active; fill with highest-MPG inactives
    # (guarantees >=5 to field a lineup).
    if len(active) < min_active:
        inactive = sorted((p for p in players if p["id"] not in active_ids),
                          key=lambda p: -(p.get("mpg") or 0.0))
        active = active + inactive[:min_active - len(active)]
    # Allocate 240 among ACTIVE players. Each starts at their real MPG; the short-handed
    # SURPLUS (240 - Σmpg, positive when a light lineup is active) is filled toward a soft
    # cap so LOW-MPG players absorb it — real coaches cap the star (~cap) and give the extra
    # minutes to the bench, rather than scaling everyone up proportionally (which over-serves
    # the highest-MPG player). A deep active lineup (Σmpg > 240) scales down proportionally.
    out = [dict(p) for p in active]
    s = sum((p.get("mpg") or 0.0) for p in out) or 1.0
    if s >= 240.0:
        for p in out:
            p["minutes"] = round((p.get("mpg") or 0.0) / s * 240, 1)
    else:
        cap = getattr(cfg, "availability_minutes_cap", 40.0)
        surplus = 240.0 - s
        weights = [max(0.0, cap - (p.get("mpg") or 0.0)) for p in out]
        wtot = sum(weights) or 1.0
        for p, w in zip(out, weights):
            p["minutes"] = round((p.get("mpg") or 0.0) + surplus * w / wtot, 1)
    out.sort(key=lambda p: -p["minutes"])
    for i, p in enumerate(out):
        p["is_starter"] = i < 5
    return out
