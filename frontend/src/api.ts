import type {
  CreateSimulationBody,
  PlayerProfile,
  SeasonCoverage,
  SimulateGameResponse,
  SimulationCreated,
  SimulationStatus,
  SimulationSummary,
  Team,
} from "./types";

async function post<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r.text();
    const err = new Error(`${r.status} ${detail}`) as Error & { status?: number };
    err.status = r.status;
    throw err;
  }
  return r.json();
}

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

export async function getPlayerProfile(id: number, season: string): Promise<PlayerProfile> {
  return get<PlayerProfile>(`/players/${id}/profile?season=${encodeURIComponent(season)}`);
}

export interface SimulateArgs {
  home_team: string;
  away_team: string;
  season: string;
  seed?: number;
  preset: string;
  include_pbp: boolean;
}

// Season sim (B1: read-only browse).
export async function listSimulations(): Promise<SimulationSummary[]> {
  return get<SimulationSummary[]>("/simulations/");
}

export async function getSimulation(id: number): Promise<SimulationStatus> {
  return get<SimulationStatus>(`/simulations/${id}`);
}

export async function getSeasonGame(
  simId: number,
  gameId: string
): Promise<SimulateGameResponse> {
  return get<SimulateGameResponse>(`/simulations/${simId}/games/${encodeURIComponent(gameId)}`);
}

export async function createSimulation(body: CreateSimulationBody): Promise<SimulationCreated> {
  return post<SimulationCreated>("/simulations/", body);
}

export async function startSimulation(id: number): Promise<SimulationCreated> {
  return post<SimulationCreated>(`/simulations/${id}/start`, {});
}

export async function cancelSimulation(id: number): Promise<{ id: number; status: string }> {
  return post<{ id: number; status: string }>(`/simulations/${id}/cancel`, {});
}

export async function deleteSimulation(id: number): Promise<{ id: number; deleted: boolean }> {
  const r = await fetch(`/simulations/${id}`, { method: "DELETE" });
  if (!r.ok) {
    const err = new Error(`${r.status} ${await r.text()}`) as Error & { status?: number };
    err.status = r.status;
    throw err;
  }
  return r.json();
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
