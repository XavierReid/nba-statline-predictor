"""Translate a possession result dict into a sequence of granular typed events.

Every simulation possession resolves via `resolve_possession` into a single result
dict. `possession_to_events` expands that dict into an ordered list of typed events
(SHOT / FOUL / FT / REB / TOV / STL / BLK / AST) per RFC.md "Event-Sourced PBP".

Pure function: no RNG, no I/O, no state. Behavior invariance is the whole point
(see feedback-refactor-behavior-invariance in memory) — same possession result
must produce the same event list, and downstream box-score derivation must
reproduce the pre-refactor box exactly.
"""
from typing import List, Optional


_THREE_SHOT_TYPES = ("three", "corner_three", "above_break_three")


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

    # Turnover paths first — a possession terminated by TOV never emits a SHOT.
    if result.get("turnover_by"):
        events: List[dict] = [_event("TOV", result["turnover_by"], header)]
        if result.get("steal_by"):
            events.append(_event("STL", result["steal_by"], header))
        # Offensive foul: fouled_by == turnover_by (same player charged both)
        if result.get("fouled_by") is not None and result.get("fouled_by") == result.get("turnover_by"):
            events.append(_event(
                "FOUL", result["fouled_by"], header,
                foul_kind="offensive", fouled_on=None,
            ))
        return events

    # Non-shooting foul (pre-bonus): terminal, no FTs. Distinct field on result.
    if result.get("nonshooting_foul_by"):
        return [_event(
            "FOUL", result["nonshooting_foul_by"], header,
            foul_kind="non_shooting", fouled_on=result.get("nonshooting_foul_on"),
        )]

    # Bonus foul: non-shooting foul that awards FTs. No shot_type; scorer is the
    # fouled-ON player (that's how the possession result carries the FT scorer).
    if result.get("scorer") and not result.get("shot_type") and result.get("fta", 0) > 0:
        events = [_event(
            "FOUL", result.get("fouled_by"), header,
            foul_kind="non_shooting", fouled_on=result["scorer"],
        )]
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
            events = [_event(
                "FOUL", result.get("fouled_by"), header,
                foul_kind="shooting", fouled_on=shooter,
            )]
            events.extend(_ft_events(result, header))
            _append_ft_rebound(events, result, header)
            return events

        # Made shot or clean miss — emit SHOT.
        is_three = result["shot_type"] in _THREE_SHOT_TYPES
        shot_pts = (3 if is_three else 2) if made else 0
        events = [_event(
            "SHOT", shooter, header,
            shot_type=result["shot_type"],
            sub_type=result.get("sub_type"),
            made=made,
            pts=shot_pts,
        )]

        if made:
            if result.get("assisted_by"):
                events.append(_event("AST", result["assisted_by"], header))
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
                events.append(_event("BLK", result["block_by"], header))
            if result.get("rebounded_by"):
                events.append(_event(
                    "REB", result["rebounded_by"], header,
                    is_oreb=bool(result.get("is_oreb")),
                ))

        return events

    # Defensive: no known outcome. Should not happen in practice — resolve_possession
    # always returns one of the shapes above — but returning [] keeps this pure.
    return []


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
