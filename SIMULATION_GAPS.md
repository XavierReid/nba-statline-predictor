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

**INSTRUMENT BUILT — OLD HYPOTHESIS FALSIFIED, OWNER NARROWED (2026-07-14,
`app/analysis/game_texture.py`, 4th analysis pillar).** Quarter-granular real-vs-sim from
line scores (`Game.home_qN` / sim `quarter_scores`), 2024-25, real n=1190 / sim n=2450.

**Headline result — the "structural per-possession variance" hypothesis is REJECTED:**

| quarter point-diff VARIANCE | Q1 | Q2 | Q3 | Q4 |
|---|---|---|---|---|
| real | 74.1 | 69.2 | 79.8 | **62.6** |
| sim | 69.4 | 75.0 | 71.3 | **74.1** |

If the possession model were globally too random we would see elevated variance in EVERY
quarter. Instead Q1 and Q3 sim run *below* real, Q2 is only slightly above, and the only
material divergence is Q4. That **largely exonerates the shot-outcome model as the owner**
(further confirmed by the sub-type view: three var ~2.1 vs two ~1.0 is 3-vs-2 arithmetic,
three share 0.39 plausible — nothing over-produced). The problem is not global
per-possession randomness; it is a **game-state behavior that emerges late in a game.**

**Careful with the framing — "mean reversion" is the STATISTICAL SIGNATURE, not the owner.**
Real basketball compresses in Q4 (variance 79.8→62.6, −22%; transition Δ|margin| negative at
entering-margin 11-20: real −1.12 vs sim −0.06) and the sim stays flat. But mean reversion is
an emergent property; the OWNER is whatever real basketball BEHAVIOR produces it. This project
models behaviors and lets statistics emerge (guardrails #1–#7) — do NOT implement "mean
reversion" directly. The candidate behavior: teams adapt to sustained runs, repeated empty
possessions, and offensive stagnation (coaches call timeouts, key on hot players, rest
starters when comfortably ahead, trailing teams raise effort) — a real adaptation, not
mystical momentum. It is BROAD across the second half (present at entering-margin 11-20), so
it is NOT owned by the clutch-window layers (endgame pacing / COMPETITIVE_LATE fire in the
final 2 min only).

**Architectural observation — the pipeline is MEMORYLESS.** GameState → GamePhase → Objectives
→ BehaviorProfile → PossessionContext → Decision: every possession knows score / clock / phase
but NOTHING knows how the game has been *evolving* (runs, droughts, changing flow). That reads
as a missing ABSTRACTION, not a missing modifier. If one emerges (working names GameFlow /
CompetitiveState) it must stay SEPARATE from `team_defense_factor` — that factor owns lineup
defensive quality, and mixing roster quality with game adaptation would blur responsibilities.

**Lead changes → promote to a first-class regression metric** (sim 6.8 vs real ~9.5). Q4
variance is one expression; lead changes are a direct behavioral outcome that is hard to fake
with tuning. If a future behavioral change raises lead changes toward real AND produces the Q4
compression, that is strong evidence we modeled something real rather than compensated
statistically. Track it alongside scoring / margin / blowout% / strength slope.

**RUN & DROUGHT ANALYSIS DONE (2026-07-14) — refines the owner from "magnitude" to
"response".** Ingested real scoring events via the NBA CDN play-by-play feed (new
`game_scoring_events` table, 1225 games; stats.nba.com playbyplayv2 now returns empty {}).
`game_texture.py --runs` builds scoring sequences for real and sim:

| run/drought | real | sim | diff |
|---|---|---|---|
| mean unanswered run (pts) | 3.42 | 3.33 | −0.09 |
| runs ≥6 / game | 8.88 | 8.48 | −0.40 |
| runs ≥8 / game | 3.60 | 3.27 | −0.33 |
| runs ≥10 / game | 1.45 | 1.23 | −0.21 |
| mean scoring drought (s) | 48.5 | 55.1 | +6.6 |

This FALSIFIES the literal "sim runs over-extend" reading — the sim makes slightly FEWER and
SMALLER runs with LONGER droughts, yet blowout% is higher. That invited a "runs go unanswered"
(response/sequencing) hypothesis, which the confirming instrument then TESTED AND FALSIFIED.

**RUN-RESPONSE HYPOTHESIS FALSIFIED (2026-07-14, `game_texture.py` response metrics +
phase split).** Lag-1 autocorrelation of 2-min windowed margins and answered-run rate
(after a ≥8-0 run, opponent ≥6-0 within 3 min), whole-game and split Q1-3 vs Q4:

| | real | sim |
|---|---|---|
| answered-run rate, Q1-3 | 0.200 | 0.172 |
| answered-run rate, **Q4** | 0.170 | **0.172** |
| lag-1 autocorr, Q1-3 | −0.079 | −0.087 |
| lag-1 autocorr, **Q4** | −0.214 | **−0.208** |

Both real AND sim strengthen mean-reversion in Q4 (−0.08 → −0.21) and the sim MATCHES real in
every phase. **The sim already answers runs at the right rate; scoring sequencing is not the
owner.** Run-response / memoryless-coupling is ruled out alongside per-possession variance,
run magnitude, and team_defense_factor.

**WHAT SURVIVES — a Q4-specific LEVEL effect (not a sequencing effect).** The robust,
un-falsified signal is the Q4 transition-delta table: the sim over-grows the Q4 margin by
~+1 pt at EVERY competitive entering-margin band, while real is more stabilizing:

| entering Q4 | real Δ\|m\| | sim Δ\|m\| | sim excess |
|---|---|---|---|
| 0-5 | +3.35 | +4.65 | +1.31 |
| 6-10 | +1.16 | +2.07 | +0.91 |
| 11-20 | −1.12 | −0.06 | +1.06 |
| 21+ | −0.84 | −0.54 | +0.30 |

Real Q4 is a net margin STABILIZER across 0-20; the sim keeps expanding. Note the STRUCTURAL
coverage gap: the sim's Q4 behaviors act only at ≤8 (COMPETITIVE_LATE objectives) and ≥20
(garbage rotation) — the 9-19 "comfortable but not garbage" band has NO Q4 behavior, and that
is exactly where the miss is largest (11-20: +1.06). This is a LEVEL/rate effect (how much the
leading vs trailing team scores in Q4), not a sequencing effect — consistent with sequencing
metrics matching while the differential variance/level differs.

**ROLE-SPLIT MEASUREMENT DONE (2026-07-14) — OWNER CONFIRMED + VALIDATION TARGET SET.**
Q4 points by team role (leading vs trailing entering Q4), by entering-margin band, real vs sim:

| entering Q4 | real lead / trail | sim lead / trail | real NET(L−T) | sim NET(L−T) | miss |
|---|---|---|---|---|---|
| 0-5 | 27.36 / 27.79 | 30.44 / 31.04 | −0.43 | −0.60 | −0.17 |
| 6-10 | 26.40 / 27.07 | 30.28 / 30.06 | **−0.67** | **+0.22** | +0.90 |
| 11-20 | 27.21 / 28.66 | 29.33 / 29.76 | **−1.46** | **−0.42** | +1.03 |
| 21+ | 27.24 / 28.15 | 28.69 / 29.23 | −0.91 | −0.54 | +0.36 |

**Confirmed owner: the real LEADING team eases its Q4 scoring, concentrated in the 6-20
"comfortable lead" band, and the sim does not.** Real NET(lead−trail) is negative in every
band (the trailing team outscores the leader in Q4 = the compression); the sim fails to
reproduce it and even flips the sign at 6-10 (+0.22). The miss is larger on the LEADING team
(11-20: leader +2.13 over real vs trailer +1.09), so the dominant generator is leading-team
Q4 easing, not trailing-team push. This sits exactly in the coverage gap between
COMPETITIVE_LATE (≤8) and garbage rotation (≥20).

**Secondary (level, not margin): sim Q4 is over-paced** — total Q4 points run +3 to +6 over
real per band (real Q4 ~55, sim ~60); real Q4 slows for BOTH teams and the sim doesn't. Not
the margin driver (both teams inflate together) but a texture note; full-game scoring still
reconciles, so sim Q1-3 must run correspondingly under.

**VALIDATION TARGET for the fix (design next milestone, NOT now):** reproduce real
NET(lead−trail) by band — ≈ −0.4 / −0.7 / −1.5 / −0.9 for 0-5 / 6-10 / 11-20 / 21+ — while
holding Q1-3, full-game scoring, and the sequencing metrics (autocorr, answered-run) that
already match. Mechanism: a whole-Q4 (not final-2-min) leading-team ease in the 9-20 band,
plugged into the GamePhase/Objectives layer (the memoryless-pipeline note). Do NOT touch
`team_defense_factor`; do NOT reach for per-possession variance or a run-response coupling
(both falsified). **Gap 3.2 measurement phase COMPLETE — owner identified, target set.**

**FIX IMPLEMENTED (2026-07-14) — comfortable-lead Q4 PROTECT.** The objectives layer already
spanned 6-20 (derive_objective fires PROTECT for any Q4 margin ≥6) but at clutch strength it
was far too weak there. Rather than retune the shared ≤8 clutch constants (gap 3.1), added a
SEPARATE stronger PROTECT regime for the comfortable band (margin > competitive_late_margin,
i.e. 9-20): `objective_adjustments` selects `comfortable_lead_efficiency_cost` (0.22) /
`_three_shift` (0.12) / `_pace_bonus` (0.16) when margin_abs is comfortable, else the clutch
constants. Pure GamePhase/Objectives-layer change; possession engine untouched; the leading
team trades aggression for worse shots + fewer threes + milked clock, and the compression
EMERGES. Leading-team-only (CHASE unchanged — the miss was the leader). Swept against the NET
target (`scratch/q4_role_split.py`). Results (2024-25, sim n=2450 vs real n=1190):

| metric | before | after | real |
|---|---|---|---|
| Q4 Δ\|m\|, enter 6-10 | +2.07 | **+1.26** | +1.16 |
| Q4 Δ\|m\|, enter 11-20 | −0.06 | **−1.13** | −1.12 |
| Q4 Δ\|m\|, enter 21+ | −0.54 | −0.97 | −0.84 |
| blowout% (20+) | 25.1 | **22.0** | 20.5 |
| close% (≤5) | 24.8 | **25.3** | 27.2 |
| Q4 diff variance | 74.1 | **69.6** | 62.6 |
| Q4 \|margin\| | 13.85 | **13.18** | 12.39 |

The core target — Q4 transition deltas at 6-10 and 11-20 — now MATCH real. Blowout excess cut
from +4.6 to +1.5; Q4-variance excess roughly halved. **Guardrails HELD:** Q1-3 variance
unchanged (69/75/71), runs/drought/autocorr/answered-run unchanged (sequencing intact), 296
tests green, full-game scoring not worsened (the fix only removes points; the residual
2024-25 sim +3.7 over real is the pre-existing modern over-scoring, a separate gap, and the
fix nudged it slightly down). **Partial residuals (honest):** (1) Q4 variance improved but not
fully closed (69.6 vs 62.6); (2) **lead changes did NOT move (6.8 vs 9.5)** — this Q4
comfortable-lead behavior cannot create lead changes (a 12→8 lead never crosses zero). At the
time this was read as a 6.8-vs-9.5 residual owned elsewhere — but see the 3.6 update below:
that "9.5" was a bad anchor and the residual is not real. Gap 3.2 blowout/Q4-compression:
SUBSTANTIALLY CLOSED.

---

## Gap 3.2-OLD — Mid-game dispersion (FOLDED INTO 3.2 above)

**Status (2026-07-14):** the "sim lacks within-game correction" hypothesis is now the
surviving direction (Q1-Q3 variance matches real, only Q4 fails to compress). Folded into gap
3.2. (The lead-change "6.8 vs 9.5" once cited here was a bad anchor — see 3.6, dismissed.) The
owner is deliberately left UNNAMED pending run/drought
instrumentation — it is a late-emerging game-state adaptation behavior, to be modeled through
a new game-evolution abstraction (not `team_defense_factor`, not a direct variance knob).

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

### 3.3 — OT rate — RE-MEASURED (2026-07-14): a distinct TIE-GENERATION owner, NOT downstream of 3.2
Sim OT rate **1.92% vs real 4.8%** (2024-25, n=2450) — still ~2.5× under after the 3.2 close-game
improvement, so NOT downstream of 3.2. Diagnosis via end-of-regulation margin distribution:
`|margin|≤2` is **12.0% (matches real ~12–13%)** — the sim produces the right number of
ultra-close finishes — but it lands on an EXACT tie only 1.92% (real 4.8%), with margins of 1–2
correspondingly too high (4.5% / 5.6%). So the close-game DISTRIBUTION is right; the sim just
doesn't spike at zero. **Owner: end-of-regulation tie generation** — real trailing teams shoot
specifically TO TIE in the final possession (down 3 → a three; down 2 → a two), producing a
spike at margin 0; the sim plays generic possessions and lands at 1–2 instead. A clean,
self-contained future milestone (endgame shot-selection keyed to the exact deficit), distinct
from the close-game distribution work. Not started.

### 3.4 — Player-level stat realism (DECOMPOSED into independent owners; measured via `player_accounting.py`)
All prior calibration was team-aggregate; team scoring can be exactly right while the
distribution ACROSS players is wrong. Built `app/analysis/player_accounting.py` (third analysis
pillar) — per-player possession accounting with tier aggregation (star/primary/secondary/
rotation/bench by real usage rank) and Δ Points / Δ Assists decompositions, run across all 7
eras. The instrument turned one vague "player realism" item into independent behavioral owners,
each with its own validation harness (the 7-era tier reconciliation):

- **3.4a Offensive possession ownership — ✅ DONE (2026-07-14, commit c477d2b).** Root: ball-handler
  chosen by *linear* `usage_rate` weights allocated load too democratically (stars under, bench
  over, every era). Fix: `usage_rate ** usage_concentration`, one global constant, DRAMA_M3 γ=1.6.
  Swept: zeroes the oldest era, reduces star-under bias in every era, over-corrects none, team
  scoring still reconciles. Star Δpts e.g. 2016-17 -3.51→-0.42. Scope deliberately NOT expanded —
  over-cranking γ just trades one residual for another; the modern residual has *other* causes.
- **3.4b Turnover ownership — ✅ DONE (commit 1ba1e07).** Sim INVERTED the turnover economy
  (stars 0.17-0.18/used-poss vs real flat ~0.12) because unforced TOV used TOV/36 (volume) as a
  per-possession rate. Fix: observed `tov_per_poss` derived at load; one anchor `tov_scale=0.9`.
  Economy now flat, team TOV reconciles. → CLAUDE.md guardrail #7 (per-opportunity not per-36).
- **3.4c Playmaker / assist generation — ✅ DONE (commit 1f4bf82).** Root: assist was a random
  post-make draw among non-shooters (lead creators capped ~5.9 ast/36). Fix: a possession INITIATOR
  role (assist-weighted, independent of shooter); assisted make credits the initiator, self-creates
  unassisted; rate re-derived 0.85/0.66 to hold AST/FGM ~0.60. Playmaker share now within 1-2% of real.
- **3.4d Player game-to-game variance — BLOCKED ON DATA (2026-07-14).** Does a 25-ppg scorer
  have realistic per-game spread (ties into `player_variance`)? Cannot be measured: real
  per-game player lines are NOT ingested (PlayerSeasonStats is season averages only). Needs a
  `PlayerGameLog` / BoxScoreTraditional ingestion (a data pull like the PBP feed for 3.2) before
  the sim's per-game player-stat distribution can be compared to real. Decision pending.

See project-player-allocation-diagnosis memory.

### 3.5 — Team box-score aggregates — MEASURED (2026-07-14): steals & blocks the owners
Full league-level per-team-game pass via `app/analysis/team_boxscore.py`. Real from
PlayerSeasonStats totals; that table is rotation-filtered (431 players, ~8% short on
roster-games), so non-scoring totals are scaled by the completeness factor derived from
the ONE complete+accurate stat, team points from the Game table (×1.083). Adjusted real
then matches known 2024-25 averages. Sim from a schedule replay's box scores (n=2450):

| stat | real (adj) | sim | sim/real |
|---|---|---|---|
| assists | 26.2 | 26.0 | 0.99× ✓ (3.4c initiator holds) |
| points | 113.8 | 116.7 | 1.03× (known modern over-scoring) |
| turnovers | 13.4 | 14.7 | 1.09× |
| rebounds | 44.0 | 38.3 | **0.87×** |
| **steals** | 8.1 | 3.0 | **0.37×** |
| **blocks** | 4.9 | 1.0 | **0.20×** |

**Owners: blocks (5× under) and steals (~2.7× under); rebounds ~13% low a secondary.**
These are box-score realism gaps that DON'T touch team scoring (a block is still a miss, a
steal is still a turnover, a rebound re-assigns an already-counted possession), which is why
they survived every scoring-calibration pass.

