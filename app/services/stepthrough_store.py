"""In-memory session store for step-through game simulations.

Sessions are keyed by UUID token and expire after TTL seconds.
Cleanup is lazy — expired sessions are evicted on each access.
"""
import time
import uuid
from typing import Optional

_sessions: dict = {}
TTL = 3600  # 1 hour


def _cleanup_expired() -> None:
    now = time.time()
    expired = [t for t, s in _sessions.items() if now - s["created_at"] > TTL]
    for t in expired:
        del _sessions[t]


def create_session(
    chunks: list,
    chunk_events: list,
    home_players: list,
    away_players: list,
    home_team: str,
    away_team: str,
    season: str,
    seed: int,
) -> str:
    _cleanup_expired()
    token = str(uuid.uuid4())
    _sessions[token] = {
        "chunks": chunks,
        "chunk_events": chunk_events,
        "cursor": 0,
        "created_at": time.time(),
        "home_players": home_players,
        "away_players": away_players,
        "home_team": home_team,
        "away_team": away_team,
        "season": season,
        "seed": seed,
    }
    return token


def pop_next_chunk(token: str) -> Optional[dict]:
    """Advance the session cursor and return the next chunk.

    Returns None if the token is unknown or expired.
    Evicts the session when the final chunk is consumed.
    """
    _cleanup_expired()
    session = _sessions.get(token)
    if not session:
        return None

    cursor = session["cursor"]
    total = len(session["chunks"])

    if cursor >= total:
        return None

    # Capture everything before possible eviction
    chunk = session["chunks"][cursor]
    events = session["chunk_events"][cursor] if session["chunk_events"] else []
    home_players = session["home_players"]
    away_players = session["away_players"]
    home_team = session["home_team"]
    away_team = session["away_team"]
    season = session["season"]
    seed = session["seed"]

    session["cursor"] += 1
    complete = session["cursor"] >= total

    if complete:
        del _sessions[token]

    return {
        "chunk": chunk,
        "events": events,
        "step": cursor + 1,
        "total_steps": total,
        "complete": complete,
        "home_players": home_players,
        "away_players": away_players,
        "home_team": home_team,
        "away_team": away_team,
        "season": season,
        "seed": seed,
    }
