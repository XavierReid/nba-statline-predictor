"""Translate a possession result dict into a sequence of granular typed events,
and describe individual typed events as human-readable strings.

Every simulation possession resolves via `resolve_possession` into a single result
dict. `possession_to_events` expands that dict into an ordered list of typed events
(SHOT / FOUL / FT / REB / TOV / STL / BLK / AST) per RFC.md "Event-Sourced PBP".

Pure function: no RNG, no I/O, no state. Behavior invariance is the whole point
(see feedback-refactor-behavior-invariance in memory) — same possession result
must produce the same event list, and downstream box-score derivation must
reproduce the pre-refactor box exactly.
"""
from typing import List, Optional

THREE_SHOT_TYPES = ("three", "corner_three", "above_break_three")

_SHOT_LABELS = {
    "three": "3-pointer",
    "corner_three": "corner 3-pointer",
    "above_break_three": "3-pointer",
    "mid": "mid-range jumper",
    "mid_range": "mid-range jumper",
    "close": "layup/close shot",
    "layup": "layup",
    "dunk": "dunk",
    "floater": "floater",
}

_FOUL_LABELS = {
    "shooting": "shooting foul",
    "non_shooting": "non-shooting foul",
    "offensive": "offensive foul",
}


def possession_to_events(
    result: dict,
    *,
    possession: int,
    quarter: int,
    game_clock_seconds: int,
    is_home: bool,
) -> List[dict]:
    """Expand one possession result dict into granular events.

    Order matches the canonical orderings in RFC.md "Event-Sourced PBP":
      Clean make:        SHOT(made) [+ AST]
      Clean miss:        SHOT(missed) [+ BLK] [+ REB]
      And-1:             SHOT(made) [+ AST] -> FOUL(shooting) -> FT [+ REB]
      Foul-drawn miss:   FOUL(shooting) -> FT x N [+ REB]   (no SHOT — PR #8)
      Bonus FTs:         FOUL(non_shooting) -> FT x N [+ REB]
      Non-shooting foul: FOUL(non_shooting)
      Turnover:          TOV [+ STL]
      Offensive foul:    TOV -> FOUL(offensive)

    A trailing REB on any FT trip is the live rebound on a missed LAST FT
    (see _credit_ft_rebound in possession.py). Always defensive (is_oreb=False)
    — no OREB-off-FT is modeled.
    """
    header = {
        "possession": possession,
        "quarter": quarter,
        "game_clock_seconds": game_clock_seconds,
        "is_home": is_home,
    }

    events: List[dict] = []

    # Pre-bonus non-shooting foul: possession CONTINUES (statistical possession
    # is unchanged; play restarts with an inbound). Emit the FOUL FIRST, then
    # accumulate whatever terminal outcome follows (TOV / bonus / shot). Under
    # the old representation this was a terminal on its own; under the new one
    # it's an inline event followed by the resumed outcome.
    if result.get("nonshooting_foul_by"):
        events.append(_event(
            "FOUL", result["nonshooting_foul_by"], header,
            foul_kind="non_shooting", fouled_on=result.get("nonshooting_foul_on"),
        ))

    # Turnover paths — a possession terminated by TOV never emits a SHOT.
    if result.get("turnover_by"):
        events.append(_event("TOV", result["turnover_by"], header))
        if result.get("steal_by"):
            # `stolen_from` mirrors AST/BLK's `shot_by`: gives the STL event enough
            # context to stand alone in the stealer's modal PBP as "X steals from Y".
            events.append(_event(
                "STL", result["steal_by"], header,
                stolen_from=result["turnover_by"],
            ))
        # Offensive foul: fouled_by == turnover_by (same player charged both)
        if result.get("fouled_by") is not None and result.get("fouled_by") == result.get("turnover_by"):
            events.append(_event(
                "FOUL", result["fouled_by"], header,
                foul_kind="offensive", fouled_on=None,
            ))
        return events

    # Bonus foul: non-shooting foul that awards FTs. No shot_type; scorer is the
    # fouled-ON player (that's how the possession result carries the FT scorer).
    if result.get("scorer") and not result.get("shot_type") and result.get("fta", 0) > 0:
        events.append(_event(
            "FOUL", result.get("fouled_by"), header,
            foul_kind="non_shooting", fouled_on=result["scorer"],
        ))
        events.extend(_ft_events(result, header))
        _append_ft_rebound(events, result, header)
        return events

    # Shot paths.
    if result.get("shot_type"):
        shooter = result["scorer"]
        made = bool(result.get("made"))
        fta = result.get("fta", 0)

        # Foul-drawn miss (post PR #8, no FGA): FOUL + FTs, NO SHOT event.
        if not made and fta > 0:
            events.append(_event(
                "FOUL", result.get("fouled_by"), header,
                foul_kind="shooting", fouled_on=shooter,
            ))
            events.extend(_ft_events(result, header))
            _append_ft_rebound(events, result, header)
            return events

        # Made shot or clean miss — emit SHOT.
        is_three = result["shot_type"] in THREE_SHOT_TYPES
        shot_pts = (3 if is_three else 2) if made else 0
        events.append(_event(
            "SHOT", shooter, header,
            shot_type=result["shot_type"],
            sub_type=result.get("sub_type"),
            made=made,
            pts=shot_pts,
        ))

        if made:
            if result.get("assisted_by"):
                # `shot_by` gives the AST event enough context to render "X assists
                # Y's mid-range jumper" — matters when the modal filters PBP to one
                # player (the assister) and the shot row isn't there to collate onto.
                events.append(_event(
                    "AST", result["assisted_by"], header,
                    shot_by=shooter, shot_type=result["shot_type"],
                ))
            # And-1: made shot + foul + 1 FT.
            if fta > 0:
                events.append(_event(
                    "FOUL", result.get("fouled_by"), header,
                    foul_kind="shooting", fouled_on=shooter,
                ))
                events.extend(_ft_events(result, header))
                _append_ft_rebound(events, result, header)
        else:
            # Clean miss: BLK (attribution) and REB (live rebound) if present.
            if result.get("block_by"):
                # Same reasoning as AST: shot_by lets BLK stand alone in the
                # blocker's modal PBP as "X blocks Y's layup".
                events.append(_event(
                    "BLK", result["block_by"], header,
                    shot_by=shooter, shot_type=result["shot_type"],
                ))
            if result.get("rebounded_by"):
                events.append(_event(
                    "REB", result["rebounded_by"], header,
                    is_oreb=bool(result.get("is_oreb")),
                ))

        return events

    # No terminal outcome — should not happen except in the isolated pre-bonus
    # foul test (result with only nonshooting_foul_by set). Return whatever
    # pre-bonus events we accumulated.
    return events


