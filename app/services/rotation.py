"""Rotation schedule — build and patch per-minute lineup assignments."""
import random

GAME_MINUTES = 48
SUB_VARIANCE = 2.0  # σ in minutes for substitution timing (Normal dist)


def _in_foul_trouble(pf: int, minute: int) -> bool:
    """Coach heuristic: sit a player who is ONE foul from fouling out (5 fouls) until Q4,
    so early foul-outs move to Q4 without benching 3-4-foul stars. Q4/OT play through
    (finish with your best). Modern-lenient: only the about-to-foul-out sit, so a player
    who catches an early cluster keeps playing until genuinely at risk."""
    q = minute // 12   # 0-based quarter; >= 3 is Q4/OT (play through)
    return q < 3 and pf >= 5


def build_rotation(players: list[dict], rng: random.Random) -> list[list[int]]:
    """Build a 48-slot minute schedule with 5 player IDs per slot.

    Substitution timing is sampled from a Normal distribution so rotations
    vary between games even for the same team.
    """
    slots: list[set] = [set() for _ in range(GAME_MINUTES)]

    def assign_minutes(player: dict, target_min: float, preferred_start: int) -> None:
        remaining = int(target_min)
        minute = max(0, min(47, int(rng.gauss(preferred_start, SUB_VARIANCE))))
        visited: set = set()
        while remaining > 0 and len(visited) < GAME_MINUTES:
            idx = minute % GAME_MINUTES
            if len(slots[idx]) < 5 and idx not in visited:
                slots[idx].add(player["id"])
                remaining -= 1
            visited.add(idx)
            minute = (minute + 1) % GAME_MINUTES

    starters = [p for p in players if p["is_starter"]]
    bench = [p for p in players if not p["is_starter"]]

    for p in starters:
        assign_minutes(p, p["minutes"], preferred_start=0)
    for p in bench:
        assign_minutes(p, p["minutes"], preferred_start=12)

    # Backfill short slots with the player furthest BELOW their target minutes, not the
    # highest-mpg player: the old "grab the top name" rule piled every gap onto the star
    # (34 mpg -> 46 scheduled) while mid-rotation players (whose assignment got squeezed out
    # by full slots) went under-served. Deficit-based fill keeps everyone near their mpg.
    count: dict = {}
    for slot in slots:
        for pid in slot:
            count[pid] = count.get(pid, 0) + 1
    for slot in slots:
        while len(slot) < 5:
            cand = min(
                (p for p in players if p["id"] not in slot),
                key=lambda p: count.get(p["id"], 0) - p["minutes"],
                default=None,
            )
            if cand is None:
                break
            slot.add(cand["id"])
            count[cand["id"]] = count.get(cand["id"], 0) + 1

    return [list(s) for s in slots]


