"""Game simulation orchestrator.

Public surface (unchanged for callers):
    load_roster(db, team_id, season) -> list[dict]
    simulate_game(home_players, away_players, seed, ...) -> dict
"""
import random
from typing import Optional

from sqlalchemy import select

from app.models.team_season_stats import TeamSeasonStats
from app.services.behavior.pipeline import BehaviorPipeline
from app.services.behavior_profile import NORMAL_PROFILE, profile_for_phase
from app.services.box_score import apply_typed_event, empty_stats, snapshot_box
from app.services.diagnostics import SimulationDiagnostics
from app.services.game_phase import derive_phase
from app.services.game_state import GameState
from app.services.late_game import (
    build_context,
    possession_time_override,
    should_concede,
)
from app.services.lineup_quality import compute_lineup_quality, rotation_baseline
from app.services.modifiers.base import GameSnapshot, ModifierAdjustments, PlayerGameState
from app.services.possession import OREB_RATE, resolve_possession
from app.services.possession_context import make_context
from app.services.possession_events import describe_typed_event, possession_to_events
from app.services.roster import load_roster
from app.services.rotation import (
    GAME_MINUTES,
    MODE_CLOSE_LATE,
    MODE_GARBAGE,
    MODE_OT_CLOSE,
    MODE_SCHEDULED,
    build_rotation,
    build_rotation_interval,
    patch_rotation,
    resolve_lineup,
)