def _append_ft_rebound(events: List[dict], result: dict, header: dict) -> None:
    """Emit a REB event for the live rebound off a missed LAST FT, if present.

    `_credit_ft_rebound` in possession.py sets result["rebounded_by"] only when
    the last FT missed, and only ever credits a DEFENSIVE rebounder — no
    OREB-off-FT is modeled. So `is_oreb=False` always.
    """
    rebounded_by = result.get("rebounded_by")
    if rebounded_by is not None:
        events.append(_event("REB", rebounded_by, header, is_oreb=False))


def _event(event_type: str, player_id: Optional[int], header: dict, **fields) -> dict:
    """Build one event dict with the standard header + type + player_id + extras."""
    ev = dict(header)
    ev["type"] = event_type
    ev["player_id"] = player_id
    ev["pts"] = fields.pop("pts", 0)
    ev.update(fields)
    return ev


def _ft_events(result: dict, header: dict) -> List[dict]:
    """One FT event per attempt, tagged made/missed from result['ft_makes'].

    Falls back to a make-first order if ft_makes is missing (older synthesized
    results in tests) — the aggregate stays correct either way.
    """
    fta = result.get("fta", 0)
    ftm = result.get("ftm", 0)
    shooter = result.get("scorer")
    makes = result.get("ft_makes") or []
    if len(makes) != fta:
        # Fallback: reconstruct a plausible ordered list. This path is used only by
        # unit tests that synthesize results without going through _shoot_free_throws.
        makes = [i < ftm for i in range(fta)]
    return [
        _event(
            "FT", shooter, header,
            attempt=i + 1, of=fta, made=makes[i],
            pts=1 if makes[i] else 0,
        )
        for i in range(fta)
    ]


# ---------------------------------------------------------------------------
# Description dispatch (RFC.md "Event-Sourced PBP")
# ---------------------------------------------------------------------------


