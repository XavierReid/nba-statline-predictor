# CLAUDE.md — NBA Franchise Simulator

Project-level instructions. Every-session content only. Ad-hoc reference lives in `RFC.md`, `ARCHITECTURE.md`, `RUNBOOK.md`, or the `project-next-session-focus` memory.

## Stack

Possession-based NBA game sim. FastAPI + SQLAlchemy 2.0 + PostgreSQL + Docker Compose. Python **3.9** — use `Optional[X]`, `List[X]`, `Dict[K, V]` in model/schema files (not `X | None`, `list[X]`, `dict[K, V]`). Route files can use built-in generics.

Primary files: `app/services/game_simulator.py`, `app/services/possession.py`, `app/services/box_score.py`, `app/services/possession_events.py`, `app/api/routes/`.

Run tests: `docker compose run --rm api sh -c "pip install pytest httpx pytest-asyncio -q 2>/dev/null && python -m pytest tests/ -q 2>&1 | tail -3"`.

## Simulation architecture guardrails (non-negotiable)

If a feature request seems to require bypassing one, flag it and discuss first.

1. **`overall_rating` is never a sim input.** It exists for UI/comparison only. `resolve_possession` reads underlying attributes (`three_point`, `perimeter_defense`, etc.) and tendencies (`usage_rate`, etc.).

2. **Tendencies describe behavior; attributes describe execution.** `PlayerTendencies` = *what* a player does. `PlayerAttributes` = *how well*. Keep separate — high shooting ability + low three-point tendency is a valid profile.

3. **Modifiers adjust probabilities, not ratings.** `GameStateModifier` returns `ModifierAdjustments` (probability deltas). Never writes to attributes. Never persists across games. Always toggled via `SimConfig`. See `app/services/modifiers/base.py`.

4. **Outcomes emerge from possessions.** Never compute expected points and work backward. Flow: `context → decision → matchup → outcome → box score accumulation`.

5. **Features affecting possession count must expose their contribution.** Season pace already contains fast breaks, second chances, late-game fouling. Predictable mechanics are compensated in the pace budget; state-dependent mechanics (strategic fouls) emerge but must report diagnostics. No feature silently adds possessions.

6. **Future systems extend the possession chain, not bypass it.** `TeamTendencies`, player archetypes, defensive assignments all plug INTO `resolve_possession`. No parallel resolution paths.

7. **Per-opportunity, not per-minute, for in-possession attributes.** Any attribute driving an in-possession event (shot make, TOV, foul draw, assist) is per-possession or per-attempt, NOT per-36 or per-game. Per-minute stats are VOLUME; treating them as intrinsic rate silently inflates high-usage players. Bug found + fixed 3 times (make-rate zones, three-point rate, `tov_per_poss`). Derive per-opportunity at roster load; anchor with league constants if needed.

## Locked architecture facts

- Box score is **derived** from the typed event stream, not accumulated inside `resolve_possession`. `derive_box_score(events, roster_ids)` + `apply_typed_event` in `box_score.py`; 90-game byte-identical fence at `tests/test_box_score_derivation_fixture.py`.
- Every modifier behind a `SimConfig` toggle so it can be isolated for calibration.
- Modifiers are additive probability deltas, clamped per possession.
- `TeamSeasonStats` holds historical results only; behavioral/tendency data will go in `TeamTendencies` when built.

## Code conventions

- Comments explain WHY when non-obvious, never WHAT. No trailing summaries in responses.
- No defensive error handling for scenarios that can't happen inside the sim engine.
- Show significant changes for review before applying; small isolated fixes can be applied directly.

Priority tracking: `project-next-session-focus` memory. Roadmap: `RFC.md` + memory. Not duplicated here.
