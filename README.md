# NBA Franchise Simulator

**A basketball engine that plays out NBA games one possession at a time — and produces stat lines that look like the real thing.**

Give it two real rosters and it simulates the game the way it actually unfolds: a player is chosen, a shot goes up, it's contested, it's made or missed, someone grabs the rebound — ~200 times, until the clock runs out. Nobody tells it the final score. The box score, the momentum swings, the star performances — all of it *emerges* from the possessions.

Think of the season-mode simulators in NBA 2K or Football Manager, rebuilt from scratch as a proper backend engine and grounded in real NBA data.

---

## See it in action

One simulated game, Celtics vs Lakers:

```
                              Q1   Q2   Q3   Q4   TOT
  Boston Celtics              19   33   40   36   128
  Los Angeles Lakers          24   37   22   30   113

  Boston Celtics (Home)
  Name                          MIN  PTS  REB  AST  STL  BLK  TOV  PF       FG      3PT       FT
  Jaylen Brown                 34.2   23    5    2    0    0    5   2     9/14      5/7      0/1
  Payton Pritchard             28.9   19    3    4    0    0    2   0      6/9      5/6      2/2
  Nikola Vučević               25.0   16    2    2    0    0    0   2     6/11      1/4      3/3
  Derrick White                32.0   14    3    2    3    0    1   2     6/10      2/5      0/0
  Neemias Queta                23.1    6   10    4    2    0    0   2      3/5      0/0      0/0
```

Nothing here is scripted. Brown taking 14 shots, Queta grabbing 10 boards, the Celtics pulling away in the third — every number is the result of the simulation, not a target it was told to hit.

---

## The idea, in plain English

Most "simulators" cheat: they take a team's average stats, add some randomness, and print a final score. This one doesn't. It models the actual decisions of a basketball possession and lets the statistics fall out naturally. That matters because it means the *right things* happen for the *right reasons*:

- **Stars play like themselves.** Elite passers rack up assists, rim protectors swat shots at the basket, knock-down shooters hit threes — because their real abilities drive what happens on each possession, not because anyone hard-coded "Jokić gets a triple-double."
- **Games feel real.** Teams go on runs. Close games tighten up late. Blowouts empty the bench. Trailing teams start fouling. It's basketball, not a dice roll.
- **It's honest.** Every simulated season is checked against *real* NBA data — scoring, margins of victory, home-court advantage, even shooting percentages by shot type. When the numbers drift from reality, that's treated as a bug to investigate, not something to fudge.

The guiding rule throughout: **model the behavior, and let the statistics emerge** — never tune the statistics directly.

---

## Does it actually match real basketball?

Yes — that's the part I'm proudest of. Simulating the real 2024-25 schedule and comparing to what actually happened:

| Metric | Real NBA | Simulated |
|---|---|---|
| Points per team per game | 115.6 | ~115 |
| Home-win rate | 55.4% | ~56% |
| How much better good teams are than bad ones | baseline | matched |
| Free-throw %, shot mix, quarter-by-quarter flow | — | validated |

And it holds up player-by-player: the league's best passers, rim finishers, and perimeter defenders in the sim are the same names you'd expect in real life.

---

## Under the hood

```
Real NBA data  →  player ratings  →  one possession at a time  →  full game  →  box score + play-by-play
   (ingestion)     (rating engine)     (possession engine)         (game engine)
```

- **Ingestion** — pulls real teams, rosters, and stats from the NBA's data API.
- **Rating engine** — turns raw stats into player abilities (shooting, defense, rebounding, playmaking). This is the *only* place real data becomes simulation abilities.
- **Possession engine** — resolves a single possession: who has the ball, what shot, who's defending, make or miss, rebound, foul.
- **Game engine** — runs the clock, manages substitutions and overtime, and layers in realistic behavior (momentum, fatigue, late-game strategy).
- **REST API** — kick off simulations and browse results.

Built with **FastAPI · PostgreSQL · SQLAlchemy · Docker**. For the full technical walkthrough, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Reading the engine

A game is 48 minutes of basketball, sliced into possessions. Each possession is a small state machine: pick a ball-handler, pick a defender, decide what shot to attempt, resolve it, produce a stream of granular events (shots, fouls, rebounds, assists, blocks, steals, turnovers), which then fold into the box score. There's no top-down score projection — every point on the board came from a specific event on a specific possession.

The engine is organized into layers. Each layer has one job and doesn't reach up or across.

### `app/services/game_simulator.py` — the orchestrator

Contains `simulate_game()`. Walks the clock through 4 regulation quarters + any OTs. Inside each period it loops `while quarter_clock > 0`: samples a possession time (halfcourt / fastbreak / second-chance / etc.), calls `resolve_possession` to figure out what happened, translates the outcome into typed events, and applies those events to the box. It doesn't KNOW what a shot is or how to pick a defender — it just runs the clock and orchestrates.

### `app/services/possession.py` — the heart

