export interface Team {
  id: number;
  abbreviation: string;
  city: string;
  nickname: string;
}

export interface SeasonCoverage {
  season: string;
  stats_players: number;
  attrs_seeded: number;
  tends_seeded: number;
  ready: boolean;
}

export interface PlayerLine {
  player_id: number;
  name: string;
  minutes: number;
  points: number;
  rebounds: number;
  assists: number;
  steals: number;
  blocks: number;
  turnovers: number;
  personal_fouls: number;
  plus_minus: number;
  fgm: number;
  fga: number;
  fg3m: number;
  fg3a: number;
  ftm: number;
  fta: number;
  fouled_out: boolean;
}

export interface QuarterScores {
  home: number[];
  away: number[];
}

// One granular typed event in the PBP stream (RFC.md "Event-Sourced PBP"). `type` is
// the discriminator; type-specific fields are all optional so a single flexible shape
// covers every event kind. See app/services/possession_events.py for the source of
// truth on each shape.
export type SimEventType =
  | "SHOT" | "FOUL" | "FT" | "REB" | "TOV" | "STL" | "BLK" | "AST"
  | "SUBSTITUTION";

export interface SimEvent {
  type: SimEventType;
  possession: number;
  quarter: number;
  game_clock_seconds: number;
  is_home: boolean;
  player_id?: number | null;
  pts: number;
  running_home_score?: number | null;
  running_away_score?: number | null;
  description?: string | null;
  is_fastbreak?: boolean | null;
  strategic?: boolean | null;

  // SHOT
  shot_type?: string | null;
  sub_type?: string | null;
  made?: boolean | null;

  // FOUL
  foul_kind?: "shooting" | "non_shooting" | "offensive" | null;
  fouled_on?: number | null;
  intentional?: boolean | null;

  // FT
  attempt?: number | null;
  of?: number | null;

  // REB
  is_oreb?: boolean | null;

  // AST / BLK — reference back to the parent SHOT so an event can stand alone
  // in a filtered view (e.g. a modal filtered to the assister/blocker).
  shot_by?: number | null;

  // STL — reference back to the parent TOV.
  stolen_from?: number | null;

  // SUBSTITUTION — player_in enters for player_out. player_out is null on
  // initial-lineup SUBs (game start). player_id mirrors player_in when
  // present, else player_out, so tag/lookup code can still find "the player
  // this event is about".
  player_in?: number | null;
  player_out?: number | null;
}

// Back-compat alias for existing imports.
export type PossessionEvent = SimEvent;

export interface PlayerProfile {
  id: number;
  full_name: string;
  position: string;
  team: string | null;
  season: string;
  season_averages: {
    gp: number;
    min: number;
    pts: number;
    reb: number;
    ast: number;
    stl: number;
    blk: number;
    tov: number;
    fg_pct: number | null;
    fg3_pct: number | null;
    ft_pct: number | null;
  };
  ratings: Record<string, number>;
}

// One game inside a completed SimulationRun (from GET /simulations/{id}).
export interface SimulatedGameSummary {
  game_id: string;
  game_date: string;
  home_team: string;
  away_team: string;
  home_score: number;
  away_score: number;
  went_to_ot: boolean;
  win: boolean;   // true iff the run's team won
}

export type SimulationScope = "team" | "league" | "myleague";

// POST /simulations/ request body
export interface CreateSimulationBody {
  team: string;         // abbr
  season: string;
  seed?: number | null;
  config?: { preset?: string } | null;
}

// POST /simulations/league request body
export interface CreateLeagueSimulationBody {
  season: string;
  seed?: number | null;
  config?: { preset?: string } | null;
}

// POST /simulations/ response (also start response)
export interface SimulationCreated {
  id: number;
  team: string | null;                // null for league sims
  scope: SimulationScope;
  season: string;
  seed: number;
  status: "pending" | "running" | "complete" | "cancelled" | "failed";
}

// GET /simulations/{id} response
export interface SimulationStatus {
  id: number;
  team: string | null;                // null for league sims
  scope: SimulationScope;
  season: string;
  seed: number;
  status: "pending" | "running" | "complete" | "cancelled" | "failed";
  games_completed: number;
  total_games: number;
  wins: number | null;
  losses: number | null;
  created_at: string;
  completed_at: string | null;
  games: SimulatedGameSummary[] | null;
  failure_reason: string | null;
}

