import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PlayerModal from "./PlayerModal";
import type { PlayerLine, PlayerProfile, PossessionEvent } from "../types";
import * as api from "../api";

vi.mock("../api", () => ({ getPlayerProfile: vi.fn() }));

const profile: PlayerProfile = {
  id: 5, full_name: "Test Guy", position: "G", team: "BOS", season: "2025-26",
  season_averages: { gp: 70, min: 32, pts: 24.5, reb: 4, ast: 6, stl: 1, blk: 0.2, tov: 2, fg_pct: 0.45, fg3_pct: 0.4, ft_pct: 0.9 },
  ratings: { overall: 90, three_point: 95, clutch: 88 },
};

function line(over: Partial<PlayerLine> = {}): PlayerLine {
  return {
    player_id: 5, name: "Test Guy", minutes: 30, points: 25, rebounds: 5, assists: 6,
    steals: 1, blocks: 0, turnovers: 2, personal_fouls: 2, plus_minus: 0,
    fgm: 9, fga: 15, fg3m: 3, fg3a: 6, ftm: 4, fta: 4, fouled_out: false, ...over,
  };
}

function ev(over: Partial<PossessionEvent>): PossessionEvent {
  return { possession: 1, game_clock_seconds: 600, quarter: 1, is_home: true, pts: 0, ...over };
}

const events: PossessionEvent[] = [
  ev({ scorer: 5, made: true, pts: 2, description: "Test Guy makes a layup" }),
  ev({ scorer: 99, description: "Someone Else scores" }),
  ev({ assisted_by: 5, description: "Teammate scores (assisted by Test Guy)" }),
];

beforeEach(() => vi.mocked(api.getPlayerProfile).mockResolvedValue(profile));

describe("PlayerModal", () => {
  it("shows only the player's own PBP events, tagged by involvement", async () => {
    const { container } = render(<PlayerModal line={line()} season="2025-26" events={events} onClose={() => {}} />);
    expect(screen.getByText("Test Guy makes a layup")).toBeInTheDocument();
    expect(screen.getByText("Teammate scores (assisted by Test Guy)")).toBeInTheDocument();
    expect(screen.queryByText("Someone Else scores")).not.toBeInTheDocument();
    // scope to the PBP involvement tags ("AST" also appears as a stat-column label)
    const tags = [...container.querySelectorAll(".pm-tag")].map((t) => t.textContent);
    expect(tags).toContain("SCORE");
    expect(tags).toContain("AST");
  });

  it("shows an empty message when the player has no events", () => {
    render(<PlayerModal line={line()} season="2025-26" events={[]} onClose={() => {}} />);
    expect(screen.getByText("No play-by-play events.")).toBeInTheDocument();
  });

  it("renders season averages and ratings once the profile loads", async () => {
    render(<PlayerModal line={line()} season="2025-26" events={events} onClose={() => {}} />);
    expect(await screen.findByText("24.5")).toBeInTheDocument(); // season PTS
    expect(screen.getByText("Overall")).toBeInTheDocument();
  });

  it("closes on overlay click", () => {
    const onClose = vi.fn();
    const { container } = render(<PlayerModal line={line()} season="2025-26" events={[]} onClose={onClose} />);
    (container.querySelector(".pm-overlay") as HTMLElement).click();
    expect(onClose).toHaveBeenCalled();
  });
});
