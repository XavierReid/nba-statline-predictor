# Simulation Gaps & Limitations

Structural review of the game engine vs real NBA basketball, written at the close of M3
(M3a–M3e complete). This is the working document for the post-M3 calibration diagnostic:
each gap gets a status that moves through `open → investigating → confirmed/dismissed → fixed`.

## MEASURED CALIBRATION TARGETS (source of truth — 2026-07-08)

All memory-based references are RETIRED. These are measured from ingested real data:
final scores (`games`), quarter line scores (`ingest_line_scores`, 1,190 games 2024-25),
and league shooting totals. Re-measure when a new season is ingested.

**Season-level (2024-25 measured / 2025-26 measured):**

| Metric | 2024-25 | 2025-26 |
|---|---|---|
| Avg team score | 113.8 | 115.6 |
| Avg |margin| | 12.71 | 13.3 |
| Blowout (20+) | 20.9% | 22.9% |
| Close (≤5) | 25.2% | 24.5% |
| League FT% | 0.780 | 0.783 |

**Quarter dynamics (2024-25 line scores; retired memory values in strikethrough spirit):**

| Target | Measured | Previously assumed |
|---|---|---|
| Q1 \|margin\| (σ) | **6.88 (8.61)** | ~5.5-6 (WRONG) |
| Q2 / Q3 \|margin\| | 9.21 / 11.58 | — |
| End-of-regulation \|margin\| | 12.39 | — |
| Per-team quarter scoring σ | 6.09 | — |
| Regulation ties (OT rate) | **4.8%** | ~6% (WRONG) |
| Within 5 entering Q4 | 30.4% | — |
| …of those: tie / widen 6+ | 8.0% / **44.8%** | ~14% / ~40% ("40% pull-away" was real, not a bug) |

**Q4 transition deltas (entering bucket → Q4 |margin| change) — the Q4 calibration target:**

| Entering Q4 | Real Δ\|m\| |
|---|---|
| 0-5 | +3.35 |
| 6-10 | +1.16 |
| 11-20 | **−1.12** (real compression) |
| 21+ | **−0.84** (real compression) |

---

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

## MILESTONE: cross-era scoring reconciliation (2026-07-14) ✅

The `app/analysis/` accounting layer decomposes the scoring equation into named
basketball behaviors (shot mix, zone FG%, PPP, FTA/TOV/OREB rate) so every residual
has an owner instead of "the engine scores too much". Validated by schedule replay +
possession decomposition across three generations, each fix making a behavior EMERGE
from era data rather than a constant:

| pts/game vs real | 1996-97 | 2005-06 | 2025-26 |
|---|---|---|---|
| before | +5.2 | +7.3 | −0.3* |
| **after** | **+0.4** | **+1.5** | **−1.2** |

*modern was only "exact" via canceling errors (over-efficiency masking under-FGA).

Fixes (all commits on `main`): observed zone-FG% make model; observed non-rim shot
split (replaced 0.4 constant); second chances additive not folded into pace budget;
defense penalty centered on the defending lineup, not a fixed 50; `three_point_rate`
falsy-zero (real 0.0 → 30%); `team_defense_factor` era-league def_rating (not the
modern 113); `oreb_pct` dropped on team-stats re-ingest. Every defect was the same
class — an era-invariant constant or silent data gap distorting old vs new.

**Known second-order limitation (NOT chased — overfitting risk, no clean owner):**
uniform three-point efficiency ~+0.013 from the home_bonus (asymmetric +home/0-away)
× contest interaction on the observed base.

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
**Status:** FIXED (2026-07-08)
**Suspected impact:** OT rate (indirectly), OT realism (directly)

**Fix:** quarter loop extracted into `_run_clock_period(q_idx, period_seconds, tip)`;
OT is now another timed period (300s, new jump ball, closing lineups via the minute-47
clamp) running identical mechanics — modifiers, strategic fouls (final-period guard
`q_idx >= 3`), fast breaks, second chances, foul escalation, possession accounting.
Legacy fixed-possession OT survives only for non-clock mode. Verified: real clock
consumption in OT, strategic fouls firing in OT, schedule replay identical to
`attr-v2-baseline` (no regulation regression).

The OT loop (`game_simulator.py`, "Overtime" section) is a fixed 20-possession
alternating loop with no clock and **no modifier adjustments at all** — no momentum,
fatigue, clutch, team defense, strategic fouls, fast breaks, second-chance possessions,
or M3e late-game foul escalation. It is effectively the pre-M1 baseline engine for
5 minutes, using the minute-47 lineup.

