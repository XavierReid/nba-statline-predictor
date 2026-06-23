# Context Primer for a New Conversation

Copy the block between the `---` markers below as the first message in a fresh
Claude conversation to bring the new agent up to speed.

---

## The primer (copy from here)

I have a Python/FastAPI backend project at
`/Users/xavierreid/Desktop/projects_2026/nba-statline-predictor/`.

**Important: the project is pivoting direction.** The original idea was a
statline predictor (and the existing repo name and predictor service reflect
that). The new direction is an **NBA Franchise Simulator** — closer in spirit
to MyLeague mode in NBA 2K. I want to simulate seasons (games, standings,
playoffs), and later add trades, draft, multi-year player progression. No
graphics or game-engine polish — just a backend simulation engine with REST
APIs.

A production-grade scaffold (FastAPI, SQLAlchemy 2.0, Alembic, Docker Compose,
GitHub Actions CI, pytest) is in place and pushed to
https://github.com/XavierReid/nba-statline-predictor. **The teams ingestion
has been implemented** (see `app/ingestion/jobs.py` and `nba_client.py`);
the predictor service in `app/services/predictor.py` is currently working
code but no longer matches the new direction — we'll be replacing it with
a game simulator and season simulator.

Note: the GitHub repo name still reflects the old "predictor" framing. I may
want to rename it to `nba-franchise-simulator` or similar. Worth discussing
how to handle that without breaking the resume link.

**Before answering**, please read `README.md` and skim the file tree so you
understand what's already built. Then read this primer end-to-end before
proposing what to do.

---

### About me

- Backend engineer, 5+ years pro experience (mostly Java + Spring Boot at LinkedIn).
- Between roles after the mid-2025 LinkedIn layoff round; actively job searching
  for mid-level backend / full-stack roles.
- Python-first mental models right now — not translating from Java. Unfamiliar
  with production project structure (how layers compose), not the language itself.
  Don't over-explain Python syntax; do explain SQLAlchemy 2.0 patterns, FastAPI's
  Depends, Pydantic Settings, and similar framework-specific idioms.

---

### Goals for this project

1. **Backstop my resume's Kafka claim.** I want to be able to honestly say
   "I designed and built a Kafka producer/consumer end-to-end" by the time
   v2 is done.
2. **Demonstrate clean backend engineering** — schema design, REST API design,
   data pipelines, state machines, event-driven systems.
3. **Understand every piece of the code I ship.** Not just what it does, but
   why. I will be defending this in interviews.
4. **Build something I find genuinely interesting.** The simulator pivot
   matters here — I'll actually finish it because I want to see "who won my
   simulated 2025-26 season."

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

I want a pragmatic collaboration, not strict learn-by-writing. Generate code
freely for scaffolding, boilerplate, and routine implementation — but make
sure I understand it before we move on.

1. **Generate code when it's faster, but explain it.** You can write whole
   files when it makes sense (models, route handlers, test files, etc.).
   When you do, walk me through the key parts in a few sentences so I'm not
   blindly copying.
2. **Explain "why" before "how" for design choices.** Before introducing a
   new pattern (event-driven flow, state machine, schema decision), discuss
   the trade-offs. I should be able to defend every architectural decision
   without you present.
3. **For *core* simulation logic, slow down.** The game simulator, season
   simulator, and any prediction/decision code is what I'll defend in
   interviews. For those specifically, walk me through the design step by
   step, and either let me write the code or explain every line of generated
   code so I can defend it. Everything else (data plumbing, ingestion,
   migrations, API stubs) — generate freely.
4. **Start each feature with the simplest version that works.** Don't pre-empt
   complexity. If 5 lines work, that's the right answer for v1 — even if
   you'd add more in production. Add layers when I feel a real pain.
5. **Stay focused per session.** Don't try to do ingestion + simulator + API
   + tests in one sitting. Pick a logical chunk, finish it cleanly with
   tests, commit, and stop.
6. **Ask me "what if…?" questions.** What if the API times out? What if two
   ingestion jobs run concurrently? What if the season changes mid-run? Make
   me think about edge cases — these are interview gold.
7. **End every session with three deliverables:**
   - Green test run (`pytest`)
   - Git commit with a clear message
   - Update to the "Current state" and "Today's plan" sections of this primer

If I push back wanting to write something myself, hand me the keyboard. The
default is your generation, the exception is my hand-coding the parts I
want to own.

---

### Token / context efficiency rules

Long Code-tab sessions chew through context. Stick to these:

1. **No preamble, no recap, no apologies.** Answer the question and stop.
2. **Don't quote my code back to me.** If you changed something, say what you
   changed in one sentence — don't paste the file back.
3. **Use Edit, not Write.** When modifying existing files, prefer the Edit
   tool (sends diffs only) over Write (sends the whole file).
4. **Don't re-read files unless something likely changed.** Trust your prior
   read of a file in the same conversation.
5. **Minimal formatting.** Use prose paragraphs. Headers and bullets only
   when they materially help comprehension. No decorative emojis.
