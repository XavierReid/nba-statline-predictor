# Context Primer for a New Conversation

Copy the block between the `---` markers below as the first message in a fresh
Claude conversation to bring the new agent up to speed.

---

## The primer (copy from here)

I have a Python/FastAPI backend project at
`/Users/xavierreid/Desktop/projects_2026/nba-statline-predictor/` —
it predicts NBA player statlines for upcoming games using rule-based heuristics
over historical data. A production-grade scaffold (FastAPI, SQLAlchemy 2.0,
Alembic, Docker Compose, GitHub Actions CI, pytest) is in place and pushed to
https://github.com/XavierReid/nba-statline-predictor. The predictor service
itself is fully implemented (pure functions with unit tests); everything else
is mostly stubs that I'll fill in.

**Before answering**, please read `README.md` and skim the file tree so you
understand what's already built. Then read this primer end-to-end before
proposing what to do.

---

### About me

- Backend engineer, 5+ years pro experience (mostly Java + Spring Boot at LinkedIn).
- Between roles after the mid-2025 LinkedIn layoff round; actively job searching
  for mid-level backend / full-stack roles.
- Currently sharp in Python from interview prep, but my Java instincts are
  deeper. Treat me as "experienced engineer learning Python idioms" — don't
  re-explain ORMs, REST, migrations, dependency injection in the abstract;
  do explain the Python-specific syntax and gotchas (SQLAlchemy 2.0 style,
  FastAPI's Depends pattern, Pydantic Settings, etc.).

---

### Goals for this project

1. **Backstop my resume's Kafka claim.** I want to be able to honestly say
   "I designed and built a Kafka producer/consumer end-to-end" by the time
   v2 is done.
2. **Demonstrate clean backend engineering** — schema design, REST API design,
   data pipelines, explainable prediction logic.
3. **Understand every piece of the code I ship.** Not just what it does, but
   why. I will be defending this in interviews.

---

### Build philosophy: barebones-first, level up to production-grade

The scaffold is the *target architecture*, not the starting point. For every
feature, the progression I want is:

1. **Phase 1 — Throwaway script** in `scratch/`. Simplest possible thing that
   does the job. No DB, no abstractions, no error handling. Just prove the
   concept works.
2. **Phase 2 — Move into the scaffold structure.** Take what worked, write it
   as a function inside the appropriate `app/` module. Add the minimum
   integration needed (DB session, basic models) but nothing fancy.
3. **Phase 3 — Production hardening.** Add error handling, retries, logging,
   idempotency checks, and tests. Only when I've felt the absence of these
   things first.

**The point of barebones-first**: I learn *why* each layer exists by feeling
the pain it solves, before I add it. If you let me skip phases by writing
production code from the start, I'll have a working project I can't fully
defend in interviews.

The `scratch/` folder exists for Phase 1 experiments. Code there is meant to
be deleted once it graduates to the scaffold (or kept around as documentation
of the learning progression).

---

### How I want you to work with me (rules of engagement)

These are non-negotiable. Please follow them strictly.

1. **Coach me through implementation; don't generate code for me.** When we
   tackle a feature, you describe the approach + trade-offs, I write the code,
   you review and explain. Pair-programming style, with me at the keyboard.
2. **Explain "why" before "how."** Before any code, we discuss the design
   choice. I should be able to defend every decision without you present.
3. **Start each feature with the dumbest possible version.** Don't pre-empt
   complexity. If I write 5 lines that work, that's correct — even if it's
   missing things you'd add in production.
4. **One concept per session.** Don't try to do ingestion + the prediction API
   + tests in one sitting. Pick one, finish it cleanly with tests, commit,
   and stop.
5. **Ask me "what if…?" questions.** What if the API times out? What if two
   ingestion jobs run concurrently? What if the season changes mid-run? Make
   me think about edge cases — these are interview gold.
6. **End every session with three deliverables:**
   - Green test run (`pytest`)
   - Git commit with a clear message
   - Update to the "Current state" and "Today's plan" sections of this primer
7. **Don't generate large code blocks.** If you find yourself writing more
   than ~20 lines of code in a single response, stop and ask if I want to
   write it instead.

If I push back on a constraint or ask you to "just write it," gently remind me
of these rules. The constraint protects my learning.

---

### Current state of the scaffold

- Docker Compose brings up Postgres + the FastAPI app on `docker compose up`.
- 7 routes registered (`/health`, `/players/...`, `/games/...`,
  `/predictions/...`, `/backtest`). Most return stubs; the predictor service
  itself is fully implemented as pure functions.
- 9 tests passing: 7 unit tests on the predictor, 2 smoke tests on the FastAPI app.
- Alembic migration `0001_initial_schema.py` creates all 5 tables (teams,
  players, games, player_game_stats, team_defensive_ratings).
- Ingestion is stubs only — `app/ingestion/nba_client.py` and
  `app/ingestion/jobs.py` have function signatures with `raise NotImplementedError`.
- `scratch/` folder exists for Phase 1 experiments (see scratch/README.md).

---

### Suggested barebones-first build progression

Each session = one feature, progressed through phases 1 → 2 → 3.

**Session 1 — Fetch teams (a single API call, end to end)**

- *Phase 1:* In `scratch/01_fetch_teams.py`, write a 5-line script that imports
  nba_api, calls `teams.get_teams()`, and prints the results.
- *Phase 2:* Move into `app/ingestion/nba_client.py::fetch_all_teams()`. Add a
  simple upsert into the `teams` table via SQLAlchemy session. Call it from
  `app/ingestion/jobs.py::ingest_teams()`.
- *Phase 3:* Add error handling for API failures, a unit test for `nba_client`
  using a mock, and idempotency (running twice doesn't duplicate).

**Session 2 — Fetch players and games for one season**

- Similar phased progression. End state: you can run
  `python -m scripts.run_ingestion --season 2024-25` and have your DB populated
  with one season of teams, players, and games.

**Session 3 — Box scores and the stat history function**

- Pull per-player box scores. Then write the function that, given a `player_id`,
  returns their `recent_avg`, `season_avg`, `vs_opponent_avg` — feeding into
  the predictor.

**Session 4 — Wire the prediction endpoint to real data**

- Update `app/api/predictions.py` so `/predictions/{player_id}/{game_id}` returns
  real numbers. **The project becomes demoable here.**

**Session 5 — The backtest endpoint**

- For a past date, re-run predictions and compare to actuals. Compute MAE per
  stat. This is the killer interview demo feature.

**Session 6 (optional, big resume payoff) — Kafka layer**

- Add producer/consumer for live in-game updates. Now the Kafka resume claim
  is airtight.

---

### Today's plan

[Pick ONE of: walk through the scaffold | start Session 1 Phase 1 (the
throwaway script) | something else. Tell me which.]

---

## Tips for using this primer (don't copy this section — for your reference only)

- **Update this file as the project evolves.** When you finish Session 1,
  edit the "Current state" section to reflect what's now built, and "Today's
  plan" to point at the next thing. The primer should always describe *where
  you are*, not where you started.
- **Reference this file directly in chat.** Instead of pasting the whole thing
  every time after the first, you can say "read CONTEXT_PRIMER.md and let's
  continue from where we left off."
- **Don't relax the "How I want you to work with me" section.** That's the
  contract that keeps you learning. If you start letting Claude generate
  larger blocks because it's faster, you'll end up with a working project you
  can't fully defend in interviews.
