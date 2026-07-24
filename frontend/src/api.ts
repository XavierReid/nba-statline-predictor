import type { SeasonCoverage, SimulateGameResponse, Team } from "./types";

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} for ${url}`);
  return r.json();
}

export async function getSeasons(): Promise<SeasonCoverage[]> {
  const seasons = await get<SeasonCoverage[]>("/ingestion/seasons");
  return seasons.filter((s) => s.ready).sort((a, b) => b.season.localeCompare(a.season));
}

export async function getTeams(season: string): Promise<Team[]> {
  return get<Team[]>(`/teams?season=${encodeURIComponent(season)}`);
}

export interface SimulateArgs {
  home_team: string;
  away_team: string;
  season: string;
  seed?: number;
  preset: string;
  include_pbp: boolean;
}

export async function simulateGame(args: SimulateArgs): Promise<SimulateGameResponse> {
  const body = {
    home_team: args.home_team,
    away_team: args.away_team,
    season: args.season,
    seed: args.seed ?? null,
    include_pbp: args.include_pbp,
    config: { preset: args.preset },
  };
  const r = await fetch("/simulations/game", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`Simulation failed (${r.status}): ${detail}`);
  }
  return r.json();
}
