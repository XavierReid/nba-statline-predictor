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

### Stage C — Behavior Pipeline ✅ STRUCTURAL EXTRACTION DONE (2026-07-09)
`app/services/behavior/` — `BehaviorPipeline` owns all per-possession behavior sources:
builds the active list from cfg (registry logic moved out of the orchestrator), combines
their adjustments (`.adjustments(is_home, snapshot)`), and fans out the post-possession
`.update(...)`. The Q4 `TeamObjective` became a normal pipeline member (`ObjectiveModifier`)
instead of an inline special case — the orchestrator now has one `behavior.adjustments()`
/ `behavior.update()` pair, no registry/combine/objective/update sprawl. `GameSnapshot`
gained `home_conceded`/`away_conceded` so the objective's concede gate reads through the
standard interface. Started the `behavior/` namespace (new files born there; existing
`modifiers/` migrate in stage G). Behavior-neutral: 266 tests green + replay identical to
`demoable-v1`.

**Deferred (per discipline):** converting sources from returning `ModifierAdjustments`
(deltas) to returning richer *intentions* combined into a `BehaviorState`. That's a
behavioral redesign (the Q4 work proved intention→outcome mapping is non-trivial) and
can't be proven neutral — it comes later, one source at a time, each measured. This pass
delivered the structural pipeline only.

### GamePhase — "what kind of basketball" layer ✅ INTRODUCED (2026-07-09)
`app/services/game_phase.py` — `GamePhase {NORMAL, COMPETITIVE_LATE, GARBAGE, OVERTIME}` +
`derive_phase(...)`, sitting between GameState (what is true) and Objectives (what each team
optimizes for). Threaded onto `GameSnapshot.phase` so behavior sources can read it.
Behavior-neutral: nothing keys off it yet, replay identical to `demoable-v1`.

**Honest scope finding:** the goal of "migrate existing behaviors onto GamePhase to retire
duplicated clock/margin checks" ran into a real obstacle — what looked like duplication is
actually **genuinely different phase definitions per behavior** (strategic foul: Q4 +
margin 3-8; clutch: Q4/OT + clock + margin ≤ X; garbage modifier: Q3+ + margin ≥ 20;
concede: ≥20/<12 hysteresis). They ask different precise questions, so one shared classifier
can't neutrally replace them. GamePhase's real role is therefore (1) a NEW named phase
(`COMPETITIVE_LATE`) that new behaviors read — the seam for the gap-3.2 competitive-Q4
variance work — and (2) a future, DELIBERATE (non-neutral, measured) harmonization of those
scattered thresholds if we choose it. Introduced as the layer + seam; existing behaviors
keep their own definitions for now.

Target pipeline: GameState → GamePhase → Objectives → Behavior Sources → Aggregation →
PossessionContext → Decision Pipeline → Outcome.

### BehaviorProfile — baseline behavior per phase ✅ INTRODUCED (2026-07-09)
`app/services/behavior_profile.py` — `BehaviorProfile` (foul_draw / turnover / pace /
transition / offensive_rebound mults + a `ShotProfile` sub-object) answers "how is
basketball *normally* played during this phase?", distinct from Objectives ("what is each
team trying to accomplish?"). A phase resolves to a profile via `profile_for_phase()` (the
lookup layer that can later return playoff / team-identity / coach profiles without touching
the engine). Profiles COMPOSE with objectives and other sources — none overwrites another.
Threaded onto `PossessionContext.behavior_profile`; the possession engine applies the mults
(NORMAL_PROFILE = identity, so non-competitive play is unchanged). `COMPETITIVE_LATE` is
populated from **measured 2024-25 clutch splits** (FTA 1.86×, TOV 0.92×, OREB 1.16×, 3PA
flat). Toggle `use_behavior_profile` (on in DRAMA_M3); replaces the ad-hoc M3e late-foul
zones as the canonical owner of competitive-late fouling.

**Important:** this layer owns *measured behavior*, NOT statistical calibration. It did NOT
close gap 3.2's Q4-variance target — instrumentation proved that gap is structural to the
shot-outcome model, not behavioral (SIMULATION_GAPS.md). We kept the measured behaviors
(realistic, headline-neutral-to-better: slope 1.00) rather than tuning the profile to chase
a variance metric it was never meant to model.

