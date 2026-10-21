"""Trade value model and CPU acceptance rule (M-6a increment 4) — PROVISIONAL, NOT WIRED.

Nothing in the app imports this yet. It exists so the validation experiments in
TRADE_VALUE_VALIDATION.md can score it; promotion into CPU trade decisions waits for that
evidence. Pure functions only: no DB, no RNG, no I/O.

Value of a player-season, from PlayerSeasonStats alone (never `overall_rating`, guardrail #1):

    production  G  = Hollinger Game Score per game
    replacement R  = mean G of the league's 11th-13th men (data-derived, per season)
    value          = (max(0, G' - R)) ** k * availability

where G' optionally shrinks thin samples toward R, and availability optionally scales by the
share of the team's games the player appeared in. `k`, shrink and availability are the
variants the protocol compares; every component is returned by `explain()` so a value can be
audited by hand.
"""
from dataclasses import dataclass, field
from statistics import mean
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Hollinger Game Score. PlayerSeasonStats stores total rebounds only, so rebounds take the
# average of Hollinger's 0.7 (offensive) and 0.3 (defensive) weights.
GAME_SCORE_WEIGHTS = {
    "points": 1.0, "fgm": 0.4, "fga": -0.7, "ft_missed": -0.4, "rebounds": 0.5,
    "steals": 1.0, "assists": 0.7, "blocks": 0.7, "pf_per_game": -0.4, "turnovers": -1.0,
}

REPLACEMENT_WINDOW = (11, 13)      # 1-indexed MPG ranks on each team, inclusive
REPLACEMENT_MIN_GP = 10            # cameo rows must not define "replacement"
SHRINK_GAMES = 20.0                # prior weight, in games, toward replacement level
CPU_ACCEPT_MARGIN = 1.1            # locked: incoming must be >= 1.1x outgoing
_EPS = 1e-9                        # so 11.0 vs 1.1 * 10.0 is not rejected by float error


def _num(stats, name: str) -> float:
    v = stats.get(name) if isinstance(stats, Mapping) else getattr(stats, name, None)
    return float(v) if v is not None else 0.0


def game_score(stats) -> float:
    """Hollinger Game Score per game from a PlayerSeasonStats row (or a mapping of its fields)."""
    w = GAME_SCORE_WEIGHTS
    return (
        w["points"] * _num(stats, "points")
        + w["fgm"] * _num(stats, "fgm")
        + w["fga"] * _num(stats, "fga")
        + w["ft_missed"] * (_num(stats, "fta") - _num(stats, "ftm"))
        + w["rebounds"] * _num(stats, "rebounds")
        + w["steals"] * _num(stats, "steals")
        + w["assists"] * _num(stats, "assists")
        + w["blocks"] * _num(stats, "blocks")
        + w["pf_per_game"] * _num(stats, "pf_per_game")
        + w["turnovers"] * _num(stats, "turnovers")
    )


@dataclass(frozen=True)
class PlayerProduction:
    """One player-season, reduced to what the value model reads."""
    player_id: int
    team_id: int
    games_played: int
    mpg: float
    game_score: float

    @classmethod
    def from_stats(cls, stats) -> "PlayerProduction":
        return cls(
            player_id=int(_num(stats, "player_id")),
            team_id=int(_num(stats, "team_id")),
            games_played=int(_num(stats, "games_played")),
            mpg=_num(stats, "minutes_per_game"),
            game_score=game_score(stats),
        )


@dataclass(frozen=True)
class ReplacementLevel:
    value: float
    n_players: int
    team_means: Mapping[int, float]          # per-team mean over the window (audit)
    teams_used: int


def replacement_level(
    players: Iterable[PlayerProduction],
    window: Tuple[int, int] = REPLACEMENT_WINDOW,
    min_gp: int = REPLACEMENT_MIN_GP,
) -> ReplacementLevel:
    """Mean Game Score of each team's `window` ranks by MPG, pooled across teams.

    Applied identically to every team: players with fewer than `min_gp` games are excluded
    before ranking, and a team contributes only if it still has players at every rank in the
    window (so a short roster cannot drag the level toward its starters). Ties in MPG break
    by player_id, so the result is deterministic.
    """
    lo, hi = window
    by_team: Dict[int, List[PlayerProduction]] = {}
    for p in players:
        if p.games_played >= min_gp:
            by_team.setdefault(p.team_id, []).append(p)
    pooled: List[float] = []
    team_means: Dict[int, float] = {}
    for team_id, plist in sorted(by_team.items()):
        ranked = sorted(plist, key=lambda p: (-p.mpg, p.player_id))
        if len(ranked) < hi:
            continue
        chunk = [p.game_score for p in ranked[lo - 1:hi]]
        pooled.extend(chunk)
        team_means[team_id] = mean(chunk)
    if not pooled:
        raise ValueError("no team has enough players to define a replacement level")
    return ReplacementLevel(mean(pooled), len(pooled), team_means, len(team_means))


