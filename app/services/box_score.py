"""Box score helpers — accumulate and snapshot per-player stat lines.

Two accumulator styles coexist during the event-sourced PBP refactor (RFC.md
"Event-Sourced PBP"):

- `apply_event` (legacy): consumes a monolithic possession result dict.
  Being deleted once simulate_game is wired to the event stream.
- `apply_typed_event` + `derive_box_score`: consume the granular typed events
  from `possession_to_events`. Drop-in replacement for the legacy path.
"""
from typing import Iterable, List, Optional, Tuple


_THREE_SHOT_TYPES = ("three", "corner_three", "above_break_three")


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
                # A miss that draws a shooting foul is not a FGA in real NBA — the attempt is
                # negated and the shooter goes to the line. And-1 (made + fta) still counts.
                counts_as_attempt = event["made"] or event["fta"] == 0
                if shot_type in ("three", "corner_three", "above_break_three"):
                    if counts_as_attempt:
                        box[pid]["fg3a"] += 1
                        box[pid]["fga"] += 1
                    if event["made"]:
                        box[pid]["fg3m"] += 1
                        box[pid]["fgm"] += 1
                        box[pid]["pts"] += 3
                        pts = 3
                else:
                    if counts_as_attempt:
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

    # both the shooting/bonus fouler and a pre-bonus non-shooting fouler count a personal foul
    fouled_out_pid = None
    for fouled_pid in (event.get("fouled_by"), event.get("nonshooting_foul_by")):
        if fouled_pid and fouled_pid in box and not box[fouled_pid]["fouled_out"]:
            box[fouled_pid]["pf"] += 1
            if box[fouled_pid]["pf"] >= 6:
                box[fouled_pid]["fouled_out"] = True
                fouled_out_pid = fouled_pid

    event["pts"] = pts
    return pts, fouled_out_pid


# ---------------------------------------------------------------------------
# Event-sourced accumulation (RFC.md "Event-Sourced PBP")
# ---------------------------------------------------------------------------


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
            is_three = event.get("shot_type") in _THREE_SHOT_TYPES
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
