# Simulation Gaps & Limitations

Structural review of the game engine vs real NBA basketball, written at the close of M3
(M3a–M3e complete). This is the working document for the post-M3 calibration diagnostic:
each gap gets a status that moves through `open → investigating → confirmed/dismissed → fixed`.

**Calibration state at time of writing (DRAMA_M3, 500 games):**

| Metric | Real 2025-26 | Sim | Gap |
|---|---|---|---|
| Avg team score | 115.6 | 119.7 | +4.1 |
| Avg margin | 13.3 | 14.0 | +0.7 |
| Home win rate | 55.4% | 54.8% | −0.6 ✓ |
| Blowout rate (20+) | 22.9% | 25.2% | +2.3 |
| OT rate | ~6% | 1.2% | −4.8 (worst) |
| FTA/team/game | ~21.8 | 21.9 | ✓ |

**Diagnostic pipeline:** instrument → isolate → hypothesize → fix (each fix gets tests +
before/after calibration) → final M3 calibration pass at 1000 games.

---

## Engine structure (for orientation)

```
simulate_game
 ├─ rosters + per-minute rotation schedule (48 slots, minutes-driven)
 ├─ regulation: per-quarter clock loop, Gaussian possession times
 │    └─ resolve_possession: foul? → steal? → TOV? → shot type → block?
 │         → defender → shot prob → foul/assist/rebound
 ├─ modifiers adjust probabilities per possession (momentum, fatigue, etc.)
 └─ OT: fixed 20-possession loop, no clock, no modifiers
```

---

## Tier 1 — likely drivers of the calibration misses

### 1.1 OT is a different game engine
**Status:** open
**Suspected impact:** OT rate (indirectly), OT realism (directly)

The OT loop (`game_simulator.py`, "Overtime" section) is a fixed 20-possession
alternating loop with no clock and **no modifier adjustments at all** — no momentum,
fatigue, clutch, team defense, strategic fouls, fast breaks, second-chance possessions,
or M3e late-game foul escalation. It is effectively the pre-M1 baseline engine for
5 minutes, using the minute-47 lineup.

**Fix direction:** make OT a fifth iteration of the regulation quarter loop with a
300-second clock. Unlocks every clock-gated feature in OT.

### 1.2 No end-game margin compression
**Status:** confirmed (Phase 1, 300 games)
**Suspected impact:** OT rate (primary hypothesis), close-game rate

**Evidence:** Only 26.7% of games are within 5 entering the final 2 min (real ~40%+).
Of those close-late games, 40% see the margin *widen* to 6+ and only 7.5% reach a tie
(real tie-conversion of close-late games ~13-15%). Both stages of the OT funnel are
leaking: too few close games late, and too little convergence once close.

Real close games converge in the final minute: trailing team fouls to stop the clock,
made FTs consume zero clock, 2-for-1s, timeouts advance the ball, intentional quick 3s.
Sim possessions consume Gaussian time regardless of context — a 4-point game with 40
seconds left mostly just ends. Catch-up shifts shot selection and mildly shortens
possessions, but nothing models clock-stopping behavior. The margin distribution near
zero is thin as a result → few ties at the buzzer → low OT rate.

**Fix direction:** context-aware possession time in the final ~2 minutes (foul-stopped
clock ≈ 2-4s possessions for the leading team, quick-shot possessions for the trailing
team), FT possessions consume near-zero clock.

### 1.3 Rich-get-richer feedback without counterweights
**Status:** investigating — reframed by Phase 1 data
**Suspected impact:** blowout rate, avg margin

**Evidence:** Margin growth by quarter is 7.4 → 10.5 → 12.3 → 14.2, which is almost
exactly √t (random-walk) growth — so there is **no runaway positive feedback in
aggregate**. The real problem is the starting dispersion: |margin| at end of Q1 is
already 7.4 (real ~5.5-6). Per-quarter scoring variance between teams is too high
from the opening tip. Lead changes 6.0/game vs real ~9-10 corroborates. Focus shifts
from "momentum compounds" to "per-possession outcome dispersion too wide" —
possibly the same possession-count inflation as 1.4 (more possessions = more
variance accumulation), plus matchup strength gaps.

`team_defense_factor` multiplies the entire shot probability — a persistent edge on
every possession all game. Momentum adds positive feedback on top. Real games have
negative feedback the sim lacks: timeouts to stop runs, defensive keying on hot
players, effort asymmetry when leading. Everything pushing margins in the sim is
positive-feedback; nothing pulls back toward the mean mid-game (catch-up only fires
in late Q4).

**Fix direction:** instrument per-modifier margin contribution first. Candidate fixes:
timeout-like run-stopper, momentum cap tuning, converting team_defense_factor from
multiplicative to additive.

### 1.4 Possession count possibly double-counting extra possessions
**Status:** confirmed (Phase 1, 300 games)
**Suspected impact:** avg score (+4.1 unexplained with FG% and FTA both verified realistic)

