"""Shared event enrichment logic for play-by-play endpoints.

Both the step-through events endpoint and the season sim game events
endpoint use flatten_and_enrich to produce the final event list.
"""
from typing import Optional
from app.services.game_simulator import describe_event


def build_name_map(home_players: list[dict], away_players: list[dict]) -> dict:
    return {p["id"]: p["name"] for p in home_players + away_players}


def flatten_and_enrich(
    chunk_events: list[list],
    home_player_ids: set,
    name_map: Optional[dict] = None,
) -> list[dict]:
    """Flatten chunk_events into a single list and add running scores + descriptions.

    chunk_events: list of per-chunk event lists (from simulate_game or session store)
    home_player_ids: set of home team player IDs — used to assign pts to home/away
    name_map: if provided, generates description strings for events that lack one

    Returns a flat list of enriched event dicts ordered by possession.
    """
    enriched = []
    home_score = away_score = 0
    possession = 0

    for chunk in chunk_events:
        for event in chunk:
            possession += 1
            pts = event.get("pts", 0)
            is_home = event.get("is_home", False)

            if is_home:
                home_score += pts
            else:
                away_score += pts

            out = {
                "possession": possession,
                "quarter": event.get("quarter"),
                "game_clock_seconds": event.get("game_clock_seconds"),
                "is_home": is_home,
                "pts": pts,
                "running_home_score": home_score,
                "running_away_score": away_score,
                "scorer": event.get("scorer"),
                "shot_type": event.get("shot_type"),
                "made": event.get("made"),
                "assisted_by": event.get("assisted_by"),
                "rebounded_by": event.get("rebounded_by"),
                "turnover_by": event.get("turnover_by"),
                "steal_by": event.get("steal_by"),
                "block_by": event.get("block_by"),
                "fouled_by": event.get("fouled_by"),
                "fta": event.get("fta", 0),
                "ftm": event.get("ftm", 0),
                "description": event.get("description"),
            }

            if out["description"] is None and name_map is not None:
                out["description"] = describe_event(event, name_map)

            enriched.append(out)

    return enriched