**Fix direction:** make OT a fifth iteration of the regulation quarter loop with a
300-second clock. Unlocks every clock-gated feature in OT.

### 1.2 No end-game margin compression
**Status:** COMPLETE FOR DEFINED SCOPE (2026-07-08) — late-game realism delivered;
remaining blowout excess explicitly re-assigned to gap 2.1 (see closure note below)
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

**Closure (2026-07-08):** implemented as incentive modeling, not outcome targeting —
`late_game.py` provides a centralized `LateGameContext` (final period, ≤120s, |margin| ≤ 8)
consumed by endgame pacing: trailing offense plays ~9s urgency possessions (possessions
over efficiency), leading offense milks ~20s (time over expected points); strategic fouls
intercept ~70% of leading-team possessions in-window. Catch-up pace multiplier skipped for
endgame-paced possessions (no double-shortening). Toggle `use_endgame_pacing` (on in
DRAMA_M3); `endgame` category added to possession accounting.

**Validation vs `attr-v2-baseline`:** close% 18.9 → 20.1 (target 24.5), tie conversion of
close-late games 9.2% → 12.2% (real ~14%), OT rate 2.7% → 3.7% (target 4-6%), strength
slope 0.88 → 0.91, scoring/home-win pinned. Converging for the right reasons.

**Negative experiment (documented to close the path):** widening the window
(`endgame_margin_max` and `strategic_foul_margin_max` 8 → 10 → 12) does NOT move blowout%
(26.7 flat at all widths) and slightly hurts close% and scoring. Blowout games are already
15+ entering the final 2 minutes — their margins were built over the first 46 minutes,
outside any compression mechanic's reach. Window stays at 8. **The remaining blowout
excess (26.7 vs 22.9) is owned by gap 2.1 (game-state-aware rotations)**: real teams
protect leads by changing personnel — stars sit in garbage time, bench units stabilize
margins. The sim currently models reduced effort but not reduced talent. Residual
early-game dispersion (Q1 margin 7.0 vs ~5.5-6) stays on the watch list: if blowouts
remain elevated after 2.1, the variance issue is earlier in games, not a missing mechanic.

### 1.3 Rich-get-richer feedback without counterweights
**Status:** FIXED — Attribute Derivation v2 + signal_gain=1.25 (top-10 strength slope 0.88).
Remaining blowout/close-game deficits are explicitly NOT stage B failures — they belong to
gap 1.2 (see closure note at the end of this section).
**Suspected impact:** blowout rate, avg margin

**Evidence:** Margin growth by quarter is 7.4 → 10.5 → 12.3 → 14.2, which is almost
exactly √t (random-walk) growth — so there is **no runaway positive feedback in
aggregate**. The real problem is the starting dispersion: |margin| at end of Q1 is
already 7.4 (real ~5.5-6). Per-quarter scoring variance between teams is too high
from the opening tip. Lead changes 6.0/game vs real ~9-10 corroborates. Focus shifts
from "momentum compounds" to "per-possession outcome dispersion too wide" —
possibly the same possession-count inflation as 1.4 (more possessions = more
variance accumulation), plus matchup strength gaps.

**Post-1.4-fix update:** Q1 dispersion eased 7.4 → 7.0 (fewer possessions = less variance
accumulation) but remains above the ~5.5-6 real target. Avg margin still 14.5 at 1000
games (real 13.3). Note: blowout rate is sample-sensitive (22.6% at n=500 vs 26.5% at
n=1000, ~2 SE apart) — use 1000+ game samples when measuring this gap. Next suspects:
multiplicative `team_defense_factor`, per-possession outcome variance, matchup strength
gaps in the calibration set.

**Root cause identified (schedule replay, 1225 real games × 4 sims):** the original
"rich-get-richer" hypothesis is REVERSED. Toggle isolation showed mirror matchups (team
vs itself) produce Q1 dispersion 5.3 — at the real level — so per-possession noise is
healthy; momentum contributes nothing measurable. The schedule replay then showed the
engine **compresses team quality**: real win% spread 58 pts (OKC 79% → WAS 21%), sim
spread 29 pts. Strength slope (sim regressed on real): win% 0.27 all / 0.57 top-10;
net margin 0.34 all / **0.66 top-10** — below the ~0.8 threshold that tanking/rest/trade
confounds could explain (bottom-10 slope 0.17 is confound-dominated; sim deliberately
plays full-effort rosters). Elite teams keep only ⅓-⅔ of their real point differential
(OKC +11.3 → +7.0, SAS +8.4 → +2.5, BOS +7.7 → +2.1).