**BLOCKS + STEALS FIXED (2026-07-14) — pure scoring-neutral attribution.**
- **Blocks 0.20×→1.03×** (5.05 vs real 4.90). A block is a KIND of missed FG, so beyond the
  possession-ending rim-protection path, `_resolve_outcome` now RELABELS already-missed
  block-eligible (rim) shots as blocked at `block_attribution_scale=0.60 ×` the blocker's
  block attribute. The shot already missed → scoring, possessions, and the rebound untouched.
- **Steals 0.37×→1.01×** (8.16 vs real 8.08). Composition shift, NOT more turnovers: raised
  `steal_rate` 0.034→0.093 and lowered `tov_scale` 0.9→0.44 so TOTAL TOV is held at the
  pre-fix level (14.3) — a steal and an unforced TOV are both a turnover charged to the ball
  handler, so the 3.4b per-player economy is preserved. Decoupled fast breaks
  (`steal_fastbreak_prob=0.37`) so the realistic steal COUNT doesn't inflate fast-break
  frequency / the possession budget (real: not every steal is a fast break).
- Guardrails: scoring ~neutral (117.4 vs pre-fix 116.8), 3.2 metrics unchanged/slightly better
  (blowout 20.1 vs real 20.5, close 26.7 vs 27.2), 296 tests green (OT_SEED 37→27).

