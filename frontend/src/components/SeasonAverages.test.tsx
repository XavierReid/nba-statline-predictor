import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { SeasonAverages } from "../types";
import * as api from "../api";
import SeasonAveragesView from "./SeasonAverages";

vi.mock("../api", () => ({ getSeasonAverages: vi.fn() }));

const payload: SeasonAverages = {
  sim_id: 30,
  team: "LAL",
  season: "2025-26",
  team_totals: {
    sim:  { gp: 82, ppg: 113.5, opp_ppg: 119.6, fga: 89.2, fta: 20.8, pf: 22.4, tov: 13.5, stl: 7.3, blk: 3.4, ast: 25.2, reb: 42.1, fgm: 42.0, ftm: 16.4, fg3m: 13.0, fg3a: 37.2 } as unknown as Record<string, number>,
    real: { pace: 99.1, off_rating: 117.3, def_rating: 113.8, oreb_pct: 0.27 } as unknown as Record<string, number>,
  },
  players: [
    {
      player_id: 1, name: "LeBron James",
      sim:  { gp: 82, mpg: 34.7, ppg: 22.3, rpg: 7.8, apg: 8.1, spg: 1.2, bpg: 0.6, topg: 3.4, pf_per_game: 1.8, fg_pct: 0.5, fg3_pct: 0.36, ft_pct: 0.78 },
      real: { gp: 66, mpg: 34.2, ppg: 24.1, rpg: 7.5, apg: 8.3, spg: 1.3, bpg: 0.5, topg: 3.5, pf_per_game: 1.6, fg_pct: 0.51, fg3_pct: 0.38, ft_pct: 0.75 },
    },
    {
      player_id: 2, name: "Rui Hachimura",
      sim:  { gp: 82, mpg: 28.0, ppg: 14.0, rpg: 4.2, apg: 1.5, spg: 0.5, bpg: 0.4, topg: 1.1, pf_per_game: 2.2, fg_pct: 0.49, fg3_pct: 0.42, ft_pct: 0.71 },
      real: null,   // rookie / missing anchor
    },
  ],
};

beforeEach(() => {
  vi.mocked(api.getSeasonAverages).mockResolvedValue(payload);
});

describe("SeasonAveragesView", () => {
  it("renders team strip + player table with sim / real inline", async () => {
    render(<SeasonAveragesView simId={30} />);
    // Team strip: sim PPG cell shows the sim value; real column is "—" per design (no derived).
    expect(await screen.findByText("Team averages")).toBeInTheDocument();
    // Legend hint
    expect(screen.getAllByText("sim").length).toBeGreaterThan(0);
    // Player rows
    expect(screen.getByText("LeBron James")).toBeInTheDocument();
    // LeBron sim MPG 34.7 rendered
    expect(screen.getAllByText("34.7").length).toBeGreaterThan(0);
  });

  it("renders '—' for missing real anchors", async () => {
    render(<SeasonAveragesView simId={30} />);
    await screen.findByText("Rui Hachimura");
    // Missing real produces "—" cells for Rui's real values
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("sorts by column when header clicked", async () => {
    render(<SeasonAveragesView simId={30} />);
    // Default sort: MPG desc — LeBron (34.7) before Rui (28.0).
    await screen.findByText("LeBron James");
    const bodyText = document.body.textContent ?? "";
    const lIdx = bodyText.indexOf("LeBron James");
    const rIdx = bodyText.indexOf("Rui Hachimura");
    expect(lIdx).toBeLessThan(rIdx);

    // Click MPG to flip to ascending — Rui should now come first.
    fireEvent.click(screen.getByText(/^MPG/));
    const afterText = document.body.textContent ?? "";
    const lIdx2 = afterText.indexOf("LeBron James");
    const rIdx2 = afterText.indexOf("Rui Hachimura");
    expect(rIdx2).toBeLessThan(lIdx2);
  });
});