@dataclass(frozen=True)
class ValueModel:
    k: float
    shrink: bool
    availability: bool
    replacement: float
    shrink_games: float = SHRINK_GAMES
    team_games: Mapping[int, int] = field(default_factory=dict)   # team_id -> max GP on that team

    def explain(self, p: PlayerProduction) -> Dict[str, float]:
        """Every component of the value, so it can be checked by hand."""
        gp_weight = p.games_played / (p.games_played + self.shrink_games) if self.shrink else 1.0
        shrunk = self.replacement + (p.game_score - self.replacement) * gp_weight
        above = max(0.0, shrunk - self.replacement)
        team_gp = self.team_games.get(p.team_id, 0)
        avail = min(1.0, p.games_played / team_gp) if (self.availability and team_gp > 0) else 1.0
        return {
            "game_score": p.game_score, "replacement": self.replacement, "gp_weight": gp_weight,
            "shrunk_game_score": shrunk, "above_replacement": above, "k": self.k,
            "availability": avail, "value": (above ** self.k) * avail,
        }

    def value(self, p: PlayerProduction) -> float:
        return self.explain(p)["value"]


def build_value_model(
    players: Sequence[PlayerProduction], *, k: float = 1.0, shrink: bool = False,
    availability: bool = False, window: Tuple[int, int] = REPLACEMENT_WINDOW,
    min_gp: int = REPLACEMENT_MIN_GP,
) -> ValueModel:
    """Derive the season's replacement level and team game counts, then fix the variant."""
    team_games: Dict[int, int] = {}
    for p in players:
        team_games[p.team_id] = max(team_games.get(p.team_id, 0), p.games_played)
    rl = replacement_level(players, window, min_gp)
    return ValueModel(k=k, shrink=shrink, availability=availability,
                      replacement=rl.value, team_games=team_games)


# ------------------------------------------------------------------ the locked CPU rule

ACCEPT = "ACCEPT"
VALUE_SHORTFALL = "VALUE_SHORTFALL"
ZERO_VALUE_TRADE = "ZERO_VALUE_TRADE"


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    code: str                                  # stable machine-readable reason
    message: str                               # human-readable explanation
    incoming: float
    outgoing: float
    ratio: Optional[float]                     # None when outgoing == 0
    missing_value_players: Tuple[int, ...] = ()


def cpu_verdict(
    incoming: Sequence[Tuple[int, Optional[float]]],
    outgoing: Sequence[Tuple[int, Optional[float]]],
    *, margin: float = CPU_ACCEPT_MARGIN,
) -> Verdict:
    """The locked acceptance rule, from the CPU team's side.

    `incoming` / `outgoing` are (player_id, value) pairs for the players the CPU receives /
    gives up. A value of None (no stats) counts as 0 and is listed in the verdict, never
    silently dropped; negative values are clamped to 0. Accept iff incoming >= margin *
    outgoing; when outgoing is 0, accept iff incoming > 0; both 0 -> ZERO_VALUE_TRADE.
    Deterministic: no RNG.
    """
    missing = tuple(sorted(pid for pid, v in list(incoming) + list(outgoing) if v is None))
    tot_in = sum(max(0.0, v) for _, v in incoming if v is not None)
    tot_out = sum(max(0.0, v) for _, v in outgoing if v is not None)
    note = f" (no stats for player(s) {list(missing)}; counted as 0)" if missing else ""
    if tot_out <= 0.0:
        if tot_in <= 0.0:
            return Verdict(False, ZERO_VALUE_TRADE,
                           "Neither side of this trade has any value to the team." + note,
                           tot_in, tot_out, None, missing)
        return Verdict(True, ACCEPT,
                       f"The team gives up nothing of value and receives {tot_in:.1f}." + note,
                       tot_in, tot_out, None, missing)
    ratio = tot_in / tot_out
    if tot_in + _EPS >= margin * tot_out:
        return Verdict(True, ACCEPT,
                       f"Receives {tot_in:.1f} for {tot_out:.1f} ({ratio:.2f}x, needs {margin:.2f}x)." + note,
                       tot_in, tot_out, ratio, missing)
    return Verdict(False, VALUE_SHORTFALL,
                   f"Receives {tot_in:.1f} for {tot_out:.1f} ({ratio:.2f}x); "
                   f"the team needs at least {margin:.2f}x." + note,
                   tot_in, tot_out, ratio, missing)