**DISTRIBUTION FIX (2026-07-14) — steal/block CREDIT was over-concentrated (surfaced by CLI
testing).** Team totals were right but every steal/block was credited to the single best
defender on the floor (`max(defense, key=steal/block)`), funneling a team's whole total onto
one player — 16-steal / 11-block box lines; ≥5-steal games in 7.2% of player-games, ≥5-block in
2.5%. Raising steal/block VOLUME to real team totals (this milestone) amplified it. Fix:
`_credit_defender` distributes the credit WEIGHTED by ability (`rng.choices(defense, weights=
[p[attr]])`) — the RATE is still gated by the best defender so team totals are unchanged
(STL 8.22 / BLK 5.04, matched). Result: max steals 16→7, ≥5 7.2%→0.33%; max blocks 11→6,
≥5 2.5%→0.06% — realistic single-game highs, led by the specialist. This is a 3.4d
(per-player distribution) defect caught WITHOUT PlayerGameLog, via hands-on single-game testing.
Foul-outs measured in the same pass: **0.22/game (below real ~0.4-0.5), 3+ foul-outs in only
0.1% of games, PF/36 mean 2.20** — NOT systematically high; a single-game 3-foul-out report was
a rare (~0.1%) variance tail, not a bug.

**GUARD ADDED: `app/analysis/player_distribution.py`** (6th analysis pillar). Per-player-game
distribution check — game-highs (steals/blocks/points), ≥5-steal/≥5-block rates, ≥8-TOV rate,
PF/36, foul-outs/game — flagged against generous real-NBA sanity ceilings. This is the check
that would have auto-caught the steal/block concentration (max steals 16 and ≥5-steal 7.2%
both trip it); it PASSES now. A GUARDRAIL for the 3.4d class of per-player distribution defects
that team-level aggregates (team_boxscore.py) are blind to — precise per-game calibration still
needs PlayerGameLog.