Contains `resolve_possession()`. Given a `PossessionContext` (who's on offense/defense, game state, config), it does the possession-level decisions:

- Pick a ball-handler (usage-weighted)
- Check for a steal (best defender's steal attribute × steal_rate)
- Check for a turnover
- Pick a shot sub-type (layup, dunk, floater, mid-range, corner three, above-break three)
- Pick a defender (positional matchup)
- Evaluate the shot: base make prob × defense penalty × contest model × modifiers
- Roll the outcome, then check for shooting fouls, rebounds, and-1s

Returns a single dict describing what happened. Everything downstream reads that dict.

### `app/services/possession_events.py` — the event translator

Takes the flat outcome dict from `resolve_possession` and expands it into a stream of granular typed events (SHOT / FOUL / FT / REB / TOV / STL / BLK / AST). Real NBA play-by-play is granular: a made three with an assist is TWO events (SHOT + AST), a foul-drawn miss is a FOUL + N FTs with NO shot event. The translator handles all these compositions and also renders the descriptions used for PBP display.

### `app/services/box_score.py` — the accounting sink

Two functions:

- `apply_typed_event(box, event)` — applies ONE event to the live box, returns `(points_scored, fouled_out_pid)`. This is the SOLE accounting authority. Every point on the scoreboard came through this function.
- `derive_box_score(events, roster_ids)` — a pure function that folds an event stream into a complete box.

The invariant that `simulate_game`'s live box equals `derive_box_score(typed_events)` is verified by a 90-game byte-identical fixture (`tests/fixtures/box_score_baseline.json`).

### `app/services/roster.py` — the player loader

`load_roster(db, team_id, season)` reads from the database and returns a list of player dicts. Each dict carries:

- **Attributes** (0-100 scale): three_point, mid_range, layup, dunk, passing, perimeter_defense, interior_defense, etc.
- **Tendencies**: usage_rate, three_point_rate, foul_drawing_rate, etc.
- **Observed data**: per-zone FG% (shrunk toward league prior), FT%, per-poss TOV rate

The engine consumes these dicts and never touches the database. The database-to-dict boundary is here.

### `app/services/sim_config.py` — the configuration schema

`SimConfig` is a dataclass with every toggle and calibration constant. Every mechanism has a `use_*` toggle so it can be isolated for testing. `DRAMA_M3` is the default preset (everything on). Individual constants like `tov_scale = 0.36`, `steal_rate = 0.086`, `foul_draw_scale = 0.19` live here with a comment explaining how they were measured.

### `app/services/diagnostics.py` — the instrument

`SimulationDiagnostics` accumulates per-game chain counts + times + pace budget. Every mechanic that touches possessions must report here — the guardrail is that no feature can silently add possessions.

### `app/services/modifiers/` — game-state modifiers

Each modifier is a class that reads the current game state and returns `ModifierAdjustments` (probability deltas). Examples: momentum from runs, fatigue from heavy minutes, foul_trouble softening defense, clutch amplification in tight endgames. Modifiers never write back to player attributes and never persist across games — they just tilt probabilities.

### How they connect

```
load_roster                     ← reads DB, produces player dicts
    ↓
simulate_game (orchestrator)
    ↓
    while clock > 0:
      ctx     = build_context(...)
      outcome = resolve_possession(ctx)             ← the basketball
      events  = possession_to_events(outcome)       ← granular translation
      for e in events:
          apply_typed_event(box, e)                 ← sole accounting sink
      diagnostics.record_possession(category, time) ← instrument
    ↓
returns {box_score, typed_events, quarter_scores, possession_accounting}
```

### What holds it together

- **Layer discipline** — each file has one job. If asked "where would you add X?", the answer fits in one sentence.
- **Sole accounting sink** — one function owns scoring. `grep 'gs.home_score +='` finds exactly one call site.
- **Event-sourced invariance** — live box always equals derived box across 90 seed-fixed games.
- **Cross-era honesty** — the same engine runs 1996-97 and 2024-25. No hardcoded era tables.
- **Every constant has a sweep** — no magic numbers. Each has a comment showing what was measured to derive it.

---

## Running it locally

**You'll need:** Docker + Python 3.9+

```bash
docker compose up -d postgres              # start the database
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head                       # set up tables
python -m scripts.run_ingestion --all      # pull real NBA data for every season
python -m scripts.run_ingestion --verify   # audit shot-location coverage across seasons
uvicorn app.main:app --reload              # start the API
```

Simulate a single game with full play-by-play from the command line:

```bash
python scratch/03_game_simulator.py BOS LAL 7 2025-26 --pbp
```

Or use the web UI (`frontend/` — React + Vite). With the API running on :8000:

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173, proxies to the API
```

---

## Where it's headed

**Done:** real-data ingestion · data-grounded player ratings · a possession-based game engine with clock, rotations, overtime, and late-game strategy · full-season simulation · a REST API · and a calibration suite that holds the whole thing to real NBA numbers.

**Next:** finishing a clean, readable engine architecture (so new basketball systems slot in easily), then richer realism — team offensive identity, coaching tendencies, and eventually multi-season play with player development.

Deeper docs for the curious: [`ARCHITECTURE.md`](ARCHITECTURE.md) (how it works) · [`RUNBOOK.md`](RUNBOOK.md) (commands & tools) · [`SIMULATION_GAPS.md`](SIMULATION_GAPS.md) (the calibration detective work).

---

*Built by Xavier Reid — [GitHub](https://github.com/xavierreid) · [LinkedIn](https://www.linkedin.com/in/xavier-reid-246814115/)*
