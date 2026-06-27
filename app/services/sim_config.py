from dataclasses import dataclass, field


@dataclass
class SimConfig:
    # --- feature toggles (all off = current behavior) ---
    use_pace: bool = False            # pace-derived possession count vs fixed 200
    use_clock: bool = False           # real clock tracking vs post-hoc distribution
    use_second_chance: bool = False   # oreb extends possession chain
    use_fast_break: bool = False      # steal → transition modifier next possession
    use_team_defense: bool = False    # team def_rating suppresses opponent FG%
    use_strategic_foul: bool = False  # trailing team intentionally fouls late-game
    use_momentum: bool = False        # per-team momentum from runs/stops/steals
    use_fatigue: bool = False         # heavy-minutes lineup efficiency decay
    use_foul_trouble: bool = False    # defense softens when players have 4+ fouls
    use_clutch: bool = False          # clutch_rating boosts late close-game efficiency
    use_player_variance: bool = False # per-game form factor drawn from player-specific distribution
    use_team_oreb: bool = False       # per-team OREB% from TeamSeasonStats replaces flat 22% constant
    use_catch_up: bool = False        # trailing team shifts pace/shot selection in late Q4
    use_garbage_time: bool = False    # large-lead late-game intensity reduction

    # --- M3c tuning constants ---
    catch_up_clock_threshold: int = 150   # seconds remaining when catch-up activates
    catch_up_max_deficit: int = 15        # max pts down for catch-up to trigger
    garbage_time_margin: int = 20         # min lead for garbage time to activate
    garbage_time_clock_threshold: int = 600  # seconds remaining in quarter when GT activates

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
    league_avg_def_rating: float = 113.0
    league_avg_pace: float = 100.0
    momentum_max: float = 0.05
    momentum_decay_rate: float = 0.20


# Pre-built config with all drama M1 modifiers enabled — used by calibration script
DRAMA_M1 = SimConfig(
    use_pace=True,
    use_clock=True,
    use_second_chance=True,
    use_fast_break=True,
    use_team_defense=True,
    use_strategic_foul=True,
)

DRAMA_M2 = SimConfig(
    use_pace=True,
    use_clock=True,
    use_second_chance=True,
    use_fast_break=True,
    use_team_defense=True,
    use_strategic_foul=True,
    use_momentum=True,
    use_fatigue=True,
    use_foul_trouble=True,
    use_clutch=True,
)

DRAMA_M3 = SimConfig(
    use_pace=True,
    use_clock=True,
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