**REBOUNDS RECONCILED (2026-07-14) — credit-assignment fix + residual reassigned. GAP 3.5
CLOSED.** Instrumented first (opportunities vs credits): **credited rebounds (38.45) == live
FG-miss opportunities (38.45)** — every live FG miss was already credited, zero dropped. The
deficit split cleanly:
- **Credit-assignment part (FIXED):** missed LAST free throws credited nobody (`_resolve_outcome`
  only rebounded live misses with `fta==0`). Now `_shoot_free_throws` tracks the last FT and
  `_credit_ft_rebound` credits a DEFENSIVE rebounder on a missed final FT (all three FT sites:
  bonus foul, 2PT/3PT shooting fouls, incl. missed and-1s). Defensive → `is_oreb` stays False →
  cannot trigger a second chance; the possession already flipped to defense, so it's purely the
  box-score credit that was missing. Rebounds 38.5→41.4. Neutrality verified: OREB count
  unchanged (8.56 — offensive-rebound rate / second-chance mechanism untouched), scoring neutral
  (117.3), possessions unchanged, 296 tests green (OT_SEED 27→163).
- **Residual REASSIGNED, not chased (0.94×, 41.4 vs 44.0):** the sim has fewer missed FGs than
  real because its **FG% is 0.515 vs real 0.476** (85.8 FGA, 41.6 misses vs real ~46.4). Fewer
  misses → fewer rebounds. That is the SHOT-EFFICIENCY / over-scoring owner (same root as the
  pre-existing avg-score +3), NOT rebound crediting — per the instrument-first guardrail we do
  NOT manufacture misses to close it. The sim's rebound accounting is now INTERNALLY CONSISTENT
  (every miss it produces is credited a rebounder).