def build_rotation_interval(players: list[dict], rng: random.Random) -> list[list[int]]:
    """5th rotation-scheduler attempt (2026-09-29): shift-based instead of
    block-fill. Fixes the Q2 substitution deadzone (`build_rotation`'s
    contiguous greedy fill lets starters lock up all of Q1+Q2 before bench
    finds an open slot — confirmed 2026-09-24, Q2 gets 6.8x fewer subs than
    Q1). Four prior scheduler rewrites were reverted for regressing star MPG,
    standings correlation, or an accounting invariant; this one is gated
    behind `SimConfig.use_interval_rotation` (default False) pending the
    same validation panel those attempts failed.

    Starters get one shift per quarter (so every quarter sees fresh starter
    minutes, not a single contiguous block). Bench gets 1-3 shifts anchored
    at the windows real coaches typically use (mid-Q1, mid-Q3, late-Q4).
    Deficit-based backfill (same as `build_rotation`) reconciles any slot
    left under 5 after the shift placement.
    """
    slots: list[set] = [set() for _ in range(GAME_MINUTES)]

    def try_place_shift(pid: int, start: int, length: int) -> None:
        for m in range(start, min(GAME_MINUTES, start + length)):
            if len(slots[m]) < 5 and pid not in slots[m]:
                slots[m].add(pid)

    starters = [p for p in players if p["is_starter"]]
    bench = [p for p in players if not p["is_starter"]]

    for p in starters:
        target = int(round(p["minutes"]))
        shift_len = max(3, target // 4)
        for q in range(4):
            q_start = q * 12
            max_offset = max(0, 12 - shift_len)
            if q == 0:
                # The announced starting five tips off together at minute 0 —
                # no randomized offset. A random Q1 offset let the deficit
                # backfill occasionally seat a non-starter in the opening
                # minute (a starter's shift hadn't started yet), which
                # test_lineup_reconstruction correctly flags as an invalid
                # 6-player "initial lineup" (caught 2026-09-29 promoting the
                # interval scheduler). Later-quarter shifts still vary.
                offset = 0
            else:
                offset = max(0, min(max_offset, int(rng.gauss(1, 1.5))))
            try_place_shift(p["id"], q_start + offset, shift_len)

    for p in bench:
        target = int(round(p["minutes"]))
        if target < 3:
            continue
        n_shifts = 3 if target >= 15 else (2 if target >= 8 else 1)
        shift_len = max(3, target // n_shifts)
        anchors = [7, 30, 40] if n_shifts == 3 else ([7, 30] if n_shifts == 2 else [30])
        for anchor in anchors:
            offset = int(rng.gauss(0, 2))
            actual_start = max(0, min(GAME_MINUTES - shift_len, anchor + offset))
            try_place_shift(p["id"], actual_start, shift_len)

    # Same deficit-based backfill as build_rotation: give any under-5 slot to
    # whoever is furthest below their target minutes.
    count: dict = {}
    for slot in slots:
        for pid in slot:
            count[pid] = count.get(pid, 0) + 1
    for slot in slots:
        while len(slot) < 5:
            cand = min(
                (p for p in players if p["id"] not in slot),
                key=lambda p: count.get(p["id"], 0) - p["minutes"],
                default=None,
            )
            if cand is None:
                break
            slot.add(cand["id"])
            count[cand["id"]] = count.get(cand["id"], 0) + 1

    return [list(s) for s in slots]


# Rotation modes — the resolver picks a mode from game state, and the lineup is
# the output of that mode. Future behaviors (foul-trouble subs, injury overrides)
# become additional modes, not special cases in the game loop.
MODE_SCHEDULED = "scheduled"
MODE_GARBAGE = "garbage"
MODE_OT_CLOSE = "ot_close"
MODE_CLOSE_LATE = "close_late"   # Q4 last-5 close-game star concentration (Fix #3a.2)


def resolve_lineup(
    rotation: list,
    minute: int,
    players_by_min: list,
    box: dict,
    mode: str,
    foul_trouble_subs: bool = False,
) -> list:
    """Answer "who should be on the floor?" for the current rotation mode.

    MODE_SCHEDULED: the pre-built minute schedule, exactly as before — unless
    foul_trouble_subs, which benches a scheduled player whose fouls exceed the stage
    threshold (2 in Q1, 3 in Q2, 4 in Q3; Q4/OT plays through) for the best available
    bench player. This is why real foul-outs cluster in Q4 (a coach sits a trouble
    player until it's safe) instead of the player fouling out early.
    MODE_GARBAGE: empty the bench according to the coach's rotation — the five
    players deepest in the planned rotation hierarchy (players_by_min order),
    skipping foul-outs and backfilling up the hierarchy if the bench is short.
    Deterministic: hierarchy order, not accumulated in-game minutes.
    MODE_OT_CLOSE: real coaches close OT with their best five. Returns the top
    five by hierarchy (players_by_min front) with foul-outs excluded, backfilling
    from the next-highest available. Takes precedence over MODE_GARBAGE in OT.
    Foul-trouble subs are already a no-op in q>=3 (see _in_foul_trouble), so this
    mode doesn't need to consult them.
    """
    if mode == MODE_OT_CLOSE:
        eligible = [p["id"] for p in players_by_min if not box[p["id"]]["fouled_out"]]
        return eligible[:5]
    if mode == MODE_CLOSE_LATE:
        # EXPERIMENT Fix #3a.2 (2026-09-02): projection-aware close-late cap.
        # Trigger window is at most 5 min (last 5 of Q4). If we boost a player
        # who's already close to real MPG, they'll exit the game ABOVE it — that
        # inflates well-calibrated stars (SGA/Sengun regression). Instead skip
        # any player whose projected end-of-window minutes would exceed real_mpg
        # by more than a small tolerance. Buffer keeps the boost for genuinely
        # under-served players (Tatum/Brunson/Mitchell) while leaving already-
        # calibrated ones alone.
        CLOSE_LATE_RESERVE = 4.0  # ≤ trigger window minutes
        eligible = []
        for p in players_by_min:
            pid = p["id"]
            if box[pid]["fouled_out"]:
                continue
            current_min = box[pid].get("min", 0.0)
            real_mpg = p.get("mpg") or 40.0
            if current_min + CLOSE_LATE_RESERVE <= real_mpg:
                eligible.append(pid)
        # Backfill from hierarchy if under 5 pass the cap.
        if len(eligible) < 5:
            for p in players_by_min:
                if p["id"] not in eligible and not box[p["id"]]["fouled_out"]:
                    eligible.append(p["id"])
                    if len(eligible) >= 5:
                        break
        return eligible[:5]
    if mode == MODE_SCHEDULED:
        lineup = rotation[minute]
        if not foul_trouble_subs:
            return lineup
        result: list = []
        for pid in lineup:
            if not _in_foul_trouble(box[pid]["pf"], minute):
                result.append(pid)
                continue
            repl = next(
                (p["id"] for p in players_by_min
                 if p["id"] not in lineup and p["id"] not in result
                 and not box[p["id"]]["fouled_out"]
                 and not _in_foul_trouble(box[p["id"]]["pf"], minute)),
                None,
            )
            result.append(repl if repl is not None else pid)
        return result

    eligible = [p["id"] for p in players_by_min if not box[p["id"]]["fouled_out"]]
    # deepest five in the rotation hierarchy; backfill happens naturally by
    # taking the last five eligible ids
    return eligible[-5:] if len(eligible) >= 5 else eligible


def patch_rotation(
    rotation: list,
    fouled_out_id: int,
    players_by_min: list,
    from_minute: int,
    box: dict,
) -> None:
    """Replace a fouled-out player in all remaining rotation slots."""
    for m in range(from_minute, GAME_MINUTES):
        if fouled_out_id not in rotation[m]:
            continue
        replacement = next(
            (p["id"] for p in players_by_min
             if p["id"] != fouled_out_id
             and p["id"] not in rotation[m]
             and not box[p["id"]]["fouled_out"]),
            None,
        )
        rotation[m].remove(fouled_out_id)
        if replacement:
            rotation[m].append(replacement)
