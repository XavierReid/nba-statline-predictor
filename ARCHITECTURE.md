# Architecture — NBA Franchise Simulator

> **New here?** Read [`README.md`](README.md) first for the plain-English pitch. This doc
> is the technical walkthrough — how the engine is built, layer by layer.

## The big picture (30 seconds)

The engine is a pipeline. Real NBA data comes in one end; a fully simulated game comes
out the other. Each stage has exactly one job, and the stages only talk to each other
through clean hand-offs — so you can understand (or change) one stage without holding the
whole thing in your head:

```
NBA data  →  player abilities  →  one possession  →  a full game  →  box score + play-by-play
```

The single most important design choice: **outcomes emerge from possessions.** The engine
never computes "this player should score 25" and works backward. It plays out each
possession and lets the box score add up on its own. Everything below serves that principle.

## The one-sentence version (technical)

Real NBA data becomes player attributes, attributes become per-possession
probabilities, and game outcomes emerge from ~200 simulated possessions per game —
never from projected box scores. For operational commands see RUNBOOK.md; for milestone
specs see RFC.md; for the calibration evidence trail see SIMULATION_GAPS.md.

```
NBA API → PlayerSeasonStats (observations: "what happened")
        → rating_engine → PlayerAttributes / PlayerTendencies ("how good / what they do")
        → resolve_possession (one possession → one event)
        → simulate_game (clock, rotations, modifiers, diagnostics)
        → box score + play-by-play + possession accounting
```

## Layer 1 — Data ingestion (`app/ingestion/`)

`nba_client.py` fetches from the NBA stats API (teams, players, season stats,
shot locations, defensive matchups, clutch splits). `jobs.py` upserts into:

