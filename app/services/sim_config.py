from dataclasses import dataclass


@dataclass
class SimConfig:
    # --- feature toggles (all off = current behavior) ---
    # NB: pace-derived possessions and real-clock tracking are now unconditional
    # (the fixed-200 / post-hoc engine paths were removed 2026-07-14).
    use_second_chance: bool = False   # oreb extends possession chain
    use_fast_break: bool = False      # steal → transition modifier next possession
    use_team_defense: bool = False    # team def_rating suppresses opponent FG%
    use_strategic_foul: bool = False  # trailing team intentionally fouls late-game
    use_momentum: bool = False        # per-team momentum from runs/stops/steals
    use_fatigue: bool = False         # heavy-minutes lineup efficiency decay
    use_foul_trouble: bool = False    # defense softens when players have 4+ fouls
    use_foul_trouble_subs: bool = False  # bench a player in foul trouble until safe (Q4 plays through)
    use_bonus_system: bool = False    # team-foul/bonus: pre-bonus non-shooting fouls draw no FTs (reset shot clock), bonus (>=5) awards 2 FTs
    use_clutch: bool = False          # clutch_rating boosts late close-game efficiency
    use_player_variance: bool = False # per-game form factor drawn from player-specific distribution
    use_team_oreb: bool = False       # per-team OREB% from TeamSeasonStats replaces flat 22% constant
    use_catch_up: bool = False        # DEPRECATED by use_team_objectives; kept for isolation replays
    use_team_objectives: bool = False # Q4 objective shift (PROTECT/CHASE) drives late-game behavior
    use_garbage_time: bool = False    # large-lead late-game intensity reduction
    use_shot_subtypes: bool = False   # six sub-types instead of three coarse buckets
    use_contest_model: bool = False   # separates contest probability from contest impact
    use_positional_matchups: bool = False  # position-aware defender pool (uniform within group)
    use_foul_drawing: bool = False    # player-specific foul draw rate with shot-type multipliers
    use_endgame_pacing: bool = False  # incentive-driven possession time in the endgame window
    use_garbage_rotation: bool = False  # game-state-aware rotation: bench units in garbage time
    use_lineup_quality: bool = False    # defense quality emerges from the five on the floor
    use_behavior_profile: bool = False  # GamePhase resolves to a baseline-behavior profile

    # --- COMPETITIVE_LATE behavior profile (gap 3.2; behavior_profile.profile_for_phase) ---
    # Seeded from measured 2024-25 clutch splits (last 5 min, <=5 pts) vs overall:
    # FTA/FGA 1.86x, TOV/poss 0.92x, OREB 1.16x, 3PA flat 1.0x, pace/PPP flat.
    # foul mult is TUNED against the competitive-Q4 variance target (60.6) because the
    # phase (whole competitive Q4) is broader than the clutch window it was measured on.
    comp_late_foul_mult: float = 1.86    # measured clutch FTA/FGA ratio (tuning anchor)
    comp_late_tov_mult: float = 0.92
    comp_late_oreb_mult: float = 1.16
    comp_late_three_mult: float = 1.0    # real clutch 3PA is flat vs overall

    # --- endgame window + pacing (gap 1.2; consumed via late_game.LateGameContext) ---
    endgame_clock_window: int = 120      # seconds remaining in final period (Q4/OT)
    endgame_margin_max: int = 8          # |margin| for the window to be active
    endgame_urgency_time_mean: float = 9.0   # trailing offense — possessions over efficiency
    endgame_urgency_time_std: float = 1.5
    endgame_milk_time_mean: float = 20.0     # leading offense — time over expected points
    endgame_milk_time_std: float = 2.0

    # --- Q4 team objectives (gap 3.1; late_game.derive_objective/objective_adjustments) ---
    # Behavior-first: selection + tempo only, efficiency emerges. Constants are
    # calibrated against the measured Q4 transition deltas (SIMULATION_GAPS.md).
    competitive_late_margin: int = 8     # |margin| for GamePhase.COMPETITIVE_LATE (final period)
    objective_min_margin: int = 6        # below this, both teams stay NEUTRAL (toss-up / one possession)
    objective_full_margin: int = 20      # intensity maxes here
    # PROTECT efficiency cost is the primary compression lever (behavior-first
    # selection backfired — see late_game.objective_adjustments). Tuned against the
    # measured Q4 transition deltas (SIMULATION_GAPS.md): steep ramp so 6-10 is barely
    # touched (+1.16 real) while 11-20 compresses (-1.12 real).
    protect_efficiency_cost: float = 0.06  # leading team: max shot-prob reduction (worse shots)
    protect_three_shift: float = 0.06    # leading team: mild reduction in three rate (variance ↓)
    chase_three_shift: float = 0.0       # trailing variance (off by default — costs efficiency here)
    protect_pace_bonus: float = 0.10     # leading team: max +10% possession time (milk)
    chase_pace_bonus: float = 0.10       # trailing team: max -10% possession time (hurry)
    # --- comfortable-lead PROTECT (gap 3.2; margin 9-20 in Q4, above competitive_late_margin) ---
    # The clutch constants above own <=8 (gap 3.1) and are frozen. The 9-20 band is a
    # DIFFERENT regime: a team protecting a comfortable (not decided) fourth-quarter
    # lead manages the game harder — trades aggression for clock management and
    # lower-risk possessions. Measured owner of gap 3.2: real NET(lead-trail) Q4 is
    # negative every band (trailing team outscores leader = compression), strongest at
    # 11-20 (-1.46); the sim's clutch-strength PROTECT was far too weak here (sim -0.42,
    # even +0.22 at 6-10). These are a stronger, independently-calibrated PROTECT applied
    # only in the comfortable band, swept against the NET(lead-trail) target. 0 would fall
    # back to clutch strength (behavior-neutral vs pre-3.2). Leading-team-only: CHASE is
    # unchanged (measurement showed the miss is dominated by the leader).
    # Swept 2026-07-14 against the real Q4 NET(lead-trail) target (scratch/q4_role_split.py):
    # cost 0.22 lands 11-20 at -1.54 (real -1.46), 21+ -0.99 (-0.91), 6-10 -0.50 (-0.67) —
    # all bands within ~1 SE of the (noisy, single-season) real NET. Efficiency cost is the
    # primary NET lever; pace/three milk the clock and pull Q4 totals toward real (~55).
    comfortable_lead_efficiency_cost: float = 0.22  # worse leader shots (primary NET lever)
    comfortable_lead_three_shift: float = 0.12      # fewer leader threes (lower risk)
    comfortable_lead_pace_bonus: float = 0.16       # milk clock harder (also slows Q4 total)

    # --- M3e tuning constants ---
    # Naively 0.055 / 0.22 = 0.25 would match the old flat rate for a league-average
    # foul drawer, but bonus fouls are drawn per ball-handler selection, which is
    # usage-weighted — and high-usage stars also have above-average FTA/FGA. 0.19
    # compensates for that correlation (measured: FTA/team/gm 21.6 baseline) so total
    # bonus foul volume stays at pre-M3e levels while distribution shifts to stars.
    foul_draw_scale: float = 0.19
    # --- team-foul / bonus model (gap 3.7 step 2b) ---
    bonus_foul_threshold: int = 5     # defensive team fouls per period before the bonus (2 FTs on non-shooting fouls)
    # Non-shooting foul rate is scaled up under the bonus system: pre-bonus ones draw NO
    # FTs, so more fouls are needed to keep FTA (~21.8) while total PF rises to real ~19-20.
    # Swept in Stage 1 (basketball counts) — see SIMULATION_GAPS 3.7.
    nonshooting_foul_scale: float = 1.0
    # Shooting-foul rate multiplier. The old always-FT "bonus" fouls masked that shooting
    # fouls under-produced FTA; with the bonus system gating non-shooting FTs, real FTA
    # (~21.8, mostly shooting fouls) needs this >1. Swept in Stage 1.
    shooting_foul_scale: float = 1.0
    # A pre-bonus non-shooting foul resets the shot clock to 14s: the possession continues
    # and consumes this much ADDITIONAL game clock (analog of second_chance_time). Stage 1
    # applies it UNCOMPENSATED and instruments the pace impact; Stage 2 adds one clock-budget
    # compensation constant only if measurement shows one is needed.
    foul_reset_time_mean: float = 10.0
    foul_reset_time_std: float = 2.5
    foul_draw_late_zone1_clock: int = 120   # seconds: heightened intensity window
    foul_draw_late_zone1_margin: int = 8    # max margin for zone 1
    foul_draw_late_zone1_mult: float = 1.3
    foul_draw_late_zone2_clock: int = 60    # seconds: active fouling window
    foul_draw_late_zone2_margin: int = 5    # max margin for zone 2
    foul_draw_late_zone2_mult: float = 1.8

    # --- M3c tuning constants ---
    catch_up_clock_threshold: int = 150   # seconds remaining when catch-up activates
    catch_up_max_deficit: int = 15        # max pts down for catch-up to trigger
    garbage_time_margin: int = 20         # min lead for garbage time to activate
    garbage_time_clock_threshold: int = 600  # seconds remaining in quarter when GT activates
    garbage_exit_margin: int = 12         # hysteresis: starters return only below this margin
    concede_trailing_margin: int = 28     # trailing team holds starters until deficit is hopeless
    concede_trailing_clock: int = 240     # ... or a 20+ deficit with little clock remaining
    q3_concede_margin_bonus: int = 5      # Q3 concession needs margins this much larger than Q4

    # --- tuning constants ---
    oreb_chain_cap: int = 5
    fastbreak_time_mean: float = 7.0
    fastbreak_time_std: float = 1.5
    halfcourt_time_std: float = 3.0
    second_chance_time_mean: float = 9.0
    second_chance_time_std: float = 2.0
    strategic_foul_margin_min: int = 3
    strategic_foul_margin_max: int = 8
    strategic_foul_clock_threshold: int = 120  # seconds remaining in quarter
    strategic_foul_probability: float = 0.70
    # --- possession-mixture compensation (measured, not heuristic) ---
    # Pace budgets already include short possessions; these fractions lengthen the
    # halfcourt possession-time mean to offset them. Values come from possession
    # accounting runs (result["possession_accounting"] / app/analysis/), not analytic estimates.
    # 0.0 = no compensation (measurement mode).
    fastbreak_poss_frac: float = 0.026  # measured 2026-07-07, 300 games DRAMA_M3 (2.6% of possessions)
    catch_up_clock_frac: float = 0.0026 # measured 2026-07-07: +7.4s/game saved = 0.26% of regulation clock
    # Stage B signal gain — stretches each shot's deviation from the measured
    # league-average make probability for its sub-type (possession.py _LEAGUE_AVG_MAKE).
    # Re-swept 2026-07-09 after the FT-observation fix and Q4 objectives added their own
    # differentiation: at 1.25 the top-10 strength slope had climbed to 1.26 (over). 1.0
    # (no amplification — the raw pipeline) now gives slope 1.07 and close-game rate 23.2%
    # (real 24.5). Blowout% is invariant to gain here, confirming the residual is
    # mid-game dispersion, not team over-separation. Sweep: 1.10→slope 1.20, 1.15→1.25.
    signal_gain: float = 1.0
    # Ball-handler usage concentration exponent (gap 3.4): possession ownership
    # weight = usage_rate ** this. 1.0 = linear allocated offensive load too
    # democratically (stars under, bench over). Swept 2026-07-14 against the 7-era
    # player-line tier reconciliation: 1.6 zeroes the oldest era (the one-engine
    # baseline), reduces the star-under bias in every era, over-corrects none, and
    # keeps team scoring reconciled. Modern retains a smaller residual (real usage
    # is more concentrated than a single exponent captures) — a named follow-up, not
    # a reason to over-crank gamma. Default 1.0 stays behavior-neutral for tests.
    usage_concentration: float = 1.0
    # Unforced-turnover scale (gap 3.4b): unforced TOV prob = player tov_per_poss *
    # this. The player value sets the relative economy (flat across usage tiers);
    # this one league anchor keeps aggregate team TOV correct (steal / offensive-foul
    # paths also contribute, so <1). Swept against the turnover-economy harness.
    tov_scale: float = 1.0  # DRAMA_M3 lowers this (0.9→0.44) to hold TOTAL TOV as steals rose (3.5, composition-only)
    # Block attribution (gap 3.5): a block is a KIND of missed FG, so beyond the
    # possession-ending rim-protection path (which forces a miss pre-draw), missed
    # block-eligible shots are relabeled as blocked at this rate × the blocker's block
    # attribute. Scoring/possession-neutral (only labels existing misses). Swept so
    # blocks/team/game ≈ real 4.9 (sim was ~1.0 from the forced-miss path alone). 0 =
    # off (pre-3.5 behavior).
    block_attribution_scale: float = 0.60
    # Steal rate (gap 3.5): live-ball steal prob = best on-ball defender's steal attr /
    # 100 × this. Raised from the old 0.034 so steals ≈ real 8.1/team (was ~3.0); total
    # TOV is held at real ~13.4 by lowering tov_scale in tandem (a steal and an unforced
    # turnover are both a turnover charged to the ball handler — composition shift, not
    # more turnovers). Real steals are ~60% of TOV; the sim was ~20%.
    steal_rate: float = 0.093
    # Only a FRACTION of steals lead to a transition possession — decouples the (now
    # realistic) steal COUNT from fast-break frequency so the measured possession/pace
    # budget (fastbreak_poss_frac) still holds. Set so fast breaks stay ~constant vs the
    # pre-3.5 steal rate. Real: not every steal is a fast break.
    steal_fastbreak_prob: float = 0.37
    league_avg_def_rating: float = 113.0
    league_avg_pace: float = 100.0
    momentum_max: float = 0.05
    momentum_decay_rate: float = 0.20


