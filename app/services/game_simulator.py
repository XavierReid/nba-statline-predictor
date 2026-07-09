"""Game simulation orchestrator.

Public surface (unchanged for callers):
    load_roster(db, team_id, season) -> list[dict]
    simulate_game(home_players, away_players, seed, ...) -> dict
"""
import random
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.team_season_stats import TeamSeasonStats
from app.services.modifiers.base import GameState, ModifierAdjustments, PlayerGameState
from app.services.box_score import apply_event, empty_stats, snapshot_box
from app.services.late_game import build_context, possession_time_override, should_concede
from app.services.lineup_quality import compute_lineup_quality, rotation_baseline
from app.services.rotation import MODE_GARBAGE, MODE_SCHEDULED, resolve_lineup
from app.services.possession import OREB_RATE, describe_event, resolve_possession
from app.services.roster import load_roster
from app.services.rotation import GAME_MINUTES, build_rotation, patch_rotation

# Re-export so existing callers (API, tests, scratch scripts) need no changes.
__all__ = ["load_roster", "simulate_game", "describe_event"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
POSSESSIONS_PER_GAME = 200
SECONDS_PER_POSSESSION = (GAME_MINUTES * 60) / POSSESSIONS_PER_GAME
POSSESSIONS_PER_OT = 20
OT_MINUTES = 5
SECONDS_PER_OT_POSSESSION = (OT_MINUTES * 60) / POSSESSIONS_PER_OT
QUARTER_SECONDS = 720
OT_SECONDS = 300
HOME_ADVANTAGE = 3.0
ELIGIBLE_MISS_RATE = 0.32


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
    if db is not None and (cfg.use_pace or cfg.use_team_defense or cfg.use_team_oreb) and season:
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

    home_oreb_rate = ((home_stats or {}).get("oreb_pct") or OREB_RATE) if cfg.use_team_oreb else OREB_RATE
    away_oreb_rate = ((away_stats or {}).get("oreb_pct") or OREB_RATE) if cfg.use_team_oreb else OREB_RATE

    home_pace = (home_stats or {}).get("pace", cfg.league_avg_pace) if cfg.use_pace else cfg.league_avg_pace
    away_pace = (away_stats or {}).get("pace", cfg.league_avg_pace) if cfg.use_pace else cfg.league_avg_pace
    expected_possessions = round((home_pace + away_pace) / 2) * 2 if cfg.use_pace else POSSESSIONS_PER_GAME

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
    home_by_min = sorted(home_players, key=lambda p: p["minutes"], reverse=True)
    away_by_min = sorted(away_players, key=lambda p: p["minutes"], reverse=True)

    chunk_duration = GAME_MINUTES / steps if steps else None
    next_threshold = [chunk_duration]
    chunks: list = []
    chunk_events: list = []
    current_chunk_events: list = []
    all_events: list = []
    home_total = 0
    away_total = 0
    possession_counter = 0
    q_idx = 0
    quarter_scores: dict = {"home": [0, 0, 0, 0], "away": [0, 0, 0, 0]}
    game_clock = 0.0
    min_per_poss = GAME_MINUTES / POSSESSIONS_PER_GAME

    def _maybe_snapshot(elapsed_minutes: float, current_q_idx: int) -> None:
        while chunk_duration and elapsed_minutes >= next_threshold[0]:
            chunks.append({
                "home_score": home_total,
                "away_score": away_total,
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
    ):
        nonlocal game_clock, home_total, away_total, possession_counter, q_idx

        game_clock += sec_per_poss
        elapsed_minutes = game_clock / 60
        q_idx = current_q_idx

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
        event = resolve_possession(
            offense, defense, rng, home_bonus, name_map,
            team_defense_factor=team_defense_factor,
            is_fastbreak=is_fastbreak,
            adjustments=adjustments,
            form_factors=form_factors if form_factors else None,
            offense_oreb_rate=offense_oreb,
            use_shot_subtypes=cfg.use_shot_subtypes,
            use_contest_model=cfg.use_contest_model,
            use_positional_matchups=cfg.use_positional_matchups,
            use_foul_drawing=cfg.use_foul_drawing,
            foul_draw_scale=cfg.foul_draw_scale,
            signal_gain=cfg.signal_gain,
            quarter=current_q_idx + 1,
            clock_seconds=quarter_clock,
            score_margin=home_total - away_total if is_home else away_total - home_total,
            foul_draw_late_zone1_clock=cfg.foul_draw_late_zone1_clock,
            foul_draw_late_zone1_margin=cfg.foul_draw_late_zone1_margin,
            foul_draw_late_zone1_mult=cfg.foul_draw_late_zone1_mult,
            foul_draw_late_zone2_clock=cfg.foul_draw_late_zone2_clock,
            foul_draw_late_zone2_margin=cfg.foul_draw_late_zone2_margin,
            foul_draw_late_zone2_mult=cfg.foul_draw_late_zone2_mult,
        )

        pts, fouled_out_pid = apply_event(box, event)

        if is_home:
            home_total += pts
        else:
            away_total += pts
        quarter_scores["home" if is_home else "away"][current_q_idx] += pts

        home_delta = pts if is_home else -pts
        for pid in home_active_ids:
            if pid in box:
                box[pid]["plus_minus"] += home_delta
        for pid in away_active_ids:
            if pid in box:
                box[pid]["plus_minus"] -= home_delta

        possession_counter += 1
        clock_secs = game_clock_override if game_clock_override is not None else round(game_clock)
        poss_record = {
            "possession": possession_counter,
            "game_clock_seconds": clock_secs,
            "quarter": current_q_idx + 1,
            "is_home": is_home,
            "pts": pts,
            **event,
        }
        if steps:
            current_chunk_events.append(poss_record)
        elif capture_descriptions:
            all_events.append(poss_record)

        _maybe_snapshot(elapsed_minutes, current_q_idx)
        return fouled_out_pid, event

    # -----------------------------------------------------------------------
    # Regulation
    # -----------------------------------------------------------------------
    home_ids = set(home_by_id.keys())
    away_ids = set(away_by_id.keys())

    home_player_gs = {
        p["id"]: PlayerGameState(player_id=p["id"], clutch_rating=p.get("clutch_rating", 50))
        for p in home_players
    }
    away_player_gs = {
        p["id"]: PlayerGameState(player_id=p["id"], clutch_rating=p.get("clutch_rating", 50))
        for p in away_players
    }

    active_modifiers: list = []
    if cfg.use_clock and cfg.use_momentum:
        from app.services.modifiers.momentum import MomentumModifier
        home_composure = (
            sum(p["overall"] for p in home_players) / len(home_players) / 100.0
            if home_players else 0.75
        )
        away_composure = (
            sum(p["overall"] for p in away_players) / len(away_players) / 100.0
            if away_players else 0.75
        )
        active_modifiers.append(MomentumModifier(cfg, home_composure, away_composure))

    if cfg.use_clock and cfg.use_fatigue:
        from app.services.modifiers.fatigue import FatigueModifier
        active_modifiers.append(FatigueModifier(cfg))

    if cfg.use_clock and cfg.use_foul_trouble:
        from app.services.modifiers.foul_trouble import FoulTroubleModifier
        active_modifiers.append(FoulTroubleModifier(cfg))

    if cfg.use_clock and cfg.use_clutch:
        from app.services.modifiers.clutch import ClutchModifier
        active_modifiers.append(ClutchModifier(cfg))

    if cfg.use_clock and cfg.use_catch_up:
        from app.services.modifiers.catch_up import CatchUpModifier
        active_modifiers.append(CatchUpModifier(cfg))

    if cfg.use_clock and cfg.use_garbage_time:
        from app.services.modifiers.garbage_time import GarbageTimeModifier
        active_modifiers.append(GarbageTimeModifier(cfg))

    ot_period = 0
    # Per-team concession state (hysteresis lives in late_game.should_concede)
    home_conceded = False
    away_conceded = False

    # Possession accounting — every mechanic that affects possession count reports its
    # contribution here (see CLAUDE.md guardrail 5 / SIMULATION_GAPS.md §1.4). Seed for
    # the future SimulationDiagnostics object.
    poss_acct: dict = {
        "counts": {"halfcourt": 0, "fastbreak": 0, "second_chance": 0, "strategic_foul": 0, "endgame": 0},
        "time": {"halfcourt": 0.0, "fastbreak": 0.0, "second_chance": 0.0, "strategic_foul": 0.0, "endgame": 0.0},
        "catch_up_time_delta": 0.0,  # net clock seconds saved (+) / added (−) by pace multipliers
        "endgame_time_delta": 0.0,   # net clock seconds saved (+) / added (−) by endgame pacing
        "garbage_rotation": {
            "entries": 0, "possessions": 0, "entry_margin_sum": 0,
            "mismatch_poss": 0, "mismatch_margin_delta": 0,  # leader-bench vs trailer-starters window
        },
        "lineup_defense": {
            "scheduled_sum": 0.0, "scheduled_n": 0,
            "garbage_sum": 0.0, "garbage_n": 0,
            "min": 1.0, "max": 1.0,
        },
        "pace_budget": expected_possessions,
    }

    if cfg.use_clock:
        mean_quarter_possessions = expected_possessions / 4
        target_mean = QUARTER_SECONDS / mean_quarter_possessions

        # Pace targets already include short possessions (second chances, fastbreaks) and
        # catch-up urgency, so halfcourt possessions must run longer than the naive mean:
        #   t_hc = (target − f_sc·t_sc − f_fb·t_fb) / (1 − f_sc − f_fb)
        # Fractions are measured, not heuristic: f_sc from actual team OREB rates (real
        # NBA data), f_fb and the catch-up clock fraction from possession accounting runs
        # (see SimConfig provenance comments). Strategic fouls are deliberately NOT
        # compensated — state-dependent, validated separately via poss_acct.
        f_sc = ((home_oreb_rate + away_oreb_rate) / 2.0) * ELIGIBLE_MISS_RATE if cfg.use_second_chance else 0.0
        f_fb = cfg.fastbreak_poss_frac if cfg.use_fast_break else 0.0
        if cfg.use_catch_up:
            target_mean *= 1.0 + cfg.catch_up_clock_frac
        mean_poss_time_clock = (
            (target_mean - f_sc * cfg.second_chance_time_mean - f_fb * cfg.fastbreak_time_mean)
            / (1.0 - f_sc - f_fb)
        )
        def _run_clock_period(q_idx: int, period_seconds: float, period_tip_is_home: bool) -> None:
            """One timed period (regulation quarter or OT) — identical mechanics either way.

            OT is not a separate simulation path: it is another timed period with
            different initial conditions (length, jump ball, closing lineups via the
            minute clamp). Every possession-level mechanic applies in any period.
            """
            nonlocal home_total, away_total, possession_counter, game_clock, home_conceded, away_conceded
            quarter_clock = float(period_seconds)
            current_is_home = period_tip_is_home
            oreb_depth = 0
            next_is_fastbreak = False

            while quarter_clock > 0:
                # Strategic foul check — final period only (Q4 or any OT): intentional
                # fouling is an end-of-GAME tactic. (Accounting run caught this firing
                # at the end of Q1-Q3 too: 83% of games had foul sequences vs ~25% real.)
                if cfg.use_strategic_foul and q_idx >= 3 and quarter_clock <= cfg.strategic_foul_clock_threshold:
                    lead = home_total - away_total
                    trailing_is_home = lead < 0
                    if current_is_home != trailing_is_home:
                        margin = abs(lead)
                        if cfg.strategic_foul_margin_min <= margin <= cfg.strategic_foul_margin_max:
                            if rng.random() < cfg.strategic_foul_probability:
                                offense_on_court = [
                                    p for p in (home_players if current_is_home else away_players)
                                    if p["id"] in (home_ids if current_is_home else away_ids)
                                ]
                                target = min(offense_on_court, key=lambda p: p["free_throw"])
                                from app.services.possession import attr_to_prob
                                ft_prob = attr_to_prob(target["free_throw"], lo=0.60, hi=0.95)
                                fta = 2
                                ftm = sum(1 for _ in range(fta) if rng.random() < ft_prob)
                                foul_time = max(2.0, min(8.0, rng.gauss(4.0, 1.0)))
                                quarter_clock = max(0.0, quarter_clock - foul_time)
                                game_clock += foul_time
                                poss_acct["counts"]["strategic_foul"] += 1
                                poss_acct["time"]["strategic_foul"] += foul_time
                                pts = ftm
                                if current_is_home:
                                    home_total += pts
                                    quarter_scores["home"][q_idx] += pts
                                else:
                                    away_total += pts
                                    quarter_scores["away"][q_idx] += pts
                                possession_counter += 1
                                foul_event = {
                                    "scorer": target["id"], "shot_type": None, "made": False,
                                    "assisted_by": None, "rebounded_by": None,
                                    "turnover_by": None, "steal_by": None, "block_by": None,
                                    "fouled_by": None, "fta": fta, "ftm": ftm,
                                    "description": (
                                        f"{target['name']} shoots {ftm}/{fta} FTs (intentional foul)"
                                        if name_map else None
                                    ),
                                }
                                poss_record = {
                                    "possession": possession_counter,
                                    "game_clock_seconds": int(quarter_clock),
                                    "quarter": q_idx + 1,
                                    "is_home": current_is_home,
                                    "pts": pts,
                                    **foul_event,
                                }
                                if steps:
                                    current_chunk_events.append(poss_record)
                                elif capture_descriptions:
                                    all_events.append(poss_record)
                                _maybe_snapshot(game_clock / 60, q_idx)
                                current_is_home = not current_is_home
                                oreb_depth = 0
                                next_is_fastbreak = False
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
                    lg_ctx = build_context(q_idx, quarter_clock, home_total, away_total, current_is_home, cfg)
                    override = possession_time_override(lg_ctx, cfg, rng)
                    if override is not None:
                        poss_acct["endgame_time_delta"] += poss_time - override
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
                    margin_abs = abs(home_total - away_total)
                    home_leads = home_total >= away_total
                    was_any = home_conceded or away_conceded
                    home_conceded = should_concede(
                        home_leads, margin_abs, quarter_clock, q_idx, cfg, home_conceded)
                    away_conceded = should_concede(
                        not home_leads, margin_abs, quarter_clock, q_idx, cfg, away_conceded)
                    if (home_conceded or away_conceded) and not was_any:
                        poss_acct["garbage_rotation"]["entries"] += 1
                        poss_acct["garbage_rotation"]["entry_margin_sum"] += margin_abs
                    if home_conceded or away_conceded:
                        poss_acct["garbage_rotation"]["possessions"] += 1
                home_active_ids = resolve_lineup(
                    home_rotation, current_minute, home_by_min, box,
                    MODE_GARBAGE if home_conceded else MODE_SCHEDULED)
                away_active_ids = resolve_lineup(
                    away_rotation, current_minute, away_by_min, box,
                    MODE_GARBAGE if away_conceded else MODE_SCHEDULED)
                in_mismatch = home_conceded != away_conceded
                pre_poss_margin = abs(home_total - away_total)

                team_defense_factor = 1.0
                if cfg.use_team_defense:
                    defending_stats = away_stats if current_is_home else home_stats
                    if defending_stats:
                        raw = defending_stats["def_rating"] / cfg.league_avg_def_rating
                        team_defense_factor = 1.0 + (raw - 1.0) * 0.5

                # Lineup quality: season def_rating describes the normal rotation;
                # the factor below moves with the five actually defending.
                if cfg.use_lineup_quality:
                    if current_is_home:
                        def_lineup = [away_by_id[pid] for pid in away_active_ids if pid in away_by_id]
                        def_baseline = away_def_baseline
                        def_mode = MODE_GARBAGE if away_conceded else MODE_SCHEDULED
                    else:
                        def_lineup = [home_by_id[pid] for pid in home_active_ids if pid in home_by_id]
                        def_baseline = home_def_baseline
                        def_mode = MODE_GARBAGE if home_conceded else MODE_SCHEDULED
                    lq = compute_lineup_quality(def_lineup, def_baseline)
                    team_defense_factor *= lq["defense"]
                    acc = poss_acct["lineup_defense"]
                    acc[f"{def_mode}_sum"] += lq["defense"]
                    acc[f"{def_mode}_n"] += 1
                    acc["min"] = min(acc["min"], lq["defense"])
                    acc["max"] = max(acc["max"], lq["defense"])

                active_home_gs = {pid: home_player_gs[pid] for pid in home_active_ids if pid in home_player_gs}
                active_away_gs = {pid: away_player_gs[pid] for pid in away_active_ids if pid in away_player_gs}
                game_state = GameState(
                    home_score=home_total,
                    away_score=away_total,
                    quarter=q_idx + 1,
                    clock_seconds=quarter_clock,
                    possession_number=possession_counter,
                    home_players=active_home_gs,
                    away_players=active_away_gs,
                )

                poss_adjustments: Optional[ModifierAdjustments] = None
                if active_modifiers:
                    poss_adjustments = ModifierAdjustments()
                    for mod in active_modifiers:
                        poss_adjustments = poss_adjustments + mod.get_adjustments(current_is_home, game_state)

                # Apply pace_multiplier: quarter_clock was already reduced by poss_time above,
                # so readjust the net clock consumption for the new pace. Endgame-paced
                # possessions skip it — the override already encodes the pacing intent,
                # and stacking both would double-shorten trailing possessions.
                if poss_adjustments and poss_adjustments.pace_multiplier != 1.0 and poss_category != "endgame":
                    orig_poss_time = poss_time
                    poss_time = max(3.0, orig_poss_time * poss_adjustments.pace_multiplier)
                    quarter_clock = max(0.0, quarter_clock + orig_poss_time - poss_time)
                    poss_acct["catch_up_time_delta"] += orig_poss_time - poss_time
                poss_acct["counts"][poss_category] += 1
                poss_acct["time"][poss_category] += poss_time

                fouled_out_pid, event = _apply_possession(
                    home_active_ids, away_active_ids, current_is_home,
                    poss_time, poss_time / 60.0, q_idx,
                    game_clock_override=int(quarter_clock),
                    team_defense_factor=team_defense_factor,
                    is_fastbreak=next_is_fastbreak,
                    adjustments=poss_adjustments,
                    quarter_clock=quarter_clock,
                )
                for mod in active_modifiers:
                    mod.update(event, current_is_home, game_state)

                if in_mismatch:
                    poss_acct["garbage_rotation"]["mismatch_poss"] += 1
                    poss_acct["garbage_rotation"]["mismatch_margin_delta"] += (
                        abs(home_total - away_total) - pre_poss_margin
                    )

                poss_minutes = poss_time / 60.0
                for pid in home_active_ids:
                    if pid in home_player_gs:
                        home_player_gs[pid].minutes_played += poss_minutes
                for pid in away_active_ids:
                    if pid in away_player_gs:
                        away_player_gs[pid].minutes_played += poss_minutes
                foul_pid = event.get("fouled_by")
                if foul_pid is not None:
                    if foul_pid in home_player_gs:
                        home_player_gs[foul_pid].fouls += 1
                    elif foul_pid in away_player_gs:
                        away_player_gs[foul_pid].fouls += 1

                if fouled_out_pid:
                    if fouled_out_pid in home_by_id:
                        patch_rotation(home_rotation, fouled_out_pid, home_by_min, current_minute + 1, box)
                    else:
                        patch_rotation(away_rotation, fouled_out_pid, away_by_min, current_minute + 1, box)

                next_is_fastbreak = False
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
                    if cfg.use_fast_break and event.get("steal_by") is not None:
                        next_is_fastbreak = True

        for reg_q in range(4):
            _run_clock_period(
                reg_q, QUARTER_SECONDS,
                tip_winner_is_home if reg_q % 2 == 0 else not tip_winner_is_home,
            )

        # OT: another timed period — new jump ball, 300s, closing lineups
        while home_total == away_total:
            ot_period += 1
            quarter_scores["home"].append(0)
            quarter_scores["away"].append(0)
            _run_clock_period(3 + ot_period, OT_SECONDS, rng.random() < 0.5)

    else:
        # Fixed-possession loop (original behavior)
        current_is_home = tip_winner_is_home
        for poss_idx in range(expected_possessions):
            current_minute = min(GAME_MINUTES - 1, int((game_clock + SECONDS_PER_POSSESSION) / 60))
            reg_q_idx = min(3, current_minute // 12)

            home_active_ids = home_rotation[current_minute]
            away_active_ids = away_rotation[current_minute]

            fouled_out_pid, event = _apply_possession(
                home_active_ids, away_active_ids, current_is_home,
                SECONDS_PER_POSSESSION, min_per_poss, reg_q_idx,
            )
            if fouled_out_pid:
                if fouled_out_pid in home_by_id:
                    patch_rotation(home_rotation, fouled_out_pid, home_by_min, current_minute + 1, box)
                else:
                    patch_rotation(away_rotation, fouled_out_pid, away_by_min, current_minute + 1, box)

            offense_ids = home_ids if current_is_home else away_ids
            rebounded_by = event.get("rebounded_by")
            is_oreb = (
                rebounded_by is not None
                and rebounded_by in offense_ids
                and event.get("shot_type") is not None
                and not event.get("made")
            )
            if not is_oreb:
                current_is_home = not current_is_home

    # -----------------------------------------------------------------------
    # Overtime — legacy fixed-possession loop (non-clock mode only; clock mode
    # runs OT as a real timed period above)
    # -----------------------------------------------------------------------
    min_per_ot_poss = OT_MINUTES / POSSESSIONS_PER_OT

    while not cfg.use_clock and home_total == away_total:
        ot_period += 1
        ot_tip_is_home = rng.random() < 0.5
        ot_q_idx = 3 + ot_period
        quarter_scores["home"].append(0)
        quarter_scores["away"].append(0)

        home_ot_ids = list(home_rotation[GAME_MINUTES - 1])
        away_ot_ids = list(away_rotation[GAME_MINUTES - 1])

        for ot_poss_idx in range(POSSESSIONS_PER_OT):
            is_home = (ot_poss_idx % 2 == 0) == ot_tip_is_home
            fouled_out_pid, _ = _apply_possession(
                home_ot_ids, away_ot_ids, is_home,
                SECONDS_PER_OT_POSSESSION, min_per_ot_poss, ot_q_idx,
            )
            if fouled_out_pid:
                if fouled_out_pid in home_ot_ids:
                    home_ot_ids = [p for p in home_ot_ids if p != fouled_out_pid]
                    repl = next((p["id"] for p in home_by_min
                                 if p["id"] not in home_ot_ids and not box[p["id"]]["fouled_out"]), None)
                    if repl:
                        home_ot_ids.append(repl)
                else:
                    away_ot_ids = [p for p in away_ot_ids if p != fouled_out_pid]
                    repl = next((p["id"] for p in away_by_min
                                 if p["id"] not in away_ot_ids and not box[p["id"]]["fouled_out"]), None)
                    if repl:
                        away_ot_ids.append(repl)

    # Final snapshot
    if steps and (not chunks or chunks[-1]["home_score"] != home_total or chunks[-1]["away_score"] != away_total):
        chunks.append({
            "home_score": home_total,
            "away_score": away_total,
            "elapsed_minutes": round(game_clock / 60, 1),
            "quarter": q_idx + 1,
            "box": snapshot_box(box),
        })
        chunk_events.append(list(current_chunk_events))

    return {
        "season": season,
        "home_score": home_total,
        "away_score": away_total,
        "quarter_scores": quarter_scores,
        "box_score": box,
        "chunks": chunks,
        "chunk_events": chunk_events,
        "events": all_events,
        "went_to_ot": ot_period > 0,
        "ot_periods": ot_period,
        "possession_accounting": poss_acct,
    }