Aggregate margins look right only because excess within-game noise substitutes for the
missing between-team signal — which is also why close games run 5.7 pts under real
(18.8% vs 24.5%) while blowouts run slightly over.

**Investigation complete — three-stage decomposition of the signal chain:**

```
real player stats
   │
   ▼  Stage A: rating derivation (rating_engine.py)
attributes 0-100
   │
   ▼  Stage B: probability mapping (possession.py)
per-possession probabilities
   │
   ▼  Stage C: aggregation (game loop)
game outcomes
```

| Stage | Verdict | Evidence |
|---|---|---|
| A — rating derivation | **MAJOR LEAK** | `close_shot`, `layup`, `dunk`, `perimeter_defense`, `interior_defense` are position-adjusted constants, never derived from data. Team-level stdev: dunk 0.00, close_shot/layup 0.59, perim_def 0.70, int_def 1.19 — vs 3.5-5.7 for data-derived attributes. Interior shots are ~55% of attempts and ALL individual defense runs through the dead attributes. |
| B — probability mapping | **ATTENUATOR** | +1σ (team-level) in every live attribute ≈ +1.0 pts/game through the possession model (three_point 0.34, FT 0.28, steal 0.22, mid 0.13, block 0.03, passing 0.00 — assist routing only). Team def_rating factor adds ~1.4/σ. Real team separation ≈ 5.5 pts/game per σ. |
| C — aggregation | **HEALTHY** | Sim net margin correlates 0.67 with the live-attribute composite — the game loop transmits what it's given; residual is def_rating/OREB/pace inputs + sampling noise. |

**Fix plan (ordered — A before B, C untouched):**
1. **Attribute derivation v2 (new milestone):** derive interior finishing from NBA shooting-split
   data (FG% by distance); derive individual defense from defensive matchup data
   (`LeagueDashPtDefend` defended FG%) or an interim proxy (team def_rating × position ×
   steals/blocks). Same percentile-curve pipeline as existing derived attributes.
2. **Stage B recalibration (after A):** widen `attr_to_prob` spans / defense penalty factors
   against measured targets (strength slope, FG% vs defender quality) — measured constants,
   not hand-set. Must trade off against dispersion: wider deltas push blowout rate up.
   Validation loop: schedule replay strength slope ≥ 0.8 (top-10 net margin) with close-game
   rate improving toward 24.5% and blowout/scoring holding.

Deliberately NOT pursued: touching stage C (usage weighting, rotations) — it tested healthy.

**Closure (2026-07-08) — post-Attribute-Derivation-v2 / signal-gain baseline:**

Stage A fixed: five dead attributes now derived from shot-location and defensive-matchup
data (team-level stdev 3.6-7.4, was 0.0-1.2; basketball sanity checks pass). Alone this
moved the top-10 net-margin slope 0.66 → 0.73.

Stage B fixed with a single global `signal_gain` stretching each shot's deviation from
measured per-sub-type league FG% anchors (scoring-neutral by construction; home_bonus and
modifier deltas excluded). Sweep on the full schedule replay:

| gain | top-10 slope | avg score | blowout% | close% |
|---|---|---|---|---|
| 1.0 | 0.73 | 116.3 | 25.6 | 19.1 |
| **1.25 (adopted)** | **0.88** | 115.9 | 26.7 | 18.9 |
| 1.5 | 0.96 | 115.7 | 27.2 | 18.2 |
| 1.75 | 1.10 | 115.6 | 26.8 | 18.2 |

The smooth, monotone slope response with scoring/home-win pinned confirms the architecture
is sound — offense and defense are not independently miscalibrated; the system was
transmitting quality too weakly. 1.25 adopted over higher gains deliberately: don't
calibrate today's system around tomorrow's missing features.

**Explicit note: the remaining blowout (+~3) and close-game (−~5.6) deficits are NOT
stage B failures.** They are gap 1.2 — no late-game compression mechanics exist
(clock-stopping fouls, urgency possessions, trailing-team strategy). Stronger team
differentiation without compression inevitably widens margins; the original funnel
diagnostic predicted exactly this. Do not split signal_gain into offense/defense factors
unless post-1.2 replays expose a genuine imbalance.

