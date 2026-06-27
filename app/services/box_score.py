"""Box score helpers — accumulate and snapshot per-player stat lines."""
from typing import Optional, Tuple


def empty_stats() -> dict:
    return {
        "pts": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0,
        "tov": 0, "pf": 0, "fgm": 0, "fga": 0,
        "fg3m": 0, "fg3a": 0, "ftm": 0, "fta": 0,
        "min": 0.0, "fouled_out": False, "plus_minus": 0,
    }


def snapshot_box(box: dict) -> dict:
    """Shallow-copy a box score dict. Safe because all values are primitives."""
    return {pid: dict(stats) for pid, stats in box.items()}


def apply_event(box: dict, event: dict) -> Tuple[int, Optional[int]]:
    """Apply one possession event to the box score in place.

    Returns (pts_scored, fouled_out_player_id or None). Rotation patching for
    foul-outs is left to the caller since it requires simulation state.
    """
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
            shot_type = event.get("shot_type")
            if shot_type:  # bonus fouls have no shot attempt — skip FGA
                if shot_type == "three":
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

    fouled_out_pid = None
    fouled_pid = event.get("fouled_by")
    if fouled_pid and fouled_pid in box and not box[fouled_pid]["fouled_out"]:
        box[fouled_pid]["pf"] += 1
        if box[fouled_pid]["pf"] >= 6:
            box[fouled_pid]["fouled_out"] = True
            fouled_out_pid = fouled_pid

    event["pts"] = pts
    return pts, fouled_out_pid