### Stage D — Decision pipeline ✅ DONE (2026-07-09) — LAST FOUNDATIONAL MILESTONE
`resolve_possession()` was one ~250-line function performing every basketball decision.
It is now a short readable orchestrator over four named stages in `possession.py`:
`_select_action → _resolve_matchup → _evaluate_shot → _resolve_outcome`, with lightweight
`Action` / `Matchup` / `ShotQuality` products. The stages preserve the **exact RNG draw
order** of the monolith, so it is a pure readability extraction — 261 tests green + replay
byte-identical to `demoable-v1`. `_evaluate_shot` is deliberately make/miss-free (it
computes shot *quality*); the make draw lives in `_resolve_outcome`. Every future feature
now has an obvious home: coaching/identity/fatigue → `_select_action`; defensive schemes →
`_resolve_matchup`; contest/difficulty → `_evaluate_shot`; fouls/rebounds → `_resolve_outcome`.

**Foundational architecture (A–D) is complete.** Remaining stages are not refactors: E
(Team Identity) is a feature, F is demand-driven, G is trivial namespace churn.

Original goal (kept for reference) — make the basketball engine **readable and ownable**,
not just extensible; the stages already existed *implicitly* and extracting them introduced
NO new behavior, only visibility, organized by basketball concept:

```
GameState → PossessionContext
  → select_action()        # who acts, what action is attempted
  → resolve_matchup()      # defender / assignment
  → evaluate_shot()        # shot quality / difficulty / contest
  → resolve_outcome()      # made/missed, foul, block, rebound
  → apply_post_possession_updates()
```

**The goal is comprehension, not future-proofing.** Success test for any extraction:
*can you understand that basketball decision by opening one file, without tracing the
rest?* The win holds even if no feature ever follows. That said, the seams also give every
future feature an obvious home — coaching / Team Identity / fatigue → `select_action()`;
defensive schemes / switching → `resolve_matchup()`; momentum / confidence / contest /
difficulty → `evaluate_shot()`; fouls / blocks / rebounds / made-miss → `resolve_outcome()`.

**Discipline (same as A/B/C):** pure behavior-neutral wraps of existing logic, proven by
replay identical to the frozen baseline. The moment a stage drifts from "wrap the existing
step" into "redesign the step" (e.g. rework shot quality), it is a FEATURE needing
calibration validation — not part of this refactor.

---
**The stages below are NOT architecture-refactor work — they are feature layers with their
own calibration loops. Do not "finish" them as part of the foundational architecture. Let
calibration needs (the Calibration Frontier in SIMULATION_GAPS.md) pull them.**

### Stage E — Team Identity (FEATURE, not refactor)
Teams differ because their *offense* differs (pace, philosophy, action mix), not only
their players. This is a new behavioral system with its own measure/validate loop; it
slots into `select_action()` once Stage D exists. Belongs in the calibration/feature
stream, demand-driven.

### Stage F — EffectivePlayer / PlayerProfile split (DEMAND-DRIVEN)
Immutable profile + per-possession effective player (fatigue/momentum/form applied). NOT
urgent — modifiers don't mutate player dicts today. Do it when fatigue/archetype work
actually needs it, not speculatively.

### Stage G — Domain namespaces (TRIVIAL — whenever)
Reorganize existing files into `state/ behavior/ decision/ resolution/ rotation/
calibration/ data/ simulation/`. Cheap, low-value churn; do it once the abstractions have
settled. New files are already born in the right package (e.g. `behavior/`).

## Sequencing (2026-07-09)

Foundational architecture = A, B, C, **D** — the pure structural refactors. A/B/C done; D
is the last one. E/F/G are NOT foundational: E is a feature, F is demand-driven, G is
trivial. Recommended path: **finish gap 3.2 (closes game-outcome distributions) → Stage D
(the readability milestone) → reassess.** After D, resume the Calibration Frontier
(3.3 OT, 3.4 player realism, 3.5 box-score, 3.6 lead changes); Team Identity enters there
as a feature. Do not build E/F ahead of a real need.