**Validation protocol for gap 1.2:** rerun the full replay against this baseline
(git tag `attr-v2-baseline`). Success = strength slope stays ≥0.8 while close% rises
toward 24.5 and blowout% falls toward 22.9 — converging for the right reasons.

**Fixed along the way (found by replay):** home advantage was ~0.03 pts/game instead of
~3 — `possession.py` divided the already-converted probability delta by 100 again. Sim
home win 50.7% → 56.6% after fix (real 55.4%). Also fixed: `calibrate_simulator.py` used
process-randomized `hash()` in seeds, making runs non-reproducible (now crc32).

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
**Status:** FIXED — validated at 1000 games (avg score 115.5 vs 115.6 real)
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

**Fix implemented (2026-07-07):**
- Generalized mixture compensation in `game_simulator.py`: halfcourt possession-time mean
  derived as `(target − f_sc·t_sc − f_fb·t_fb) / (1 − f_sc − f_fb)`; `f_sc` from actual
  team OREB rates per matchup, `f_fb` and catch-up clock fraction from measured constants
  (`SimConfig.fastbreak_poss_frac=0.026`, `catch_up_clock_frac=0.0026`, provenance documented)
- Strategic fouls deliberately uncompensated; possession accounting added
  (`result["possession_accounting"]`: counts/time by category, catch-up delta, pace budget)
- Accounting immediately caught a strategic-foul bug: fired in final 2 min of Q1-Q3
  (83% of games with sequences, mean 5.7). Q4-only guard added → 35.3% of games, mean 2.8 —
  plausible vs real NBA

**Validation (1000 games):** possessions 104.3 → 101.5/team (remaining ~+2 over budget is
legitimate strategic fouls + end-of-quarter truncation); avg score 115.5 vs 115.6 real ✓;
blowout 22.6-26.5% across runs (sample-sensitive — see 1.3); OT 2.0% (gaps 1.2/1.1 remain).
Side effect: Q1 margin dispersion 7.4 → 7.0, close-late pull-away 40% → 33%.

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
**Status:** COMPLETE FOR DEFINED SCOPE (2026-07-08) — garbage-time rotations + lineup quality
**Was:** open — promoted with primary target blowout 26.7% → 22.9%.

**Built (three iterations, each measured):**
1. *Symmetric benching* (rotation modes + `resolve_lineup`, hierarchy-based bench five,
   hysteresis 20-enter/12-exit): star minutes became realistic but margins froze at entry
   value — **negative result: symmetric benching preserves margins** (bench-vs-bench is
   neutral). Blowout unchanged.
2. *Asymmetric concession* (`late_game.should_concede` decision layer — leader concedes
   at 20, trailer holds until 28 or ≤4 min; Q3 extension at +5 margins): mismatch window
   25 poss/game, loser stars now outplay winner stars in blowouts (28.1 vs 27.5 min ✓),
   but mismatch margin delta stayed ~0 — the engine's starter/bench gap per possession
   was too small to compress.
3. *Lineup quality* (`lineup_quality.py` — defense factor from the five on the floor vs
   minutes-weighted rotation baseline; generic `compute_lineup_quality` interface for
   future offense/rebounding/spacing dimensions): instrumentation verified transmission
   (scheduled 0.999, range 0.937-1.067) and revealed **the real starter/bench gap is
   offensive, not defensive** — garbage lineups average just 1.007 on defense because
   benches carry defense-first role players. Mismatch delta finally negative (−0.2) but weak.

**Calibration outcome:** blowout 26.7 → 26.3 (directionally right, within noise);
top-10 slope 1.03 (lineup defense added differentiation — flag: signal_gain may warrant
a small reduction at the final calibration pass); close 19.9; scoring/home-win held.

**Verdict per the pre-registered protocol:** rotation behavior is now realistic and
blowouts did NOT come down materially → **the residual blowout/close excess is a genuine
early/mid-game dispersion issue** (Q1 |margin| 7.0 vs ~5.5-6 real), not a missing
late-game or rotation mechanic. Promote the watch-list dispersion item to the next
calibration investigation (after the cleanup/documentation phase).

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

## Gap 3.1 — Q4 objective shift (identified 2026-07-08; the last calibration gap)

