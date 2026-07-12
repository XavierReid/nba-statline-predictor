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

## Running it locally

**You'll need:** Docker + Python 3.9+

```bash
docker compose up -d postgres              # start the database
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head                       # set up tables
python -m scripts.run_ingestion --season 2024-25   # pull real NBA data
uvicorn app.main:app --reload              # start the API
```

Simulate a single game with full play-by-play from the command line:

```bash
python scratch/03_game_simulator.py BOS LAL 7 2025-26 --pbp
```

---

## Where it's headed

**Done:** real-data ingestion · data-grounded player ratings · a possession-based game engine with clock, rotations, overtime, and late-game strategy · full-season simulation · a REST API · and a calibration suite that holds the whole thing to real NBA numbers.

**Next:** finishing a clean, readable engine architecture (so new basketball systems slot in easily), then richer realism — team offensive identity, coaching tendencies, and eventually multi-season play with player development.

Deeper docs for the curious: [`ARCHITECTURE.md`](ARCHITECTURE.md) (how it works) · [`RUNBOOK.md`](RUNBOOK.md) (commands & tools) · [`SIMULATION_GAPS.md`](SIMULATION_GAPS.md) (the calibration detective work).

---

*Built by Xavier Reid — [GitHub](https://github.com/xavierreid) · [LinkedIn](https://www.linkedin.com/in/xavier-reid-246814115/)*