// GET /simulations/ list-response entry (subset of SimulationStatus)
export interface SimulationSummary {
  id: number;
  team: string | null;                // null for league sims
  scope: SimulationScope;
  season: string;
  seed: number;
  status: SimulationStatus["status"];
  games_completed: number;
  total_games: number;
  wins: number | null;
  losses: number | null;
  created_at: string;
  completed_at: string | null;
  controlled_team_abbr?: string | null;  // myleague scope only
}

// GET /simulations/{id}/standings response
export interface StandingsRow {
  rank: number;
  team_id: number;
  team_abbr: string;
  wins: number;
  losses: number;
  pct: number;
  gb: number;
  streak: string;   // "W3", "L2", or "-" if no games
}
export interface StandingsResponse {
  sim_id: number;
  season: string;
  is_complete: boolean;
  games_completed: number;
  total_games: number;
  standings: StandingsRow[];
}

// Sim season averages per player, side-by-side with the ingested real anchors.
export interface PlayerAveragesSim {
  gp: number;
  mpg: number;
  ppg: number;
  rpg: number;
  apg: number;
  spg: number;
  bpg: number;
  topg: number;
  pf_per_game: number;
  fg_pct: number | null;
  fg3_pct: number | null;
  ft_pct: number | null;
}
export type PlayerAveragesReal = PlayerAveragesSim;   // same key set (real values from PlayerSeasonStats)

export interface PlayerAveragesRow {
  player_id: number;
  name: string;
  sim: PlayerAveragesSim;
  real: PlayerAveragesReal | null;
}

// Team-level averages — sim carries a full per-game aggregate; real from
// TeamSeasonStats only exposes advanced-rating fields (pace, off_rating,
// def_rating, oreb_pct). Deliberately no derived values — [Xavier 2026-08-11].
export interface SeasonAverages {
  sim_id: number;
  team: string;
  season: string;
  team_totals: {
    sim: Record<string, number>;
    real: Record<string, number>;
  };
  players: PlayerAveragesRow[];
}

export interface SimulateGameResponse {
  season: string;
  seed: number;
  home_team: string;
  away_team: string;
  home_score: number;
  away_score: number;
  quarter_scores: QuarterScores;
  home_box: PlayerLine[];
  away_box: PlayerLine[];
  events?: PossessionEvent[] | null;
  // Schedule context — populated only by season/league drill-in endpoints.
  game_date?: string | null;
  matchup_index?: number | null;
  matchup_total?: number | null;
  home_game_no?: number | null;
  away_game_no?: number | null;
}

// --- MyLeague (M-1c) -------------------------------------------------------

export interface MyLeagueStateDTO {
  simulation_id: number;
  season: string;
  root_seed: number;
  controlled_team_id: number | null;
  controlled_team_abbr: string | null;
  current_calendar_date: string;   // ISO YYYY-MM-DD
  games_completed: number;
  total_games: number;
  status: string;
}

export interface MyLeagueRecentGame {
  game_id: string;
  game_date: string;
  home_team: string;
  away_team: string;
  home_score: number;
  away_score: number;
  went_to_ot: boolean;
}

export interface MyLeagueUpcomingGame {
  game_id: string;
  game_date: string;
  home_team: string;
  away_team: string;
}

export interface PreviewRosterPlayer {
  player_id: number;
  name: string;
  position: string;
  mpg: number;
  is_starter: boolean;
  ppg?: number | null;
  rpg?: number | null;
  apg?: number | null;
}

export interface NextGamePreview {
  game_id: string;
  game_date: string;
  is_home: boolean;
  opponent_abbr: string;
  matchup_index: number;
  matchup_total: number;
  series_wins_controlled: number;
  series_wins_opponent: number;
  controlled_roster: PreviewRosterPlayer[];
  opponent_roster: PreviewRosterPlayer[];
}

export interface MyLeagueSummary {
  state: MyLeagueStateDTO;
  standings: StandingsRow[];
  recent_games: MyLeagueRecentGame[];
  upcoming_games: MyLeagueUpcomingGame[];
  next_game_preview: NextGamePreview | null;
}

export interface CreateMyLeagueBody {
  season: string;
  seed?: number | null;
  controlled_team_id?: number | null;
}

export interface AppendMyLeagueEventBody {
  event_type: string;
  applied_at_date: string;
  payload: Record<string, unknown>;
}

export interface MyLeagueEventDTO {
  id: number;
  event_type: string;
  applied_at_date: string;
  payload: Record<string, unknown>;
  created_at: string;
}