6. **One question at a time.** Don't ask 3 clarifying questions in one turn.
7. **Don't restate the plan.** If we agreed on the next step, just do it.
8. **Show only what changed, not the whole file.** A 3-line patch doesn't
   need 100 lines of context.
9. **Skip the "let me know if you'd like" closers.** End on the action or
   stop. No filler.
10. **Match the depth of the question.** A yes/no answer doesn't need three
    paragraphs of reasoning.

**Session-level habits that save context across sessions:**

- **Update the "Current state" and "Today's plan" sections of this primer**
  at the end of every session. Context lives in the file, not the chat.
- **Use git commits as memory.** Clear commit messages mean future sessions
  can `git log` to understand what's been done — no need to re-explain.
- **End each session with a clean state.** Don't leave dangling half-implemented
  features that require chat-history context to understand.

If I ever say "be more concise" mid-session, immediately tighten further.

---

### Current state of the scaffold

- Docker Compose brings up Postgres + the FastAPI app on `docker compose up`.
- Block 1 complete: 30 teams, 530 players, 1225 games ingested.
- Migrations 0001 + 0002 applied.
- 2024-25 season stats ingested (431 players). PlayerAttributes + PlayerTendencies seeded.
- RatingEngine live: percentile-based, volume-weighted, position-adjusted defaults, position-specific overall weights, non-linear overall curve. See RFC.md for full design rationale.
- Rating validation passed: Jokić 94, Wemby/Luka/Tatum 86-87, bench 65-74.
- 8 RatingEngine + health tests passing.
- Python 3.9 — use `Optional[X]` not `X | None`.
- RFC.md is the source of truth for design decisions. Read it before proposing changes.

**Simulator requirements aligned on (2026-06-02):**
- Box-score level simulation (player stat lines per game, not just final score)
- Real NBA rosters seeded from `nba_api`
- Flexible scope: full season, playoffs, single series, single game
- One active simulation at a time to start; results persisted and referenceable
- Long-tail (out of scope for now): trades, draft, contracts, salary cap

---

### Suggested barebones-first build progression (post-pivot)

Each session = one feature, progressed through phases 1 → 2 → 3.

**Done already (predictor era — keep the ingestion, discard the prediction logic later):**
- `fetch_all_teams()` is implemented and persists to Postgres
- Teams model updated with `city` and `nickname` columns

**Session 1 — Fetch players + season schedule**

- *Phase 1:* In `scratch/02_fetch_players.py`, get the list of active players
  from `nba_api.stats.static.players.get_active_players()` and print it.
- *Phase 2:* Move to `app/ingestion/nba_client.py::fetch_all_active_players()`
  and the corresponding `ingest_active_players()` job. Same for the game
  schedule for a chosen season.
- *Phase 3:* Error handling + idempotency + unit test with a mock.

**Session 2 — Single-game simulator (the heart of v1)**

- Build `app/services/game_simulator.py`. Given two team rosters and a date,
  return a simulated final score plus simulated box-score lines.
- Phase 1 prototype: weighted-coin-flip outcome based on combined team strength
  (use averages of player stats). 30 lines, no fanciness.
- Phase 2: move into the scaffold, write tests against synthetic rosters.
- Discuss interview-worthy questions: how do you model team strength? How
  random is too random? How explainable should the result be?

**Session 3 — Full-season simulator**

- Build `app/services/season_simulator.py` that runs all 82 games for all 30
  teams against a real schedule. Persist results to a `simulated_games` table
  (separate from real `games`).
- Phase 1: hardcode the schedule loop, persist results.
- Phase 2: add a `seasons` parent table so multiple sim runs can coexist.
- Phase 3: standings calculation + playoff bracket generation.

**Session 4 — REST endpoints + first demoable artifact**

- `POST /simulations/seasons` — kick off a new season sim, return the
  simulation ID
- `GET /simulations/seasons/{id}` — return final standings + bracket + champ
- `GET /simulations/seasons/{id}/games` — list simulated games
- **The project becomes demoable here.**

**Session 5 — Multi-season + player aging (v2 territory)**

- Add `player_seasons` table tracking per-season stats and age.
- Simple linear aging curve: peak years 25-29, decline after 30.
- Rookies enter via random team assignment each year (skip real draft logic).
- Free agents move randomly at season end.

**Session 6 (optional, big resume payoff) — Kafka layer**

- Producer emits events: `season.started`, `game.completed`,
  `season.completed`, `champion.crowned`.
- Consumer updates per-team and per-player stats in near-real-time.
- Now the Kafka resume claim is airtight.

**Out of scope for v1/v2 (don't get sucked in):**
- Real draft with team-need logic
- Trade evaluation / negotiation
- Salary cap modeling
- Player development curves
- Coaching effects, injury simulation, etc.

These are MyLeague's depth — beyond what's needed for a portfolio piece.

---

### Today's plan

1. Build migration 0003: Lineup, SimulationRun, SimulatedGame, SimulatedPlayerLine models
2. Build GameSimulator — possession-based (see RFC.md for design). Start with Phase 1 in scratch/ before moving into app/services/.

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
