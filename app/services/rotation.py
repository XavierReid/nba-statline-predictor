"""Rotation schedule — build and patch per-minute lineup assignments."""
import random

GAME_MINUTES = 48
SUB_VARIANCE = 2.0  # σ in minutes for substitution timing (Normal dist)


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

    sorted_by_min = sorted(players, key=lambda x: x["minutes"], reverse=True)
    for slot in slots:
        while len(slot) < 5:
            for p in sorted_by_min:
                if p["id"] not in slot:
                    slot.add(p["id"])
                    break

    return [list(s) for s in slots]


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