def describe_typed_event(event: dict, name_map: dict) -> str:
    """Human-readable description of a single typed event.

    Server produces one string per event. Display collation (e.g. rendering an
    AST inline on its parent SHOT row) is a frontend concern; each event
    describes itself in isolation.
    """
    handler = _DESCRIBE.get(event["type"])
    if handler is None:
        return f"[unknown event type: {event['type']}]"
    return handler(event, name_map)


def _name(name_map: dict, pid: Optional[int]) -> str:
    if pid is None:
        return "Unknown"
    return name_map.get(pid, f"Player {pid}")


def _describe_shot(ev: dict, name_map: dict) -> str:
    shooter = _name(name_map, ev.get("player_id"))
    label = _SHOT_LABELS.get(ev.get("shot_type"), "shot")
    verb = "hits" if ev.get("made") else "misses"
    return f"{shooter} {verb} a {label}"


def _describe_foul(ev: dict, name_map: dict) -> str:
    fouler = _name(name_map, ev.get("player_id"))
    if ev.get("foul_kind") == "offensive":
        return f"{fouler} commits an offensive foul"
    # `intentional` flags a late-game strategic foul (see game_simulator strategic-
    # foul path); rendered as "intentional foul" rather than the generic non-shooting.
    kind_label = "intentional foul" if ev.get("intentional") else _FOUL_LABELS.get(ev.get("foul_kind"), "foul")
    fouled_on = ev.get("fouled_on")
    if fouled_on is None:
        return f"{fouler} commits a {kind_label}"
    return f"{fouler} commits a{'n' if kind_label[0] in 'aeiou' else ''} {kind_label} on {_name(name_map, fouled_on)}"


def _describe_ft(ev: dict, name_map: dict) -> str:
    shooter = _name(name_map, ev.get("player_id"))
    attempt, of, made = ev.get("attempt"), ev.get("of"), ev.get("made")
    verb = "makes" if made else "misses"
    return f"{shooter} {verb} free throw {attempt} of {of}"


def _describe_reb(ev: dict, name_map: dict) -> str:
    player = _name(name_map, ev.get("player_id"))
    kind = "offensive rebound" if ev.get("is_oreb") else "defensive rebound"
    return f"{player} {kind}"


def _describe_tov(ev: dict, name_map: dict) -> str:
    return f"{_name(name_map, ev.get('player_id'))} turnover"


def _describe_stl(ev: dict, name_map: dict) -> str:
    stealer = _name(name_map, ev.get("player_id"))
    stolen_from = ev.get("stolen_from")
    if stolen_from is None:
        return f"{stealer} steal"
    return f"{stealer} steals from {_name(name_map, stolen_from)}"


def _describe_blk(ev: dict, name_map: dict) -> str:
    blocker = _name(name_map, ev.get("player_id"))
    shot_by = ev.get("shot_by")
    if shot_by is None:
        return f"{blocker} blocks the shot"
    label = _SHOT_LABELS.get(ev.get("shot_type"), "shot")
    return f"{blocker} blocks {_name(name_map, shot_by)}'s {label}"


def _describe_ast(ev: dict, name_map: dict) -> str:
    assister = _name(name_map, ev.get("player_id"))
    shot_by = ev.get("shot_by")
    if shot_by is None:
        return f"{assister} assist"
    label = _SHOT_LABELS.get(ev.get("shot_type"), "shot")
    return f"{assister} assists {_name(name_map, shot_by)}'s {label}"


def _describe_sub(ev: dict, name_map: dict) -> str:
    # Initial-lineup emit has player_out=None; render as a starter announcement
    # rather than a sub (there is no one being replaced at game start).
    player_in = _name(name_map, ev.get("player_in"))
    player_out = ev.get("player_out")
    if player_out is None:
        return f"{player_in} starts"
    return f"{player_in} enters for {_name(name_map, player_out)}"


_DESCRIBE = {
    "SHOT": _describe_shot,
    "FOUL": _describe_foul,
    "FT":   _describe_ft,
    "REB":  _describe_reb,
    "TOV":  _describe_tov,
    "STL":  _describe_stl,
    "BLK":  _describe_blk,
    "AST":  _describe_ast,
    "SUBSTITUTION": _describe_sub,
}
