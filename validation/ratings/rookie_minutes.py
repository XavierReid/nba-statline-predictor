"""M3: give first-year / no-prior-season players an expected minutes line from their draft bucket.

Reader-only (no database imports): `rookie_defaults` sees only what is knowable before the season (the early
roster proxy's identity pairs, the prior season's table, the draft slot). The bucket means (`priors`) are
fitted elsewhere on earlier development seasons. The profile of these players stays at the engine defaults;
only presence, team and minutes are supplied, and they are labelled as guesses.
"""
from typing import Dict, Optional

from validation.ratings.asof import AsOfReader, prev_season, season_start_year

BUCKETS = ("R-A", "R-B", "R-C", "R-D")


def bucket_of(fact: Optional[dict], target_year: int) -> str:
    """R-A picks 1-14, R-B 15-30, R-C second round, R-D undrafted / drafted earlier / unknown."""
    if not fact or fact.get("draft_year") != target_year:
        return "R-D"
    try:
        pick = int(fact["pick"])
    except (TypeError, ValueError):
        return "R-D"
    return "R-A" if pick <= 14 else "R-B" if pick <= 30 else "R-C"


def unseen_players(reader: AsOfReader, have) -> Dict[int, dict]:
    """Players on the early roster proxy who are not in `have` (those V1 can already project)."""
    ty = season_start_year(reader.target)
    facts = reader.draft_facts()
    return {pid: {"team_id": team, "bucket": bucket_of(facts.get(pid), ty)}
            for pid, team in reader.preseason_roster_proxy("early", 10).items() if pid not in have}


def rookie_defaults(reader: AsOfReader, have, priors: Dict[str, dict]) -> Dict[int, dict]:
    out = {}
    for pid, u in unseen_players(reader, have).items():
        p = priors.get(u["bucket"]) or priors["ALL"]
        out[pid] = {"team_id": u["team_id"], "mpg": p["mpg"], "gp": int(round(p["gp"])), "bucket": u["bucket"], "rookie": True}
    return out
