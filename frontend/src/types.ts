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
  | "SHOT" | "FOUL" | "FT" | "REB" | "TOV" | "STL" | "BLK" | "AST";

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
}