DRAMA_M3 = SimConfig(
    use_second_chance=True,
    use_fast_break=True,
    use_team_defense=True,
    use_strategic_foul=True,
    use_momentum=True,
    use_fatigue=True,
    use_foul_trouble=True,
    use_clutch=True,
    use_player_variance=True,
    use_team_oreb=True,
    use_team_objectives=True,
    use_garbage_time=True,
    use_shot_subtypes=True,
    use_contest_model=True,
    use_positional_matchups=True,
    use_foul_drawing=True,
    use_endgame_pacing=True,
    use_garbage_rotation=True,
    use_lineup_quality=True,
    use_behavior_profile=True,
    usage_concentration=1.6,
    tov_scale=0.44,
    use_foul_trouble_subs=True,
    use_bonus_system=True,
    nonshooting_foul_scale=1.1,
    shooting_foul_scale=1.65,
)

DRAMA_M3_NO_SUBTYPES = SimConfig(
    use_second_chance=True,
    use_fast_break=True,
    use_team_defense=True,
    use_strategic_foul=True,
    use_momentum=True,
    use_fatigue=True,
    use_foul_trouble=True,
    use_clutch=True,
    use_player_variance=True,
    use_team_oreb=True,
    use_catch_up=True,
    use_garbage_time=True,
)