**Status:** IMPLEMENTED (2026-07-09) — Team Objectives; residual blowout re-attributed to
signal_gain over-separation (see closure below)
**Owns:** close-game deficit (improved), Q4 margin compression (fixed for 11-20)

**Evidence trail:**
1. Real line scores (1,190 games) show sim Q1-Q3 dispersion ≈ real basketball
   (Q1 6.88/8.61 real vs 7.10/8.62 sim). The "early-game dispersion" hypothesis is dead —
   it was an artifact of a wrong memory-based target.
2. Real Q4 compression is margin-proportional and monotone (+3.35/+1.16/−1.12/−0.84 by
   entering bucket); sim is non-monotone and wrong at both ends (+5.26/+1.43/−0.09/+1.75).
3. Toggle tests EXONERATE garbage rotation, lineup quality, team defense, and the
   garbage-time modifier — none moves the bucket deltas materially.
4. Possession decomposition: in close Q4 games the sim's LEADING side is more efficient
   (1.136 vs 1.079 PPP) — the catch-up modifier nets out as a trailer penalty (+2% TOV,
   lower-efficiency shot mix) while the leader pays no clock-priority cost until 20+.

**Root cause (behavioral, not mechanical):** both teams keep maximizing expected points
per possession all through Q4. Real teams switch objectives as win probability shifts:
the leader maximizes win probability by valuing clock over efficiency (late-clock
possessions, conservative selection, fewer transition attempts); the trailer maximizes
possession count and variance. Frame as OBJECTIVES, not buffs/nerfs — decisions emerge
from what each team is optimizing.

**Implementation (2026-07-09) — first Behavior-Engine citizen (ARCHITECTURE_ROADMAP.md):**
`late_game.derive_objective` (game state → PROTECT/CHASE/NEUTRAL + intensity) and
`objective_adjustments` (intention → behavior), replacing `CatchUpModifier`
(`use_team_objectives`, on in DRAMA_M3). Intensity scales with margin and elapsed period.

**Key findings during build:**
- Behavior-first (shot-selection shift) BACKFIRED: cutting a protecting team's three rate
  pushes shots into the mid/close split, and close = layups/dunks = the most efficient
  shot — so "conservative" RAISED efficiency, opposite of real basketball. The engine's
  coarse buckets can't express "worse late-clock shots" via selection. Per pre-authorized
  fallback, PROTECT now carries an explicit clock-priority efficiency cost
  (`protect_efficiency_cost`, shot_prob_delta < 0). CHASE stays tempo-only, efficiency-neutral.
- Objectives and garbage rotation are mutually exclusive: a CONCEDED team (bench in) goes
  NEUTRAL — scrubs don't run a strategy. This produces the real non-monotone curve (peak
  compression at 11-20, eased at 21+ once benches enter).

**Result (schedule replay + q4_diagnostics):** 11-20 transition delta −1.35 vs real −1.12
(fixed, was −0.09); close-game rate 20.1% → 21.9%. Q4-bucket tuning is noise-limited on the
fixed matchup set — parameters set at principled values, validated on the 1,225-game replay.

**Residual blowout re-attributed (RESOLVED the attribution):** blowout% stayed ~26 because
objectives move games within the 6-18 range, not the 20+ mass. Signal-gain re-sweep
(2026-07-09) settled it: gain 1.0 restores slope 1.07 and close% 23.2, but **blowout% is
invariant to gain (~26 at 1.0/1.10/1.15/1.25)** — so blowout is NOT team over-separation.

---

## Gap 3.2 — Competitive-Q4 variance (REFRAMED — STRUCTURAL, not behavioral)

**Status (2026-07-09):** the dispersion is fully isolated and the behavioral hypothesis is
DISPROVEN. Real competitive-Q4 point-differential variance is ~60.6 vs the sim's ~76
(+26%); the sim's Q1 variance already MATCHES real (73 vs 74), so per-possession randomness
is right — real basketball drops variance ~18% in competitive Q4 and the sim stays flat.

**Behavioral levers tested, all negative (instrument-first):**
- Ingested real 2024-25 clutch team splits (last 5 min, ≤5 pts) vs overall. Measured shifts:
  FTA/FGA **1.86×**, TOV **0.92×**, OREB **1.16×**, 3PA **flat (0.99)**, pace/PPP **flat**.
  So the naive fixes (fewer threes, slower pace) are ruled out by real data.
