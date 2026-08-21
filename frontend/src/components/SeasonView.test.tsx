import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { SimulatedGameSummary, SimulationStatus, SimulationSummary } from "../types";
import * as api from "../api";
import SeasonView, { computeStandings, extendRow } from "./SeasonView";

vi.mock("../api", () => ({
  listSimulations: vi.fn(),
  getSimulation: vi.fn(),
  getSeasonGame: vi.fn(),
  createSimulation: vi.fn(),
  startSimulation: vi.fn(),
  cancelSimulation: vi.fn(),
  deleteSimulation: vi.fn(),
  getSeasons: vi.fn(async () => []),
  getTeams: vi.fn(async () => []),
}));

const TEAM = "BOS";
const SEASON = "2025-26";

function summary(over: Partial<SimulatedGameSummary> = {}): SimulatedGameSummary {
  return {
    game_id: "0022500001",
    game_date: "2025-10-22",
    home_team: TEAM,
    away_team: "NYK",
    home_score: 110,
    away_score: 100,
    went_to_ot: false,
    win: true,
    ...over,
  };
}

function runStatus(games: SimulatedGameSummary[]): SimulationStatus {
  return {
    id: 1,
    team: TEAM,
    scope: "team",
    season: SEASON,
    seed: 26,
    status: "complete",
    games_completed: games.length,
    total_games: games.length,
    wins: games.filter((g) => g.win).length,
    losses: games.filter((g) => !g.win).length,
    created_at: "2026-08-10T00:00:00Z",
    completed_at: "2026-08-10T00:00:07Z",
    games,
  };
}

describe("computeStandings", () => {
  it("counts wins, home/away splits, PPG, blowouts, OT", () => {
    const games = [
      summary({ game_id: "1", home_team: TEAM, away_team: "NYK", home_score: 120, away_score: 100, win: true }),
      summary({ game_id: "2", home_team: "NYK", away_team: TEAM, home_score: 90,  away_score: 95,  win: true }),
      summary({ game_id: "3", home_team: TEAM, away_team: "MIA", home_score: 100, away_score: 130, win: false }),
      summary({ game_id: "4", home_team: "MIA", away_team: TEAM, home_score: 110, away_score: 108, went_to_ot: true, win: false }),
    ];
    const rows = games.map((g) => extendRow(g, TEAM));
    const s = computeStandings(rows);
    expect(s.gp).toBe(4);
    expect(s.w).toBe(2);
    expect(s.l).toBe(2);
    expect(s.homeW).toBe(1);   // game 1 win
    expect(s.homeL).toBe(1);   // game 3 loss (was home)
    expect(s.awayW).toBe(1);   // game 2 win
    expect(s.awayL).toBe(1);   // game 4 loss
    // scored: 120, 95, 100, 108 → mean 105.75
    expect(s.ppgScored).toBeCloseTo(105.75, 2);
    // allowed: 100, 90, 130, 110 → mean 107.5
    expect(s.ppgAllowed).toBeCloseTo(107.5, 2);
    // blowouts: game 1 (+20), game 3 (-30) → 2/4
    expect(s.blowoutRate).toBeCloseTo(0.5, 2);
    expect(s.otRate).toBeCloseTo(0.25, 2);
  });

  it("handles empty rows without dividing by zero", () => {
    const s = computeStandings([]);
    expect(s.gp).toBe(0);
    expect(s.wPct).toBe(0);
    expect(s.ppgScored).toBe(0);
  });
});

describe("extendRow", () => {
  it("computes margin from the team's perspective on both home and away", () => {
    const home = extendRow(summary({ home_team: TEAM, home_score: 110, away_score: 100 }), TEAM);
    expect(home.isHome).toBe(true);
    expect(home.margin).toBe(10);
    expect(home.opponent).toBe("NYK");

    const away = extendRow(summary({ home_team: "NYK", away_team: TEAM, home_score: 100, away_score: 110 }), TEAM);
    expect(away.isHome).toBe(false);
    expect(away.margin).toBe(10);
    expect(away.opponent).toBe("NYK");
  });
});

