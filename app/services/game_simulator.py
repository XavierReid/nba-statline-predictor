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
    MODE_GARBAGE,
    MODE_OT_CLOSE,
    MODE_SCHEDULED,
    build_rotation,
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
) -> dict:
    """Simulate one full game including any overtime periods.

    Returns a dict with:
        home_score, away_score, quarter_scores, box_score, season,
        chunks, chunk_events, events, went_to_ot, ot_periods
    """
    from app.services.sim_config import SimConfig
    cfg: SimConfig = config if config is not None else SimConfig()

    rng = random.Random(seed)

    # Per-game availability (gap 3.4): ~10 of a deeper roster are active tonight, drawn from
    # games_played. Eligibility only — the rotation engine is untouched. Returns fresh dicts.
    # Availability needs the deeper pool: if handed a shallow roster (a caller that loaded the
    # default depth), reload to roster_depth here so every path benefits without caller surgery.
    # Full loaded pool (pre-selection) is retained so callers can distinguish who was
    # rostered-but-inactive (DNP) from who simply never entered the box score.
    home_pool, away_pool = home_players, away_players
    if cfg.use_availability:
        from app.services.availability import select_active_roster
        depth = getattr(cfg, "roster_depth", 10)
        if db is not None and season and len(home_players) < depth and home_team_id and away_team_id:
            from app.services.roster import load_roster
            hp = load_roster(db, home_team_id, season, depth=depth,
                             pre_negation=cfg.use_pre_negation_probs)
            ap = load_roster(db, away_team_id, season, depth=depth,
                             pre_negation=cfg.use_pre_negation_probs)
            if hp:
                home_pool = hp
            if ap:
                away_pool = ap
    # gap 3.4g: annotate each pool's full-strength creation reference BEFORE availability copies
    # the active subset (select_active_roster does dict(p), so the ref propagates to the copies).
    if cfg.use_lineup_creation and db is not None and season:
        from app.services.lineup_creation import annotate_team_baseline, ensure_league_baseline
        ensure_league_baseline(db, season, cfg.creation_form)
        annotate_team_baseline(home_pool, cfg.creation_form)
        annotate_team_baseline(away_pool, cfg.creation_form)
    if cfg.use_availability:
        home_players = select_active_roster(home_pool, rng, cfg)
        away_players = select_active_roster(away_pool, rng, cfg)

    # Era-anchored league normalization for the foul model. Built from the FULL
    # pool (pre-availability) so the anchor reflects the season's rostered pool,
    # not tonight's active subset. Only computed when the toggle is on; when off,
    # ctx.season_ctx stays None and possession.py falls back to module constants
    # (byte-identical to previous behavior).
    season_ctx = None
    if cfg.use_season_context and season:
        from app.services.season_context import build_season_context
        season_ctx = build_season_context(season, [home_pool, away_pool])

    home_by_id = {p["id"]: p for p in home_players}
    away_by_id = {p["id"]: p for p in away_players}
    name_map = (
        {p["id"]: p["name"] for p in home_players + away_players}
        if (capture_descriptions or steps)
        else None
    )

    # Load team season stats for pace/defense/OREB modifiers
    home_stats: Optional[dict] = None
    away_stats: Optional[dict] = None
    if db is not None and season:
        if home_team_id:
            row = db.execute(select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == home_team_id,
                TeamSeasonStats.season == season,
            )).scalar_one_or_none()
            if row:
                home_stats = {
                    "pace": row.pace,
                    "def_rating": row.def_rating,
                    "oreb_pct": row.oreb_pct,
                }
        if away_team_id:
            row = db.execute(select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == away_team_id,
                TeamSeasonStats.season == season,
            )).scalar_one_or_none()
            if row:
                away_stats = {
                    "pace": row.pace,
                    "def_rating": row.def_rating,
                    "oreb_pct": row.oreb_pct,
                }

    league_avg_def = (
        _league_avg_def_rating(db, season, cfg.league_avg_def_rating)
        if db is not None and season and cfg.use_team_defense else cfg.league_avg_def_rating
    )

    home_oreb_rate = ((home_stats or {}).get("oreb_pct") or OREB_RATE) if cfg.use_team_oreb else OREB_RATE
    away_oreb_rate = ((away_stats or {}).get("oreb_pct") or OREB_RATE) if cfg.use_team_oreb else OREB_RATE

    home_pace = (home_stats or {}).get("pace", cfg.league_avg_pace)
    away_pace = (away_stats or {}).get("pace", cfg.league_avg_pace)
    expected_possessions = round((home_pace + away_pace) / 2) * 2

    # Per-game form factors — drawn once at game start, held for full game.
    # When use_player_variance is off, all factors default to 1.0 (no effect).
    if cfg.use_player_variance:
        form_factors: dict = {
            p["id"]: max(0.90, min(1.10, rng.gauss(1.0, p.get("player_variance", 0.03))))
            for p in home_players + away_players
        }
    else:
        form_factors = {}

    home_rotation = build_rotation(home_players, rng)
    away_rotation = build_rotation(away_players, rng)
    home_def_baseline = rotation_baseline(home_players)
    away_def_baseline = rotation_baseline(away_players)
    tip_winner_is_home = rng.random() < 0.5

    box: dict = {pid: empty_stats() for pid in list(home_by_id) + list(away_by_id)}
    # Hierarchy sort by availability-normalized `minutes` (not raw mpg). Ships
    # WITH MODE_OT_CLOSE (2026-08-17) but the mpg-hierarchy change is bundled
    # with the unresolved scheduler question — see project-rotation-attempt-4
    # for the GSW compensation-removal that recurs when this sort key changes
    # without a scheduler fix. Left as-is until scheduler + hierarchy can ship
    # together.
    home_by_min = sorted(home_players, key=lambda p: p["minutes"], reverse=True)
    away_by_min = sorted(away_players, key=lambda p: p["minutes"], reverse=True)

    chunk_duration = GAME_MINUTES / steps if steps else None
    next_threshold = [chunk_duration]
    chunks: list = []
    chunk_events: list = []
    current_chunk_events: list = []
    # Typed event stream (RFC.md "Event-Sourced PBP") — one list of granular typed
    # events per possession (SHOT/FOUL/FT/REB/TOV/STL/BLK/AST). Descriptions attached
    # server-side via describe_typed_event when name_map is available.
    all_events: list = []
    # Always-populated typed stream (regardless of capture_descriptions/steps).
    # Used by the regression fence + any consumer that wants raw events without
    # opting into description building. Downstream of API routes ignore it.
    _typed_all: list = []
    gs = GameState()   # persistent, authoritative game state (roadmap stage B)

    def _maybe_snapshot(elapsed_minutes: float, current_q_idx: int) -> None:
        while chunk_duration and elapsed_minutes >= next_threshold[0]:
            chunks.append({
                "home_score": gs.home_score,
                "away_score": gs.away_score,
                "elapsed_minutes": round(elapsed_minutes, 1),
                "quarter": current_q_idx + 1,
                "box": snapshot_box(box),
            })
            chunk_events.append(list(current_chunk_events))
            current_chunk_events.clear()
            next_threshold[0] += chunk_duration

    def _apply_possession(
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
        # gs is a captured object; mutating its attributes needs no `nonlocal`.
        gs.game_clock += sec_per_poss
        elapsed_minutes = gs.game_clock / 60
        gs.period_index = current_q_idx

        home_active = [home_by_id[pid] for pid in home_active_ids if pid in home_by_id]
        away_active = [away_by_id[pid] for pid in away_active_ids if pid in away_by_id]

        for pid in home_active_ids:
            if pid in box:
                box[pid]["min"] += min_per_poss_val
        for pid in away_active_ids:
            if pid in box:
                box[pid]["min"] += min_per_poss_val

        offense, defense = (home_active, away_active) if is_home else (away_active, home_active)
        if not offense or not defense:
            return None, {}

        home_bonus = HOME_ADVANTAGE / expected_possessions if is_home else 0.0
        offense_oreb = home_oreb_rate if is_home else away_oreb_rate
        ctx = make_context(
            offense, defense, rng, cfg=cfg,
            adjustments=adjustments if adjustments is not None else ModifierAdjustments(),
            home_bonus=home_bonus,
            name_map=name_map,
            team_defense_factor=team_defense_factor,
            is_fastbreak=is_fastbreak,
            form_factors=form_factors if form_factors else None,
            offense_oreb_rate=offense_oreb,
            quarter=current_q_idx + 1,
            clock_seconds=quarter_clock,
            score_margin=gs.home_score - gs.away_score if is_home else gs.away_score - gs.home_score,
            behavior_profile=behavior_profile if behavior_profile is not None else NORMAL_PROFILE,
            defense_in_bonus=defense_in_bonus,
            foul_counts={p["id"]: box[p["id"]]["pf"] for p in defense if p["id"] in box} if cfg.use_foul_caution else None,
            season_ctx=season_ctx,
        )
        event = resolve_possession(ctx)

        # Event-sourced possession (RFC.md "Event-Sourced PBP"): translate the
        # possession result into granular typed events, apply each to the box via
        # apply_typed_event, then emit into the event stream (chunk_events for
        # steps, all_events otherwise) with per-event descriptions when captured.
        gs.possession_number += 1
        clock_secs = game_clock_override if game_clock_override is not None else round(gs.game_clock)
        typed = possession_to_events(
            event,
            possession=gs.possession_number,
            quarter=current_q_idx + 1,
            game_clock_seconds=clock_secs,
            is_home=is_home,
        )
        pts = 0
        fouled_out_pid: Optional[int] = None
        for tev in typed:
            ev_pts, ev_fo = apply_typed_event(box, tev)
            pts += ev_pts
            if ev_fo is not None:
                fouled_out_pid = ev_fo
        # Momentum modifier reads event.get("pts", 0) off the possession result;
        # preserve that field for the behavior pipeline call below.
        event["pts"] = pts

        if is_home:
            gs.home_score += pts
        else:
            gs.away_score += pts
        gs.quarter_scores["home" if is_home else "away"][current_q_idx] += pts

        home_delta = pts if is_home else -pts
        for pid in home_active_ids:
            if pid in box:
                box[pid]["plus_minus"] += home_delta
        for pid in away_active_ids:
            if pid in box:
                box[pid]["plus_minus"] -= home_delta

        if name_map is not None:
            for tev in typed:
                tev["description"] = describe_typed_event(tev, name_map)
        # is_fastbreak is a per-possession context field the frontend uses to
        # tint fast-break shots. Stamp it on every event in this possession.
        if is_fastbreak:
            for tev in typed:
                tev["is_fastbreak"] = True
        _typed_all.extend(typed)
        if steps:
            current_chunk_events.extend(typed)
        elif capture_descriptions:
            all_events.extend(typed)

        _maybe_snapshot(elapsed_minutes, current_q_idx)
        return fouled_out_pid, event

    # -----------------------------------------------------------------------
    # Regulation
    # -----------------------------------------------------------------------
    home_ids = set(home_by_id.keys())
    away_ids = set(away_by_id.keys())

    # Game-scope foul-out set. INVARIANT: once a player is added to this set
    # during a game they cannot return in any subsequent period (Q1→OTn).
    # Every on-court consumer (regular possession lineup, strategic-foul
    # picker) filters against this so a fouled-out player is removed the
    # instant their 6th PF is credited, not delayed to the next-minute
    # rotation patch. Fixes the 7-fouls bug (see project-bug-7-fouls-jokic).
    fouled_out: set[int] = set()

    home_player_gs = {
        p["id"]: PlayerGameState(player_id=p["id"], clutch_rating=p.get("clutch_rating", 50))
        for p in home_players
    }
    away_player_gs = {
        p["id"]: PlayerGameState(player_id=p["id"], clutch_rating=p.get("clutch_rating", 50))
        for p in away_players
    }

    behavior = BehaviorPipeline(cfg, home_players, away_players)

    ot_period = 0
    # Per-team concession state (hysteresis lives in late_game.should_concede)
    gs.home_conceded = False
    gs.away_conceded = False

    # Possession accounting — every mechanic that affects possession count reports
    # its contribution (CLAUDE.md guardrail 5). See app/services/diagnostics.py.
    diag = SimulationDiagnostics(pace_budget=expected_possessions)

    mean_quarter_possessions = expected_possessions / 4
    target_mean = QUARTER_SECONDS / mean_quarter_possessions

    # NBA pace = DISTINCT possessions; an offensive rebound continues the same
    # possession, it is not a new one (see analysis/accounting.py). So the budget is
    # spent only on distinct possessions — fastbreaks (which ARE distinct, just fast)
    # are compensated so the average distinct possession still hits target_mean, but
    # second chances are NOT: they are extra shot opportunities layered on top, taking
    # their own clock. Folding them into the budget (the old f_sc term) starved the
    # sim of ~10 distinct possessions/game — the FGA/OREB shortfall the accounting found.
    f_fb = cfg.fastbreak_poss_frac if cfg.use_fast_break else 0.0
    if cfg.use_catch_up:
        target_mean *= 1.0 + cfg.catch_up_clock_frac
    # pre-bonus shot-clock resets extend possessions (gap 3.7 2b); reclaim that time from
    # the halfcourt mean so distinct-possession pace holds (Stage 2 compensation).
    reset_comp = cfg.foul_reset_poss_frac * cfg.foul_reset_time_mean if cfg.use_bonus_system else 0.0
    mean_poss_time_clock = (target_mean - f_fb * cfg.fastbreak_time_mean - reset_comp) / (1.0 - f_fb)
    def _run_clock_period(q_idx: int, period_seconds: float, period_tip_is_home: bool) -> None:
        """One timed period (regulation quarter or OT) — identical mechanics either way.

        OT is not a separate simulation path: it is another timed period with
        different initial conditions (length, jump ball, closing lineups via the
        minute clamp). Every possession-level mechanic applies in any period.
        """
        # gs is captured; attribute mutation needs no `nonlocal`.
        quarter_clock = float(period_seconds)
        current_is_home = period_tip_is_home
        gs.home_quarter_fouls = 0   # team fouls reset each period (bonus tracking)
        gs.away_quarter_fouls = 0
        gs.home_last2_fouls = 0
        gs.away_last2_fouls = 0
        oreb_depth = 0
        next_is_fastbreak = False
        foul_reset_depth = 0        # consecutive pre-bonus non-shooting fouls on this possession

        while quarter_clock > 0:
            # Strategic foul check — final period only (Q4 or any OT): intentional
            # fouling is an end-of-GAME tactic. (Accounting run caught this firing
            # at the end of Q1-Q3 too: 83% of games had foul sequences vs ~25% real.)
            if cfg.use_strategic_foul and q_idx >= 3 and quarter_clock <= cfg.strategic_foul_clock_threshold:
                lead = gs.home_score - gs.away_score
                trailing_is_home = lead < 0
                if current_is_home != trailing_is_home:
                    margin = abs(lead)
                    if cfg.strategic_foul_margin_min <= margin <= cfg.strategic_foul_margin_max:
                        if rng.random() < cfg.strategic_foul_probability:
                            offense_on_court = [
                                p for p in (home_players if current_is_home else away_players)
                                if p["id"] in (home_ids if current_is_home else away_ids)
                                and p["id"] not in fouled_out
                            ]
                            from app.services.possession import _free_throw_prob
                            target = min(offense_on_court, key=_free_throw_prob)
                            ft_prob = _free_throw_prob(target)
                            fta = 2
                            ftm = sum(1 for _ in range(fta) if rng.random() < ft_prob)
                            foul_time = max(2.0, min(8.0, rng.gauss(4.0, 1.0)))
                            quarter_clock = max(0.0, quarter_clock - foul_time)
                            gs.game_clock += foul_time
                            diag.record_possession("strategic_foul", foul_time)
                            gs.possession_number += 1
                            # Pick a defender to display as the fouler. Foul-rate
                            # weighted so the choice mirrors the normal non-shooting
                            # foul selection in possession.py. Both FOUL and FT
                            # events flow through apply_typed_event below —
                            # `apply_typed_event` is the sole accounting sink, so
                            # PF, FTA/FTM, and points all reach the box and the
                            # score via the same event stream (no bypass paths).
                            defense_on_court = [
                                p for p in (away_players if current_is_home else home_players)
                                if p["id"] in (away_ids if current_is_home else home_ids)
                                and p["id"] not in fouled_out
                            ]
                            # Deterministic pick: the defender most willing to foul (matches
                            # the coaching pattern of sending a bench player with fouls to
                            # spare). Draws no RNG.
                            fouler = max(defense_on_court,
                                         key=lambda p: (p.get("foul_rate", 0.09), p["id"]))
                            hdr = dict(
                                possession=gs.possession_number,
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
                            if name_map is not None:
                                for tev in strategic_events:
                                    tev["description"] = describe_typed_event(tev, name_map)
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
                                ev_pts, ev_fo = apply_typed_event(box, tev)
                                strategic_pts += ev_pts
                                if ev_fo is not None:
                                    strategic_fouled_out_pid = ev_fo
                            if current_is_home:
                                gs.home_score += strategic_pts
                                gs.quarter_scores["home"][q_idx] += strategic_pts
                            else:
                                gs.away_score += strategic_pts
                                gs.quarter_scores["away"][q_idx] += strategic_pts
                            if strategic_fouled_out_pid:
                                fouled_out.add(strategic_fouled_out_pid)   # immediate — next lookups filter them out
                                current_minute = min(
                                    GAME_MINUTES - 1,
                                    q_idx * 12 + int((period_seconds - quarter_clock) / 60),
                                )
                                if strategic_fouled_out_pid in home_by_id:
                                    patch_rotation(home_rotation, strategic_fouled_out_pid, home_by_min, current_minute + 1, box)
                                else:
                                    patch_rotation(away_rotation, strategic_fouled_out_pid, away_by_min, current_minute + 1, box)
                            _typed_all.extend(strategic_events)
                            if steps:
                                current_chunk_events.extend(strategic_events)
                            elif capture_descriptions:
                                all_events.extend(strategic_events)
                            _maybe_snapshot(gs.game_clock / 60, q_idx)
                            current_is_home = not current_is_home
                            oreb_depth = 0
                            next_is_fastbreak = False
                            foul_reset_depth = 0
                            continue

            # Sample possession time
            if next_is_fastbreak:
                poss_category = "fastbreak"
                poss_time = max(3.0, min(12.0, rng.gauss(cfg.fastbreak_time_mean, cfg.fastbreak_time_std)))
            elif oreb_depth > 0:
                poss_category = "second_chance"
                poss_time = max(3.0, min(14.0, rng.gauss(cfg.second_chance_time_mean, cfg.second_chance_time_std)))
            else:
                poss_category = "halfcourt"
                poss_time = max(5.0, min(24.0, rng.gauss(mean_poss_time_clock, cfg.halfcourt_time_std)))

            # Endgame incentive pacing (gap 1.2): inside the window, possession
            # time reflects incentives — trailing plays fast, leading milks.
            # Uncompensated in the pace budget on purpose: like strategic fouls,
            # extra endgame possessions are state-dependent and should emerge.
            if cfg.use_endgame_pacing and poss_category == "halfcourt":
                lg_ctx = build_context(q_idx, quarter_clock, gs.home_score, gs.away_score, current_is_home, cfg)
                override = possession_time_override(lg_ctx, cfg, rng)
                if override is not None:
                    diag.endgame_time_delta += poss_time - override
                    poss_time = override
                    poss_category = "endgame"
            poss_time = min(poss_time, quarter_clock)
            quarter_clock -= poss_time

            # OT (q_idx >= 4) clamps to minute 47 — closing lineups stay on the floor
            current_minute = min(GAME_MINUTES - 1, q_idx * 12 + int((period_seconds - quarter_clock) / 60))

            # Rotation mode: reactive to game state, schedule as baseline. Each
            # team decides independently whether to concede (asymmetric
            # incentives — see late_game.should_concede).
            if cfg.use_garbage_rotation:
                margin_abs = abs(gs.home_score - gs.away_score)
                home_leads = gs.home_score >= gs.away_score
                was_any = gs.home_conceded or gs.away_conceded
                gs.home_conceded = should_concede(
                    home_leads, margin_abs, quarter_clock, q_idx, cfg, gs.home_conceded)
                gs.away_conceded = should_concede(
                    not home_leads, margin_abs, quarter_clock, q_idx, cfg, gs.away_conceded)
                if (gs.home_conceded or gs.away_conceded) and not was_any:
                    diag.record_garbage_entry(margin_abs)
                if gs.home_conceded or gs.away_conceded:
                    diag.record_garbage_possession()
            # OT closing lineup takes precedence over garbage: real coaches
            # close OT with their best five regardless of margin. See
            # project-star-mpg-margin-bucket (real OT Jokić 44.83 vs sim 34.37).
            is_ot = q_idx >= 4
            home_mode = (MODE_OT_CLOSE if is_ot
                         else (MODE_GARBAGE if gs.home_conceded else MODE_SCHEDULED))
            away_mode = (MODE_OT_CLOSE if is_ot
                         else (MODE_GARBAGE if gs.away_conceded else MODE_SCHEDULED))
            home_active_ids = resolve_lineup(
                home_rotation, current_minute, home_by_min, box,
                home_mode,
                foul_trouble_subs=cfg.use_foul_trouble_subs)
            away_active_ids = resolve_lineup(
                away_rotation, current_minute, away_by_min, box,
                away_mode,
                foul_trouble_subs=cfg.use_foul_trouble_subs)
            # Enforce the foul-out invariant: rotation lookups schedule the
            # replacement for `current_minute + 1`, so the same-minute lookup
            # (or OT's clamped-back-to-47 lookup) can still return a player
            # who fouled out this minute. Filter here so downstream sees
            # the physically-legal 5-on-court set. See project-bug-7-fouls-jokic.
            if fouled_out:
                home_active_ids = [pid for pid in home_active_ids if pid not in fouled_out]
                away_active_ids = [pid for pid in away_active_ids if pid not in fouled_out]
            in_mismatch = gs.home_conceded != gs.away_conceded
            pre_poss_margin = abs(gs.home_score - gs.away_score)

            team_defense_factor = 1.0
            if cfg.use_team_defense:
                defending_stats = away_stats if current_is_home else home_stats
                if defending_stats:
                    raw = defending_stats["def_rating"] / league_avg_def
                    team_defense_factor = 1.0 + (raw - 1.0) * 0.5

            # Lineup quality: season def_rating describes the normal rotation;
            # the factor below moves with the five actually defending.
            if cfg.use_lineup_quality:
                if current_is_home:
                    def_lineup = [away_by_id[pid] for pid in away_active_ids if pid in away_by_id]
                    def_baseline = away_def_baseline
                    def_mode = away_mode
                else:
                    def_lineup = [home_by_id[pid] for pid in home_active_ids if pid in home_by_id]
                    def_baseline = home_def_baseline
                    def_mode = home_mode
                lq = compute_lineup_quality(def_lineup, def_baseline)
                team_defense_factor *= lq["defense"]
                diag.record_lineup_defense(def_mode, lq["defense"])

            active_home_gs = {pid: home_player_gs[pid] for pid in home_active_ids if pid in home_player_gs}
            active_away_gs = {pid: away_player_gs[pid] for pid in away_active_ids if pid in away_player_gs}
            phase = derive_phase(
                q_idx, abs(gs.home_score - gs.away_score),
                gs.home_conceded, gs.away_conceded, cfg,
            )
            game_state = GameSnapshot(
                home_score=gs.home_score,
                away_score=gs.away_score,
                quarter=q_idx + 1,
                clock_seconds=quarter_clock,
                possession_number=gs.possession_number,
                home_players=active_home_gs,
                away_players=active_away_gs,
                home_conceded=gs.home_conceded,
                away_conceded=gs.away_conceded,
                phase=phase.value,
            )

            # All behavior sources (momentum, fatigue, clutch, garbage time, the
            # Q4 objective, ...) combine here — one owner, no inline special cases.
            poss_adjustments = behavior.adjustments(current_is_home, game_state)

            # Apply pace_multiplier: quarter_clock was already reduced by poss_time above,
            # so readjust the net clock consumption for the new pace. Endgame-paced
            # possessions skip it — the override already encodes the pacing intent,
            # and stacking both would double-shorten trailing possessions.
            if poss_adjustments and poss_adjustments.pace_multiplier != 1.0 and poss_category != "endgame":
                orig_poss_time = poss_time
                poss_time = max(3.0, orig_poss_time * poss_adjustments.pace_multiplier)
                quarter_clock = max(0.0, quarter_clock + orig_poss_time - poss_time)
                diag.catch_up_time_delta += orig_poss_time - poss_time
            diag.record_possession(poss_category, poss_time)

            poss_profile = profile_for_phase(phase, cfg) if cfg.use_behavior_profile else NORMAL_PROFILE
            # the DEFENSIVE team (not on offense) is in the bonus at the team-foul limit,
            # OR (NBA last-2:00 rule) once it has already committed a foul in the window,
            # so its next (2nd) foul there draws FTs.
            def_fouls_now = gs.away_quarter_fouls if current_is_home else gs.home_quarter_fouls
            def_last2_now = gs.away_last2_fouls if current_is_home else gs.home_last2_fouls
            defense_in_bonus = cfg.use_bonus_system and (
                def_fouls_now >= cfg.bonus_foul_threshold
                or (quarter_clock <= cfg.last2min_clock and def_last2_now >= 1)
            )
            fouled_out_pid, event = _apply_possession(
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
            behavior.update(event, current_is_home, game_state)

            if in_mismatch:
                diag.record_mismatch(abs(gs.home_score - gs.away_score) - pre_poss_margin)

            poss_minutes = poss_time / 60.0
            for pid in home_active_ids:
                if pid in home_player_gs:
                    home_player_gs[pid].minutes_played += poss_minutes
            for pid in away_active_ids:
                if pid in away_player_gs:
                    away_player_gs[pid].minutes_played += poss_minutes
            for foul_pid in (event.get("fouled_by"), event.get("nonshooting_foul_by")):
                if foul_pid is None:
                    continue
                if foul_pid in home_player_gs:
                    home_player_gs[foul_pid].fouls += 1
                elif foul_pid in away_player_gs:
                    away_player_gs[foul_pid].fouls += 1

            # team fouls this period (bonus tracking) — only DEFENSIVE fouls count. A
            # shooting/bonus foul has fouled_by != turnover_by (offensive fouls set both
            # to the ball handler); a pre-bonus non-shooting foul is always defensive.
            if cfg.use_bonus_system:
                def_committed = int(
                    event.get("fouled_by") is not None
                    and event.get("fouled_by") != event.get("turnover_by")
                ) + int(event.get("nonshooting_foul_by") is not None)
                in_last2 = quarter_clock <= cfg.last2min_clock
                if current_is_home:
                    gs.away_quarter_fouls += def_committed
                    if in_last2:
                        gs.away_last2_fouls += def_committed
                else:
                    gs.home_quarter_fouls += def_committed
                    if in_last2:
                        gs.home_last2_fouls += def_committed

            if fouled_out_pid:
                fouled_out.add(fouled_out_pid)   # immediate — next lookups filter them out
                if fouled_out_pid in home_by_id:
                    patch_rotation(home_rotation, fouled_out_pid, home_by_min, current_minute + 1, box)
                else:
                    patch_rotation(away_rotation, fouled_out_pid, away_by_min, current_minute + 1, box)

            next_is_fastbreak = False
            # Pre-bonus non-shooting foul is now an in-possession event (see
            # possession._select_action + _restart_offensive_phase). The
            # statistical possession does NOT terminate on the foul; the offense
            # keeps the ball and play resumes within the same resolve_possession
            # call. What the game_simulator still owns is the CLOCK time that
            # the foul + inbound consumes — deducted inline here so the total
            # clock accounting per pre-bonus stat_poss matches the pre-refactor
            # (halfcourt time + foul_reset_time).
            if event.get("nonshooting_foul_by") is not None:
                extra = max(3.0, min(14.0, rng.gauss(cfg.foul_reset_time_mean, cfg.foul_reset_time_std)))
                extra = min(extra, quarter_clock)
                quarter_clock -= extra
                # Attribute the extra time to the foul_reset diagnostic bucket but
                # do NOT increment the possession COUNT — this is time consumed
                # WITHIN the same statistical possession (not a new possession).
                diag.time["foul_reset"] += extra
                diag.pre_bonus_fouls += 1
            foul_reset_depth = 0  # cap obsolete under in-line representation
            rebounded_by = event.get("rebounded_by")
            offense_ids = home_ids if current_is_home else away_ids
            is_oreb = (
                cfg.use_second_chance
                and rebounded_by is not None
                and rebounded_by in offense_ids
                and event.get("shot_type") is not None
                and not event.get("made")
            )
            if is_oreb and oreb_depth < cfg.oreb_chain_cap:
                oreb_depth += 1
            else:
                oreb_depth = 0
                current_is_home = not current_is_home
                if (cfg.use_fast_break and event.get("steal_by") is not None
                        and rng.random() < cfg.steal_fastbreak_prob):
                    next_is_fastbreak = True

    for reg_q in range(4):
        _run_clock_period(
            reg_q, QUARTER_SECONDS,
            tip_winner_is_home if reg_q % 2 == 0 else not tip_winner_is_home,
        )

    # OT: another timed period — new jump ball, 300s, closing lineups
    while gs.home_score == gs.away_score:
        ot_period += 1
        gs.quarter_scores["home"].append(0)
        gs.quarter_scores["away"].append(0)
        _run_clock_period(3 + ot_period, OT_SECONDS, rng.random() < 0.5)

    # Final snapshot
    if steps and (not chunks or chunks[-1]["home_score"] != gs.home_score or chunks[-1]["away_score"] != gs.away_score):
        chunks.append({
            "home_score": gs.home_score,
            "away_score": gs.away_score,
            "elapsed_minutes": round(gs.game_clock / 60, 1),
            "quarter": gs.period_index + 1,
            "box": snapshot_box(box),
        })
        chunk_events.append(list(current_chunk_events))

    return {
        "season": season,
        "home_score": gs.home_score,
        "away_score": gs.away_score,
        "quarter_scores": gs.quarter_scores,
        "box_score": box,
        "chunks": chunks,
        "chunk_events": chunk_events,
        "events": all_events,
        "typed_events": _typed_all,
        "went_to_ot": ot_period > 0,
        "ot_periods": ot_period,
        "possession_accounting": diag.as_dict(),
        # Rosters as the engine actually used them: `active` are the players who dressed
        # (post-availability), `pool` is the full loaded roster so callers can render DNPs.
        "home_active": home_players,
        "away_active": away_players,
        "home_pool": home_pool,
        "away_pool": away_pool,
    }
