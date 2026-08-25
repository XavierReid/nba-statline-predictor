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
7. `resolve_possession` → `possession_events.possession_to_events` translates the possession
   result into a stream of granular typed events (SHOT / FOUL / FT / REB / TOV / STL / BLK /
   AST / SUBSTITUTION) → `box_score.apply_typed_event` folds each into the live box (or
   `derive_box_score(events, roster_ids)` reproduces the same box from the stream after the
   fact — same result; guarded by the 90-game byte-identical fence). SUBSTITUTION events
   emit at every rotation transition; `apply_typed_event` treats them as a stat no-op so
   they don't disturb the box fence, but they carry the lineup deltas needed to reconstruct
   who was on the floor at any point (guarded by
   `tests/test_lineup_reconstruction.py`).

## Layer 4a — Season-scale execution

Three modes layer over `simulate_game` — all call the same engine, differ in what they do
with the results.

- **`app/services/season_simulator.py` — team-season batch (B-arc).** One team's 82 games in
  a background task. Persists a `SimulationRun` + per-game `SimulatedGame` +
  `SimulatedPlayerLine` rows. Deterministic per-game seeding via `_game_seed(root_seed, game_id)`
  so drill-in re-simulation matches the batch run exactly.
- **`app/services/league_simulator.py` — full-league batch (C-1 arc).** All 30 teams'
  1230 games in one background task. Schedule integrity gate up front (per-era: 1230/30/82
  for modern, 725 lockout, 971 COVID, etc.). Reproducibility gate: pause/resume yields
  byte-identical persisted games because per-game seeds are pure functions of `(root_seed,
  game_id)`. Standings computed on-demand from persisted games (never cached). Full
  2016-17 season sims in ~19 seconds.
- **`app/services/myleague_engine.py` + `myleague_state.py` — stateful franchise mode
  (M-1 arc).** `SeasonState` is the authoritative object: `simulation_id`, `season`,
  `root_seed`, `controlled_team_id` (nullable — God-mode door open), `current_calendar_date`,
  and an event-sourced availability layer. `advance_to(state, target_date, db)` folds one or
  more games in, mutates cursor + games_completed, respects future `MyLeagueEvent` records
  when loading rosters. Invariants: monotonic time, idempotent advance, retroactive-event
  rejection (no mutation of already-simulated games), C-1 batch mode preserved unchanged.
  See `tests/test_myleague_engine.py` for the correctness gate.

`SimulationRun.scope` disambiguates all three: `'team'`, `'league'`, `'myleague'`. CHECK
constraint enforces `team_id` is set for team scope only. MyLeague adds a sibling
`myleague_state` table (1:1 with SimulationRun) and an append-only `myleague_events` log.

## Layer 4b — Frontend surface

React + Vite + TypeScript SPA at `frontend/`. Three top-level tabs:

- **Single Game** — pick two rosters, sim inline, browse full boxscore + PBP with
  filters + STARTERS row + substitutions.
- **Season Sim** — Team or Full League scope toggle. Batch execution with progress bar
  and cancellation. Team drill-in reuses `TeamStandingsBlock`; game drill-in reuses
  `GameDetailView`.
- **MyLeague** — pick a franchise → dashboard (hero + advance-day + recent games +
  upcoming games + E/W standings with W/L streaks). NextGameCard shows opponent identity,
  matchup + series context, and top-8 rotations. Recent Results rows are clickable →
  same `GameDetailView` drill-in.

Shared components (`frontend/src/components/`): `LineScore`, `BoxScore`, `PlayByPlay`,
`PlayerModal`, `TeamStandingsBlock`, `GameDetailView`, `GameContextHeader`, `NextGameCard`,
`TeamLogo`. Shared helpers (`frontend/src/lib/`): `teamStandings.ts` (extendRow,
computeStandings, TeamStandings — used by both Season Sim and MyLeague drill-ins).

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
  `team_boxscore.py` (team box aggregates), `player_accounting.py` (per-player
  reconciliation), `player_distribution.py` (per-player-game distribution guard —
  game-highs / foul-outs vs sanity ceilings; catches concentration bugs team totals hide)

## Try it yourself

```bash
docker compose up -d
# run the test suite (~400 tests)
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