**Gap 3.5 is a scoring-neutral bookkeeping reconciliation, COMPLETE:** blocks 1.03×, steals
1.01×, rebounds internally consistent (residual owned by shot efficiency), assists 1.00×, TOV
held. Box-score accounting is now internally consistent. Fouls (pf) still have no real anchor
in PlayerSeasonStats (sim 14.3/team) — needs box ingestion if ever pursued.

**CROSS-ERA GENERALIZATION VERIFIED (2026-07-14).** The 3.5 fixes use single GLOBAL constants
(block_attribution_scale, steal_rate, tov_scale) calibrated on 2024-25 — but they multiply
ERA-DERIVED player attributes, so they should generalize. Confirmed by running team_boxscore on
all 7 ingested seasons (sim/real ratio):

| season | pts | reb | stl | blk | tov |
|---|---|---|---|---|---|
| 1996-97 | 1.01 | 1.02 | 0.91 | 1.10 | 0.91 |
| 2000-01 | 1.03 | 0.99 | 0.97 | 0.84 | 0.95 |
| 2005-06 | 1.02 | 1.01 | 1.03 | 1.04 | 0.97 |
| 2013-14 | 1.01 | 1.00 | 1.01 | 1.03 | 0.98 |
| 2016-17 | 1.01 | 1.00 | 1.02 | 1.00 | 1.03 |
| 2019-20 | 1.00 | 1.00 | 1.07 | 0.96 | 1.03 |
| 2024-25 | 1.03 | 0.94 | 1.01 | 1.04 | 1.06 |

Scoring 1.00–1.03× and rebounds 0.99–1.02× in EVERY era (cross-era reconciliation intact — the
tov_scale/steal changes did not break it). Steals within ~9%, blocks within ~4% for 2005–2019.
The only material stretch is the OLDEST eras: 1996-97 blk 1.10× / tov 0.91×, 2000-01 blk 0.84×
— a single global block/steal/TOV constant doesn't perfectly capture the highest-block/turnover
late-90s/early-2000s. Consistent with the accepted "one engine, small era-edge residual" pattern
(cf. the modern star-usage residual); deliberately NOT chased with era constants.

**Honest scope caveat — 3.2 is single-season-validated.** The Q4-texture milestone
(comfortable-lead PROTECT, blowout/compression, lead changes, run/drought) was measured ONLY on
2024-25, the sole season with ingested line scores + PBP. The mechanism is game-state-driven
(no era constants) and did not break cross-era SCORING (table above), but its Q4 blowout/
compression BENEFIT is unverified on other eras. Validating it cross-era would require ingesting
line scores (+ PBP) for those seasons — a data pull, not a code change.