**Evidence:** 104.3 possessions/team/game (p50 104, range 95-115) vs ~99 real.
+5.3 possessions × ~1.15 pts/possession ≈ +6 pts — more than the full scoring gap.
Fixing this alone may overshoot below target; expect interaction with 1.2/1.3 fixes.

**Root cause proven (toggle isolation, 100 games/variant):** The pace budget itself is
correct — with the four possession-affecting features off, the clock loop delivers
100.6 poss/team vs a 99.9 pace input. The inflation is entirely features layering
extra/shorter possessions on top of a budget that already includes them in real pace:

| Source | Contribution |
|---|---|
| strategic_foul (2-8s micro-possessions, no compensation) | +2.0 |
| fast_break (~7s vs ~14.5s halfcourt, no compensation) | +1.8 |
| second_chance (compensation exists but uses flat OREB_RATE, under-compensates) | +1.1 |
| catch_up (pace multiplier shortens trailing possessions) | +0.6 |
| base clock model | +0.7 |

**Agreed fix (hybrid, per review):**
- Fast breaks: analytically compensated — predictable, already inside historical pace
- Second chances: analytically compensated using actual team OREB rates, not the flat constant
- Catch-up: partially compensated (deterministic modifier)
- Strategic fouls: NOT compensated initially — state-dependent and concentrated in close
  games, so their possessions should emerge. Instead, instrument: intentional fouls/game,
  possessions created, avg possession length in foul sequences, % of games with sequences.
  Compare to real NBA; if frequency is unrealistic, fix the strategic foul model, not the pace model.

**Architectural principle (adopted):** every feature that affects possession count must
expose its contribution via possession accounting diagnostics — e.g. "pace budget 99.9,
base +0.7, fast breaks +1.8, …" — so each new mechanic justifies the possessions it adds.

`expected_possessions = round((home_pace + away_pace) / 2) * 2`, but OREB chains and
fast breaks add possessions on top. Real pace statistics already include second-chance
and transition possessions, so the sim may run more possessions than the pace input
implies. Scoring efficiency checks out (FG% by sub-type verified in M3d, FT volume
verified in M3e) — volume is the remaining suspect.

**Fix direction:** instrument actual possessions per simulated game vs the ~99/team
real figure. If inflated, derive the halfcourt possession budget net of expected
OREB/fastbreak extras.

---

## Tier 2 — realism gaps, weaker link to current metrics

### 2.1 Static rotation
**Status:** open

Minutes pre-assigned from season averages. Nothing responds to game state: no benching
starters in blowouts (garbage time changes probabilities but stars still play their
scheduled minutes), no riding the hot hand, no explicit closing lineups (minute-47
slot is implicit), no matchup-based subs. Blowout-benching in particular would
naturally dampen blowout margins (ties into 1.3).

### 2.2 Best-defender determinism
**Status:** open (deferred from M3d)

Steal checks always use the best stealer; block checks the best blocker from the full
defense (`possession.py`). M3d positional matchups select the shot defender but blocks
still ignore the matchup. RFC already flags this (line ~339).

### 2.3 No timeouts or end-of-quarter effects
**Status:** open

No 2-for-1 possessions, no buzzer heaves, no clock-stoppage semantics (FT/out-of-bounds
vs live-ball), no timeout ball advancement. Overlaps with 1.2 and 1.3.

### 2.4 Assists are decorative
**Status:** open

Flat 65% (three/mid) / 50% (close) assist probability, weighted by season assist rate.
No creation model (drive-and-kick vs iso), so playmaker impact on teammate efficiency
is absent. Matters for stat-line realism more than team-level calibration.

---

## Tier 3 — known, accepted simplifications

- No injuries or ejections (Phase 4 roadmap)
- No coaching identity / scheme (post-M3 TeamTendencies milestone)
- Player archetypes approximated by proxies (Phase 3)
- Uniform defensive attention — no double-teams or scheme attention on stars
- Foul-out replacement is next-best-by-minutes with no positional awareness

---

## Watch list (small items queued during M3)

| Item | Origin | Status |
|---|---|---|
| Q1 bonus-foul frequency looked high in one UAT sample (11.5% vs ~5% design) | M3e UAT | open — verify league-wide during instrumentation |
| StrategicFoul and M3e zone-2 escalation overlap in Q4 ≤120s close games | M3e code review | open — check for double-counted late FTs |
| `foul_draw_scale` default duplicated in `possession.py` and `SimConfig` | M3e code review | accepted — comment marks the sync requirement |
| Legacy non-clock path (`use_clock=False`) bypasses all clock-gated features | design | accepted by design |

---

## Change log

| Date | Item | Action |
|---|---|---|
| 2026-07-07 | — | Document created at close of M3e |
| 2026-07-07 | Phase 1 | `scratch/diagnose_calibration.py` run (300 games): 1.4 confirmed (104.3 poss/team vs ~99), 1.2 confirmed (26.7% close-late, 7.5% tie conversion), 1.3 reframed (√t growth = no runaway feedback; Q1 dispersion 7.4 vs ~5.5-6 real is the issue) |
