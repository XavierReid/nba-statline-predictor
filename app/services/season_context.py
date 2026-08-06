"""SeasonContext — league-relative reference points for one simulated season.

The possession model interprets per-player rates by dividing them by "league
mean" anchors. When those anchors are hard-coded modern constants, applying the
sim to older-era rosters produces multiplicative overshoots — 2000-01 players
have naturally higher FTA/FGA (drive-heavy era, few threes) and higher PF/min
(more physical whistle), so every ratio ends up > 1 and shooting fouls +
non-shooting fouls both over-produce (measured: ~76% + ~26% of the 2000-01
scoring gap).

This object is computed once per season from the rostered player pool it will
actually simulate, so 'league mean' means THIS season's league mean. Modern
seasons are unchanged by construction (the anchors already were the modern
mean); older seasons self-normalize.

Consumed by `possession.resolve_possession` when `SimConfig.use_season_context`
is True. When OFF, the module-level constants in `possession.py` are used and
behavior is byte-identical to the previous engine.
"""
from dataclasses import dataclass
from typing import Iterable, List

# Fallback anchors — used when a season context is requested but the roster
# sample is empty. These are the same modern constants the possession module
# uses when the toggle is off, so the two code paths converge when data is
# missing.
_FALLBACK_SHOOTER_DRAW_ANCHOR = 0.28   # shot-weighted league mean of foul_drawing_rate
_FALLBACK_LEAGUE_FOUL_RATE = 0.085     # minutes-weighted league mean of foul_rate
_FALLBACK_LEAGUE_AVG_FOUL_DRAW_RATE = 0.22  # unweighted floor for missing players


@dataclass(frozen=True)
class SeasonContext:
    """League-relative anchors for one season.

    All fields have the same semantics as the same-named module constants in
    possession.py — the difference is that these are measured from the season's
    own player pool instead of being fixed at modern-era values.
    """
    season: str
    shooter_draw_anchor: float
    league_foul_rate: float
    league_avg_foul_draw_rate: float


def build_season_context(season: str, rosters: Iterable[List[dict]]) -> SeasonContext:
    """Build a SeasonContext from an iterable of team rosters.

    `rosters` is any iterable yielding lists of player dicts (the same shape
    `load_roster` returns). Deduplicates by player id in case the same player
    appears on multiple team rosters (traded mid-season). Each anchor uses the
    weighting appropriate to how the possession model consumes it:

      - shooter_draw_anchor: SHOT-weighted mean of foul_drawing_rate — because
        the model applies it per shot attempt (weighting by usage/attempts).
      - league_foul_rate: MINUTES-weighted mean of foul_rate — because it
        represents the rate at which a lineup accumulates fouls per unit time.
      - league_avg_foul_draw_rate: unweighted mean of foul_drawing_rate — this
        is the FLOOR used when a player has no history, so it's a "typical
        journeyman" reference, not a shot-weighted one.
    """
    seen: dict = {}
    for roster in rosters:
        for p in roster:
            pid = p.get("id")
            if pid is not None and pid not in seen:
                seen[pid] = p
    players = list(seen.values())

    if not players:
        return SeasonContext(
            season=season,
            shooter_draw_anchor=_FALLBACK_SHOOTER_DRAW_ANCHOR,
            league_foul_rate=_FALLBACK_LEAGUE_FOUL_RATE,
            league_avg_foul_draw_rate=_FALLBACK_LEAGUE_AVG_FOUL_DRAW_RATE,
        )

    # Shot-weighted foul_drawing_rate. Weight by (minutes × games_played) as an
    # attempts proxy — matches how the model consumes it (per shot). The roster
    # dict doesn't carry raw FGA, but minutes × games_played is monotonic with
    # it and available on every ingested roster.
    def _shot_weight(p: dict) -> float:
        return (p.get("minutes") or p.get("mpg") or 0.0) * (p.get("games_played") or 0.0)
    fdr_pairs = [(p.get("foul_drawing_rate"), _shot_weight(p)) for p in players]
    fdr_pairs = [(v, w) for v, w in fdr_pairs if v is not None and w > 0]
    if fdr_pairs:
        w_total = sum(w for _, w in fdr_pairs)
        shooter_draw_anchor = sum(v * w for v, w in fdr_pairs) / w_total
    else:
        vals = [p.get("foul_drawing_rate") for p in players if p.get("foul_drawing_rate") is not None]
        shooter_draw_anchor = sum(vals) / len(vals) if vals else _FALLBACK_SHOOTER_DRAW_ANCHOR

    # Minutes-weighted foul_rate.
    fr_pairs = [(p.get("foul_rate"), p.get("mpg") or p.get("min_per_game") or 0)
                for p in players]
    fr_pairs = [(v, w) for v, w in fr_pairs if v is not None and w > 0]
    if fr_pairs:
        w_total = sum(w for _, w in fr_pairs)
        league_foul_rate = sum(v * w for v, w in fr_pairs) / w_total
    else:
        vals = [p.get("foul_rate") for p in players if p.get("foul_rate") is not None]
        league_foul_rate = sum(vals) / len(vals) if vals else _FALLBACK_LEAGUE_FOUL_RATE

    # Unweighted foul_drawing_rate floor (typical-journeyman reference).
    fdr_vals = [p.get("foul_drawing_rate") for p in players if p.get("foul_drawing_rate") is not None]
    if fdr_vals:
        league_avg_foul_draw_rate = sum(fdr_vals) / len(fdr_vals)
    else:
        league_avg_foul_draw_rate = _FALLBACK_LEAGUE_AVG_FOUL_DRAW_RATE

    return SeasonContext(
        season=season,
        shooter_draw_anchor=shooter_draw_anchor,
        league_foul_rate=league_foul_rate,
        league_avg_foul_draw_rate=league_avg_foul_draw_rate,
    )
