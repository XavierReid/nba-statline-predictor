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


# Pre-built config with all drama M1 modifiers enabled — used by calibration script
DRAMA_M1 = SimConfig(
    use_pace=True,
    use_clock=True,
    use_second_chance=True,
    use_fast_break=True,
    use_team_defense=True,
    use_strategic_foul=True,
)
