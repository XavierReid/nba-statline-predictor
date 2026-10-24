"""Backfill contiguous seasons into an ISOLATED database with raw-payload capture.

    docker compose run --rm -e DATABASE_URL=postgresql+psycopg2://nba:nba@postgres:5432/nba_backfill \\
        api python -m validation.ratings.backfill [--seasons 2013-14 ... ]

Refuses to run against anything but the nba_backfill database. Re-ingests every season from
scratch (including those production already has) so provenance is complete and the overlap can be
diffed against production. Uses the existing ingestion jobs unchanged, minus the current-roster
refresh (time-varying state does not belong in a historical store). Also saves, raw only (no
tables yet), per-season bio/age data and draft history for the rating backtests.
"""
import argparse
import json
import os
import time

from app.config import settings

assert settings.database_url.endswith("/nba_backfill"), (
    f"refusing to run: DATABASE_URL must point at the isolated nba_backfill database, got {settings.database_url}")

from app.database import SessionLocal                       # noqa: E402  (after the safety assertion)
from app.ingestion import jobs, nba_client                  # noqa: E402
from validation.ratings import rawstore                     # noqa: E402

DEFAULT_SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(2013, 2026)]       # 2013-14 .. 2025-26
STATUS_PATH = os.path.join(rawstore.RAW_DIR, "backfill_status.json")


def _install_capture():
    for name in ("fetch_games_for_season", "fetch_season_stats", "fetch_team_season_stats",
                 "fetch_clutch_stats", "fetch_shot_locations", "fetch_defense_stats",
                 "fetch_player_game_logs"):
        setattr(nba_client, name, rawstore.captured(name, getattr(nba_client, name)))


def _fetch_bio(season: str):
    from nba_api.stats.endpoints import leaguedashplayerbiostats
    return leaguedashplayerbiostats.LeagueDashPlayerBioStats(
        season=season, season_type_all_star="Regular Season", headers=nba_client.CUSTOM_HEADERS,
        timeout=120).get_normalized_dict()["LeagueDashPlayerBioStats"]


def _fetch_draft(year: int):
    from nba_api.stats.endpoints import drafthistory
    return drafthistory.DraftHistory(season_year_nullable=str(year), headers=nba_client.CUSTOM_HEADERS,
                                     timeout=120).get_normalized_dict()["DraftHistory"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="*", default=DEFAULT_SEASONS)
    args = ap.parse_args()
    _install_capture()
    status = json.load(open(STATUS_PATH)) if os.path.exists(STATUS_PATH) else {}
    db = SessionLocal()
    try:
        jobs.ingest_teams(db); db.commit()
        for season in args.seasons:
            t0 = time.time()
            steps = [
                ("games", lambda: jobs.ingest_games_for_season(db, season)),
                ("season_stats", lambda: jobs.ingest_season_stats(db, season)),
                ("shot_defense", lambda: jobs.ingest_shot_defense(db, season)),
                ("team_season_stats", lambda: jobs.ingest_team_season_stats(db, season)),
                ("attributes", lambda: jobs.seed_player_attributes(db, season)),
                ("player_game_logs", lambda: jobs.ingest_player_game_logs(db, season)),
                ("bio_raw", lambda: len(rawstore.captured("bio_" + season, _fetch_bio)(season))),
                ("draft_raw", lambda: len(rawstore.captured("draft_" + season, _fetch_draft)(int(season[:4])))),
            ]
            res = {}
            for name, fn in steps:
                try:
                    res[name] = {"ok": True, "count": fn()}
                    db.commit()
                except Exception as e:
                    db.rollback()
                    res[name] = {"ok": False, "error": repr(e)[:200]}
                    print(f"  [{season}] {name} FAILED: {e!r}"[:220], flush=True)
            status[season] = res
            json.dump(status, open(STATUS_PATH, "w"), indent=1)
            print(f"[{season}] {time.time() - t0:.0f}s  " +
                  " ".join(f"{k}={'ok' if v['ok'] else 'FAIL'}({v.get('count')})" for k, v in res.items()), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