**→ "shot over-efficiency" INVESTIGATED (2026-07-14) — a 2024-25 DATA GAP, not an engine
behavior.** The +0.039 FG% (0.515 vs 0.476) looked like a shot-model owner, but zone
decomposition on a COMPLETE-data season proves the engine is correctly calibrated: **2016-17
interior FG% 0.604 vs real 0.612 (sim UNDER), mid 0.419 vs 0.407, three 0.372 vs 0.358, shot
mix matched, scoring +0.9.** The over-efficiency is specific to 2024-25 because its
`PlayerSeasonStats` is incomplete: **`ra_fga` non-null = 0 (NO zone data) and only 431 players
(vs 486 in 2016-17).** With no observed zone FG% to read, `roster.py` falls back to the
attribute-derived make probs (which run hot), and the missing worse-shooting fringe players
lift the pool. This ONE root explains all three 2024-25 residuals — over-FG% → over-scoring
(+4) → fewer misses → rebound 0.94×. The calibrated observed-zone path is sound; there is NO
engine over-efficiency to fix. **Resolution = complete the 2024-25 shot-location ingest
(populate `ra_fga` etc. + full rosters), same class as the OREB data gap.** That is a data
task; it re-seeds 2024-25 attributes, so it re-touches the 2024-25-specific 3.2/3.5 numbers
(the GLOBAL constants are validated cross-era regardless — see CROSS_ERA_VALIDATION.md) and
should be its own careful pass, not folded in here.

**OREB investigated (2026-07-14) — MECHANIC SOUND, was a DATA GAP (not a behavior).** The one
remaining thread ("modern offensive rebounding looks low") turned out to be measurement
artifact: 2024-25 had NO `TeamSeasonStats` row, so `use_team_oreb` fell back to the flat
`OREB_RATE=0.22`, producing sim OREB% 0.22 vs real ~0.29. Proof the mechanic is sound: on
**2025-26 (which has team OREB data) sim OREB% is 0.310 vs real 0.305** — a match. Closed the
gap by ingesting 2024-25 `TeamSeasonStats` (30 teams, real OREB% 0.293); sim 2024-25 OREB% is
now **0.297 vs 0.293** — matched. Side effect (honest): scoring rose ~+0.8 (117.3→118.0, now
+4.2 over) because the correct second chances were previously suppressed by the 0.22 fallback —
i.e. low OREB was partially MASKING the shot over-efficiency, which is now more visible.
Rebounds 41.6 (residual still owned by shot efficiency). 3.2 metrics unchanged (blowout 21.1,
Q4 var 65.5). OREB is NOT an under-modeled behavior — it is data-driven and correct wherever
team data exists. No constant changed.

---

## ACCOUNTING MILESTONE — BANKED COMPLETE (2026-07-14)

Every meaningful cross-era discrepancy now has a named behavioral explanation, and the
era-invariant defects that blocked generalization are gone. The accounting layer (`app/analysis/`
— accounting, decomposition, player_accounting, game_texture, team_boxscore + real PBP/line-score
data) is now the mechanism for proving the engine behaves for the RIGHT reasons, not just a
debugger. Closed this milestone: cross-era reconciliation (7 eras); 3.4a/b/c (player allocation,
turnover economy, assist initiator); 3.2 (comfortable-lead Q4 PROTECT); 3.6 (dismissed — not a
gap); 3.5 (blocks/steals/rebounds — scoring-neutral bookkeeping reconciliation); OREB (validated,
data gap closed).

**Documented second-order limitations (deliberately NOT chased — decision):**
- **Shot over-efficiency** (FG% 0.515 vs 0.476 → avg-score +~4 over, rebound residual). A real
  owner, next scoring-realism target IF pursued — measure interior-vs-perimeter first.
- **3-pt efficiency residual** — deliberately NOT chased. It sits inside interactions between
  home advantage, contest effects and already-calibrated systems; at this point that is
  interacting-constant tuning that risks a new cancellation / overfit and would trade away the
  stronger property already achieved (one engine generalizing across eras from behaviors, not
  accumulated constants). Only revisit MEASURED via the ShotChartDetail milestone, never tuned.
- **3.4d player game-to-game variance** — blocked on `PlayerGameLog` ingestion (unmeasured).
- **Fouls (pf)** — no real per-team anchor in PlayerSeasonStats.