- **`PlayerSeasonStats`** — raw per-game observations, including shot-zone
  FGM/FGA/FG% (restricted area, paint, mid-range, corner 3) and defensive
  matchup data (defended FG% vs shooters' normal, rim and overall).
- **`TeamSeasonStats`** — pace, def_rating, oreb_pct (team-level context).

Principle: observations answer *"what happened."* Nothing in this layer knows
about the simulation.

## Layer 2 — Rating derivation (`app/services/rating_engine.py`)

The **single translation layer** from observations to abilities. Every derived
attribute uses the same pipeline: `raw_score = efficiency × volume_weight`,
percentile-ranked across the league, mapped through a curve (most players 45–75,
99 is rare). Volume gates keep small samples out; players below the gates get
position-adjusted defaults.

Notable derivations:
- `layup`/`close_shot`/`dunk` — restricted-area and paint FG% (dunk is a
  0.7 rim-finishing + 0.3 layup hybrid with a positional modifier — no clean
  NBA dunk endpoint exists)
- `perimeter_defense` — **non-rim** defended plus-minus (overall minus rim);
  defended-3P% alone is luck-dominated and punishes on-ball stoppers
- `interior_defense` — rim defended plus-minus
- Tendencies (`usage_rate`, `three_point_rate`, `corner_three_rate`,
  `foul_drawing_rate`, ...) describe *what a player does*; attributes describe
  *how well*. They are never mixed.

Guardrail: `overall_rating` is UI-only and never a simulation input.

## Layer 3 — Possession resolution (`app/services/possession.py`)

`resolve_possession(ctx)` simulates exactly one possession, as a short orchestrator over
four named stages (each visible on its own in `possession.py`):

```
_select_action   → who has the ball + what they attempt
                   (bonus foul / steal / turnover / offensive foul end it here;
                    otherwise a shot type: three/mid/close → sub-type)
_resolve_matchup → rim protection (block) then the on-ball defender
_evaluate_shot   → make probability: base ability − defense penalty, contest model,
                   signal gain, home court, modifier/form deltas  (no make/miss draw)
_resolve_outcome → the make/miss draw, shooting fouls, assist, rebound
```

Extracted from a former ~250-line monolith with the exact RNG order preserved
(behavior-neutral). Every future basketball system has an obvious home among these stages.

Key concepts:
- **Signal gain** (`SimConfig.signal_gain`): stretches each shot's deviation from
  the measured league-average make probability for its sub-type, amplifying
  player/team differentiation while holding league scoring fixed by construction.
- **Modifier adjustments**: game-state modifiers pass probability deltas in;
  possession logic never reads game state directly.

## Layer 4 — Game orchestration (`app/services/game_simulator.py`)

`simulate_game` runs periods through one loop (`_run_clock_period`) — regulation
quarters and OT are the same code with different initial conditions (720s vs
300s, new jump ball, closing lineups). Within each period, per possession:

1. **Strategic foul check** (final period, trailing defense, margin 3–8)
2. **Possession time** sampled by category (halfcourt / fastbreak / second
   chance), with a mixture-compensated halfcourt mean so pace budgets hold —
   pace stats already include short possessions
3. **Endgame pacing** (`late_game.py`): inside the endgame window the trailing
   offense plays ~9s urgency possessions, the leading offense milks ~20s —
   incentives, not outcome targeting
4. **Rotation resolution** (`rotation.py`): "who should be on the floor?" —
   scheduled minutes normally; in garbage time each team independently decides
   to concede (`late_game.should_concede`, asymmetric: leaders concede at 20,
   trailers hold until 28) and empties the bench by rotation hierarchy
5. **Lineup quality** (`lineup_quality.py`): the defending five's quality vs
   the team's minutes-weighted rotation baseline scales the team defense factor
6. **Modifiers** (`app/services/modifiers/`): momentum, fatigue, foul trouble,
   clutch, catch-up, garbage time — each returns `ModifierAdjustments`
   (probability deltas), toggled via `SimConfig`, never persisting across games
7. `resolve_possession` → `box_score.apply_event` accumulates the stat lines

## Layer 5 — Configuration (`app/services/sim_config.py`)

Every mechanic sits behind a `SimConfig` boolean so it can be isolated for
testing and calibration. Presets: `baseline` (legacy fixed-possession engine),
`drama-m1/m2/m3` (cumulative feature sets), `drama-m3-no-subtypes` (isolation).
Tuning constants carry provenance comments (value, date, sample, preset,
re-measurement trigger) — measured, not hand-set.

## Layer 6 — Diagnostics (`app/services/diagnostics.py`)

`SimulationDiagnostics` rides on every game result as
`result["possession_accounting"]`: possession counts/durations by category vs
the pace budget, clock deltas from pacing mechanics, garbage-rotation entries
and mismatch-window tracking, lineup-defense factor distribution. Principle:
**no feature silently changes possessions, clock, or quality — it must report
its contribution.**

Analysis tools consume this (details in RUNBOOK.md):
- `scratch/calibrate_simulator.py` — headline metrics, fixed matchups
- `scratch/replay_schedule.py` — replays the real season schedule; the gold
  standard (no matchup bias; per-team strength slopes)
- `app/analysis/` — the analysis pillar: `decomposition.py` (scoring/possession
  accounting), `game_texture.py` (margin walk, Q4 compression, run/drought),
  `team_boxscore.py` (box aggregates), `player_accounting.py` (per-player)

## Try it yourself

```bash
docker compose up -d
# run the test suite (~220 tests)
docker compose run --rm api sh -c "pip install pytest httpx pytest-asyncio -q && python -m pytest tests/"

# simulate one game via the API
curl -s -X POST http://localhost:8000/simulations/game \
  -H "Content-Type: application/json" \
  -d '{"home_team":"BOS","away_team":"LAL","season":"2025-26","seed":42,
       "config":{"preset":"drama-m3"},"include_pbp":true}' | python3 -m json.tool | head -40

# calibration snapshot
docker compose run --rm api python scratch/calibrate_simulator.py --drama-m3 --games 500
```

## Design principles (see CLAUDE.md for the enforced version)

1. Outcomes emerge from possessions — never work backward from expected stats
2. Tendencies (behavior) and attributes (execution) stay separate
3. Modifiers adjust probabilities, not ratings
4. Features affecting possessions/clock/quality expose diagnostics
5. Measured constants over heuristics, with documented provenance
6. Model incentives, not outcomes (late-game behavior emerges from what each
   team values)
7. Feature loop: define behavior → implement extensibly → instrument →
   validate against real data → complete