# Re-export so existing callers (API, tests, scratch scripts) need no changes.
__all__ = ["load_roster", "simulate_game", "describe_typed_event"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
QUARTER_SECONDS = 720
OT_SECONDS = 300
HOME_ADVANTAGE = 3.0
ELIGIBLE_MISS_RATE = 0.32

# Fix #3a.2 (2026-09-03, shipped): MODE_CLOSE_LATE — real coaches close a tight Q4
# with their best five. Fires in the last 5 min of Q4 when |margin| ≤ 5. Uses the
# projection-aware cap (see rotation.py) so already-calibrated stars don't get
# pushed above their real MPG. Per-min efficiency inflation for a subset of stars
# is a separate mechanism banked as a follow-up (see project-full-league-realism-audit).
_ENABLE_CLOSE_LATE = True

# team_defense_factor divides a team's def_rating by the LEAGUE average to get a
# relative multiplier centered at 1.0. That average must be the era's, not a fixed
# modern constant (~113) — otherwise old eras (league def_rating ~105) are uniformly
# suppressed and modern boosted, biasing shot efficiency by era. Cached per season.
_LEAGUE_DEF_CACHE: dict = {}


def _league_avg_def_rating(db, season: str, fallback: float) -> float:
    if season not in _LEAGUE_DEF_CACHE:
        vals = [r.def_rating for r in db.execute(
            select(TeamSeasonStats).where(TeamSeasonStats.season == season)
        ).scalars().all() if r.def_rating is not None]
        _LEAGUE_DEF_CACHE[season] = sum(vals) / len(vals) if vals else fallback
    return _LEAGUE_DEF_CACHE[season]


def _select_game_rosters(
    home_players: list, away_players: list, cfg, rng, season, db,
    home_team_id, away_team_id, unavailable_player_ids,
) -> tuple:
    """Resolve tonight's rosters. Returns (home_pool, away_pool, home_active, away_active).

    Pool = full loaded roster (pre-selection), retained so callers can tell rostered-but-
    inactive (DNP) from never-entered. Active = who dresses: with `use_availability`, ~10 of
    a deeper roster drawn from games_played (eligibility only — the rotation engine is
    untouched; returns fresh dicts). Draw order on `rng` matters: home then away.
    """
    home_pool, away_pool = home_players, away_players
    if cfg.use_availability:
        from app.services.availability import select_active_roster
        # Availability needs the deeper pool: if handed a shallow roster (a caller that
        # loaded the default depth), reload to roster_depth here so every path benefits
        # without caller surgery.
        depth = getattr(cfg, "roster_depth", 10)
        if db is not None and season and len(home_players) < depth and home_team_id and away_team_id:
            from app.services.roster import load_roster
            hp = load_roster(db, home_team_id, season, depth=depth,
                             pre_negation=cfg.use_pre_negation_probs,
                             paint_mid_split=cfg.use_paint_mid_split)
            ap = load_roster(db, away_team_id, season, depth=depth,
                             pre_negation=cfg.use_pre_negation_probs,
                             paint_mid_split=cfg.use_paint_mid_split)
            if hp:
                home_pool = hp
            if ap:
                away_pool = ap
        # M-4 bug fix: the reload above pulls the FULL team roster from PSS/Player and would
        # silently re-include players who are OUT per MyLeague events. When the caller (e.g.
        # advance_to) passes an unavailable set, apply it to the reloaded pool so events
        # actually gate who can play.
        if unavailable_player_ids:
            home_pool = [p for p in home_pool if p["id"] not in unavailable_player_ids]
            away_pool = [p for p in away_pool if p["id"] not in unavailable_player_ids]
    # gap 3.4g: annotate each pool's full-strength creation reference BEFORE availability
    # copies the active subset (select_active_roster does dict(p), so the ref propagates).
    if cfg.use_lineup_creation and db is not None and season:
        from app.services.lineup_creation import annotate_team_baseline, ensure_league_baseline
        ensure_league_baseline(db, season, cfg.creation_form)
        annotate_team_baseline(home_pool, cfg.creation_form)
        annotate_team_baseline(away_pool, cfg.creation_form)
    if cfg.use_availability:
        home_players = select_active_roster(home_pool, rng, cfg)
        away_players = select_active_roster(away_pool, rng, cfg)
    return home_pool, away_pool, home_players, away_players


def _load_team_stats(db, season, team_id) -> Optional[dict]:
    """Pace / def_rating / oreb_pct for the pace, defense and OREB modifiers; None if absent."""
    if db is None or not season or not team_id:
        return None
    row = db.execute(select(TeamSeasonStats).where(
        TeamSeasonStats.team_id == team_id,
        TeamSeasonStats.season == season,
    )).scalar_one_or_none()
    if not row:
        return None
    return {"pace": row.pace, "def_rating": row.def_rating, "oreb_pct": row.oreb_pct}


def _pair_subs(added: list, removed: list, by_pos: dict) -> list:
    """Position-aware 1:1 pairing so PBP reads "P_in for P_out" sensibly.

    A pure sorted-id zip (pre-2026-09-30) paired whichever added/removed players happened
    to land at the same list index — with multi-player swaps now routine (interval rotation
    scheduler), that produced nonsensical labels like a backup PG "subbing in for" the
    starting C (caught in UAT 2026-09-30). Greedily match each added player to a removed
    player at the SAME position first, falling back to leftover sorted-id pairing for
    anyone left over. Display-only — box score / accounting are unaffected either way.
    Returns [(player_in, player_out)], either side None if the counts differ.
    """
    leftover_removed = list(removed)
    pairs: list = []
    for pid_in in added:
        pos_in = by_pos.get(pid_in, {}).get("position")
        match = next((r for r in leftover_removed if by_pos.get(r, {}).get("position") == pos_in), None)
        if match is not None:
            leftover_removed.remove(match)
            pairs.append((pid_in, match))
        else:
            pairs.append((pid_in, None))
    for pid_out in leftover_removed:
        # First unmatched pair absorbs a leftover "out" with no position match.
        for idx, (pid_in, matched_out) in enumerate(pairs):
            if matched_out is None:
                pairs[idx] = (pid_in, pid_out)
                break
        else:
            pairs.append((None, pid_out))
    return pairs


class _GameSim:
    """One game's mutable simulation state plus the per-period / per-possession steps
    that advance it. `simulate_game` builds one and calls `run()`."""

    def __init__(
        self,
        home_players: list[dict],
        away_players: list[dict],
        seed: int,
        season: Optional[str] = None,
        steps: Optional[int] = None,
        capture_descriptions: bool = False,
        config: Optional["SimConfig"] = None,
        home_team_id: Optional[int] = None,
        away_team_id: Optional[int] = None,
        db: Optional[object] = None,
        unavailable_player_ids: Optional[set] = None,
    ) -> None:
        self.home_players = home_players
        self.away_players = away_players
        self.season = season
        self.steps = steps
        self.capture_descriptions = capture_descriptions
        from app.services.sim_config import SimConfig
        self.cfg: SimConfig = config if config is not None else SimConfig()

        self.rng = random.Random(seed)

        self.home_pool, self.away_pool, self.home_players, self.away_players = _select_game_rosters(
            self.home_players, self.away_players, self.cfg, self.rng, self.season, db,
            home_team_id, away_team_id, unavailable_player_ids,
        )

        # Era-anchored league normalization for the foul model. Built from the FULL
        # pool (pre-availability) so the anchor reflects the season's rostered pool,
        # not tonight's active subset. Only computed when the toggle is on; when off,
        # ctx.season_ctx stays None and possession.py falls back to module constants
        # (byte-identical to previous behavior).
        self.season_ctx = None
        if self.cfg.use_season_context and self.season:
            from app.services.season_context import build_season_context
            self.season_ctx = build_season_context(self.season, [self.home_pool, self.away_pool])

        self.home_by_id = {p["id"]: p for p in self.home_players}
        self.away_by_id = {p["id"]: p for p in self.away_players}
        self.name_map = (
            {p["id"]: p["name"] for p in self.home_players + self.away_players}
            if (self.capture_descriptions or self.steps)
            else None
        )

        # Load team season stats for pace/defense/OREB modifiers
        self.home_stats = _load_team_stats(db, self.season, home_team_id)
        self.away_stats = _load_team_stats(db, self.season, away_team_id)

        self.league_avg_def = (
            _league_avg_def_rating(db, self.season, self.cfg.league_avg_def_rating)
            if db is not None and self.season and self.cfg.use_team_defense else self.cfg.league_avg_def_rating
        )

        self.home_oreb_rate = ((self.home_stats or {}).get("oreb_pct") or OREB_RATE) if self.cfg.use_team_oreb else OREB_RATE
        self.away_oreb_rate = ((self.away_stats or {}).get("oreb_pct") or OREB_RATE) if self.cfg.use_team_oreb else OREB_RATE

        home_pace = (self.home_stats or {}).get("pace", self.cfg.league_avg_pace)
        away_pace = (self.away_stats or {}).get("pace", self.cfg.league_avg_pace)
        self.expected_possessions = round((home_pace + away_pace) / 2) * 2

        self._init_lineups()
        self._init_state()

    def _init_lineups(self) -> None:
        """Per-game form factors, rotations and defensive baselines. RNG draws happen here in a
        fixed order (form factors, home rotation, away rotation, tip), so do not reorder."""
        # Per-game form factors — drawn once at game start, held for full game.
        # When use_player_variance is off, all factors default to 1.0 (no effect).
        if self.cfg.use_player_variance:
            self.form_factors: dict = {
                p["id"]: max(0.90, min(1.10, self.rng.gauss(1.0, p.get("player_variance", 0.03))))
                for p in self.home_players + self.away_players
            }
        else:
            self.form_factors = {}

        _build_rotation = build_rotation_interval if self.cfg.use_interval_rotation else build_rotation
        self.home_rotation = _build_rotation(self.home_players, self.rng)
        self.away_rotation = _build_rotation(self.away_players, self.rng)
        self.home_def_baseline = rotation_baseline(self.home_players)
        self.away_def_baseline = rotation_baseline(self.away_players)
        self.tip_winner_is_home = self.rng.random() < 0.5

        self.box: dict = {pid: empty_stats() for pid in list(self.home_by_id) + list(self.away_by_id)}
        # Hierarchy sort by availability-normalized `minutes` (not raw mpg). Ships
        # WITH MODE_OT_CLOSE (2026-08-17) but the mpg-hierarchy change is bundled
        # with the unresolved scheduler question — see project-rotation-attempt-4
        # for the GSW compensation-removal that recurs when this sort key changes
        # without a scheduler fix. Left as-is until scheduler + hierarchy can ship
        # together.
        self.home_by_min = sorted(self.home_players, key=lambda p: p["minutes"], reverse=True)
        self.away_by_min = sorted(self.away_players, key=lambda p: p["minutes"], reverse=True)

    def _init_state(self) -> None:
        """Chunking, event buffers, game state, foul-out set and the pace-derived clock mean."""
        self.chunk_duration = GAME_MINUTES / self.steps if self.steps else None
        self.next_threshold = [self.chunk_duration]
        self.chunks: list = []
        self.chunk_events: list = []
        self.current_chunk_events: list = []
        # Typed event stream (RFC.md "Event-Sourced PBP") — one list of granular typed
        # events per possession (SHOT/FOUL/FT/REB/TOV/STL/BLK/AST). Descriptions attached
        # server-side via describe_typed_event when name_map is available.
        self.all_events: list = []
        # Always-populated typed stream (regardless of capture_descriptions/steps).
        # Used by the regression fence + any consumer that wants raw events without
        # opting into description building. Downstream of API routes ignore it.
        self._typed_all: list = []
        self.gs = GameState()   # persistent, authoritative game state (roadmap stage B)

        # Substitution tracking. `SUBSTITUTION` events emit at every rotation
        # transition so the typed event stream is internally sufficient to
        # reconstruct on-court state at any point. Initial 5 emits as
        # possession=0 SUBs with player_out=None; deltas emit tagged with the
        # just-completed possession's number so `apply_typed_event(SUB)` is a
        # stat no-op relative to the possession that just resolved.
        # See tests/test_lineup_reconstruction.py for the correctness gate.
        self.prev_home_ids: Optional[list] = None
        self.prev_away_ids: Optional[list] = None

        # -----------------------------------------------------------------------
        # Regulation
        # -----------------------------------------------------------------------
        self.home_ids = set(self.home_by_id.keys())
        self.away_ids = set(self.away_by_id.keys())

        # Game-scope foul-out set. INVARIANT: once a player is added to this set
        # during a game they cannot return in any subsequent period (Q1→OTn).
        # Every on-court consumer (regular possession lineup, strategic-foul
        # picker) filters against this so a fouled-out player is removed the
        # instant their 6th PF is credited, not delayed to the next-minute
        # rotation patch. Fixes the 7-fouls bug (see project-bug-7-fouls-jokic).
        self.fouled_out: set[int] = set()

        self.home_player_gs = {
            p["id"]: PlayerGameState(player_id=p["id"], clutch_rating=p.get("clutch_rating", 50))
            for p in self.home_players
        }
        self.away_player_gs = {
            p["id"]: PlayerGameState(player_id=p["id"], clutch_rating=p.get("clutch_rating", 50))
            for p in self.away_players
        }

        self.behavior = BehaviorPipeline(self.cfg, self.home_players, self.away_players)

        self.ot_period = 0
        # Per-team concession state (hysteresis lives in late_game.should_concede)
        self.gs.home_conceded = False
        self.gs.away_conceded = False

        # Possession accounting — every mechanic that affects possession count reports
        # its contribution (CLAUDE.md guardrail 5). See app/services/diagnostics.py.
        self.diag = SimulationDiagnostics(pace_budget=self.expected_possessions)

        mean_quarter_possessions = self.expected_possessions / 4
        target_mean = QUARTER_SECONDS / mean_quarter_possessions

        # NBA pace = DISTINCT possessions; an offensive rebound continues the same
        # possession, it is not a new one (see analysis/accounting.py). So the budget is
        # spent only on distinct possessions — fastbreaks (which ARE distinct, just fast)
        # are compensated so the average distinct possession still hits target_mean, but
        # second chances are NOT: they are extra shot opportunities layered on top, taking
        # their own clock. Folding them into the budget (the old f_sc term) starved the
        # sim of ~10 distinct possessions/game — the FGA/OREB shortfall the accounting found.
        f_fb = self.cfg.fastbreak_poss_frac if self.cfg.use_fast_break else 0.0
        if self.cfg.use_catch_up:
            target_mean *= 1.0 + self.cfg.catch_up_clock_frac
        # pre-bonus shot-clock resets extend possessions (gap 3.7 2b); reclaim that time from
        # the halfcourt mean so distinct-possession pace holds (Stage 2 compensation).
        reset_comp = self.cfg.foul_reset_poss_frac * self.cfg.foul_reset_time_mean if self.cfg.use_bonus_system else 0.0
        self.mean_poss_time_clock = (target_mean - f_fb * self.cfg.fastbreak_time_mean - reset_comp) / (1.0 - f_fb)


    def _emit_subs(self, new_home_ids: list, new_away_ids: list,
                   quarter: int, clock_seconds: int) -> list:
        """Diff the previous lineup against the new one, return SUB events.

        First call (prev_*_ids is None) emits initial-lineup SUBs with
        player_out=None; subsequent calls emit one SUB per delta pair.

        All SUBs are tagged with the UPCOMING possession's number
        (gs.possession_number + 1) — the possession this transition affects.
        Using the upcoming number instead of the completed one keeps SUBs
        aligned with a real possession even at game start (possession=1 for
        initial 5), so the fence invariant "distinct possessions in typed
        stream == accounting count" holds without special-casing.
        """
        subs: list = []
        upcoming_poss = self.gs.possession_number + 1
        for is_home_side, new_ids, prev_ids in (
            (True,  new_home_ids, self.prev_home_ids),
            (False, new_away_ids, self.prev_away_ids),
        ):
            if prev_ids is None:
                for pid in new_ids:
                    subs.append({
                        "type": "SUBSTITUTION",
                        "possession": upcoming_poss,
                        "quarter": quarter,
                        "game_clock_seconds": clock_seconds,
                        "is_home": is_home_side,
                        "player_id": pid,
                        "player_in": pid,
                        "player_out": None,
                        "pts": 0,
                    })
                continue
            prev_set = set(prev_ids)
            new_set = set(new_ids)
            added = sorted(new_set - prev_set)
            removed = sorted(prev_set - new_set)
            pairs = _pair_subs(added, removed, self.home_by_id if is_home_side else self.away_by_id)
            for pid_in, pid_out in pairs:
                subs.append({
                    "type": "SUBSTITUTION",
                    "possession": upcoming_poss,
                    "quarter": quarter,
                    "game_clock_seconds": clock_seconds,
                    "is_home": is_home_side,
                    "player_id": pid_in if pid_in is not None else pid_out,
                    "player_in": pid_in,
                    "player_out": pid_out,
                    "pts": 0,
                })
        self.prev_home_ids = list(new_home_ids)
        self.prev_away_ids = list(new_away_ids)
        return subs


    def _maybe_snapshot(self, elapsed_minutes: float, current_q_idx: int) -> None:
        while self.chunk_duration and elapsed_minutes >= self.next_threshold[0]:
            self.chunks.append({
                "home_score": self.gs.home_score,
                "away_score": self.gs.away_score,
                "elapsed_minutes": round(elapsed_minutes, 1),
                "quarter": current_q_idx + 1,
                "box": snapshot_box(self.box),
            })
            self.chunk_events.append(list(self.current_chunk_events))
            self.current_chunk_events.clear()
            self.next_threshold[0] += self.chunk_duration


    def _apply_possession(self, 
        home_active_ids: list,
        away_active_ids: list,
        is_home: bool,
        sec_per_poss: float,
        min_per_poss_val: float,
        current_q_idx: int,
        game_clock_override: Optional[int] = None,
        team_defense_factor: float = 1.0,
        is_fastbreak: bool = False,
        adjustments: Optional[ModifierAdjustments] = None,
        quarter_clock: float = 720.0,
        behavior_profile: object = None,
        defense_in_bonus: bool = False,
    ):
        self.gs.game_clock += sec_per_poss
        elapsed_minutes = self.gs.game_clock / 60
        self.gs.period_index = current_q_idx

        home_active = [self.home_by_id[pid] for pid in home_active_ids if pid in self.home_by_id]
        away_active = [self.away_by_id[pid] for pid in away_active_ids if pid in self.away_by_id]

        for pid in home_active_ids:
            if pid in self.box:
                self.box[pid]["min"] += min_per_poss_val
        for pid in away_active_ids:
            if pid in self.box:
                self.box[pid]["min"] += min_per_poss_val

        offense, defense = (home_active, away_active) if is_home else (away_active, home_active)
        if not offense or not defense:
            return None, {}

        home_bonus = HOME_ADVANTAGE / self.expected_possessions if is_home else 0.0
        offense_oreb = self.home_oreb_rate if is_home else self.away_oreb_rate
        ctx = make_context(
            offense, defense, self.rng, cfg=self.cfg,
            adjustments=adjustments if adjustments is not None else ModifierAdjustments(),
            home_bonus=home_bonus,
            name_map=self.name_map,
            team_defense_factor=team_defense_factor,
            is_fastbreak=is_fastbreak,
            form_factors=self.form_factors if self.form_factors else None,
            offense_oreb_rate=offense_oreb,
            quarter=current_q_idx + 1,
            clock_seconds=quarter_clock,
            score_margin=self.gs.home_score - self.gs.away_score if is_home else self.gs.away_score - self.gs.home_score,
            behavior_profile=behavior_profile if behavior_profile is not None else NORMAL_PROFILE,
            defense_in_bonus=defense_in_bonus,
            foul_counts={p["id"]: self.box[p["id"]]["pf"] for p in defense if p["id"] in self.box} if self.cfg.use_foul_caution else None,
            season_ctx=self.season_ctx,
        )
        event = resolve_possession(ctx)

        # Event-sourced possession (RFC.md "Event-Sourced PBP"): translate the
        # possession result into granular typed events, apply each to the box via
        # apply_typed_event, then emit into the event stream (chunk_events for
        # steps, all_events otherwise) with per-event descriptions when captured.
        self.gs.possession_number += 1
        clock_secs = game_clock_override if game_clock_override is not None else round(self.gs.game_clock)
        typed = possession_to_events(
            event,
            possession=self.gs.possession_number,
            quarter=current_q_idx + 1,
            game_clock_seconds=clock_secs,
            is_home=is_home,
        )
        pts = 0
        fouled_out_pid: Optional[int] = None
        for tev in typed:
            ev_pts, ev_fo = apply_typed_event(self.box, tev)
            pts += ev_pts
            if ev_fo is not None:
                fouled_out_pid = ev_fo
        # Momentum modifier reads event.get("pts", 0) off the possession result;
        # preserve that field for the behavior pipeline call below.
        event["pts"] = pts

        if is_home:
            self.gs.home_score += pts
        else:
            self.gs.away_score += pts
        self.gs.quarter_scores["home" if is_home else "away"][current_q_idx] += pts

        home_delta = pts if is_home else -pts
        for pid in home_active_ids:
            if pid in self.box:
                self.box[pid]["plus_minus"] += home_delta
        for pid in away_active_ids:
            if pid in self.box:
                self.box[pid]["plus_minus"] -= home_delta

        if self.name_map is not None:
            for tev in typed:
                tev["description"] = describe_typed_event(tev, self.name_map)
        # is_fastbreak is a per-possession context field the frontend uses to
        # tint fast-break shots. Stamp it on every event in this possession.
        if is_fastbreak:
            for tev in typed:
                tev["is_fastbreak"] = True
        self._publish_events(typed)

        self._maybe_snapshot(elapsed_minutes, current_q_idx)
        return fouled_out_pid, event

    def _run_clock_period(self, q_idx: int, period_seconds: float, period_tip_is_home: bool) -> None:
        """One timed period (regulation quarter or OT) — identical mechanics either way.

        OT is not a separate simulation path: it is another timed period with
        different initial conditions (length, jump ball, closing lineups via the
        minute clamp). Every possession-level mechanic applies in any period.
        """
        quarter_clock = float(period_seconds)
        current_is_home = period_tip_is_home
        self.gs.home_quarter_fouls = 0   # team fouls reset each period (bonus tracking)
        self.gs.away_quarter_fouls = 0
        self.gs.home_last2_fouls = 0
        self.gs.away_last2_fouls = 0
        oreb_depth = 0
        next_is_fastbreak = False

        while quarter_clock > 0:
            foul_clock = self._strategic_foul(q_idx, period_seconds, quarter_clock, current_is_home)
            if foul_clock is not None:
                quarter_clock = foul_clock
                current_is_home = not current_is_home
                oreb_depth = 0
                next_is_fastbreak = False
                continue

            poss_category, poss_time = self._sample_possession_time(
                q_idx, quarter_clock, current_is_home, next_is_fastbreak, oreb_depth)
            quarter_clock -= poss_time

            # OT (q_idx >= 4) clamps to minute 47 — closing lineups stay on the floor
            current_minute = min(GAME_MINUTES - 1, q_idx * 12 + int((period_seconds - quarter_clock) / 60))

            home_mode, away_mode, home_active_ids, away_active_ids = self._resolve_lineups(
                q_idx, quarter_clock, current_minute)
            self._emit_lineup_subs(home_active_ids, away_active_ids, q_idx, quarter_clock)

            in_mismatch = self.gs.home_conceded != self.gs.away_conceded
            pre_poss_margin = abs(self.gs.home_score - self.gs.away_score)

            team_defense_factor = self._team_defense_factor(
                current_is_home, home_active_ids, away_active_ids, home_mode, away_mode)

            active_home_gs = {pid: self.home_player_gs[pid] for pid in home_active_ids if pid in self.home_player_gs}
            active_away_gs = {pid: self.away_player_gs[pid] for pid in away_active_ids if pid in self.away_player_gs}
            phase = derive_phase(
                q_idx, abs(self.gs.home_score - self.gs.away_score),
                self.gs.home_conceded, self.gs.away_conceded, self.cfg,
            )
            game_state = GameSnapshot(
                home_score=self.gs.home_score,
                away_score=self.gs.away_score,
                quarter=q_idx + 1,
                clock_seconds=quarter_clock,
                possession_number=self.gs.possession_number,
                home_players=active_home_gs,
                away_players=active_away_gs,
                home_conceded=self.gs.home_conceded,
                away_conceded=self.gs.away_conceded,
                phase=phase.value,
            )

            # All behavior sources (momentum, fatigue, clutch, garbage time, the
            # Q4 objective, ...) combine here — one owner, no inline special cases.
            poss_adjustments = self.behavior.adjustments(current_is_home, game_state)

            # Apply pace_multiplier: quarter_clock was already reduced by poss_time above,
            # so readjust the net clock consumption for the new pace. Endgame-paced
            # possessions skip it — the override already encodes the pacing intent,
            # and stacking both would double-shorten trailing possessions.
            if poss_adjustments and poss_adjustments.pace_multiplier != 1.0 and poss_category != "endgame":
                orig_poss_time = poss_time
                poss_time = max(3.0, orig_poss_time * poss_adjustments.pace_multiplier)
                quarter_clock = max(0.0, quarter_clock + orig_poss_time - poss_time)
                self.diag.catch_up_time_delta += orig_poss_time - poss_time
            self.diag.record_possession(poss_category, poss_time)

            poss_profile = profile_for_phase(phase, self.cfg) if self.cfg.use_behavior_profile else NORMAL_PROFILE
            # the DEFENSIVE team (not on offense) is in the bonus at the team-foul limit,
            # OR (NBA last-2:00 rule) once it has already committed a foul in the window,
            # so its next (2nd) foul there draws FTs.
            def_fouls_now = self.gs.away_quarter_fouls if current_is_home else self.gs.home_quarter_fouls
            def_last2_now = self.gs.away_last2_fouls if current_is_home else self.gs.home_last2_fouls
            defense_in_bonus = self.cfg.use_bonus_system and (
                def_fouls_now >= self.cfg.bonus_foul_threshold
                or (quarter_clock <= self.cfg.last2min_clock and def_last2_now >= 1)
            )
            fouled_out_pid, event = self._apply_possession(
                home_active_ids, away_active_ids, current_is_home,
                poss_time, poss_time / 60.0, q_idx,
                game_clock_override=int(quarter_clock),
                team_defense_factor=team_defense_factor,
                is_fastbreak=next_is_fastbreak,
                adjustments=poss_adjustments,
                quarter_clock=quarter_clock,
                behavior_profile=poss_profile,
                defense_in_bonus=defense_in_bonus,
            )
            self.behavior.update(event, current_is_home, game_state)

            if in_mismatch:
                self.diag.record_mismatch(abs(self.gs.home_score - self.gs.away_score) - pre_poss_margin)

            self._record_possession_aftermath(
                event, home_active_ids, away_active_ids, poss_time, current_is_home,
                quarter_clock, fouled_out_pid, current_minute)

            quarter_clock = self._pre_bonus_foul_time(event, quarter_clock)
            oreb_depth, current_is_home, next_is_fastbreak = self._next_possession_state(
                event, current_is_home, oreb_depth)

    def _strategic_foul(self, q_idx: int, period_seconds: float, quarter_clock: float,
                        current_is_home: bool) -> Optional[float]:
        """Intentional-foul sequence for the trailing team's opponent. Returns the new
        quarter_clock when a foul sequence ran (the caller then flips possession and
        resets OREB/fastbreak state), or None when it did not fire."""
        # Strategic foul check — final period only (Q4 or any OT): intentional
        # fouling is an end-of-GAME tactic. (Accounting run caught this firing
        # at the end of Q1-Q3 too: 83% of games had foul sequences vs ~25% real.)
        if not (self.cfg.use_strategic_foul and q_idx >= 3
                and quarter_clock <= self.cfg.strategic_foul_clock_threshold):
            return None
        lead = self.gs.home_score - self.gs.away_score
        trailing_is_home = lead < 0
        if current_is_home == trailing_is_home:
            return None
        margin = abs(lead)
        if not (self.cfg.strategic_foul_margin_min <= margin <= self.cfg.strategic_foul_margin_max):
            return None
        if not (self.rng.random() < self.cfg.strategic_foul_probability):
            return None
        offense_on_court = [
            p for p in (self.home_players if current_is_home else self.away_players)
            if p["id"] in (self.home_ids if current_is_home else self.away_ids)
            and p["id"] not in self.fouled_out
        ]
        from app.services.possession import _free_throw_prob
        target = min(offense_on_court, key=_free_throw_prob)
        ft_prob = _free_throw_prob(target)
        fta = 2
        ftm = sum(1 for _ in range(fta) if self.rng.random() < ft_prob)
        foul_time = max(2.0, min(8.0, self.rng.gauss(4.0, 1.0)))
        quarter_clock = max(0.0, quarter_clock - foul_time)
        self.gs.game_clock += foul_time
        self.diag.record_possession("strategic_foul", foul_time)
        self.gs.possession_number += 1
        # Pick a defender to display as the fouler. Foul-rate
        # weighted so the choice mirrors the normal non-shooting
        # foul selection in possession.py. Both FOUL and FT
        # events flow through apply_typed_event below —
        # `apply_typed_event` is the sole accounting sink, so
        # PF, FTA/FTM, and points all reach the box and the
        # score via the same event stream (no bypass paths).
        defense_on_court = [
            p for p in (self.away_players if current_is_home else self.home_players)
            if p["id"] in (self.away_ids if current_is_home else self.home_ids)
            and p["id"] not in self.fouled_out
        ]
        # Deterministic pick: the defender most willing to foul (matches
        # the coaching pattern of sending a bench player with fouls to
        # spare). Draws no RNG.
        fouler = max(defense_on_court,
                     key=lambda p: (p.get("foul_rate", 0.09), p["id"]))
        hdr = dict(
            possession=self.gs.possession_number,
            quarter=q_idx + 1,
            game_clock_seconds=int(quarter_clock),
            is_home=not current_is_home,  # defender's team
        )
        strategic_events = [
            {**hdr, "type": "FOUL", "player_id": fouler["id"], "pts": 0,
             "foul_kind": "non_shooting", "fouled_on": target["id"],
             "intentional": True, "strategic": True},
        ]
        ft_hdr = dict(hdr, is_home=current_is_home)  # shooter's team
        for i in range(fta):
            strategic_events.append({
                **ft_hdr, "type": "FT", "player_id": target["id"],
                "pts": 1 if i < ftm else 0,
                "attempt": i + 1, "of": fta, "made": i < ftm,
                "strategic": True,
            })
        if self.name_map is not None:
            for tev in strategic_events:
                tev["description"] = describe_typed_event(tev, self.name_map)
        # Route strategic events through the sole accounting
        # sink. FOUL credits PF; FT events credit FTA/FTM/pts
        # on the target and their summed pts drive the score
        # update below. sum(ev_pts) == ftm by construction,
        # so this is score-invariant vs the previous direct
        # `gs.home_score += ftm` path — same numbers, one
        # pipeline.
        strategic_fouled_out_pid: Optional[int] = None
        strategic_pts = 0
        for tev in strategic_events:
            ev_pts, ev_fo = apply_typed_event(self.box, tev)
            strategic_pts += ev_pts
            if ev_fo is not None:
                strategic_fouled_out_pid = ev_fo
        if current_is_home:
            self.gs.home_score += strategic_pts
            self.gs.quarter_scores["home"][q_idx] += strategic_pts
        else:
            self.gs.away_score += strategic_pts
            self.gs.quarter_scores["away"][q_idx] += strategic_pts
        if strategic_fouled_out_pid:
            self.fouled_out.add(strategic_fouled_out_pid)   # immediate — next lookups filter them out
            current_minute = min(
                GAME_MINUTES - 1,
                q_idx * 12 + int((period_seconds - quarter_clock) / 60),
            )
            if strategic_fouled_out_pid in self.home_by_id:
                patch_rotation(self.home_rotation, strategic_fouled_out_pid, self.home_by_min, current_minute + 1, self.box)
            else:
                patch_rotation(self.away_rotation, strategic_fouled_out_pid, self.away_by_min, current_minute + 1, self.box)
        self._publish_events(strategic_events)
        self._maybe_snapshot(self.gs.game_clock / 60, q_idx)
        return quarter_clock

    def _sample_possession_time(self, q_idx: int, quarter_clock: float, current_is_home: bool,
                                next_is_fastbreak: bool, oreb_depth: int) -> tuple:
        """Draw this possession's clock cost. Returns (category, seconds), already clamped
        to the time left in the period."""
        if next_is_fastbreak:
            poss_category = "fastbreak"
            poss_time = max(3.0, min(12.0, self.rng.gauss(self.cfg.fastbreak_time_mean, self.cfg.fastbreak_time_std)))
        elif oreb_depth > 0:
            poss_category = "second_chance"
            poss_time = max(3.0, min(14.0, self.rng.gauss(self.cfg.second_chance_time_mean, self.cfg.second_chance_time_std)))
        else:
            poss_category = "halfcourt"
            poss_time = max(5.0, min(24.0, self.rng.gauss(self.mean_poss_time_clock, self.cfg.halfcourt_time_std)))

        # Endgame incentive pacing (gap 1.2): inside the window, possession
        # time reflects incentives — trailing plays fast, leading milks.
        # Uncompensated in the pace budget on purpose: like strategic fouls,
        # extra endgame possessions are state-dependent and should emerge.
        if self.cfg.use_endgame_pacing and poss_category == "halfcourt":
            lg_ctx = build_context(q_idx, quarter_clock, self.gs.home_score, self.gs.away_score, current_is_home, self.cfg)
            override = possession_time_override(lg_ctx, self.cfg, self.rng)
            if override is not None:
                self.diag.endgame_time_delta += poss_time - override
                poss_time = override
                poss_category = "endgame"

        # End-of-period hold-for-last-shot (2026-08-17). Real Q1-Q3 last-made
        # FGs cluster in the final 5s (~33% of them) — sim was at ~15% of
        # that rate. When quarter_clock enters the window, extend halfcourt
        # possession time so the shot leaves the intended few seconds. Only
        # LENGTHENS poss_time — if endgame_pacing already stretched it (Q4
        # milk), we keep the longer value.
        if (self.cfg.use_hold_last_shot
                and poss_category in ("halfcourt", "endgame")
                and self.cfg.hold_last_shot_leave_max < quarter_clock <= self.cfg.hold_last_shot_clock_max):
            leave = self.rng.uniform(self.cfg.hold_last_shot_leave_min, self.cfg.hold_last_shot_leave_max)
            hold_time = quarter_clock - leave
            if hold_time > poss_time:
                poss_time = hold_time
        poss_time = min(poss_time, quarter_clock)
        return poss_category, poss_time

    def _resolve_lineups(self, q_idx: int, quarter_clock: float, current_minute: int) -> tuple:
        """Update concession state, pick each team's rotation mode, and resolve the five on
        court. Returns (home_mode, away_mode, home_active_ids, away_active_ids)."""
        # Rotation mode: reactive to game state, schedule as baseline. Each
        # team decides independently whether to concede (asymmetric
        # incentives — see late_game.should_concede).
        if self.cfg.use_garbage_rotation:
            margin_abs = abs(self.gs.home_score - self.gs.away_score)
            home_leads = self.gs.home_score >= self.gs.away_score
            was_any = self.gs.home_conceded or self.gs.away_conceded
            self.gs.home_conceded = should_concede(
                home_leads, margin_abs, quarter_clock, q_idx, self.cfg, self.gs.home_conceded)
            self.gs.away_conceded = should_concede(
                not home_leads, margin_abs, quarter_clock, q_idx, self.cfg, self.gs.away_conceded)
            if (self.gs.home_conceded or self.gs.away_conceded) and not was_any:
                self.diag.record_garbage_entry(margin_abs)
            if self.gs.home_conceded or self.gs.away_conceded:
                self.diag.record_garbage_possession()
        # OT closing lineup takes precedence over garbage: real coaches
        # close OT with their best five regardless of margin. See
        # project-star-mpg-margin-bucket (real OT Jokić 44.83 vs sim 34.37).
        is_ot = q_idx >= 4
        # EXPERIMENT Fix #3a (2026-09-02): Q4 close-late star concentration.
        # Real coaches close a tight Q4 with their best five — analogous to OT_CLOSE.
        # Trigger: q_idx == 3 (Q4) AND |margin| ≤ 5 AND clock ≤ 300s (last 5 min).
        # Priority: OT_CLOSE > CLOSE_LATE > GARBAGE > SCHEDULED.
        is_close_late = (
            _ENABLE_CLOSE_LATE and q_idx == 3
            and abs(self.gs.home_score - self.gs.away_score) <= 5
            and quarter_clock <= 300.0
        )
        home_mode = (MODE_OT_CLOSE if is_ot
                     else (MODE_CLOSE_LATE if is_close_late
                           else (MODE_GARBAGE if self.gs.home_conceded else MODE_SCHEDULED)))
        away_mode = (MODE_OT_CLOSE if is_ot
                     else (MODE_CLOSE_LATE if is_close_late
                           else (MODE_GARBAGE if self.gs.away_conceded else MODE_SCHEDULED)))
        home_active_ids = resolve_lineup(
            self.home_rotation, current_minute, self.home_by_min, self.box,
            home_mode,
            foul_trouble_subs=self.cfg.use_foul_trouble_subs)
        away_active_ids = resolve_lineup(
            self.away_rotation, current_minute, self.away_by_min, self.box,
            away_mode,
            foul_trouble_subs=self.cfg.use_foul_trouble_subs)
        # Enforce the foul-out invariant: rotation lookups schedule the
        # replacement for `current_minute + 1`, so the same-minute lookup
        # (or OT's clamped-back-to-47 lookup) can still return a player
        # who fouled out this minute. Filter here so downstream sees
        # the physically-legal 5-on-court set. See project-bug-7-fouls-jokic.
        if self.fouled_out:
            home_active_ids = [pid for pid in home_active_ids if pid not in self.fouled_out]
            away_active_ids = [pid for pid in away_active_ids if pid not in self.fouled_out]
        return home_mode, away_mode, home_active_ids, away_active_ids

    def _emit_lineup_subs(self, home_active_ids: list, away_active_ids: list,
                          q_idx: int, quarter_clock: float) -> None:
        """Emit SUB events for the transition INTO this possession. Tagged with the
        just-completed possession's number (gs.possession_number is not yet incremented for
        this iteration) so SUBs sit at the tail of the completing possession's chunk, and
        box-score accumulation for the possession about to resolve unambiguously belongs to
        the NEW lineup."""
        subs = self._emit_subs(
            home_active_ids, away_active_ids,
            quarter=q_idx + 1,
            clock_seconds=int(quarter_clock),
        )
        if subs:
            if self.name_map is not None:
                for sev in subs:
                    sev["description"] = describe_typed_event(sev, self.name_map)
            self._publish_events(subs)

    def _team_defense_factor(self, current_is_home: bool, home_active_ids: list, away_active_ids: list,
                             home_mode: str, away_mode: str) -> float:
        """Season def_rating relative to the league (dampened), times the lineup-quality factor
        for the five actually defending."""
        team_defense_factor = 1.0
        if self.cfg.use_team_defense:
            defending_stats = self.away_stats if current_is_home else self.home_stats
            if defending_stats:
                raw = defending_stats["def_rating"] / self.league_avg_def
                team_defense_factor = 1.0 + (raw - 1.0) * self.cfg.team_defense_coefficient

        # Lineup quality: season def_rating describes the normal rotation;
        # the factor below moves with the five actually defending.
        if self.cfg.use_lineup_quality:
            if current_is_home:
                def_lineup = [self.away_by_id[pid] for pid in away_active_ids if pid in self.away_by_id]
                def_baseline = self.away_def_baseline
                def_mode = away_mode
            else:
                def_lineup = [self.home_by_id[pid] for pid in home_active_ids if pid in self.home_by_id]
                def_baseline = self.home_def_baseline
                def_mode = home_mode
            lq = compute_lineup_quality(def_lineup, def_baseline)
            team_defense_factor *= lq["defense"]
            self.diag.record_lineup_defense(def_mode, lq["defense"])
        return team_defense_factor

    def _record_possession_aftermath(self, event: dict, home_active_ids: list, away_active_ids: list,
                                     poss_time: float, current_is_home: bool, quarter_clock: float,
                                     fouled_out_pid: Optional[int], current_minute: int) -> None:
        """Per-player game state (minutes, fouls), period team-foul counters for the bonus,
        and the immediate rotation patch for a player who just fouled out."""
        poss_minutes = poss_time / 60.0
        for pid in home_active_ids:
            if pid in self.home_player_gs:
                self.home_player_gs[pid].minutes_played += poss_minutes
        for pid in away_active_ids:
            if pid in self.away_player_gs:
                self.away_player_gs[pid].minutes_played += poss_minutes
        for foul_pid in (event.get("fouled_by"), event.get("nonshooting_foul_by")):
            if foul_pid is None:
                continue
            if foul_pid in self.home_player_gs:
                self.home_player_gs[foul_pid].fouls += 1
            elif foul_pid in self.away_player_gs:
                self.away_player_gs[foul_pid].fouls += 1

        # team fouls this period (bonus tracking) — only DEFENSIVE fouls count. A
        # shooting/bonus foul has fouled_by != turnover_by (offensive fouls set both
        # to the ball handler); a pre-bonus non-shooting foul is always defensive.
        if self.cfg.use_bonus_system:
            def_committed = int(
                event.get("fouled_by") is not None
                and event.get("fouled_by") != event.get("turnover_by")
            ) + int(event.get("nonshooting_foul_by") is not None)
            in_last2 = quarter_clock <= self.cfg.last2min_clock
            if current_is_home:
                self.gs.away_quarter_fouls += def_committed
                if in_last2:
                    self.gs.away_last2_fouls += def_committed
            else:
                self.gs.home_quarter_fouls += def_committed
                if in_last2:
                    self.gs.home_last2_fouls += def_committed

        if fouled_out_pid:
            self.fouled_out.add(fouled_out_pid)   # immediate — next lookups filter them out
            if fouled_out_pid in self.home_by_id:
                patch_rotation(self.home_rotation, fouled_out_pid, self.home_by_min, current_minute + 1, self.box)
            else:
                patch_rotation(self.away_rotation, fouled_out_pid, self.away_by_min, current_minute + 1, self.box)

    def _pre_bonus_foul_time(self, event: dict, quarter_clock: float) -> float:
        """Clock consumed by a pre-bonus non-shooting foul + inbound. Returns the new quarter_clock."""
        # Pre-bonus non-shooting foul is now an in-possession event (see
        # possession._select_action + _restart_offensive_phase). The
        # statistical possession does NOT terminate on the foul; the offense
        # keeps the ball and play resumes within the same resolve_possession
        # call. What the game_simulator still owns is the CLOCK time that
        # the foul + inbound consumes — deducted inline here so the total
        # clock accounting per pre-bonus stat_poss matches the pre-refactor
        # (halfcourt time + foul_reset_time).
        if event.get("nonshooting_foul_by") is not None:
            extra = max(3.0, min(14.0, self.rng.gauss(self.cfg.foul_reset_time_mean, self.cfg.foul_reset_time_std)))
            extra = min(extra, quarter_clock)
            quarter_clock -= extra
            # Attribute the extra time to the foul_reset diagnostic bucket but
            # do NOT increment the possession COUNT — this is time consumed
            # WITHIN the same statistical possession (not a new possession).
            self.diag.time["foul_reset"] += extra
            self.diag.pre_bonus_fouls += 1
        return quarter_clock

    def _next_possession_state(self, event: dict, current_is_home: bool, oreb_depth: int) -> tuple:
        """Who has the ball next. An offensive rebound continues the same possession (chain capped
        by oreb_chain_cap); otherwise possession flips and a steal may start a fast break.
        Returns (oreb_depth, current_is_home, next_is_fastbreak)."""
        next_is_fastbreak = False
        rebounded_by = event.get("rebounded_by")
        offense_ids = self.home_ids if current_is_home else self.away_ids
        is_oreb = (
            self.cfg.use_second_chance
            and rebounded_by is not None
            and rebounded_by in offense_ids
            and event.get("shot_type") is not None
            and not event.get("made")
        )
        if is_oreb and oreb_depth < self.cfg.oreb_chain_cap:
            oreb_depth += 1
        else:
            oreb_depth = 0
            current_is_home = not current_is_home
            if (self.cfg.use_fast_break and event.get("steal_by") is not None
                    and self.rng.random() < self.cfg.steal_fastbreak_prob):
                next_is_fastbreak = True
        return oreb_depth, current_is_home, next_is_fastbreak

    def _publish_events(self, events: list) -> None:
        """Route events into the always-on typed stream and, per mode, the chunk or full event list."""
        self._typed_all.extend(events)
        if self.steps:
            self.current_chunk_events.extend(events)
        elif self.capture_descriptions:
            self.all_events.extend(events)


    def run(self) -> dict:

        for reg_q in range(4):
            self._run_clock_period(
                reg_q, QUARTER_SECONDS,
                self.tip_winner_is_home if reg_q % 2 == 0 else not self.tip_winner_is_home,
            )

        # OT: another timed period — new jump ball, 300s, closing lineups
        while self.gs.home_score == self.gs.away_score:
            self.ot_period += 1
            self.gs.quarter_scores["home"].append(0)
            self.gs.quarter_scores["away"].append(0)
            self._run_clock_period(3 + self.ot_period, OT_SECONDS, self.rng.random() < 0.5)

        # Final snapshot
        if self.steps and (not self.chunks or self.chunks[-1]["home_score"] != self.gs.home_score or self.chunks[-1]["away_score"] != self.gs.away_score):
            self.chunks.append({
                "home_score": self.gs.home_score,
                "away_score": self.gs.away_score,
                "elapsed_minutes": round(self.gs.game_clock / 60, 1),
                "quarter": self.gs.period_index + 1,
                "box": snapshot_box(self.box),
            })
            self.chunk_events.append(list(self.current_chunk_events))

        return {
            "season": self.season,
            "home_score": self.gs.home_score,
            "away_score": self.gs.away_score,
            "quarter_scores": self.gs.quarter_scores,
            "box_score": self.box,
            "chunks": self.chunks,
            "chunk_events": self.chunk_events,
            "events": self.all_events,
            "typed_events": self._typed_all,
            "went_to_ot": self.ot_period > 0,
            "ot_periods": self.ot_period,
            "possession_accounting": self.diag.as_dict(),
            # Rosters as the engine actually used them: `active` are the players who dressed
            # (post-availability), `pool` is the full loaded roster so callers can render DNPs.
            "home_active": self.home_players,
            "away_active": self.away_players,
            "home_pool": self.home_pool,
            "away_pool": self.away_pool,
        }


def simulate_game(
    home_players: list[dict],
    away_players: list[dict],
    seed: int,
    season: Optional[str] = None,
    steps: Optional[int] = None,
    capture_descriptions: bool = False,
    config: Optional["SimConfig"] = None,
    home_team_id: Optional[int] = None,
    away_team_id: Optional[int] = None,
    db: Optional[object] = None,
    unavailable_player_ids: Optional[set] = None,
) -> dict:
    """Simulate one full game including any overtime periods.

    Returns a dict with:
        home_score, away_score, quarter_scores, box_score, season,
        chunks, chunk_events, events, went_to_ot, ot_periods
    """

    return _GameSim(
        home_players=home_players,
        away_players=away_players,
        seed=seed,
        season=season,
        steps=steps,
        capture_descriptions=capture_descriptions,
        config=config,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        db=db,
        unavailable_player_ids=unavailable_player_ids,
    ).run()
