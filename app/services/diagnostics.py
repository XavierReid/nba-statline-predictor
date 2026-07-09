"""SimulationDiagnostics — per-game instrumentation for calibration.

Every mechanic that affects possessions, clock, or lineup quality reports its
contribution here (CLAUDE.md guardrail 5). The dict shape returned by as_dict()
is the public contract consumed by scratch/diagnose_calibration.py and API
callers via result["possession_accounting"] — extend it, don't reshape it.
"""
from typing import Dict

_CATEGORIES = ("halfcourt", "fastbreak", "second_chance", "strategic_foul", "endgame")


class SimulationDiagnostics:
    def __init__(self, pace_budget: int) -> None:
        self.pace_budget = pace_budget
        self.counts: Dict[str, int] = {c: 0 for c in _CATEGORIES}
        self.time: Dict[str, float] = {c: 0.0 for c in _CATEGORIES}
        self.catch_up_time_delta = 0.0   # net clock saved (+) / added (−) by pace multipliers
        self.endgame_time_delta = 0.0    # net clock saved (+) / added (−) by endgame pacing
        # garbage rotation (gap 2.1)
        self.garbage_entries = 0
        self.garbage_possessions = 0
        self.garbage_entry_margin_sum = 0
        self.mismatch_poss = 0           # leader-bench vs trailer-starters window
        self.mismatch_margin_delta = 0
        # lineup quality distribution (verify transmission, not just outcomes)
        self.lineup_def = {
            "scheduled_sum": 0.0, "scheduled_n": 0,
            "garbage_sum": 0.0, "garbage_n": 0,
            "min": 1.0, "max": 1.0,
        }

    def record_possession(self, category: str, seconds: float) -> None:
        self.counts[category] += 1
        self.time[category] += seconds

    def record_garbage_entry(self, margin_abs: int) -> None:
        self.garbage_entries += 1
        self.garbage_entry_margin_sum += margin_abs

    def record_garbage_possession(self) -> None:
        self.garbage_possessions += 1

    def record_mismatch(self, margin_delta: int) -> None:
        self.mismatch_poss += 1
        self.mismatch_margin_delta += margin_delta

    def record_lineup_defense(self, mode: str, factor: float) -> None:
        acc = self.lineup_def
        acc[f"{mode}_sum"] += factor
        acc[f"{mode}_n"] += 1
        acc["min"] = min(acc["min"], factor)
        acc["max"] = max(acc["max"], factor)

    def as_dict(self) -> dict:
        return {
            "counts": self.counts,
            "time": self.time,
            "catch_up_time_delta": self.catch_up_time_delta,
            "endgame_time_delta": self.endgame_time_delta,
            "garbage_rotation": {
                "entries": self.garbage_entries,
                "possessions": self.garbage_possessions,
                "entry_margin_sum": self.garbage_entry_margin_sum,
                "mismatch_poss": self.mismatch_poss,
                "mismatch_margin_delta": self.mismatch_margin_delta,
            },
            "lineup_defense": self.lineup_def,
            "pace_budget": self.pace_budget,
        }