describe("SeasonView state machine", () => {
  it("shows the new-sim form when no runs exist (B2)", async () => {
    vi.mocked(api.listSimulations).mockResolvedValue([]);
    render(<SeasonView />);
    expect(await screen.findByText(/Start a new season simulation/i)).toBeInTheDocument();
  });

  it("drops into active-run view when a running sim exists (B2)", async () => {
    const activeSummary: SimulationSummary = {
      id: 7, team: TEAM, scope: "team", season: SEASON, seed: 42, status: "running",
      games_completed: 10, total_games: 82, wins: null, losses: null,
      created_at: "2026-08-11T12:00:00Z", completed_at: null,
    };
    const activeStatus: SimulationStatus = {
      ...activeSummary, seed: 42, games: null,
    };
    vi.mocked(api.listSimulations).mockResolvedValue([activeSummary]);
    vi.mocked(api.getSimulation).mockResolvedValue(activeStatus);
    render(<SeasonView />);
    // Progress copy — "<games_completed> / <total_games> games"
    expect(await screen.findByText(/10/)).toBeInTheDocument();
    expect(await screen.findByText(/Cancel/i)).toBeInTheDocument();
  });

  it("renders standings + game list once a completed run loads", async () => {
    const games = [summary({ game_id: "g1", home_team: TEAM, home_score: 111, away_score: 105, win: true })];
    const status = runStatus(games);
    const listRow: SimulationSummary = {
      id: 1, team: TEAM, scope: "team", season: SEASON, seed: 26, status: "complete",
      games_completed: 1, total_games: 1, wins: 1, losses: 0,
      created_at: "2026-08-10T00:00:00Z", completed_at: "2026-08-10T00:00:07Z",
    };
    vi.mocked(api.listSimulations).mockResolvedValue([listRow]);
    vi.mocked(api.getSimulation).mockResolvedValue(status);

    render(<SeasonView />);
    // Standings record (1-0) renders both in the header and standings row.
    expect((await screen.findAllByText("1-0")).length).toBeGreaterThan(0);
    // Game row is present.
    expect(await screen.findByText("2025-10-22")).toBeInTheDocument();
    // W column
    expect(screen.getAllByText("W").length).toBeGreaterThan(0);
  });

  it("renders the run picker with all persisted runs (B3)", async () => {
    const games = [summary({ game_id: "g1", home_team: TEAM, home_score: 111, away_score: 105, win: true })];
    const status = runStatus(games);
    const rows: SimulationSummary[] = [
      { id: 1, team: TEAM, scope: "team", season: SEASON, seed: 26, status: "complete", games_completed: 82, total_games: 82, wins: 1, losses: 0, created_at: "2026-08-10T00:00:00Z", completed_at: "2026-08-10T00:00:07Z" },
      { id: 2, team: "OKC", scope: "team", season: SEASON, seed: 27, status: "complete", games_completed: 82, total_games: 82, wins: 55, losses: 27, created_at: "2026-08-09T00:00:00Z", completed_at: "2026-08-09T00:00:07Z" },
    ];
    vi.mocked(api.listSimulations).mockResolvedValue(rows);
    vi.mocked(api.getSimulation).mockResolvedValue(status);

    render(<SeasonView />);
    // Toggle picker
    const toggle = await screen.findByText(/Runs \(2\)/i);
    fireEvent.click(toggle);
    // Both team labels should appear inside the menu
    expect(screen.getAllByText(TEAM).length).toBeGreaterThan(0);
    expect(screen.getByText("OKC")).toBeInTheDocument();
  });

  it("delete calls the API and refreshes the picker (B3)", async () => {
    const games = [summary({ game_id: "g1", home_team: TEAM, home_score: 111, away_score: 105, win: true })];
    const status = runStatus(games);
    const rows: SimulationSummary[] = [
      { id: 1, team: TEAM, scope: "team", season: SEASON, seed: 26, status: "complete", games_completed: 82, total_games: 82, wins: 1, losses: 0, created_at: "2026-08-10T00:00:00Z", completed_at: null },
      { id: 2, team: "OKC", scope: "team", season: SEASON, seed: 27, status: "complete", games_completed: 82, total_games: 82, wins: 55, losses: 27, created_at: "2026-08-09T00:00:00Z", completed_at: null },
    ];
    vi.mocked(api.listSimulations).mockResolvedValueOnce(rows).mockResolvedValueOnce([rows[0]]);
    vi.mocked(api.getSimulation).mockResolvedValue(status);
    vi.mocked(api.deleteSimulation).mockResolvedValue({ id: 2, deleted: true });

    render(<SeasonView />);
    fireEvent.click(await screen.findByText(/Runs \(2\)/i));
    // Delete row for OKC — the second row's Delete button
    const deleteBtns = screen.getAllByText("Delete");
    fireEvent.click(deleteBtns[1]);
    // Confirmation prompt
    fireEvent.click(screen.getByText("Yes"));
    await new Promise((r) => setTimeout(r, 20));
    expect(api.deleteSimulation).toHaveBeenCalledWith(2);
  });

  it("opens the back button when a game row is clicked and fetches game detail", async () => {
    const games = [summary({ game_id: "g1", home_team: TEAM })];
    const status = runStatus(games);
    const listRow: SimulationSummary = {
      id: 1, team: TEAM, scope: "team", season: SEASON, seed: 26, status: "complete",
      games_completed: 1, total_games: 1, wins: 1, losses: 0,
      created_at: "2026-08-10T00:00:00Z", completed_at: null,
    };
    vi.mocked(api.listSimulations).mockResolvedValue([listRow]);
    vi.mocked(api.getSimulation).mockResolvedValue(status);
    // Never resolves — we only want to verify the loading + back button.
    vi.mocked(api.getSeasonGame).mockReturnValue(new Promise(() => {}));

    render(<SeasonView />);
    const row = await screen.findByText("2025-10-22");
    fireEvent.click(row);
    expect(await screen.findByText(/Back to season/i)).toBeInTheDocument();
    expect(api.getSeasonGame).toHaveBeenCalledWith(1, "g1");
  });
});