- Built the measured shifts into the `COMPETITIVE_LATE` BehaviorProfile and measured:
  bonus-foul boost 1.86× → variance ~unchanged (FT trips replace too few possessions);
  shooting-foul boost → variance WORSE (and-1s are 3-4 pt, high-variance); three reduction
  0.70-0.85 → no clean effect. **None of the measurable competitive-late behaviors reduce
  the sim's Q4 variance.**

**Conclusion:** the excess variance is STRUCTURAL to the per-possession shot-outcome model,
not a phase-behavior gap. Real's competitive-Q4 variance reduction comes from something the
current shot model can't express (contested-but-consistent execution / possession-structure
effects), not from a shot-mix or foul-rate shift. BehaviorProfile correctly owns the
*measured behaviors*; it is NOT responsible for this variance metric.

**Next work (separate investigation, do not use BehaviorProfile):** fresh instrumentation of
the shot-outcome model — start by decomposing per-possession points variance by shot sub-type
vs real, and check whether the sim over-produces high-variance outcomes (threes, and-1s)
relative to real in ALL phases (the gap only *shows* in Q4 because that's where real tightens).
Guardrail: the sim's Q1-Q3 variance already matches real — do not break it.

---

## Gap 3.2-OLD — Mid-game dispersion / games blowing open (SUPERSEDED by the above)

**Status:** open — owns the residual blowout excess (26 vs 22.9)
**Evidence:** measured Q2/Q3 dispersion runs slightly hot (Q2 sim 9.89 vs real 9.21, Q3
12.18 vs 11.58); blowout% invariant to signal_gain, so it is not team separation. The 0-5
Q4 over-growth (+6.06 vs +3.35 — no close-game rubber-banding) is likely the same root:
the sim lacks mean-reversion within a game, so mid-game runs over-extend. Candidate
mechanisms: a mild negative-feedback / run-stopper (timeout-like), or momentum tuning.
**Do not start** until instrumented the same way Q4 was (measure which quarters/game-states
create the over-extension before adding a mechanic).

---

## The Calibration Frontier (what "done" actually requires)

We have rigorously calibrated **team-level game outcomes**. We have NOT touched
**player-level or box-score realism**. 3.2 closes the game-outcome distribution work;
the frontier below is the remaining, largely-unaddressed calibration surface. Ordered by
value, not effort. Each item follows the engineering loop: instrument → measure vs real →
hypothesize → fix → validate.

### 3.2 — Mid-game dispersion (game-outcome distributions)
See section above. Owns blowout 26.1 vs 22.9, close 23.2 vs 24.5, avg margin 14.1 vs 13.3.
Closes out the margin/blowout distribution work.

### 3.3 — OT rate
Sim ~3.7% vs real (measured) 4.8%. Partly downstream of 3.2 (more close games → more ties
→ more OT). Re-measure after 3.2 before deciding whether an OT-specific gap remains.

### 3.4 — Player-level stat realism (MEASUREMENT PHASE COMPLETE 2026-07-14; fix not started)
All calibration to date is team-aggregate. Built `app/analysis/player_accounting.py` (third
analysis pillar) — per-player possession accounting with tier aggregation and Δ Points / Δ
Assists decompositions. Run across all 7 ingested eras. **ONE behavioral root: offensive load
is allocated too democratically.** Stars lose FGA + FT trips + assists to the bench and gain
turnovers; bench gains points every era. Universal, magnitude scales with era star-
concentration (star usage term -1.5 in 90s/00s → -3.2 in 2025-26). Assist deficit is
ALLOCATION not attribution (team AST/FGM only ~2-7% low). Address of the fix: `_select_action`
(possession.py) — ball-handler chosen by `usage_rate` weights that concentrate too weakly.
Proposed: a global usage-concentration transform (one measured constant, NO player bonuses),
validated by the 7-era tier reconciliation. See project-player-allocation-diagnosis memory.

### 3.5 — Team box-score aggregates
Team assists, rebounds, steals, blocks, turnovers per game vs real team averages. Only
partially spot-checked (OREB, TOV, FTA, 3PA). Never a full pass.

### 3.6 — Lead changes / game texture
Flagged earlier at ~6/game vs real ~9-10 and never revisited. Likely the same
no-mean-reversion root as 3.2 (fewer lead changes because leads don't rubber-band).
Measure alongside 3.2.

**Framing:** after 3.2/3.3 (game outcomes) the next real frontier is 3.4/3.5 (player &
box-score realism) — a category we have not started, and the one that most affects whether
individual stat lines feel like real NBA.

## Change log

| Date | Item | Action |
|---|---|---|
| 2026-07-07 | — | Document created at close of M3e |
| 2026-07-07 | Phase 1 | `scratch/diagnose_calibration.py` run (300 games): 1.4 confirmed (104.3 poss/team vs ~99), 1.2 confirmed (26.7% close-late, 7.5% tie conversion), 1.3 reframed (√t growth = no runaway feedback; Q1 dispersion 7.4 vs ~5.5-6 real is the issue) |
| 2026-07-07 | 1.4 | FIXED: mixture compensation (measured constants) + possession accounting + strategic foul Q4-only guard (accounting caught it firing Q1-Q3). Validated at 1000 games: score 115.5 vs 115.6 ✓ |
| 2026-07-07 | process | Adopted simulation-engineering loop: define → implement extensibly → instrument → validate vs real data → complete. Features aren't done until large-sample calibration confirms. `possession_accounting` is the seed of a first-class `SimulationDiagnostics` system. |
| 2026-07-07 | 1.3 | Root cause identified via `scratch/replay_schedule.py`: engine compresses team strength (top-10 net-margin slope 0.66 vs ~0.8+ explainable by confounds). Original rich-get-richer hypothesis reversed. Next: attribute curves, delta magnitudes, usage dilution. |
| 2026-07-07 | bugs | Fixed home advantage /100 units bug (was +0.03 pts/game, now ~+3; home win 50.7% → 56.6%). Fixed non-reproducible calibration seeds (`hash()` → crc32). |
| 2026-07-08 | 1.3 | Investigation complete via A/B/C stage decomposition: Stage A major leak (5 dead attributes = interior scoring + individual defense), Stage B attenuator (~1 pt/game per full-σ), Stage C healthy (corr 0.67). Fix: Attribute Derivation v2 milestone (spec in RFC), then Stage B recalibration. |
| 2026-07-08 | 1.3 | FIXED: Attribute Derivation v2 (shot-location + defensive-matchup data; slope 0.66→0.73) + signal_gain=1.25 (slope 0.88, scoring/home-win neutral). Baseline frozen as git tag `attr-v2-baseline`. Remaining margin-shape deficits assigned to gap 1.2, not stage B. |
| 2026-07-08 | 1.1 | FIXED: OT runs as a real timed period via `_run_clock_period` — all mechanics active in OT. No regulation regression vs baseline. |
| 2026-07-08 | 1.2 | COMPLETE FOR SCOPE: `late_game.py` LateGameContext + incentive pacing (urgency 9s / milk 20s). Close% 18.9→20.1, tie conversion 9.2→12.2%, OT 2.7→3.7%, slope 0.91. Negative experiment: window widening (8→10→12) does not move blowouts — margin built over first 46 min. Blowout excess re-assigned to gap 2.1 (promoted, target 26.7→22.9). Residual Q1 dispersion on watch list. |
| 2026-07-08 | 2.1 | COMPLETE FOR SCOPE: rotation modes + asymmetric `should_concede` decision layer (Q3-extended) + `lineup_quality.py`. Behavior verified (star minutes, loser-fights-longer, mismatch window 25 poss). Two documented negative results: symmetric benching preserves margins; defensive starter/bench gap is genuinely small (real gap is offensive). Blowout 26.7→26.3 only → residual excess is early-game dispersion (watch-list item promoted). Next phase: cleanup/docs, then dispersion investigation. |
| 2026-07-13 | — | Analysis pillar (`app/analysis/`): canonical PossessionAccounting (statistical possession = FGA−OREB+TOV+0.44FTA everywhere) + scoring decomposition. Multi-season Phase 2 (partial): era-aware interior derivation + box-score defense fallback. Pace hypothesis tested and REJECTED (a diagnostic bug, not the engine). |
| 2026-07-14 | MILESTONE | Cross-era scoring reconciliation (see milestone section above): observed zone-FG% make model, observed non-rim shot split, additive second chances, lineup-centered defense; + falsy-zero `three_point_rate`, era-league `team_defense_factor`, dropped `oreb_pct` on re-ingest. Result +0.4/+1.5/−1.2 across 1996-97/2005-06/2025-26 from +5.2/+7.3/−0.3. One engine, no era constants. |
