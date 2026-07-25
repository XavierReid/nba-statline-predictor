"""Box score helpers — accumulate and snapshot per-player stat lines.

Event-sourced: consumers pass one typed event at a time to `apply_typed_event`,
or a full stream to `derive_box_score`. Both are pure — no RNG, no I/O.
"""
from typing import Iterable, List, Optional, Tuple

from app.services.possession_events import THREE_SHOT_TYPES


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


def apply_typed_event(box: dict, event: dict) -> Tuple[int, Optional[int]]:
    """Apply one typed event (SHOT / FOUL / FT / REB / TOV / STL / BLK / AST) to
    the box score in place.

    Returns (pts_scored, fouled_out_player_id or None). Same return contract as
    the legacy `apply_event` so the caller (simulate_game) can trigger rotation
    patching on a foul-out without other changes.
    """
    etype = event["type"]
    pid = event.get("player_id")
    fouled_out_pid: Optional[int] = None
    pts = 0

    if etype == "SHOT":
        if pid in box:
            is_three = event.get("shot_type") in THREE_SHOT_TYPES
            box[pid]["fga"] += 1
            if is_three:
                box[pid]["fg3a"] += 1
            if event.get("made"):
                box[pid]["fgm"] += 1
                if is_three:
                    box[pid]["fg3m"] += 1
                    box[pid]["pts"] += 3
                    pts = 3
                else:
                    box[pid]["pts"] += 2
                    pts = 2

    elif etype == "FT":
        if pid in box:
            box[pid]["fta"] += 1
            if event.get("made"):
                box[pid]["ftm"] += 1
                box[pid]["pts"] += 1
                pts = 1

    elif etype == "FOUL":
        if pid is not None and pid in box and not box[pid]["fouled_out"]:
            box[pid]["pf"] += 1
            if box[pid]["pf"] >= 6:
                box[pid]["fouled_out"] = True
                fouled_out_pid = pid

    elif etype == "REB":
        if pid in box:
            box[pid]["reb"] += 1

    elif etype == "TOV":
        if pid in box:
            box[pid]["tov"] += 1

    elif etype == "STL":
        if pid in box:
            box[pid]["stl"] += 1

    elif etype == "BLK":
        if pid in box:
            box[pid]["blk"] += 1

    elif etype == "AST":
        if pid in box:
            box[pid]["ast"] += 1

    return pts, fouled_out_pid


def derive_box_score(events: Iterable[dict], roster_ids: Iterable[int]) -> dict:
    """Fold a sequence of typed events into a full box_score dict.

    Pure function: no I/O, no state, no RNG. The box_score for any list of events
    is fully determined by that list; this backs the RFC's behavior-invariance
    invariant (same events -> same box).

    Note: `min` and `plus_minus` are simulation-wide accounting (rotation minutes,
    plus/minus tracking); this function initializes them to zero. simulate_game
    populates them independently.
    """
    box = {int(pid): empty_stats() for pid in roster_ids}
    for event in events:
        apply_typed_event(box, event)
    return box


def foul_outs_from_events(events: Iterable[dict], roster_ids: Iterable[int]) -> List[int]:
    """Return the ordered list of player_ids that fouled out over the event stream.

    Convenience for tests that want to verify the sequence of foul-outs without
    threading return values through a manual accumulator loop.
    """
    box = {int(pid): empty_stats() for pid in roster_ids}
    outs: List[int] = []
    for event in events:
        _, fouled_out_pid = apply_typed_event(box, event)
        if fouled_out_pid is not None:
            outs.append(fouled_out_pid)
    return outs
