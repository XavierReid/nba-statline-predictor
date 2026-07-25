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

export interface PossessionEvent {
  possession: number;
  game_clock_seconds: number;
  quarter: number;
  is_home: boolean;
  pts: number;
  running_home_score?: number | null;
  running_away_score?: number | null;
  description?: string | null;
  made?: boolean | null;
  // per-player involvement ids — let the modal filter a game's PBP to one player
  scorer?: number | null;
  assisted_by?: number | null;
  rebounded_by?: number | null;
  turnover_by?: number | null;
  steal_by?: number | null;
  block_by?: number | null;
  fouled_by?: number | null;
}

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
