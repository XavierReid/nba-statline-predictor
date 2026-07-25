"""Shared event enrichment logic for play-by-play endpoints.

Both the step-through events endpoint and the season sim game events endpoint
use flatten_and_enrich to walk the typed event stream produced by simulate_game
and add running scores + descriptions (RFC.md "Event-Sourced PBP").
"""
from typing import Optional

from app.services.possession_events import describe_typed_event


def build_name_map(home_players: list[dict], away_players: list[dict]) -> dict:
    return {p["id"]: p["name"] for p in home_players + away_players}


def flatten_and_enrich(
    chunk_events: list[list],
    home_player_ids: set,
    name_map: Optional[dict] = None,
) -> list[dict]:
    """Flatten chunk_events into a single list and add running scores + descriptions.

    chunk_events: list of per-chunk typed event lists (from simulate_game or
      session store). Each event has type/possession/quarter/game_clock_seconds/
      is_home/player_id/pts plus type-specific fields.
    home_player_ids: unused now that pts is authoritative per event; retained for
      API compatibility with legacy callers.
    name_map: if provided, generates description strings for events that lack one.

    Returns a flat list of enriched event dicts in event-stream order.
    """
    del home_player_ids  # signature compat; is_home + pts on the event are authoritative
    enriched: list = []
    home_score = away_score = 0

    for chunk in chunk_events:
        for event in chunk:
            pts = event.get("pts", 0)
            is_home = event.get("is_home", False)
            if is_home:
                home_score += pts
            else:
                away_score += pts

            out = dict(event)
            out["running_home_score"] = home_score
            out["running_away_score"] = away_score
            if out.get("description") is None and name_map is not None:
                out["description"] = describe_typed_event(event, name_map)
            enriched.append(out)

    return enriched
