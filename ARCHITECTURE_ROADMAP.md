# Architecture Roadmap — from Simulator to Engine

Source: external architecture review (2026-07-09) + internal sequencing decisions.
This document governs *how* future milestones are built. It is a direction, not a
rewrite plan. See ARCHITECTURE.md for how the system works today; SIMULATION_GAPS.md
for calibration state.

## Where we are

Stage 3 of 4: "many interacting systems," transitioning toward "simulation platform."
The engine produces good basketball; the smell is that every new feature opens the
same core files (`game_simulator.py`, `possession.py`) and adds another `if cfg.use_x`
branch. Nothing is broken — this is the technical debt of a successful prototype.
The fix is a series of **behavior-neutral extractions**, not a rewrite.

Assessment scores from the review: data model 9/10, realism 9/10, extensibility 7/10,
maintainability 6.5-7/10 (trending down as modifiers accumulate).

## Non-negotiable principles (these extend CLAUDE.md)

1. **No big rewrite.** Every refactor wraps existing code and is behavior-neutral.
   Each one must prove neutrality: tests green **and** schedule replay identical vs a
   tagged baseline. (History: the OT restructure "looked mechanical" and still needed an
   OT_SEED fix + a nonlocal bug that only tests caught.)
2. **Communicate through data objects, not internals.** Possession resolution must not
   know how rotations are built; rotations must not know how momentum works; momentum
   must not know how shot probabilities are computed. Each system asks for information
   through an interface and stays agnostic about its source.
3. **Players stay data.** Intelligence lives in systems, not in a Player class with 80
   methods. Immutable profile in; effective (fatigue/momentum/form-adjusted) player
   computed per possession; raw ratings never mutate mid-game (no attribute drift).
4. **Modifiers contribute intentions, not outcomes.** Layer: momentum → confidence →
   shot quality → probability, not momentum → probability directly. This is also the
   empirical finding from the Q4 diagnosis (the catch-up modifier edits probabilities
   and ends up fighting the trailing team's real incentives).
5. **Think in basketball layers.** team identity → offensive philosophy → player
   decision → action → matchup → shot quality → outcome. New features slot into one
   layer instead of touching many.
6. **The engine is the product; a game sim is one consumer.** Season, playoffs,
   Monte Carlo, lineup optimization, "what if Curry were on the Thunder" should all be
   thin orchestration over the same engine. The engine should not know who Curry is —
   only role, tendencies, ratings, lineup, team identity, game context.

## Already seeded (validates "extract, don't rewrite")

- `SimulationDiagnostics` — calibration as a subsystem (principle: calibration is
  first-class, not scripts)
- `LateGameContext` / `should_concede` — a proto-context and a proto decision-layer
- `resolve_lineup` (rotation modes) — a proto-RotationEngine
- Measured constants with provenance — the measured-constants philosophy
- Modifiers already return `ModifierAdjustments` (deltas), not mutated players

## Staged extraction plan (ordered by value, not by ease)

### Stage A — PossessionContext ✅ DONE (2026-07-09)
`resolve_possession` had grown to **25 parameters** (15 were `cfg.foo` passthroughs with
"keep in sync with SimConfig" comments — the coupling smell). Built an immutable
`PossessionContext` (`app/services/possession_context.py`) holding the possession's
starting STATE only — offense/defense units, score margin, clock, quarter, pre-combined
`adjustments`, `cfg`, RNG. `resolve_possession(ctx)` unpacks fields into the same locals,
so the body is untouched (behavior-neutral: full suite green + replay identical to
`demoable-v1`). `cfg` stays the single source of static config (not copied onto the
context). Canonical builder `make_context()` routes overrides to cfg-vs-state by field
name — one construction path for production and tests.

**State/decision boundary established (deliberately):** the context holds only what
exists when a possession begins. DECISIONS produced during resolution — ball handler,
primary defender, shot sub-type, contest level, shot quality, outcome — do NOT go on the
context. They become their own domain objects (Action, Matchup, ShotQuality, Outcome) in
stages B/D. This keeps later pipeline splits clean.

### Stage B — GameState (persistent, owns the sim) ✅ DONE (2026-07-09)
`app/services/game_state.py` — owns the scalar state that survives across possessions:
score, per-quarter scores, elapsed clock, possession count, period index, and the
hysteretic `home_conceded`/`away_conceded` flags. Replaced the 8 loose `nonlocal`
scalars juggled across the two nested closures (both `nonlocal` declarations are now
gone — the closures mutate `gs` attributes). Exposes read-only computed state (`margin`,
`abs_margin`, `leading_is_home`, `is_tied`, `is_final_period`, `offense_margin(is_home)`)
as the one authoritative source. The pre-existing modifier snapshot `GameState` was
renamed `GameSnapshot` (it is a per-possession read-only view, not the owner).

**Scope (deliberate):** ownership only — fields + computed properties. State-transition
METHODS (`advance_clock`, `apply_score`, `update_concessions`, `next_period`) are
stage C+; the loop still mutates `gs.field` inline. Boundary rule now explicit: state
that survives across possessions → GameState; state within one possession → PossessionContext.
Behavior-neutral: 259 tests green + replay identical to `demoable-v1`.

### Stage C — Behavior Engine / modifier pipeline
Modifiers become near-trivial: each receives context, returns an intention/`Adjustment`;
a pipeline combines them into a `BehaviorState`; only then does resolution act. Kills the
"multiple modifiers silently fight over the same probability" failure mode.

### Stage D — Decision layers (action selection pipeline)
`select_action(context) → resolve_matchup(context) → evaluate_shot(action, matchup) →
resolve_outcome(quality) → apply_post_possession(...)`. Each function can initially wrap
existing code (behavior zero-change). Coaching tendencies, fatigue, momentum, archetypes
then influence *decisions before the shot*, not make-probabilities after.

### Stage E — Team Identity as first-class
The biggest basketball realism jump: teams differ because their *offense* differs (pace,
philosophy, action mix), not only because their players differ. Slots into the decision
layer.

### Stage F — EffectivePlayer / PlayerProfile split
Immutable profile + per-possession effective player (fatigue/momentum/form/foul-trouble
applied). Scheduled with fatigue/archetype work where it earns its keep — NOT urgent
today because modifiers don't mutate player dicts yet.

### Stage G — Domain namespaces (LAST)
Reorganize into `state/ behavior/ decision/ resolution/ rotation/ calibration/ data/
simulation/`. Deliberately last: moving files is the cheapest-looking, least-valuable
part — do it once the abstractions exist and have settled.

## How this merges with calibration work

These extractions interleave with basketball milestones rather than blocking them. The
active Q4 objective rebalance is the **first Behavior Engine citizen** (Stage C): instead
of patching the catch-up modifier, introduce a `TeamObjective`
(maximize-efficiency / protect-lead / chase) derived from game state that feeds
intention-level adjustments. It delivers the calibration milestone *and* lays the first
architectural brick — no throwaway work. Recommended order: Q4 objective (C-flavored) →
PossessionContext (A) → GameState (B) → full pipeline (C/D) → identity (E) → namespaces (G).
