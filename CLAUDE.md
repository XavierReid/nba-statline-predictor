# CLAUDE.md — NBA Franchise Simulator

Project-level instructions for Claude Code. These override default behavior where noted.

---

## Project overview

A possession-based NBA game simulation engine built with FastAPI + SQLAlchemy 2.0 + PostgreSQL + Docker Compose. Python 3.9 — use `Optional[X]`, not `X | None`.

Primary files: `app/services/game_simulator.py`, `app/services/possession.py`, `app/api/routes/`, `app/services/modifiers/`.

Run tests: `docker compose run --rm api sh -c "pip install pytest httpx pytest-asyncio -q 2>/dev/null && python -m pytest tests/ -v"` (74 tests as of M3a).

---

## Simulation architecture guardrails

These are non-negotiable constraints that protect the causal possession chain. Do not suggest or implement shortcuts that bypass them — if a feature request seems to require one, flag it and discuss first.

### 1. Overall ratings are never simulation inputs

`overall_rating` exists for UI display and roster comparison only. The game engine uses underlying attributes (`three_point`, `close_shot`, `perimeter_defense`, etc.) and tendencies (`usage_rate`, `three_point_rate`, etc.). Never use `overall_rating` in `resolve_possession`, `simulate_game`, or any modifier.

### 2. Tendencies describe behavior. Attributes describe execution.

- `PlayerTendencies` fields (usage_rate, three_point_rate, oreb_rate, etc.) control *what* a player does.
- `PlayerAttributes` fields control *how well* they do it.
- These must remain separate. A player can have high shooting ability (attribute) but low three-point tendency — that is a valid and distinct basketball profile.

### 3. Game state modifiers adjust probabilities, not ratings

`GameStateModifier` implementations return `ModifierAdjustments` (shot_prob_delta, tov_prob_delta, defense_penalty_delta). They never write back to player attributes, never persist across games, and are always toggled via `SimConfig`. See `app/services/modifiers/base.py`.

### 4. Outcomes emerge from possessions, not projected box scores

A player's stat line is the accumulated result of simulated possession events. Never compute a player's expected points and work backward. The correct flow is:
```
context → decision → matchup → outcome → box score accumulation
```

### 5. Features that affect possession count must expose their contribution

Season pace is an average over every game state and already contains fast breaks,
second chances, and late-game fouling. Any mechanic that shortens possessions or adds
possession chains inflates the count beyond the pace budget unless compensated or
measured. Predictable mechanics (fast breaks, second chances) are compensated
analytically in the possession-time budget; state-dependent mechanics (strategic
fouls) are left to emerge but must report diagnostics (counts, durations) so their
contribution can be validated against real data. No feature gets to silently add
possessions — see possession accounting in SIMULATION_GAPS.md §1.4.

### 6. Future systems extend the possession chain, not bypass it

- A `TeamTendencies` layer should influence action selection and tempo at the front of the possession chain.
- Player archetypes should map to the same variance/tendency parameters currently approximated by proxies.
- Defensive assignments should plug into the contest level input that `resolve_possession` already exposes.
- Nothing should introduce a parallel resolution path that skips `resolve_possession`.

### 7. Player attributes that drive in-possession events are PER-OPPORTUNITY, not per-minute

Whenever a player attribute drives an event inside a possession (a shot make, a
turnover, a foul drawn, an assist), its natural unit is "per opportunity" (per
possession / per attempt), not "per 36" or "per game". Per-minute/per-game stats are
VOLUME — they scale with how often the player is involved — and reading them as an
intrinsic rate silently inflates high-usage players. This exact bug was found and
fixed three times: shot make (observed zone FG% not a percentile band), three-point
rate (real 0.0 not a fallback), and turnovers (tov_per_poss not TOV/36, gap 3.4b).
Derive the per-opportunity form at roster load (like `ft_prob`, the zone probs,
`tov_per_poss`); anchor the aggregate with one league constant if needed. Usage
concentration (γ) makes any residual volume-as-rate error visible, so it must be
caught here first.

---

## Architectural decisions (locked)

| Decision | Rationale |
|---|---|
| Possession-based simulation, not score projection | Forces realistic variance and emergence; see Simulation Philosophy in RFC |
| `SimConfig` toggles for all modifiers | Every modifier can be isolated for testing and calibration |
| Modifiers are additive probability deltas | Prevents compounding errors; deltas are clamped per possession |
| No per-game persistence of modifier state | Modifiers reset between games; only box scores and seed are stored |
| `TeamSeasonStats` for historical results only | Behavioral/tendency data goes in `TeamTendencies` (post-M3 milestone), not `TeamSeasonStats` |
| Season simulation held until single-game engine is stable | Amplifying a broken game engine across 82 games creates harder-to-diagnose artifacts |

---

## Code conventions

- No comments explaining WHAT code does — only WHY when non-obvious (hidden constraint, workaround, subtle invariant).
- No trailing summaries in responses — the diff is readable.
- No defensive error handling for scenarios that can't happen inside the simulation engine.
- Python 3.9: `Optional[X]`, `List[X]`, `Dict[K, V]` — not `X | None`, `list[X]`, `dict[K, V]` in model/schema files. Route files can use built-in generics (FastAPI requires 3.9+).
- Show significant changes for review before applying; small isolated fixes can be applied directly.

---

## Feature roadmap (for context, not implementation scope)

```
M3 (active):   variance, OREB profiles, catch-up/garbage time, shot quality, foul drawing
Post-M3:       TeamTendencies model + ingestion (separate milestone)
Phase 2:       Shot sub-types, defensive assignments, positional matchups
Phase 3:       Player archetypes (derived from clustering), lineup synergy, foul drawing tendencies
Phase 4:       Full league simulation, player development, injuries, contracts/free agency
```

Phase 4 items (injuries, contracts, development) each warrant their own RFC before implementation begins.
