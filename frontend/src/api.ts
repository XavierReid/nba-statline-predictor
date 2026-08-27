import type {
  CreateLeagueSimulationBody,
  CreateSimulationBody,
  PlayerProfile,
  SeasonAverages,
  SeasonCoverage,
  SimulateGameResponse,
  SimulatedGameSummary,
  SimulationCreated,
  SimulationStatus,
  SimulationSummary,
  StandingsResponse,
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

export async function getSeasonAverages(simId: number): Promise<SeasonAverages> {
  return get<SeasonAverages>(`/simulations/${simId}/averages`);
}

export async function createSimulation(body: CreateSimulationBody): Promise<SimulationCreated> {
  return post<SimulationCreated>("/simulations/", body);
}

// League sim (C-2). Standings + team-in-league drill-in are derived state.
export async function createLeagueSimulation(
  body: CreateLeagueSimulationBody
): Promise<SimulationCreated> {
  return post<SimulationCreated>("/simulations/league", body);
}

export async function getStandings(simId: number): Promise<StandingsResponse> {
  return get<StandingsResponse>(`/simulations/${simId}/standings`);
}

export async function getLeagueTeamGames(
  simId: number, teamAbbr: string
): Promise<SimulatedGameSummary[]> {
  return get<SimulatedGameSummary[]>(
    `/simulations/${simId}/team/${encodeURIComponent(teamAbbr)}/games`
  );
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

// --- MyLeague (M-1c) -------------------------------------------------------

export async function createMyLeague(body: import("./types").CreateMyLeagueBody): Promise<import("./types").MyLeagueStateDTO> {
  return post<import("./types").MyLeagueStateDTO>("/myleague/", body);
}

export async function advanceMyLeague(id: number, targetDate: string): Promise<import("./types").MyLeagueStateDTO> {
  return post<import("./types").MyLeagueStateDTO>(`/myleague/${id}/advance`, { target_date: targetDate });
}

export async function appendMyLeagueEvent(id: number, body: import("./types").AppendMyLeagueEventBody): Promise<import("./types").MyLeagueEventDTO> {
  return post<import("./types").MyLeagueEventDTO>(`/myleague/${id}/events`, body);
}

export async function getMyLeagueTeam(
  simId: number, teamAbbr: string,
): Promise<import("./types").TeamDrillInResponse> {
  return get<import("./types").TeamDrillInResponse>(
    `/myleague/${simId}/team/${encodeURIComponent(teamAbbr)}`
  );
}

export async function getSimulationPlayerStats(
  simId: number, playerId: number,
): Promise<import("./types").MyLeaguePlayerStats> {
  // Scope-agnostic — works for team, league, and MyLeague sims. The /myleague
  // route is retained as a thin backwards-compat wrapper on the backend.
  return get<import("./types").MyLeaguePlayerStats>(
    `/simulations/${simId}/player/${playerId}`
  );
}

export async function getMyLeague(id: number): Promise<import("./types").MyLeagueSummary> {
  return get<import("./types").MyLeagueSummary>(`/myleague/${id}`);
}