### 3.6 — Lead changes — ✅ DISMISSED (2026-07-14): NOT A GAP under consistent measurement
Flagged for years at "sim ~6.8 vs real ~9-10". That real anchor was LITERATURE/memory, never
measured from our data with our definition. With real PBP now ingested, `game_texture.py`
`_leadchange_decomp` computes real lead changes with the SAME algorithm as the sim (leader
sign-flip on scoring events): **real 6.64 vs sim 6.79 — they match** (2024-25, real n=1225 /
sim n=2450). The decomposition also matches on both factors: near-tie exposure (|margin|≤3)
real 0.294 / sim 0.288, and flips per near-tie-minute real 0.410 / sim 0.438. The apparent gap
was an apples-to-oranges artifact (the "9-10" figure uses a different, unknown definition —
e.g. counting ties-into-lead, or a different era). No behavioral change warranted; the "9.5"
anchor is retired. Lesson: an unmeasured cross-source anchor is not a calibration target —
measure real and sim the same way before declaring a gap. (This also voids the "lead changes
stuck at 6.8" residual noted under the 3.2 fix — it was already correct.)

### 3.7 — Foul & bonus model — OPEN (identified 2026-07-14): no team-foul/bonus system
The engine has NO team-foul counter, bonus threshold, or penalty situation. The only
non-shooting foul path (`_bonus_foul_prob`) ALWAYS awards 2 FTs — it assumes every team is
already in the bonus. So only three foul outcomes exist, all of which draw FTs: shooting foul,
"bonus" foul (mis-named — no threshold check), and offensive foul (→ turnover). **Common
non-shooting defensive fouls that draw NO FTs (reach-ins / loose-ball / off-ball fouls before
the 5th team foul of a quarter) are not modeled at all.** This is the ROOT of two measured
residuals: sim PF **~14.3/team vs real ~19-20** (the ~5 missing fouls are exactly these
pre-bonus non-FT fouls) and foul-outs **0.22/game vs real ~0.4** (fewer fouls → fewer foul-outs).
Fix is a real milestone, not a patch: (1) per-quarter team-foul counter (reset each quarter/OT);
(2) a non-shooting-foul rate that increments personal + team fouls WITHOUT FTs until the bonus,
then awards 2 FTs; (3) recalibrate so total FTA/scoring stay reconciled while PF → ~19-20 and
foul-outs → ~0.4. Also unlocks a correct late-game penalty situation (intentional-foul value).

**Step-1 instrument done (2026-07-15) — foul-out TIMING is wrong, not just the rate** (now a
permanent check in `player_distribution.py`). Measured 2024-25 + 2020-21: **~76% of foul-outs
happen BEFORE Q4** (real cluster in Q4) and **~81-87% of players who foul out had logged <30
minutes** (real 30-40+); mean minutes-played at foul-out ~24, mean game-minute ~29.5/48. Yet
PF/36 averages a healthy 2.20 — so this is **foul CONCENTRATION** (6 fouls in ~24 min = 9/36 for
the fouled-out player), the same class as the steal/block bug, most likely the shooting-foul →
positional-matchup defender path piling fouls on whoever guards the opposing star's position.
TWO linked threads to fix in this milestone: (a) total fouls too LOW (missing bonus/non-FT
fouls) and (b) the fouls that occur landing too CONCENTRATED/EARLY.

**Step 2a DONE (2026-07-15) — foul-trouble benching.** ROOT of the early foul-outs wasn't
attribution concentration (shooting fouls already spread across the position group) — it was
that the engine NEVER benched a player in foul trouble; whoever accumulated fouls just kept
playing and fouled out early (FoulTroubleModifier only softened defense; rotation only reacted
to foul-OUTS). Added foul-trouble benching in `resolve_lineup` (`use_foul_trouble_subs`, on in
DRAMA_M3): sit a scheduled player at 3 fouls in Q1 / 4 in Q2 / 5 in Q3 for the best available
bench player; Q4 & OT play through. Deterministic (no RNG added). Result: **foul-outs before
Q4 76%→0%** (now cluster in Q4, matching real); SCORING-NEUTRAL (toggle OFF/ON: 2016-17
107.0/107.0, 2024-25 118.7/118.9); 296 tests green. Foul-outs/game dropped 0.22→0.08 (benching
protects players) — the COUNT is restored by step 2b (bonus/non-FT foul volume). The
`<30 min-played` flag still trips (mean ~25 min): foul-out players legitimately sat in trouble;
expected to ease in 2b as more total fouls spread foul-outs to higher-minute players.

**Step 2b Stage 1 DONE (2026-07-15) — team-foul / bonus model (basketball rules + calibration;
NO pace compensation, that is Stage 2).** `use_bonus_system` (on in DRAMA_M3), gated so default
config stays byte-identical (296 tests green). Implemented: `GameState.{home,away}_quarter_fouls`
reset each period; defensive team in bonus at ≥5 fouls; shooting + non-shooting defensive fouls
count toward it; a PRE-BONUS non-shooting foul draws NO FTs, increments PF + team fouls, and
RESETS the shot clock to 14 (possession continues, consumes extra clock via `foul_reset_time`);
an IN-BONUS non-shooting foul awards 2 FTs (terminal). This exposed that the old always-FT
"bonus" fouls masked UNDER-produced shooting-foul FTA → added a `shooting_foul_scale` lever.
Calibrated (`nonshooting_foul_scale=1.1`, `shooting_foul_scale=1.65`): **PF 20.4 (real ~19-20),
FTA 21.1 (real 21.8), foul-outs 0.40/game and only 2% before Q4** (were 0.22 & 76%) — the 3.7
goal met. **Observed uncompensated pace drift: possessions 102.7/team (+~2.7 over budget), score
119.9** (pre-bonus fouls 8.8/gm, reset time 87s/gm) — the clean Stage-2 handoff. Residual:
`<30 min-played` foul-outs still 82% (benching compresses foul-trouble minutes) — secondary.
**Step 2b Stage 2 DONE + calibration reopened (2026-07-15) — pace compensation + last-2-min
rule + and-1 thinner; MILESTONE COMPLETE.** Stage 2 measured the reset-time pace drift by
isolating it (reset on vs off): −3.0 distinct possessions from the 87s/game of shot-clock
resets. Added ONE measured constant `foul_reset_poss_frac` (0.075 at the final foul volume)
folded into the halfcourt possession-time budget — pace restored to the ~95 reset-off baseline.

Validating that exposed a scoring rise, which we did NOT paper over — we decomposed it (the
project's instrument-first discipline) and it FALSIFIED the "foul system regresses scoring"
reading. On complete-data 2016-17, attributing every excess point: it is **+3.0 FG points from
+2.87 FGA**, with FT points DOWN (−1.15) and pace unchanged. The old always-FT "bonus" fouls
were EATING shots — pre-3.7 FGA was 82.7 vs real 85.4. The bonus system, by continuing pre-bonus
foul possessions to a shot, **restores FGA to real (85.5 ≈ 85.4) AND FTA to real (24.3 vs
23.1)**. The residual +2.6 over real is fully explained by the pre-existing **shot over-efficiency
(FG% 0.466 vs real 0.457, 3P% 0.370 vs 0.358)** — 0.009×85.5×2 + 0.012×26×3 ≈ +2.4 — a SEPARATE,
already-deferred owner, now applied to the (correct, higher) shot volume that the old model
suppressed. So the calibration was reopened (last-2-min bonus rule + `and1_rate_factor` to thin
and-1s + cross-era-re-derived scales) and the foul work is CORRECT — it improves realism.

**OWNERSHIP CHAIN (documented so future-us doesn't "re-fix" this):**
- **Gap 3.7 owns:** foul rules, bonus behavior, foul-out timing, the FGA/FTA MIX, and possession
  accounting. Final DRAMA_M3: `use_bonus_system`+`use_foul_trouble_subs`, `bonus_foul_threshold=5`,
  `last2min_clock=120`, `nonshooting_foul_scale=1.6`, `shooting_foul_scale=1.9`,
  `and1_rate_factor=0.4`, `foul_reset_poss_frac=0.075`. Validated on complete-data eras: FGA≈real,
  FTA≈real, FT%≈real, foul-outs cluster in Q4 (before-Q4 1-3%).
- **Shot-efficiency owns:** FG%, 3P%, and therefore the remaining +2-3 scoring residual. FG% 0.466
  vs real 0.457 is now the clean, isolated next scoring target.
- **The pre-3.7 baseline is RETIRED as the scoring reference** — it hit its total through
  COMPENSATING ERRORS (FGA too low 82.7 × FG% too high 0.466 ≈ right total). Compare to REAL, not
  to pre-3.7. Residuals: PF runs a touch high on older eras (pre-bonus foul rate) — scoring-neutral,
  same cross-era-constant category as blocks/steals; 2024-25 metrics distorted by its data gaps.
296 tests green (default config byte-identical). Instrument: `player_distribution.py` foul-out
timing + `possession_accounting` (`pre_bonus_fouls`, `foul_reset_time`).

**Post-milestone refinement (2026-07-15, hands-on CLI testing).** Two measured fixes: (1)
`nonshooting_foul_scale` 1.6→1.3 — the 1.6 matched FGA exactly but over-produced PF (22-23 vs
real ~19-20), cascading into excess foul trouble; 1.3 lands PF ~20-21, FTA still ~real, FGA
drifts +~1.5 (to the shot-efficiency owner). (2) foul-trouble benching softened to "sit only at
5 fouls (one from foul-out) until Q4" (was 3/4/5 by quarter) — keeps foul-outs in Q4 (before-Q4
0%) while leaving 3-4-foul starters on the floor (Tatum 13→27-28 min in the common case). Both
address named owners (league PF too high; foul trouble pulling rotation players too early)
without touching the foul MODEL. OPEN follow-up (measure-first, NOT started): shooting-foul
CONCENTRATION — a starter occasionally fouls out from a cluster (seed 59). Before any weighted
redistribution, measure league-wide: PF distribution by role/tier real vs sim, 5-/6-foul game
frequency by tier, whether the matchup defender systematically absorbs too many shooting fouls,
and concentration-vs-tail. Only redistribute if the distribution is unrealistic, not merely
high-variance. Needs a dedicated harness (per-game real data → PlayerGameLog for the tier
frequencies).

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
