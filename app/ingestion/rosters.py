"""Pull current NBA rosters into a roster snapshot (Step 3a). Writes only the new tables.

    python -m app.ingestion.rosters 2026-27            # fetch, validate, write an UNSEALED snapshot
    python -m app.ingestion.rosters 2026-27 --dry-run  # fetch and validate only, write nothing
    python -m app.ingestion.rosters 2026-27 --seal     # seal if validation found no problems

The snapshot is sealed only if validation is clean; conflicts are reported, never auto-resolved.
"""
import argparse
import gzip
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from datetime import date, datetime

from nba_api.stats.endpoints import commonallplayers, commonteamroster
from nba_api.stats.static import teams as static_teams
from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal
from app.ingestion.nba_client import CUSTOM_HEADERS, _RATE_LIMIT_DELAY
from app.models.roster_snapshot import DataFetch, PlayerBio, RosterAssignment, RosterSnapshot

RAW_DIR = os.environ.get("RAW_DIR", "data/raw")
MAX_STANDARD, MAX_TWO_WAY = 15, 3


def _store(endpoint: str, payload) -> tuple:
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    sha = hashlib.sha256(blob).hexdigest()
    path = os.path.join(RAW_DIR, endpoint, sha[:2], sha + ".json.gz")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with gzip.open(path, "wb") as f:
            f.write(blob)
    return sha, path


def _height_in(h):
    try:
        ft, inch = h.split("-")
        return float(int(ft) * 12 + int(inch))
    except (AttributeError, ValueError):
        return None


def _status(row) -> str:
    # SUPPLEMENTAL_STATUS is a 0/1 flag; 90 players carry 1 (3 per team in the 2026-27 pull),
    # consistent with the two-way slots. Exhibit-10 deals are not distinguished by this feed.
    return "two_way" if row.get("SUPPLEMENTAL_STATUS") in (1, "1") else "standard"


def fetch(season: str):
    rosters, raw = [], {}
    for t in static_teams.get_teams():
        rows = commonteamroster.CommonTeamRoster(
            team_id=t["id"], season=season, headers=CUSTOM_HEADERS, timeout=60
        ).get_normalized_dict()["CommonTeamRoster"]
        raw[t["id"]] = rows
        rosters.extend(rows)
        time.sleep(_RATE_LIMIT_DELAY)
    index = commonallplayers.CommonAllPlayers(
        is_only_current_season=1, league_id="00", season=season, headers=CUSTOM_HEADERS, timeout=60
    ).get_normalized_dict()["CommonAllPlayers"]
    return rosters, raw, index


def validate(rosters, index) -> list:
    problems = []
    teams = {r["TeamID"] for r in rosters}
    if len(teams) != 30:
        problems.append(f"expected 30 teams, got {len(teams)}")
    by_player = defaultdict(set)
    per_team = defaultdict(Counter)
    for r in rosters:
        by_player[r["PLAYER_ID"]].add(r["TeamID"])
        per_team[r["TeamID"]][_status(r)] += 1
    problems += [f"player {p} on {len(t)} teams" for p, t in by_player.items() if len(t) > 1]
    for tid, c in per_team.items():
        if c["standard"] > MAX_STANDARD or c["two_way"] > MAX_TWO_WAY:
            problems.append(f"team {tid} over limits: {dict(c)}")
    for r in rosters:
        if not r.get("BIRTH_DATE") or not r.get("HEIGHT") or not r.get("WEIGHT"):
            problems.append(f"player {r['PLAYER_ID']} missing bio fields")
    idx = {p["PERSON_ID"]: p["TEAM_ID"] for p in index if p["TEAM_ID"]}
    for pid, tids in by_player.items():
        if pid in idx and idx[pid] not in tids:
            problems.append(f"feeds disagree on player {pid}: roster {sorted(tids)} vs index {idx[pid]}")
    problems += [f"player {p} on a team in index but absent from rosters" for p in idx if p not in by_player]
    return problems


def _first_season(season: str, exp) -> str:
    y = int(season[:4]) - (0 if str(exp).upper() in ("R", "0") else int(exp))
    return f"{y}-{str(y + 1)[2:]}"


def write(season: str, rosters, raw, index, seal: bool) -> int:
    fetched_at = datetime.utcnow()
    db = SessionLocal()
    try:
        fetch_ids = []
        for endpoint, payload in (("commonteamroster", raw), ("commonallplayers", index)):
            sha, path = _store(endpoint, payload)
            f = DataFetch(endpoint=endpoint, params={"season": season}, payload_sha256=sha,
                          row_count=len(rosters) if endpoint == "commonteamroster" else len(index),
                          store_path=path, status="ok")
            db.add(f)
            db.flush()
            fetch_ids.append(f.id)
        lines = sorted(f"{r['PLAYER_ID']}|{r['TeamID']}|{_status(r)}|{r.get('POSITION')}" for r in rosters)
        snap = RosterSnapshot(season=season, kind="live_pull", as_of_date=date.today(),
                              content_sha256=hashlib.sha256("\n".join(lines).encode()).hexdigest(),
                              source_fetch_ids=fetch_ids)
        db.add(snap)
        db.flush()
        for r in rosters:
            db.add(RosterAssignment(snapshot_id=snap.id, player_id=r["PLAYER_ID"], team_id=r["TeamID"],
                                    status=_status(r), position=r.get("POSITION"), jersey=r.get("NUM")))
            bio = dict(
                player_id=r["PLAYER_ID"], birth_date=datetime.strptime(r["BIRTH_DATE"].title(), "%b %d, %Y").date()
                if r.get("BIRTH_DATE") else None,
                height_in=_height_in(r.get("HEIGHT")),
                weight_lb=int(r["WEIGHT"]) if r.get("WEIGHT") else None,
                school=r.get("SCHOOL"), first_nba_season=_first_season(season, r.get("EXP") or 0),
                source_fetch_id=fetch_ids[0])
            db.execute(insert(PlayerBio).values(**bio).on_conflict_do_update(
                index_elements=["player_id"], set_={k: v for k, v in bio.items() if k != "player_id"}))
        if seal:
            snap.sealed_at = datetime.utcnow()
        db.commit()
        return snap.id
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("season")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seal", action="store_true")
    a = ap.parse_args()
    rosters, raw, index = fetch(a.season)
    problems = validate(rosters, index)
    print(f"{len(rosters)} players, {len(problems)} problems")
    for p in problems[:40]:
        print("  -", p)
    if a.dry_run:
        return
    if a.seal and problems:
        print("not sealing: validation found problems")
    sid = write(a.season, rosters, raw, index, seal=a.seal and not problems)
    print(f"snapshot {sid} written{' and sealed' if a.seal and not problems else ' (unsealed)'}")


if __name__ == "__main__":
    main()
